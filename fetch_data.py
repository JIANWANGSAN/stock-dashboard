# -*- coding: utf-8 -*-
"""
A股短线仪表盘 - 数据采集 / 节点判定 / 推荐计算

用法:
    python fetch_data.py            # 抓取当日数据并刷新 data.json
    python fetch_data.py --days 60  # 指定回溯的交易日数量

输出:
    data.json         前端读取的主数据
    nodes.json        节点票池（累积，含跟踪状态）
    board_history.json 板块每日涨幅累积（用于3日累计榜）
"""

import sys, os, json, time, re, argparse, threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# 板块/指数 K线取数（东财优先、被封时自动切同花顺）；勿从本模块 import 以免循环依赖
from board_kline_src import (fetch_board_klines as _bk_fetch_board_klines,
                             fetch_ths_amount as _bk_fetch_ths_amount,
                             THS_INDEX as _BK_THS_INDEX,
                             SOURCE_PREF as _BK_SOURCE_PREF)
# 产业板块清单（东财BK代码）→ 同花顺板块指数码/名
from ths_board_map import THS_MAP
# 同花顺统一取数层（2026-09-17 起：东财停用，全站数据源收敛到这里）
import ths_source as THS
# 高标跟踪（近半月 ≥4 连板 → 按题材归类 → 跟到跌停为止）
import bigboards as BB

sys.stdout.reconfigure(encoding='utf-8')
import urllib.request

try:
    from pypinyin import lazy_pinyin
    PINYIN_OK = True
    def pinyin_abbr(name):
        return ''.join([w[0].upper() for w in lazy_pinyin(name) if w and w[0].isalpha()])
except ImportError:
    # ⛔ 绝不要在这里"静默返回空串"！
    #    2026-09-22 事故：环境重建后 pypinyin 没装回来 → 全站「股票缩写」全变空字符串，
    #    脚本一声不响跑完，直到用户发现「缩写怎么又丢了」。→ 改为亮明状态 + 运行时告警。
    PINYIN_OK = False
    def pinyin_abbr(name):
        return ''


def pinyin_coverage(obj):
    """统计 data 里 pinyin 字段的非空率 → (非空数, 总数)。

    用于每次跑完自检：正常应 >90%（少数名字无汉字拼音，如「N新亚」这类除外）。
    若几乎全空 = pypinyin 没装 → 见 main() 开头的告警。
    """
    n = empty = 0

    def walk(x):
        nonlocal n, empty
        if isinstance(x, dict):
            if 'pinyin' in x:
                n += 1
                if not x.get('pinyin'):
                    empty += 1
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)
    return n - empty, n

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_JSON      = os.path.join(BASE, 'data.json')
NODES_JSON     = os.path.join(BASE, 'nodes.json')
BOARD_HIST     = os.path.join(BASE, 'board_history.json')
STOCK_CACHE    = os.path.join(BASE, '.stock_cache.json')
SEAL_CACHE     = os.path.join(BASE, '.seal_cache.json')
_SEAL_CACHE_LOCK = threading.Lock()      # 保护 SEAL_CACHE 的 load→save（多线程调用防丢条目）
EVENT_CACHE    = os.path.join(BASE, '.event_cache.json')
TRADE_DAYS_CACHE = os.path.join(BASE, '.trade_days_cache.json')
# 用户核对后的总市值修正（元），按 (code, yyyymmdd) 锁定日期，避免污染未来交易日
CAP_OVERRIDE = {
    ('605577', '20260904'): 7604000000,  # 龙版传媒 2026-09-04 核对总市值 76.04 亿（原 69.11 亿）
}

# 用户核对后的上板分时量修正（元）：(上板分时量, 全天成交额)，按 code_date 锁定
# 自动口径为"首封时刻累计成交额"，个别票以用户核对值为准
SEAL_OVERRIDE = {
    '605577_2026-09-04': (132000000, 495051040),  # 龙版传媒：用户核对上板分时量 1.32 亿
    '002403_2026-09-08': (87380475, 592205184),   # 爱仕达：一字板，集合竞价上板量 0.87亿(用户核对<1亿)
    '600354_2026-09-08': (206000000, 513000000),  # 敦煌种业：自然板首封(09:31)分时量 2.06亿(东财限流期核实)
    '002980_2026-09-08': (1084000000, 2289000000),  # 华盛昌：自然板首封(09:54)分时量 10.84亿(东财限流期核实)
}

# ============ 全局剔除规则（北交所 / 科创板 / ST）============
# 用户要求：所有股票池一律不收集 北交所、科创板、ST 票。
# 在 fetch_zt_pool() 源头过滤，下游（节点/候选/模块4）自动生效。
def is_excluded(code, name=''):
    """返回 True 表示该票需要剔除（北交所 / 科创板 / ST）"""
    c = str(code or '')
    if c.startswith(('688', '689')):          # 科创板
        return True
    if c[:1] in ('4', '8') or c.startswith('92'):   # 北交所 43/83/87/88/92
        return True
    n = str(name or '').upper().replace(' ', '').replace('\u3000', '')
    if 'ST' in n:                              # ST / *ST / S*ST / SST
        return True
    if n.endswith('退') or '退市' in n:         # 退市整理
        return True
    return False


# 节点票池总市值上限（元）：只保留 200 亿以下的小盘弹性票
NODE_CAP_LIMIT = 200 * 1e8

# 概念口径版本号：口径变更时自增，强制重抓缓存（避免旧口径的行业名污染）
TAGS_VER = 3

# 全量板块当日涨幅表 {板块名: 涨幅%}，用于给个股概念做「当下热度」排序
BOARD_HEAT = {}

# ============ 概念精准化 ============
# 东财「所属板块」混装了 行业大类 / 地域 / 财务技术标签 / 真题材概念，
# 且真正的炒作概念常被排在第 20 位之后（如华盛昌的「光通信模块」在第22位，
# 旧版截断 [:6] 只拿到 电网设备/电力设备 等行业名）。
# 新版：全量抓取 → 剔除 噪声/地域/行业 → 别名归一 → 人工兜底。

# 人工概念修正：东财命名 ≠ 短线共识命名时在此修正（用户指出一只补一只）
CONCEPT_ALIAS = {
    '光通信模块': 'CPO', '共封装光学': 'CPO', 'CPO概念': 'CPO', '光模块': 'CPO',
    '光纤概念': 'CPO', '光通信': 'CPO',
    '锂离子电池': '锂电池', '锂电池概念': '锂电池', '电池技术': '锂电池',
    '储能概念': '储能', '光伏概念': '光伏', '风能': '风电', '风电概念': '风电',
    '核电概念': '核电', '核能发电': '核电',
    '人工智能': 'AI', 'AI智能体': 'AI', 'AI眼镜': 'AI眼镜', 'ChatGPT概念': 'AI',
    'AIGC概念': 'AI', 'DeepSeek概念': 'AI', '算力租赁': '算力', '东数西算': '算力',
    '数字货币': '稳定币', '数字人民币': '稳定币', '跨境支付': '稳定币',
    '区块链': '稳定币', 'RWA概念': 'RWA',
    '人形机器人': '机器人', '工业机器人': '机器人', '机器人概念': '机器人',
    '军工': '军工', '国防军工': '军工', '中船系': '中船系', '船舶制造': '中船系',
    '半导体概念': '半导体', '芯片概念': '芯片', '集成电路': '芯片',
    '创新药': '创新药', '减肥药': '减肥药', '体外诊断概念': '体外诊断',
    '房地产开发': '地产', '房地产': '地产',
    '在线教育': '教育', '职业教育': '教育',
    '网络游戏': '游戏', '手机游戏': '游戏', '云游戏': '游戏',
    '猪肉概念': '猪肉', '生猪养殖': '猪肉', '养鸡': '养鸡', '种业': '种业',
    '粮食概念': '粮食', '转基因': '种业',
    '稀土永磁': '稀土', '小金属概念': '小金属', '黄金概念': '黄金',
    '白酒概念': '白酒', '食品饮料': '食品饮料',
    '固态电池': '固态电池', '钠离子电池': '钠电池', '氢能源': '氢能',
    '充电桩': '充电桩', '智能电网': '智能电网', '虚拟电厂': '虚拟电厂',
    '数据要素': '数据要素', '数据安全': '数据安全', '信创': '信创',
    '元宇宙概念': '元宇宙', '虚拟现实': 'VR', '鸿蒙概念': '鸿蒙',
    '物联网': '物联网', '车联网': '车联网', '智能驾驶': '智能驾驶',
    '无人驾驶': '智能驾驶', '低空经济': '低空经济', '飞行汽车': '低空经济',
    '商业航天': '商业航天', '卫星导航': '卫星互联网', '6G概念': '6G',
    '5G概念': '5G', 'PCB概念': 'PCB', '印制电路板': 'PCB',
    '超导概念': '超导', '核污染防治': '核污染防治', '节能环保': '节能环保',
    '央国企改革': '国企改革', '央企改革': '国企改革', '创投': '创投',
    '西部大开发': '西部大开发', '一带一路': '一带一路',
}

# 人工兜底：东财板块完全抓不到该题材时，直接指定（code → 概念列表）
CONCEPT_FIX = {
    '002980': ['CPO', 'AI眼镜', '物联网'],   # 华盛昌：市场按 CPO 炒，东财只给电网设备
}

# ============ 涨停原因（事件 / 消息驱动）============
# 同一只票每一波可能走不同概念：有的是题材轮动，有的是重组、中标、业绩等消息刺激。
# 涨停原因从「个股近期新闻标题」中提取，优先级高于静态概念（因为是当下驱动）。
EVENT_KW = {
    '重组':   ['重组', '并购', '收购', '资产注入', '借壳', '要约', '股权转让', '控制权变更'],
    '中标':   ['中标', '预中标', '中标候选', '拿下'],
    '业绩预增': ['业绩预增', '预增', '业绩大增', '净利', '扭亏', '扭亏为盈', '利润增长', '中报预增', '三季报预增'],
    '订单':   ['订单', '大单', '合同', '签署', '战略合作', '框架协议'],
    '涨价':   ['涨价', '提价', '价格上调', '上调价格', '涨价函'],
    '政策利好': ['政策', '补贴', '扶持', '规划', '试点', '新政', '利好政策'],
    '回购增持': ['回购', '增持', '回购股份'],
    '技术突破': ['技术突破', '首发', '量产', '研发成功', '攻克', '国产替代'],
    '产能扩张': ['扩产', '投产', '新建', '产能', '基地开工', '项目开工'],
    '龙虎榜':  ['龙虎榜'],
    '历史新高': ['创历史新高', '历史新高', '新高'],
    '异动':   ['异动', '涨停', '封板', '直线拉升', '秒板'],
    '资金流入': ['主力净流入', '资金流入', '北向', '机构买入'],
}
# 真·涨停原因（进 concepts，优先级最高）
EVENT_REAL = ['重组', '中标', '业绩预增', '订单', '涨价', '政策利好',
              '回购增持', '技术突破', '产能扩张']
# 现象描述（不进 concepts，仅作为辅助标签展示）
EVENT_PHENOM = ['龙虎榜', '历史新高', '异动', '资金流入']
# 市场综述类标题：提到个股只是碰巧，不能当作该股涨停原因
NEWS_SKIP = ['焦点复盘', '龙虎榜', '快评', '午报', '晚报', '早报', '涨跌停',
             '机构调研', '营业部', '竞价看龙头', '百元股', '晚间', '收盘',
             '开盘', '收评', '盘前', '异动股', '强势个股', '涨停股分析',
             '公司提示风险', '资金抢筹', '净买入', '调研股', '大宗交易']
# 新闻标题中提取「XX板块/概念 + 走高/异动/拉升」→ 该票当下正在炒的概念
NEWS_BOARD_RE = re.compile(
    r'([\u4e00-\u9fa5A-Za-z0-9]{2,8})(?:板块|概念股?|概念)(?:短线|快速|持续|大幅)?'
    r'(?:走高|走强|异动|拉升|大涨|爆发|活跃|领涨|上攻|走俏)')
# 「XX板块」里 XX 本身是概念的（如 CPO、光模块、固态电池）
NEWS_BOARD_RE2 = re.compile(r'([\u4e00-\u9fa5A-Za-z0-9]{2,8})(?:板块|概念股?)(?:.{0,6})(?:涨停|封板|大涨|拉升)')

# 噪声板块：财务/技术/资金/行情标签，与题材无关
NOISE_KW = ('昨日', '近期', '百日', '最近', '东方财富', '精准诊断', '趋势股', '题材股',
            '百元股', '高价股', '低价股', '破净股', '破发股', '破增发价', '高市净率',
            '低市盈率', '高质押', '低估值', '高股息', '中报', '年报', '季报', '预增',
            '预减', '首亏', '扭亏', '续亏', '预盈', '业绩', '含一字', '高换手',
            '高振幅', '融资融券', '沪股通', '深股通', '转融券', '转债标的', '可转债',
            '标准普尔', 'MSCI', '富时罗素', 'AH股', 'HS300', '上证180', '上证50',
            '中证500', '中证1000', '创业板综', '创业成份', '深成500', '深证100R',
            '机构重仓', '基金重仓', '社保重仓', 'QFII重仓', '参股银行', '参股券商',
            '参股保险', '举牌', '增持', '回购', '送转', '高送转', '分红',
            '融资', '定增', '新股与次新股', '次新股', '注册制次新股', 'ST股',
            '壳资源', '重组', '股权转让', '要约', '深股通', '同花顺', '大智慧',
            '标普', '道琼斯', '沪企改革', 'AB股', 'B股', 'H股', '科创板', '北交所',
            '专精特新', '独角兽', '超级品牌', '央视50', '茅指数', '宁组合',
            '茅概念', '宁概念', '基金重仓', '社保基金', '险资', '私募', '游资',
            '龙虎榜', '大宗交易', '股权激励', '员工持股', '限售股', '解禁',
            '首发', '打新', '市值', '流通', '总市值', '微盘股', '小盘股', '大盘股',
            '中盘股', '权重股', '蓝筹', '白马', '黑马', '妖股', '牛股', '强势股',
            '弱势股', '活跃股', '冷门股', '热门股', '人气股', '龙头', '涨停',
            '连板', '首板', '炸板', '跌停', 'ST', '退市', '风险', '警示')

# 行业大类（东财/申万行业名）：从 concepts 中剔除，只保留真题材概念。
# 该表由本仓库 .stock_cache.json 中 223 个 industry 值 + 申万一级行业汇总而来。
INDUSTRY_BLOCK = set('''
农林牧渔 种植业 渔业 林业 养殖 种子 其他种植业 其他养殖 生猪养殖 畜禽饲料 水产饲料
粮油加工 其他农产品加工 食品及饲料添加剂 动物保健 农药 化肥 氮肥 磷肥及磷化工 复合肥
基础化工 化学原料 化学制品 化学制剂 化学制药 原料药 其他化学原料 其他化学制品
纺织化学制品 无机盐 氯碱 煤化工 聚氨酯 涤纶 炭黑 合成树脂 塑料 其他塑料制品
化学纤维 橡胶 非金属材料 化学工程 民爆制品
钢铁 特钢 普钢 冶钢原料 有色金属 工业金属 贵金属 小金属 其他小金属 能源金属
金属新材料 铝 铜 铅锌 白银 黄金 稀土 磁性材料
电子 半导体 元件 光学光电子 消费电子 电子化学品 其他电子 面板 LED 光学元件
印制电路板 被动元件 分立器件 模拟芯片设计 数字芯片设计 集成电路制造 集成电路封测
汽车 汽车零部件 底盘与发动机系统 车身附件及饰件 汽车电子电气系统 其他汽车零部件
汽车服务 商用载货车 商用载客车 乘用车 摩托车及其他 轮胎轮毂 汽车销售
家用电器 白色家电 黑色家电 小家电 厨卫电器 家电零部件 照明设备 卫浴电器 厨房电器
其他家电 家用轻工 家居用品 成品家居 定制家居 其他家居用品 文娱用品 娱乐用品
食品饮料 白酒 其他酒类 啤酒 软饮料 乳品 零食 预加工食品 肉制品 调味发酵品
保健品 食品加工 饮料乳品 休闲食品
纺织服饰 纺织制造 服装家纺 非运动服装 运动服装 鞋帽及其他 钟表珠宝 辅料
轻工制造 造纸 包装印刷 纸包装 塑料包装 金属包装 印刷包装机械 特种纸 个护用品
化妆品制造及其他 珠宝首饰 文具 玩具
医药生物 医疗器械 医疗服务 医疗研发外包 体外诊断 诊断服务 其他医疗服务 医院
中药 疫苗 血液制品 其他生物制品 生物制品 医药流通 医疗设备 医疗机构 医疗美容
化学制剂 原料药 创新药
公用事业 电力 热力服务 燃气 水务及水治理 火力发电 水力发电 风力发电 光伏发电
核力发电 电能综合服务 热电
交通运输 物流 铁路公路 港口 机场 航空机场 航运 航运港口 公交 高速公路
原材料供应链服务 端到端供应链服务 仓储物流 快递
房地产 房地产开发 住宅开发 房产租赁经纪 物业管理 房地产服务 园区开发 房屋建设
建筑装饰 基础建设 专业工程 工程咨询服务 装修装饰 国际工程 园林工程 钢结构
其他专业工程 房屋建设 其他建材 水泥制造 玻璃玻纤 玻纤制造 装修建材 瓷砖地板
水泥 涂料油墨 卫浴制品 其他建材Ⅲ 耐火材料
商贸零售 贸易 一般零售 百货 超市 多业态零售 专业连锁 互联网电商 旅游零售
社会服务 教育 学历教育 教育运营及其他 培训教育 体育 本地生活服务 专业服务
酒店 餐饮 旅游及景区 人工景区 自然景区 旅游综合 其他社会服务 检测服务
综合 综合Ⅱ 综合Ⅲ
建筑材料 建筑装饰 电力设备 电池 光伏设备 风电设备 电网设备 电机 其他电源设备
光伏电池组件 光伏加工设备 光伏辅材 锂电池 蓄电池及其他电池 电池化学品
锂电专用设备 配电设备 线缆部件及其他 火电设备 电气设备
机械设备 通用设备 专用设备 轨交设备 工程机械 工程机械整机 机床工具 磨具磨料
楼宇设备 制冷空调设备 工控设备 仪器仪表 电工仪器仪表 其他自动化设备 其他通用设备
其他专用设备 能源及重型设备 农用机械 纺织服装设备 印刷包装机械 其他机械设备
国防军工 航天装备 航空装备 地面兵装 军工电子 船舶制造 航天器 兵器兵装
计算机 计算机设备 其他计算机设备 软件开发 横向通用软件 垂直应用软件 IT服务
其他通信设备 通信设备 通信线缆及配套 其他通信服务 通信工程及服务
传媒 出版 大众出版 数字媒体 广告营销 营销代理 影视院线 影视动漫制作 游戏
门户网站 社交 文化娱乐 其他传媒
通信 通信服务 通信运营 电信运营商
银行 国有大型银行 股份制银行 城商行 农商行 其他银行
非银金融 证券 证券Ⅱ 证券Ⅲ 保险 多元金融 金融控股 资产管理 金融信息服务
其他非银金融 期货 信托
煤炭 煤炭开采 焦炭 动力煤 焦煤 其他煤炭
石油石化 油气开采 油服工程 炼化及贸易 油品石化贸易 其他石化 油田服务
环保 环保设备 环境治理 固废治理 综合环境治理 水务及水治理 其他环保
美容护理 个护用品 化妆品 医疗美容 其他美容护理
机械设备 其他 其他行业 未分类
'''.split())

# ⛔ 已删（2026-09-20）：厄尔尼诺整块功能（后端 + 前端）已按用户要求删除。**不要再加回来**。

UT = '7eea3edcaed734bea9cbfc24409ed989'
UA = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
    'Accept': '*/*',
    'Referer': 'https://quote.eastmoney.com/',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

# ============ 判定参数（用户确认） ============
VOL_RATIO_THRESHOLD = 1.5   # 断板倍量阈值：昨日量 / 启动首板量 >= 1.5
BREAKOUT_LOOKBACK   = 20    # 突破节点：回看多少个交易日的最高板高度
NODE_KEEP_DAYS      = 10    # 节点票池只保留最近两周（约 10 个交易日）的节点
DT_LADDER_DAYS      = 7     # 跌停板梯队：图表只展示近 7 个交易日

# ============ 「产业板块」清单（用户指定，来自其行情软件的产业分类）============
# 用途：模块3 展示的就是**用户真正关心的这 28 个板块**，而不是东财全部二级行业。
# 左=(展示名, 东财板块代码)；代码已按当日涨幅与用户截图逐一核对（如 无人驾驶→智能驾驶 BK0802）。
SECTOR_WATCH = [
    ('CPO',       'BK1128'), ('PCB',       'BK0877'), ('半导体',    'BK1036'),
    ('存储',      'BK1137'), ('数据中心',   'BK0922'), ('云计算',    'BK0579'),
    ('AIGC',      'BK1111'), ('商业航天',   'BK0963'), ('机器人',    'BK1408'),
    ('无人驾驶',   'BK0802'), ('电力',      'BK0428'), ('电网',      'BK0457'),
    ('核聚变',     'BK1163'), ('光伏',      'BK1031'), ('锂电池',    'BK1303'),
    ('军工',      'BK0490'), ('石油',      'BK0464'), ('天然气',    'BK0843'),
    ('小金属',     'BK1027'), ('黄金',      'BK0547'), ('银行',      'BK1283'),
    ('保险',      'BK0474'), ('证券',      'BK0473'), ('创新药',    'BK1106'),
    ('CRO',       'BK0899'), ('消费',      'BK1652'), ('稀土',      'BK1626'),
    ('消费电子',   'BK1037'),
]
NEWS_PAGE_SIZE      = 200   # 每日抓取的快讯条数

# 省级行政区（用于从所属板块中识别地域）
PROVINCES = ['北京', '天津', '上海', '重庆', '河北', '山西', '辽宁', '吉林', '黑龙江',
             '江苏', '浙江', '安徽', '福建', '江西', '山东', '河南', '湖北', '湖南',
             '广东', '海南', '四川', '贵州', '云南', '陕西', '甘肃', '青海', '台湾',
             '内蒙古', '广西', '西藏', '宁夏', '新疆', '香港', '澳门']

# ============ 新闻分类关键词 ============
OVERSEA_KW = ['美股', '纳指', '纳斯达克', '道指', '标普', '隔夜', '美元指数', 'COMEX',
              '黄金', '现货黄金', '原油', '布伦特', 'WTI', '欧股', '美债', '日经', '恒生',
              '港股', '美联储', '非农', '鲍威尔', '外盘', '伦铜', 'LME', '纽约股市', '欧洲股市']
MACRO_KW = ['央行', '国常会', '国务院', '财政部', '发改委', '证监会', '金融监管', '中共中央',
            '政治局', '降准', '降息', 'MLF', 'LPR', '逆回购', '专项债', '赤字', '货币政策',
            '财政政策', '统计局', 'PMI', 'CPI', 'PPI', 'GDP', '社融', '人民币汇率', '外汇',
            '中央经济工作', '国办', '国资委', '住建部', '工信部', '税务总局', '关税',
            '资本市场', 'IPO', '退市新规', '两融']
GOOD_KW = ['中标', '签约', '订单', '扩产', '涨价', '提价', '获批', '预增', '扭亏', '突破',
           '投产', '量产', '收购', '增持', '回购', '补贴', '战略合作', '供货', '新品',
           '涨价函', '需求旺盛', '供不应求', '产能利用率', '中标候选', 'license', '获批上市',
           '业绩预增', '净利润增长', '营业收入增长', '拟投资', '增资', '拿下']
BAD_KW = ['减持', '退市', '亏损', '下滑', '降价', '产能过剩', '处罚', '问询', '预亏', '终止',
          '诉讼', '冻结', '风险提示', '商誉减值', '业绩变脸', '立案', '降价', '下调',
          '净利下降', '营收下降', '变脸', '被罚', '警示', 'ST', '面值退市', '强制退']


# ============ HTTP ============
def http_get(url, timeout=8, retry=3, silent=False, enc='utf-8'):
    # 说明：这些接口正常都在 1~2s 内返回，故超时压到 8s，避免网络抖动时单次请求阻塞 15s×重试。
    # 兼容企业代理/MITM 证书环境：关闭证书校验，避免 RemoteDisconnected / CERTIFICATE_VERIFY_FAILED
    try:
        import ssl as _ssl
        _ctx = _ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = _ssl.CERT_NONE
    except Exception:
        _ctx = None
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
                return r.read().decode(enc, errors='ignore')
        except Exception as e:
            if i == retry - 1:
                if not silent:
                    print('  [warn] 请求失败 %s: %s' % (type(e).__name__, url[:70]))
                return None
            time.sleep(0.6 + i * 0.6)
    return None


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def save_js(path, obj):
    """输出 data.js，供 file:// 直接打开（避免 fetch 的 CORS 限制）"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write('window.DASHBOARD_DATA = ')
        json.dump(obj, f, ensure_ascii=False)
        f.write(';\n')


# ⛔ 已删（2026-09-20）：厄尔尼诺整块功能（后端 + 前端）已按用户要求删除。**不要再加回来**。
# ============ 1. 交易日列表 ============
def _confirm_today():
    """确认「今天是不是**已经收盘的交易日**」，是则返回 `YYYY-MM-DD`，否则 None。

    为什么要单独确认：交易日两源（腾讯日K / 同花顺指数日K）**盘后都不含当日**
    （同花顺尤其明显，见 §9.2 第 2 条），所以「今天」不能凭空塞进列表 ——
    遇到节假日会造出一个不存在的交易日，把整条管线带偏。
    判据用**涨停池**：非交易日它返回空（A 股从未出现过全天零涨停，可放心用）。
    """
    now = datetime.now()
    if now.weekday() >= 5 or now.hour < 15:
        return None                       # 周末 / 未收盘 → 今天不可能是"已完成的交易日"
    try:
        pool, _ = THS.fetch_zt_pool(now.strftime('%Y%m%d'))
        if pool:
            return now.strftime('%Y-%m-%d')
    except Exception:
        pass
    return None


def fetch_trade_days(limit=90):
    """交易日列表：主源腾讯日K，备源同花顺上证指数日K，
    两源都失败时**回退本地缓存**（否则网络一抖整条管线直接终止）。

    ⚠️ 拿到列表后还要做一次**当日校验**：两源盘后都不含当日（指数日K要等更晚才生成），
    若今天是交易日而列表缺它，必须补上 —— 否则整条管线会把**今天的数据挂到上一交易日**，
    而且这种「源成功但数据陈旧」比「源全失败」更危险（后者会走下面的缓存兜底、反而补上了今天）。
    2026-09-21 实测踩到：15:29 跑出来 `date=09-18`，而 15:11（当时两源全失败）反而是 `09-21`。
    """
    got = None
    # 主源：腾讯
    url = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'
           'param=sh000001,day,,,%d,qfq' % limit)
    txt = http_get(url, silent=True)
    if txt:
        try:
            d = json.loads(txt)
            node = (d.get('data') or {}).get('sh000001') or {}
            arr = node.get('qfqday') or node.get('day') or []
            days = [row[0] for row in arr if row and row[0]]
            if days:
                got = days
        except Exception:
            pass
    # 备源：同花顺（原东财 push2his 已停用）
    if not got:
        got = THS.fetch_trade_days(limit) or None
    if got:
        # 当日校验：列表缺当日 + 今天确实是交易日 → 补上（详见函数 docstring 与 _confirm_today）
        ds = _confirm_today()
        if ds and got[-1] < ds:
            print('  [warn] 交易日源缺当日 %s（指数日K盘后滞后，非接口失败）→ 已确认今日为交易日并补上' % ds)
            got = got + [ds]
        try:
            save_json(TRADE_DAYS_CACHE, {'days': got,
                                         'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
        except Exception:
            pass
        return got
    # 兜底：本地缓存（交易日序列变化极慢，网络抖动时用它保住管线）
    cache = (load_json(TRADE_DAYS_CACHE, {}) or {}).get('days') or []
    if cache:
        print('  [warn] 交易日两源均失败，沿用本地缓存（最后交易日 %s，缓存于 %s）'
              % (cache[-1], (load_json(TRADE_DAYS_CACHE, {}) or {}).get('ts', '?')))
        today = datetime.now().strftime('%Y-%m-%d')
        if today > cache[-1] and datetime.now().weekday() < 5:
            cache = cache + [today]      # 今天已收盘且为工作日 → 补上今天
        return cache
    print('  [err] 交易日接口全部失败')
    return []


# ============ 市场情绪（全市场涨跌/涨跌停家数）============
def fetch_dt_count():
    """跌停家数：**同花顺涨停池响应里的 limit_down_count**（一次请求拿全，无额外调用）。

    东财 push2ex 跌停池（原实现）已废弃 —— 该口径仅剩「跌停个股明细」在用（见 fetch_dt_pool）。
    """
    if ZT_STAT.get('dt') is not None:
        return ZT_STAT.get('dt')
    # 兜底：单独拉一次当日涨停池（顺带带回情绪字段）
    try:
        THS.fetch_zt_pool(datetime.now().strftime('%Y%m%d'))
    except Exception:
        pass
    return ZT_STAT.get('dt')


def build_mood(today_pool):
    """市场情绪：涨停/跌停/连板家数（今日涨停池口径，含 20% 涨停）。

    全部取自同花顺涨停池响应：
      ZT_STAT['zt'|'dt'|'zt_open'|'zt_rate'|'y_zt' …] —— 已按用户要求不带全市场涨跌家数。
    """
    pool = today_pool or []
    return {
        'zt_pool': ZT_STAT.get('zt') if ZT_STAT.get('zt') is not None else len(pool),
        'dt': fetch_dt_count(),
        'lbc': len([s for s in pool if s.get('lbc', 0) >= 2]),
        'max_board': max((s.get('lbc', 0) for s in pool), default=0),
        'zt_open': ZT_STAT.get('zt_open'),      # 炸板家数
        'zt_rate': ZT_STAT.get('zt_rate'),      # 封板率
        'y_zt': ZT_STAT.get('y_zt'),            # 昨日涨停家数
        'y_dt': ZT_STAT.get('y_dt'),            # 昨日跌停家数
        'ok': True,
        'src': 'ths',
    }


# ============ 连板晋级 · 昨日涨停表现 ============
# 东方财富特色板块指数（板块代码固定），其当日涨幅即「昨日XX今日表现」
YTD_BOARD_CODES = {
    'BK0816': '昨日连板', 'BK0815': '昨日涨停', 'BK1630': '昨日首板',
    'BK1645': '昨日打二板以上', 'BK1631': '昨日炸板',
}
EM_HOSTS = ['push2.eastmoney.com', 'push2delay.eastmoney.com', '82.push2.eastmoney.com']


def em_get(path, timeout=15):
    """东财接口请求（与 module4 同口径；此处复制一份避免循环导入）。"""
    for host in EM_HOSTS:
        t = http_get('https://%s%s' % (host, path), timeout=timeout, retry=1, silent=True)
        if t:
            return t
    return None


# 昨日涨停表现：「昨日XX」这一类板块指数东财才有（BK0816/BK1645…），同花顺没有对应指数。
# 故改为**自算**：用同花顺涨停池取「昨日」的 涨停/连板/首板/高度板/炸板 名单，
# 再用同花顺 realhead 批量取这些票「今日」涨跌幅，算等权平均 —— 口径更直白且全程同花顺。
YTD_BOARD_DEF = {
    '昨日涨停':       lambda p, z: [s['code'] for s in p],
    '昨日连板':       lambda p, z: [s['code'] for s in p if s.get('lbc', 0) >= 2],
    '昨日首板':       lambda p, z: [s['code'] for s in p if s.get('lbc', 0) == 1],
    '昨日打二板以上': lambda p, z: [s['code'] for s in p if s.get('lbc', 0) >= 3],
    '昨日炸板':       lambda p, z: [c['code'] for c in z],
}


def fetch_ytd_lists(y_date):
    """昨日各梯队名单（同花顺历史涨停池/炸板池）。y_date 形如 '20260916'。"""
    pool, _ = THS.fetch_zt_pool(y_date)
    zb = THS.fetch_zb_pool(y_date)
    return {name: fn(pool, zb) for name, fn in YTD_BOARD_DEF.items()}


def fetch_ytd_board_pct(y_date, lists=None):
    """昨日各梯队「今日」等权平均涨跌幅（%）。返回 ({名: pct}, {名: [代码]})。"""
    lists = lists or fetch_ytd_lists(y_date)
    codes = sorted({c for v in lists.values() for c in v})
    if not codes:
        return {}, lists
    snap = THS.fetch_realhead([('hs', c) for c in codes], max_workers=10)
    out = {}
    for name, cs in lists.items():
        vals = []
        for c in cs:
            v = snap.get(('hs', c)) or {}
            if v.get('pct') is not None:
                vals.append(v['pct'])
        out[name] = round(sum(vals) / len(vals), 2) if vals else None
    return out, lists


def build_board_perf(today_pool, tdays=None):
    """连板晋级 + 昨日涨停表现。返回 dict 供前端渲染。

    tdays：交易日列表（升序）。昨日 = tdays[-2]；缺省则从 today_pool 的日期无从推断，
    此时跳过「昨日表现」部分（宁缺勿假）。
    """
    pool = today_pool or []
    zt_codes = {s.get('code') for s in pool}
    bp = {
        'today_lb': len([s for s in pool if s.get('lbc', 0) >= 2]),
        'today_fb': len([s for s in pool if s.get('lbc', 0) == 1]),
        'today_zt': len(pool),
        'max_board': max((s.get('lbc', 0) for s in pool), default=0),
        'y_zt': None, 'y_lb': None, 'y_fb': None, 'y_2plus': None, 'y_broken': None,
        'y_lb_total': 0, 'y_lb_promo': 0, 'ok': False,
    }
    km = {'昨日涨停': 'y_zt', '昨日连板': 'y_lb', '昨日首板': 'y_fb',
          '昨日打二板以上': 'y_2plus', '昨日炸板': 'y_broken'}
    y_date = None
    if tdays and len(tdays) >= 2:
        y_date = str(tdays[-2]).replace('-', '')
    if y_date:
        ytd, lists = fetch_ytd_board_pct(y_date)
        for nm, key in km.items():
            bp[key] = ytd.get(nm)
        lb_codes = lists.get('昨日连板') or []
        bp['y_lb_total'] = len(lb_codes)
        bp['y_lb_promo'] = len([c for c in lb_codes if c in zt_codes])
        bp['y_date'] = y_date
        bp['ok'] = bool(ytd) and bp['y_lb_total'] > 0
    # 降级：实时拉取失败时用上一轮复盘的结构化数据兜底（1 日滞后）
    if not bp['ok']:
        rv = load_json(os.path.join(BASE, 'review_data.json'), {})
        for x in (rv.get('ytd_boards') or []):
            key = km.get(x.get('name'))
            if key and bp.get(key) is None:
                bp[key] = x.get('pct')
        bp['ok'] = any(bp[k] is not None for k in ('y_zt', 'y_lb', 'y_fb', 'y_2plus', 'y_broken'))
    return bp


# ============ 2. 涨停池 ============
# 市场情绪缓存：涨停池响应里自带「涨停/跌停家数、封板率、炸板数」，
# 一次请求拿全，**不再需要任何东财情绪接口**（原 fetch_dt_count 已废弃）。
ZT_STAT = {}


def fetch_zt_pool(date_yyyymmdd):
    """当日涨停池（**同花顺**）。

    数据源：`data.10jqka.com.cn/dataapi/limit_up/limit_up_pool`
      · 一次请求同时返回 `limit_up_count` / `limit_down_count` → 写入全局 ZT_STAT
      · 个股字段：连板数(high_days_value hex)、封单额(order_amount)、流通/总市值、
        成交额(turnover)、换手率(turnover_rate)、涨停原因(reason_type → concepts)
      · 封板时间：本接口不返回，由 `fetch_block_top` 的 first_limit_up_time 回填（约 87%），
        其余由分时反推（见 fetch_seal_amount）
    """
    global ZT_STAT
    pool, stat = THS.fetch_zt_pool(date_yyyymmdd)
    if stat:
        ZT_STAT = stat
    if not pool:
        print('  [warn] 同花顺涨停池为空：%s' % date_yyyymmdd)
        return []
    # 总市值修正（用户核对的当前值优先，按日期锁定）
    for s in pool:
        k = (s['code'], date_yyyymmdd)
        if k in CAP_OVERRIDE:
            s['total_cap'] = CAP_OVERRIDE[k]
        # 兼容旧字段：industry 用首个题材词（下游 ferment_count / 前端 tooltip 仍读它）
        if not s.get('industry'):
            s['industry'] = (s.get('concepts') or [''])[0]
    return pool


def fetch_zt_pool_full(date_yyyymmdd):
    """涨停池 + 市场情绪（ZT_STAT）一次返回，供 main 里少跑一趟。"""
    p = fetch_zt_pool(date_yyyymmdd)
    return p, dict(ZT_STAT)


def recount_lbc_by_continuity(zt_hist, days, verbose=True):
    """用**严格连续**口径重算各交易日涨停池的连板数（交叉校验 + 降级兜底）。

    口径（用户定义，与同花顺「连板梯队」接口一致）：
        连板数 = 自本轮第一个涨停起，**每个交易日都必须涨停**，断一天即止。
        → lbc = 从当日往前、连续涨停的交易日数（当日计 1）

    为什么要自算（两层保险）：
      ① 主路径 `ths_source.fetch_zt_pool` 已改用 `continuous_limit_up`（同花顺权威连板梯队）；
         本函数逐日**交叉校验**，不一致且非窗口截断 → 打印告警（暴露接口异常 / 解析回归）。
      ② 该接口偶发无返回时（并发限流 403/空响应），`fetch_zt_pool` 会降级记 1
         （`lbc_src='fallback'`）；本函数用历史涨停池把降级值修正为真值 ——
         **不依赖任何外部接口，天然抗抖动**。

    ⚠️ 窗口限制：`days` 只有 N 天（默认 20）。若某票从窗口第一个交易日至今一路涨停，
        自算值只是**下界**（真实可能更长）→ 此时不覆盖、不告警，保留主路径的值。
      数据缺失的交易日**跳过**而不当作断板（缺失 ≠ 未涨停）。

    Args:
        zt_hist: {YYYY-MM-DD: [stock, ...]}，每只 stock 含 code / name / lbc / lbc_src
        days:    交易日列表（升序，YYYY-MM-DD）
        verbose: 是否打印统计与告警样本

    Returns:
        dict: {'checked': 校验只数, 'fixed': 降级修正只数, 'skipped': 数据缺口跳过只数,
               'warn': 与权威口径不符（可疑）只数}
    """
    days = [str(d) for d in (days or [])]
    if not days or not zt_hist:
        return {'checked': 0, 'fixed': 0, 'warn': 0}

    # 每只票 → 它出现过的交易日集合
    seen = {}
    for d, pool in zt_hist.items():
        for s in pool:
            seen.setdefault(s.get('code'), set()).add(d)

    idx = {d: i for i, d in enumerate(days)}
    checked = fixed = warn = skipped = 0
    samples = []

    for d in days:
        pool = zt_hist.get(d)
        if not pool:
            continue
        i = idx[d]
        for s in pool:
            checked += 1
            code = s.get('code')
            hit_days = seen.get(code) or set()
            # 往前连续回溯：缺失日跳过（缺失 ≠ 未涨停，不能当断板）
            c, j = 1, i - 1
            while j >= 0:
                dj = days[j]
                if dj not in zt_hist:      # 该交易日数据缺失 → 跳过
                    j -= 1
                    continue
                if dj in hit_days:
                    c += 1
                    j -= 1
                else:
                    break
            truncated = (j < 0)            # 一路连到窗口起点 → c 只是下界

            old = int(s.get('lbc') or 0)
            if s.get('lbc_src') == 'fallback':
                # 主路径降级（连板梯队接口没返回）→ 自算值直接接管
                if c != old:
                    s['lbc'] = c
                    s['lbc_src'] = 'recounted'
                    fixed += 1
            elif old != c and not truncated:
                # 「N天M板」里 N == M ⇒ 最近 N 个交易日**全部涨停** ⇒ 连板数就是 M（自证）。
                #   此时自算反而偏小，只可能是**历史涨停池漏了这只票**（接口漏股 / 抓取不全），
                #   属数据缺口而非口径异常 → 静默跳过，不告警（当日权威值本就正确）。
                mm = re.match(r'(\d+)天(\d+)板', s.get('high_days') or '')
                if mm and mm.group(1) == mm.group(2) and int(mm.group(2)) == old:
                    skipped += 1
                    continue
                # 其余情况：主路径（连板梯队）是权威 → 不改值，只告警，暴露口径异常
                warn += 1
                if len(samples) < 8:
                    samples.append((d, code, s.get('name'), old, c, s.get('high_days') or ''))

    if verbose:
        print('  连板连续性自算：校验 %d 只 · 降级修正 %d 只 · 数据缺口跳过 %d 只 · 权威口径告警 %d 只'
              % (checked, fixed, skipped, warn))
        for d, code, name, old, c, hd in samples:
            print('     ⚠️ %s %s(%s) 权威=%s 自算=%s high_days=%s（以权威为准，请查接口）'
                  % (d, name, code, old, c, hd))
    return {'checked': checked, 'fixed': fixed, 'warn': warn, 'skipped': skipped}


def fetch_dt_pool(date_yyyymmdd):
    """返回当日跌停股列表。

    ⚠️ **同花顺唯一缺口**：THS 无公开的「跌停个股池」接口（`down_limit_pool` 等均 404），
    只有跌停**家数**（见 ZT_STAT['dt']）。故明细仍走东财 push2ex `getTopicDTPool`
    —— 该域不在被封的 push2his 路径下，实测可用；若将来也不可用，前端「跌停板梯队」
    会降级为只有家数、无个股明细。

    接口踩坑：跌停池用 `getTopicDTPool`，但 **dpt 仍是 `wz.ztzt`**（写成 wz.dtzt 会返回 0 条）。
    字段：days = 连续跌停天数；oc = 当日开板次数；fund = 封单额（跌停为卖单封单）。
    """
    url = ('https://push2ex.eastmoney.com/getTopicDTPool?ut=%s&dpt=wz.ztzt&'
           'Pageindex=0&pagesize=500&sort=fund%%3Aasc&date=%s' % (UT, date_yyyymmdd))
    txt = http_get(url, silent=True)
    if not txt:
        return []
    try:
        d = json.loads(txt)
        pool = (d.get('data') or {}).get('pool') or []
    except Exception:
        return []
    out = []
    for s in pool:
        try:
            if is_excluded(s.get('c', ''), s.get('n', '')):
                continue
            out.append({
                'code':   s.get('c', ''),
                'market': s.get('m', 1),
                'name':   s.get('n', ''),
                'price':  round((s.get('p') or 0) / 1000.0, 2),
                'chg':    round(s.get('zdp') or 0, 2),
                'amount': s.get('amount') or 0,          # 成交额（元）
                'float_cap': s.get('ltsz') or 0,         # 流通市值
                'total_cap': s.get('tshare') or 0,       # 总市值
                'turnover': round(s.get('hs') or 0, 2),  # 换手率
                'dt_days':  s.get('days') or 1,          # 连续跌停天数
                'open_cnt': s.get('oc') or 0,            # 当日开板次数
                'seal_fund': s.get('fund') or 0,         # 跌停封单额
                'last_seal': s.get('lbt') or 0,          # 最后封板时间
                'industry': s.get('hybk') or '',         # 行业板块
            })
        except Exception:
            continue
    out.sort(key=lambda x: (-x['dt_days'], -x['amount']))
    return out


def build_ladder(zt_hist, days):
    """连板高度梯队：近 N 日「最高板 / 次高板 / 创业板」折线。

    返回 [{date, top, second, cyb, top_n, second_n, cyb_n, *_names, *_list}]
      · top/second/cyb = 当日 最高板 / 次高板 / 创业板最高板（线的 Y 值）
      · *_n   = 该梯队**股票家数**（图上数字显示这个）
      · *_list = 结构化名单（name/code/market/boards/industry/zbc/is_yizi），供前端 tooltip
    """
    out = []
    for d in days:
        pool = zt_hist.get(d, [])
        if not pool:
            continue
        lbcs = sorted({s['lbc'] for s in pool}, reverse=True)
        top = lbcs[0] if lbcs else 0
        second = lbcs[1] if len(lbcs) > 1 else 0
        # 创业板高度：300/301 开头
        cyb = [s['lbc'] for s in pool if str(s['code']).startswith(('300', '301'))]
        cyb_max = max(cyb) if cyb else 0

        def _is_cyb(s):
            return str(s['code']).startswith(('300', '301'))

        def _rows(lbc):
            """该高度上的**全部**个股（不截断，供前端 tooltip 完整展示「名字 + 板数 + 题材」）

            字段只留 tooltip 用得到的 4 个（market 前端可由 code 推导），避免 data.js 体积膨胀。
            """
            rs = [s for s in pool if s['lbc'] == lbc]
            rs.sort(key=lambda x: -(x.get('amount') or 0))
            return [{'name': s['name'], 'code': s['code'],
                     'boards': s['lbc'], 'industry': s.get('industry', '')}
                    for s in rs]

        out.append({
            'date': d,
            'top': top,
            'second': second,
            'cyb': cyb_max,
            # *_n = 该梯队**股票家数**（图上数字显示这个，而不是板数）
            'top_n': len([s for s in pool if s['lbc'] == top]) if top else 0,
            'second_n': len([s for s in pool if s['lbc'] == second]) if second else 0,
            'cyb_n': len([s for s in pool if _is_cyb(s) and s['lbc'] == cyb_max]) if cyb_max else 0,
            'top_names': '、'.join([s['name'] for s in pool if s['lbc'] == top][:3]),
            'second_names': '、'.join([s['name'] for s in pool if s['lbc'] == second][:3]),
            'cyb_names': '、'.join([s['name'] for s in pool if _is_cyb(s) and s['lbc'] == cyb_max][:2]),
            'top_list': _rows(top),
            'second_list': _rows(second),
            'cyb_list': [r for r in _rows(cyb_max)
                         if str(r['code']).startswith(('300', '301'))] if cyb_max else [],
        })
    return out


def build_dt_ladder(dt_hist, dt_days):
    """跌停板梯队：与「连板高度梯队」(`ladder`) **完全同构**，只把「连板数」换成「连续跌停天数」。

    返回 [{date, top, second, cyb, count, top_names, second_names, cyb_names,
            top_list, second_list, cyb_list}]
      · top / second / cyb = 当日 最高 / 次高 / 创业板 连续跌停天数（对应连板梯队的 最高板/次高板/创业板）
      · *_list  = 结构化名单（name/code/market/boards=连跌天数/industry/open_cnt），供前端 tooltip
      · count   = 当日跌停家数（额外汇总，tooltip 附注用）
    """
    def _is_cyb(s):
        return str(s['code']).startswith(('300', '301'))

    out = []
    for d in dt_days:
        pool = dt_hist.get(d, [])
        lvls = sorted({s.get('dt_days') or 0 for s in pool if s.get('dt_days')}, reverse=True)
        top = lvls[0] if lvls else 0
        second = lvls[1] if len(lvls) > 1 else 0
        cyb = [s.get('dt_days') or 0 for s in pool if _is_cyb(s)]
        cyb_max = max(cyb) if cyb else 0

        def _rows(lvl, only_cyb=False):
            """该连跌高度上的**全部**个股（不截断）"""
            rs = [s for s in pool
                  if (s.get('dt_days') or 0) == lvl and (not only_cyb or _is_cyb(s))]
            rs.sort(key=lambda x: -(x.get('amount') or 0))
            return [{'name': s['name'], 'code': s['code'],
                     'boards': s['dt_days'], 'industry': s.get('industry', '')}
                    for s in rs]

        out.append({
            'date': d,
            'top': top,
            'second': second,
            'cyb': cyb_max,
            'count': len(pool),
            # *_n = 该梯队**股票家数**（图上数字显示这个，而不是连跌天数）
            'top_n': len([s for s in pool if (s.get('dt_days') or 0) == top]) if top else 0,
            'second_n': len([s for s in pool if (s.get('dt_days') or 0) == second]) if second else 0,
            'cyb_n': len([s for s in pool if _is_cyb(s) and (s.get('dt_days') or 0) == cyb_max]) if cyb_max else 0,
            'top_names': '、'.join([s['name'] for s in pool if (s.get('dt_days') or 0) == top][:3]),
            'second_names': '、'.join([s['name'] for s in pool if (s.get('dt_days') or 0) == second][:3]),
            'cyb_names': '、'.join([s['name'] for s in pool if _is_cyb(s)
                                    and (s.get('dt_days') or 0) == cyb_max][:2]),
            'top_list': _rows(top),
            'second_list': _rows(second),
            'cyb_list': _rows(cyb_max, only_cyb=True) if cyb_max else [],
        })
    return out


def _node_type_priority(nt):
    """节点类型优先级（穿越 > 突破 > 断板）：同一只票同时出现在多节点时优先穿越场景"""
    return {'穿越节点': 3, '突破节点': 2, '最高标断板节点': 1}.get(nt, 0)


def seal_bar_label(fbt):
    """封板时间(fbt, 形如 93024 = 09:30:24) → 封板所在分时线的标签('09:31')。

    分钟线标签 = 该分钟区间的**结束**时刻（首根 09:30 = 集合竞价），
    且源站 fbt 的分秒常被截断（如 10:26:5x 报成 102600），
    故**一律进位到下一分钟**，保证取到的是「封板那一刻所在的那根」。
    """
    try:
        v = int(fbt)
    except Exception:
        return None
    h, mi = v // 10000, (v % 10000) // 100
    mi += 1
    if mi >= 60:
        mi -= 60
        h += 1
    return '%02d:%02d' % (h, mi)


def fetch_seal_amount(code, market, date_str, fbt, is_yizi=False):
    """上板分时量 = 首次封板时刻的累计成交额（元）。

    注意与「全天成交额(amount)」区分：次日竞价量的基准是上板那一刻的分时量，
    不是全天成交额（炸板/回封会把全天额撑得远大于上板量）。

    口径（2026-09-14 修正）：
      · 上板分时量 = 「封板时刻所在那一根分时线」的**成交额**（该分钟增量，不是累计）。
        东财/腾讯的分钟线标签 = 该分钟区间的**结束**时刻（首根 09:30 = 集合竞价），
        所以 fbt 带秒时要**向上进位到下一分钟**：如 09:30:24 → 落在 09:31 那根。
        （旧实现把 09:30:24 截成 '09:30'，正好命中集合竞价那根 → 上板量被严重低估，
          典型：闽东电力 000993 算出 0.82亿，实际上板那根是 2.35亿。）
      · 一字板(first_seal<=93005)：封板发生在集合竞价，取首根（09:30）成交额 = 集合竞价额。

    返回 (seal_amount, day_amount)，失败返回 (None, None)
    """
    key = '%s_%s' % (code, date_str)
    if key in SEAL_OVERRIDE:
        sv = SEAL_OVERRIDE[key]
        return sv[0], sv[1]
    with _SEAL_CACHE_LOCK:                 # build_reco 用 6 线程并发调用，load/save 必须串行，
        cache = load_json(SEAL_CACHE, {})  # 否则各线程各持一份快照互相覆盖 → 缓存条目丢失
        if key in cache:
            c = cache[key]
            return c.get('seal'), c.get('total')

    # ── 主源：同花顺分时 d.10jqka.com.cn/v6/time/hs_<码>/last.js（仅当日）
    #    字段序（**与东财不同**）：时间, 现价, 该分钟成交额(元), 均价, 该分钟量(股)
    #    —— 首根 0930 = 集合竞价那根，其余各行为**该分钟增量**（不是累计），
    #       与旧东财 trends2 的 f57 语义完全一致，故口径代码可 1:1 平移。
    seal, total = None, None
    rows = THS.fetch_minute(code)
    if rows:
        total = sum(r[2] for r in rows)
        first_own = rows[0][2]                    # 首根 09:30（含集合竞价）
        hhmm = seal_bar_label(fbt) if fbt else None
        if not hhmm:
            # fbt 缺失（block_top 未覆盖的少数票）→ 从分时反推封板时刻：
            # 涨停票的「封板价 == 当日最高价」，首次触及该价的那根即为封板那根。
            # 该根标签是区间**结束**时刻，本身已正确包含封板瞬间，**不需再进位**。
            try:
                mx = max(r[1] for r in rows)
                for r in rows:
                    if r[1] >= mx - 1e-6:
                        hhmm = '%s:%s' % (r[0][:2], r[0][2:4])
                        break
            except Exception:
                hhmm = None
        hhmm_comp = hhmm.replace(':', '') if hhmm else None
        own_at_seal = None
        for _t, _px, amt, _avg, _v in rows:
            tm = '%s:%s' % (_t[:2], _t[2:4])
            if own_at_seal is None and hhmm and tm >= hhmm:
                own_at_seal = amt                 # 首次封板那一根的成交额（仅认第一次）
        if is_yizi and first_own is not None:
            seal = first_own                      # 一字板：集合竞价额
        elif own_at_seal is not None:
            seal = own_at_seal                    # 自然/T板：封板那一根成交额，炸板回封不计

    # 兜底：腾讯分钟线（格式: 时间 价格 量(手) 累计额(元)，时间字段 'HHMM' 无冒号）
    if seal is None:
        mkt = 'sh' if str(code)[:1] in ('6', '9') else 'sz'
        url2 = 'https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=%s%s' % (mkt, code)
        txt2 = http_get(url2, silent=True)
        if txt2:
            try:
                d2 = json.loads(txt2)
                inner = (d2.get('data') or {}).get('%s%s' % (mkt, code)) or {}
                arr = ((inner.get('data') or {}).get('data') or [])
                date_field = (inner.get('data') or {}).get('date')
                # date 形如 20260907，比较与 date_str 一致
                if date_field and date_field == date_str.replace('-', '') and arr:
                    rows = [r.split() for r in arr]
                    hhmm_c = seal_bar_label(fbt)
                    hhmm_c = hhmm_c.replace(':', '') if hhmm_c else None   # 腾讯时刻 '0931'
                    first_own = None
                    own_at_seal = None
                    prev = 0.0
                    for rw in rows:
                        try:
                            cc = float(rw[3])      # 腾讯第4列 = 累计成交额
                        except Exception:
                            cc = prev
                        own = cc - prev            # 该分钟成交额
                        prev = cc
                        if first_own is None:
                            first_own = own        # 首根 0930（含集合竞价）
                        if own_at_seal is None and hhmm_c and rw[0] >= hhmm_c:
                            own_at_seal = own      # 首次封板那一根的成交额
                    total = prev
                    if is_yizi and first_own is not None:
                        seal = first_own           # 一字板：集合竞价额
                    elif own_at_seal is not None:
                        seal = own_at_seal         # 自然/T板：封板那一根成交额
            except Exception:
                pass

    # 仅在取到有效上板量时落缓存；None（接口缺失/限流）不缓存，下次运行可重试
    if seal is not None:
        with _SEAL_CACHE_LOCK:
            cache = load_json(SEAL_CACHE, {})
            cache[key] = {'seal': seal, 'total': total}
            save_json(SEAL_CACHE, cache)
    return seal, total


# ============ 3. 板块涨幅榜 ============
# 板块名噪声前缀（非题材类板块）
BOARD_SKIP = ('昨日', '近期', '百日', '东方财富', '融资', '沪股通', '深股通', '转融',
              'MSCI', '标普', '富时', '创业成份', '深证', '上证', '中证', 'AH股')


# ⛔ 已删（2026-09-20）：东财口径的板块筛选死代码 `L1_BOARD_CODES` / `STYLE_BOARD_KW` /
#    `is_l2_industry()` —— 全站切同花顺后**零调用**（同花顺行业体系 ≠ 东财/申万，这套过滤已废），
#    按 项目约定.md §9.1 第 10 条清理。**不要再加回来**：同花顺行业名由 `fetch_fund_rank('hy')`
#    直接给出，不需要按代码段判定层级。


def _is_limit_up(code, name, pct):
    """按各板涨跌停规则判定是否涨停（pct = 当日涨跌幅%）。

    科创板/创业板 20%，主板 10%，ST 5%；北交所不参与统计（与全局股票池规则一致）。
    """
    try:
        p = float(pct)
    except Exception:
        return False
    c = str(code or '')
    if c.startswith(('4', '8', '92')):        # 北交所
        return False
    if 'ST' in (name or '').upper():
        return p >= 4.8
    if c.startswith(('300', '301', '688', '689')):
        return p >= 19.8
    return p >= 9.8


# ---------- 东财 push2 板块成分股（2026-09-20 新增的第 2 处东财依赖，见 项目约定.md §5）----------
# 为什么加：同花顺「板块成分股页」(q.10jqka.com.cn/{thshy,gn}/detail/.../ajax/1/) 已被
#   **chameleon JS 反爬拦死** —— 2026-09-20 实测全变体 401（响应体是
#   `s.thsi.cn/js/chameleon/chameleon.1.7.min.*.js` 挑战脚本），带 Session / UA / Referer /
#   X-Requested-With 均无效；而 q.10jqka.com.cn 主站 200 正常 → 只有这个 ajax 明细被拦。
#   → `THS.count_board_zt()` 因此恒返回 None → 板块榜「涨停/全部」的**涨停家数全空**。
# 为什么选东财 push2：与「跌停个股池 push2ex」同属**未被封的域**，且**不是**被按 IP 封禁的
#   `push2his` 路径；返回自带 `f3`（涨跌幅）→ 配上面已有的 `_is_limit_up()` 即可得涨停家数。
#   （a-stock-data skill 的 FAQ 亦独立印证：「同花顺板块接口 2026 年初加反爬 401，东财 push2 是替代」。）
EM_CLIST_HOSTS = ('push2.eastmoney.com', 'push2delay.eastmoney.com', '82.push2.eastmoney.com')


def em_board_zt_count(em_code, page_size=100, max_page=5):
    """东财板块成分股 → `{zt_count, stock_total, leader, leader_pct}`；失败返回 None。

    与同花顺版 `THS.count_board_zt()` 返回**同构**，可直接互换。`em_code` 形如 `BK1036`。

    ⚠️ `page_size` **必须 ≤100**：东财 clist 的 `pz` 实测封顶 100（传 500 也只回 100），
       若按「返回条数 < pz 就停」判尾页，用 500 会**只取到第 1 页**（曾踩，成分股数少一半）。
    注：`zt_count` 只数涨停家数，涨停股涨幅最大 → 恒排在第 1 页最前，故第 1 页已足够；
        `stock_total` 需要全量翻页才准。
    """
    if not em_code:
        return None
    rows = []
    for pn in range(1, max_page + 1):
        t = None
        for h in EM_CLIST_HOSTS:
            t = http_get('https://%s/api/qt/clist/get?pn=%d&pz=%d&po=1&np=1'
                         '&fltt=2&invt=2&fid=f3&fs=b:%s&fields=f12,f14,f3'
                         % (h, pn, page_size, em_code), timeout=8, retry=1, silent=True)
            if t:
                break
        if not t:
            break
        try:
            diff = (json.loads(t).get('data') or {}).get('diff') or []
        except Exception:
            break
        if not diff:
            break
        rows.extend(diff)
        if len(diff) < page_size:
            break
    if not rows:
        return None
    zt, leader, lpc = 0, '', 0.0
    for it in rows:
        c, nm = it.get('f12') or '', it.get('f14') or ''
        pct = it.get('f3')
        if not c or is_excluded(c, nm):
            continue
        if _is_limit_up(c, nm, pct):
            zt += 1
        if not leader:
            leader, lpc = nm, (pct if isinstance(pct, (int, float)) else 0.0)
    return {'zt_count': zt, 'stock_total': len(rows),
            'leader': leader, 'leader_pct': lpc}


def fetch_board_zt_stats(boards, max_workers=5):
    """给每个板块补「涨停家数 / 成分股总数」（board: {zt_count, stock_total}）。

    来源（2026-09-20 起**东财优先**）：
      ① `em_board_zt_count(em_code)` —— 东财 push2 成分股（自带涨跌幅）。**这是当前唯一可用的来源**，
         因为同花顺成分股页已被 chameleon 反爬拦死（见 `EM_CLIST_HOSTS` 上方的注释）。
      ② `THS.count_board_zt(ths_code)` —— 同花顺成分股页。当前恒返回 None（401），
         **保留不动**：接口哪天恢复即自动降级生效，无需再改代码。

    板块代码口径：优先 `em_code`（东财BK），没有才退回 `ths_code`（881/885/886xxx）
    或 `code` 经 THS_MAP 转同花顺码。
    """
    if not boards:
        return

    def _one(b):
        # ① 首选：东财 push2 成分股
        r = None
        if b.get('em_code'):
            try:
                r = em_board_zt_count(b['em_code'])
            except Exception:
                r = None
        # ② 退路：同花顺成分股页（当前 401 恒 None）
        if not r:
            tc = b.get('ths_code') or ''
            if not tc:
                m = THS_MAP.get(b.get('em_code') or b.get('code') or '')
                tc = m[0] if m else ''
            if tc:
                try:
                    r = THS.count_board_zt(tc, max_page=1)
                except Exception:
                    r = None
        if not r:
            return
        # `stock_total` 沿用原值（同花顺行业榜的「公司家数」，如半导体 188），
        # 缺失时才用成分股条数补 —— 避免同一语义被两套口径来回覆盖。
        if not b.get('stock_total'):
            b['stock_total'] = r['stock_total']
        b['zt_count'] = r['zt_count']
        if r.get('leader') and not b.get('leader'):
            b['leader'] = r['leader']
            b['leader_pct'] = r['leader_pct']

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(_one, boards))


def fetch_board_klines(boards, day_n=120, week_n=60, max_workers=None, old=None):
    """预生成榜单板块的日K/周K，随 data.js 下发。

    为什么放在后端：浏览器直连同花顺 `d.10jqka.com.cn` 取板块K线需 script 注入
    （回调名固定），离线也不可用；改为 Python 抓一次、落进 data.js，前端点击即可秒开。

    实际取数在 `board_kline_src.fetch_board_klines`（**整批统一走同花顺板块指数**，
    `SOURCE_PREF='ths'`；两家指数基期不同、点位不可混用，故不允许按单个板块切换来源）。
    `old` 传入上一版 board_kline → 自动合并，新数据残缺的板块沿用老数据。

    返回 {code: {'name':.., 'day': ['日期,开,收,高,低', ...], 'week': [...], 'src': 'ths'}}
    """
    return _bk_fetch_board_klines(boards, day_n=day_n, week_n=week_n,
                                  max_workers=max_workers, old=old)


def fetch_board_rank(top=60):
    """板块榜：**只用同花顺**（东财已停用）。

    三个来源（均已实测）：
      · 行业榜/概念榜  `data.10jqka.com.cn/funds/<hy|gn>zjl/` 资金流表（分页 ajax）
            → 名称 / 当日涨跌幅 / 净额(亿) / **公司家数** / 领涨股 / 领涨股涨跌幅
      · 板块实时快照    `d.10jqka.com.cn/v6/realhead/bk_<板块指数码>/defer/last.js`
            → 当日涨跌幅、成交额、**成分股总数**（field 37）
      · 中期涨幅        同花顺板块指数日K 自算（同花顺榜页无 3日/5日 列）

    字段：pct=当日涨跌幅；d3/d5=3日/5日涨幅（自算）；up/down=上涨/下跌家数；
          leader/leader_pct=领涨股；zljlr_wan=主力净额（万元）；em_code/ths_code=板块代码

    返回 {'industry': [...], 'concept': [...], 'all': [...], 'watch': [...28 产业板块...]}
    """
    all_fund = THS.fetch_all_fund_rank()
    industry = THS.fetch_fund_rank('hy', pages=2)
    concept = THS.fetch_fund_rank('gn', pages=8)

    def _norm(b, is_ind):
        return {
            'name': b['name'], 'code': b.get('name'),
            'pct': b.get('pct') or 0.0,
            'd3': None, 'd5': None,
            'up': 0, 'down': 0,
            'total': b.get('stock_total') or 0,
            'stock_total': b.get('stock_total') or 0,
            'leader': b.get('leader') or '',
            'leader_pct': b.get('leader_pct'),
            'zljlr_wan': round((b.get('net_in_yi') or 0) * 1e4, 2),
            'amount_yi': b.get('amount_yi'),
            'em_code': '', 'ths_code': '',
        }

    board = [_norm(b, True) for b in industry]
    cpt = [_norm(b, False) for b in concept]

    # 板块名 → 同花顺板块指数码（realhead/日K 都要用）
    name2code = {}
    for c, v in (THS.index_table() or {}).items():
        nm = (v.get('name') if isinstance(v, dict) else v) or ''
        if nm and nm not in name2code:
            name2code[nm] = c
    for b in board + cpt:
        b['ths_code'] = name2code.get(b['name'], '') or ''

    # ---- 用户「产业板块」清单（28 个）：以 SECTOR_WATCH 为准，逐条取同花顺数据 ----
    watch = []
    _secs = []
    for _disp, _em in SECTOR_WATCH:
        _t = THS_MAP.get(_em)
        if not _t:
            continue
        watch.append({'disp': _disp, 'em': _em, 'ths': _t[0], 'ts_name': _t[1]})
        _secs.append(('bk', _t[0]))
    snap = THS.fetch_realhead(_secs, max_workers=10) if _secs else {}

    out_watch = []
    for w in watch:
        v = snap.get(('bk', w['ths'])) or {}
        f = THS.match_fund_row(all_fund, w['ts_name']) or {}
        # 成分股页首行 = 当日涨幅第一 → 领涨股（资金流榜匹配不到时的兜底）
        out_watch.append({
            'name': w['disp'],
            'code': w['em'],
            'em_code': w['em'],
            'ths_code': w['ths'],
            'pct': v.get('pct') if v.get('pct') is not None else (f.get('pct') or 0.0),
            'd3': None, 'd5': None,
            'up': 0, 'down': 0,
            'stock_total': int(v.get('stock_total') or f.get('stock_total') or 0),
            'total': int(v.get('stock_total') or f.get('stock_total') or 0),
            'leader': f.get('leader') or '',
            'leader_pct': f.get('leader_pct'),
            'zljlr_wan': round((f.get('net_in_yi') or 0) * 1e4, 2),
            'amount_yi': round((v.get('amount') or 0) / 1e8, 2),
        })

    board.sort(key=lambda x: -x['pct'])
    cpt.sort(key=lambda x: -x['pct'])
    out_watch.sort(key=lambda x: -x['pct'])

    # 中期涨幅（3日/5日）：同花顺榜页只有当日一列 → 按板块指数码自算（并发）
    _need = [b for b in (board[:30] + cpt[:30] + out_watch) if b.get('ths_code')]

    def _mid(b):
        try:
            b['d3'] = THS.pct_over(b['ths_code'], 3)
            b['d5'] = THS.pct_over(b['ths_code'], 5)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(_mid, _need))

    # 全量板块热度留档（行业+概念+产业清单）：个股概念按「对应板块当日涨幅」排序，
    # 实现「同一个票每一波走不同概念 → 取当下正在炒的那个」。
    try:
        BOARD_HEAT.clear()
        for b in board + cpt + out_watch:
            if b['name']:
                BOARD_HEAT[b['name']] = b['pct']
    except Exception:
        pass
    print('[板块] 同花顺口径：行业 %d 个 · 概念 %d 个 · 产业清单 %d 个'
          % (len(board), len(cpt), len(out_watch)))
    _allmap = {b['name']: b for b in board + cpt}
    for b in out_watch:
        _allmap[b['name']] = b
    _got = {w['em_code'] for w in out_watch}
    _missing = [e for e, _ in SECTOR_WATCH if e not in _got]
    return {'industry': board[:top], 'concept': cpt[:top],
            'all': list(_allmap.values()), 'watch': out_watch,
            'watch_missing': _missing}
# ============ 4. 指数行情 ============
# 同花顺指数码（`zs_` 前缀）。北证50 同花顺无此指数 → 由腾讯单条兜底（该指数不参与选股池）。
INDEX_SPEC = [
    ('sh000001', 'zs_1A0001', '上证指数'),
    ('sz399001', 'zs_399001', '深证成指'),
    ('sz399006', 'zs_399006', '创业板指'),
    ('sh000688', 'zs_1B0688', '科创50'),
    ('bj899050', None,        '北证50'),
    ('sh000300', 'zs_399300', '沪深300'),
]


def fetch_index():
    """指数行情：主源同花顺 realhead；同花顺没有的（北证50）用腾讯单条兜底。"""
    ids = [(zs, tx, disp) for tx, zs, disp in INDEX_SPEC if zs]
    snap = THS.fetch_index() if ids else []
    by_tx = {i['code']: i for i in snap}
    out = []
    missing = []
    for tx_code, zs, disp in INDEX_SPEC:
        it = by_tx.get(tx_code)
        if it and it.get('price') is not None:
            out.append(it)
        else:
            missing.append(tx_code)
    if missing:
        txt = http_get('https://qt.gtimg.cn/q=' + ','.join(missing),
                       enc='gbk', timeout=8, retry=2, silent=True)
        for seg in (txt or '').split(';'):
            if '~' not in seg:
                continue
            p = seg.split('~')
            if len(p) < 45:
                continue
            try:
                out.append({
                    'name': p[1], 'code': p[2],
                    'price': float(p[3]), 'chg_pct': float(p[32]),
                    'amount_yi': round(float(p[37]) / 10000.0, 2) if p[37] else 0,
                    'src': 'tx',
                })
            except Exception:
                continue
    return out


# ============ 4b. 沪深京量能 ============
def fetch_tx_turnover_today():
    """腾讯快照兜底：上证综指 + 深证综指「当日」成交额（腾讯字段 37 = 成交额/万元）。

    仅在当日 **15:00 之后**可用（盘中成交额不完整，用了会得出偏小的假量能）。
    两个指数都取到才返回，返回 (date, 亿元)；否则 None。
    """
    now = datetime.now()
    if now.hour < 15:
        return None
    txt = http_get('https://qt.gtimg.cn/q=sh000001,sz399106',
                   timeout=8, retry=2, silent=True)
    if not txt:
        return None
    vals = []
    for line in txt.strip().split(';'):
        if '=' not in line:
            continue
        f = line.split('=', 1)[1].strip().strip('"').split('~')
        if len(f) > 37 and f[37]:
            try:
                vals.append(float(f[37]) / 1e4)       # 万元 → 亿元
            except Exception:
                pass
    if len(vals) < 2 or sum(vals) <= 0:
        return None
    return (now.strftime('%Y-%m-%d'), round(sum(vals), 1))


def fetch_market_volume(days=15):
    """沪深两市量能（亿元）：上证综指 + 深证综指 的日成交额之和（不含北交所）。

    数据源：**同花顺指数日K**（`zs_1A0001` 上证 / `zs_399106` 深证综指，第 7 列＝成交额，单位元）
    ＋ **腾讯快照补当日**（`qt.gtimg.cn` 字段 37＝成交额/万元）。
    原东财 push2his 口径（fields2=f51,f57）已停用 —— 其 K线路径会按 IP 封禁，且用户已停用东财。
    返回升序列表 [{'date','amount_yi'}]，末尾为最新一个「已收盘」交易日。

    ⚠️ **同花顺指数日K 盘后不更新当日**（15:00 收盘后仍停在昨日）→ 只靠日K 会永远"差一天"。
    故：日K 出历史，**当日一律用腾讯快照补齐/覆盖**（15:00 后才可信，盘中不补）。
    ⚠️ 旧版有个 bug：补完当日之后，末尾那段「排除未收盘的当日（hour<15）」会把刚补的当日又删掉，
    表现为"盘后量能不刷新"。现在把「当日剔除」的判断**只作用于日K 自身**，补进来的当日不受影响。
    """
    host = 'push2his.eastmoney.com'
    secids = ['1.000001', '0.399106']   # 沪 / 深

    def _amt(secid, lmt=25):
        # ① 同花顺指数日K（首选；用户已停用东财）
        if _BK_SOURCE_PREF != 'em':
            ths = _BK_THS_INDEX.get(secid)
            if ths:
                m = _bk_fetch_ths_amount(ths, lmt)
                if m:
                    return m
            if _BK_SOURCE_PREF == 'ths':
                return None
        # ② 东财 push2his（仅在未显式停用时才尝试）
        for _ in range(2):
            url = ('https://%s/api/qt/stock/kline/get?secid=%s&fields1=f1,f2,f3&'
                   'fields2=f51,f57&klt=101&fqt=0&end=20500101&lmt=%d'
                   % (host, secid, lmt))
            txt = http_get(url, timeout=8, retry=1, silent=True)
            if txt:
                try:
                    ks = json.loads(txt)['data']['klines']
                    if ks:
                        return {k.split(',')[0]: float(k.split(',')[1]) for k in ks}
                except Exception:
                    pass
            time.sleep(0.8)          # 慢节奏，避免被限流
        return None

    # 沪/深相互独立 → 并行拉取（原先串行且各自 8 次重试，抖动时极易拖到分钟级）
    res = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        for sid, m in zip(secids, ex.map(_amt, secids)):
            if m:
                res[sid] = m

    # 当日一律用腾讯快照对齐（同花顺日K 盘后不含当日）——15:00 后才有值，盘中返回 None
    tx = fetch_tx_turnover_today()
    today = datetime.now().strftime('%Y-%m-%d')

    # 沪深为量能主体，任一缺失则走兜底（宁缺勿假，避免出现严重偏小的假量能）
    if '1.000001' not in res or '0.399106' not in res:
        if tx:
            prev = ((load_json(DATA_JSON, {}) or {}).get('market_volume') or {}).get('list') or []
            merged = {x['date']: x['amount_yi'] for x in prev if x.get('date')}
            merged[tx[0]] = tx[1]                     # 只补「当日」，历史沿用上次结果
            out = [{'date': d, 'amount_yi': merged[d]} for d in sorted(merged)]
            print('  [warn] 指数日K 不全 → 用腾讯快照补当日量能 %s = %.0f亿（历史沿用上次 %d 天）'
                  % (tx[0], tx[1], len(prev)))
            return out[-days:]
        print('  [warn] 沪深量能主体缺失，跳过')
        return []

    base = res['1.000001']
    # K线当天若为「未收盘的今日」（盘中拉到的半截数据）→ 先剔除，再由腾讯快照补全
    k_last = sorted(base.keys())[-1] if base else None
    in_session = k_last == today and datetime.now().hour < 15
    out = []
    for d in sorted(base.keys()):
        if in_session and d == today:
            continue
        tot = sum(m.get(d, 0.0) for m in res.values())
        out.append({'date': d, 'amount_yi': round(tot / 1e8, 1)})

    # 用腾讯快照的当日值补齐/覆盖（历史值保持同花顺日K 口径，不用腾讯覆盖）
    if tx:
        td, tval = tx
        out = [x for x in out if x['date'] != td]     # 去重（同花顺偶尔提前给到当日）
        out.append({'date': td, 'amount_yi': tval})
        out.sort(key=lambda x: x['date'])

    return out[-days:]


# ============ 4c. 新闻简述 ============
def news_brief(title, summary, limit=72):
    """把新闻压成「完整句子」的简述：取摘要正文（去【标题】前缀）里的完整句，拼到接近 limit 字。

    源站摘要常在句中被截断，故只保留以 。！？ 结尾的完整句；全无完整句时回退标题。
    """
    t = (title or '').strip()
    s = re.sub(r'^【[^】]*】', '', (summary or '').strip()).strip()
    if not s:
        return t
    parts = re.split(r'(?<=[。！？])', s)
    out = ''
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and len(out) + len(p) > limit:
            break
        out += p
    out = out.strip()
    return out or t or s[:60]


# ============ 5. 新闻 ============
def fetch_news(days=1):
    """抓取每日必看新闻（**同花顺 7×24 快讯**，东财已停用）。
    days=1 只取当日（盘后完整复盘口径）；
    days>1 取最近 N 天（早盘口径，用于覆盖周末与隔夜外围行情）。
    """
    # 同花顺快讯按时间倒序分页，每页 200 条；days>1 时多翻几页以覆盖前几个自然日
    pages = 1 if days <= 1 else min(4, days)
    items = THS.fetch_news(pagesize=NEWS_PAGE_SIZE, pages=pages)
    if not items:
        return {'macro': [], 'good': [], 'bad': [], 'overseas': []}

    today = datetime.now().strftime('%Y-%m-%d')
    # 允许的最早日期：days=1 时只看今天；days>1 时往前覆盖（周末/隔夜）
    earliest = (datetime.now() - timedelta(days=days - 1)).strftime('%Y-%m-%d')
    multi = days > 1
    res = {'macro': [], 'good': [], 'bad': [], 'overseas': []}
    for it in items:
        show_time = it.get('time') or ''
        if not show_time or show_time[:10] < earliest or show_time[:10] > today:
            continue
        title = (it.get('title') or '').strip()
        summary = (it.get('digest') or '').strip()
        # 同花顺快讯的 title 常是短标题、digest 才是完整句 → 合并后按句号取完整句
        brief = news_brief(title, summary)
        if not brief:
            continue
        # 跨天时时间带上日期（MM-DD HH:MM），避免分不清是周末还是当天的消息
        row = {'time': show_time[5:16] if multi else show_time[11:16], 'text': brief}
        low = brief.lower()
        # 个股公告（形如"某某(600xxx.SH)公告称..."）不入宏观，只按利好/利空归类
        is_ann = bool(re.search(r'\(\d{6}\.(SH|SZ|BJ)\)', brief)) or '公告' in brief[:20]
        if is_ann:
            if any(k in low for k in BAD_KW):
                res['bad'].append(row)
            elif any(k in low for k in GOOD_KW):
                res['good'].append(row)
            continue
        if any(k in low for k in OVERSEA_KW):
            res['overseas'].append(row)
        elif any(k in low for k in MACRO_KW):
            res['macro'].append(row)
        elif any(k in low for k in BAD_KW):
            res['bad'].append(row)
        elif any(k in low for k in GOOD_KW):
            res['good'].append(row)
        else:
            continue  # 无法归类 = 无效杂讯，剔除
    for k in res:
        res[k] = res[k][:25]
    return res


# ============ 6. 个股标签（地域 / 概念 / 拼音） ============
def fetch_stock_events(code, name, days=10, own_concepts=None):
    """个股「涨停原因」—— **同花顺涨停原因口径**（东财新闻搜索已停用）。

    返回 (events, hot_boards)：
      events     —— 事件标签，如 重组 / 中标 / 业绩预增 / 政策利好 …（由原因文本按关键词归纳）
      hot_boards —— 该股最近一次涨停时的**题材原因词**，如 ['存储芯片','资产重组']

    数据源：同花顺 `limit_up/block_top` 的 `reason_type` / `reason_info`
      （每个交易日一次请求，由 `build_reason_index()` 预先整批拉好并缓存到模块级 REASON_INDEX）。
      这比抓新闻标题更准：它就是同花顺自己给的「涨停原因」，且与全站同一数据源。
    """
    if not name or not code:
        return [], []
    rec = REASON_INDEX.get(str(code))
    if not rec:
        return [], []
    boards = [b for b in (rec.get('concepts') or [])][:3]
    text = rec.get('info') or ''
    events = []
    for ev, kws in EVENT_KW.items():
        if any(k in text for k in kws):
            events.append(ev)
    # 原因词本身可能直接就是事件（如「资产重组」「股份回购」「业绩预增」）
    joined = ' '.join(boards) + text[:200]
    for ev, kws in EVENT_KW.items():
        if ev not in events and any(k in joined for k in kws):
            events.append(ev)
    events.sort(key=lambda e: 0 if e in EVENT_REAL else 1)
    return events, boards


def build_reason_index(days):
    """整批预热「涨停原因索引」（近 N 个交易日各拉一次 block_top）。

    必须在节点/个股标注**之前**调用，否则 REASON_INDEX 为空 → 事件标签全丢。
    """
    global REASON_INDEX
    ds = [str(d).replace('-', '') for d in (days or [])]
    if not ds:
        return {}
    try:
        REASON_INDEX = THS.fetch_reason_index(ds, limit=60, max_workers=6)
    except Exception as e:
        print('  [warn] 涨停原因索引抓取失败：%s' % type(e).__name__)
        REASON_INDEX = {}
    if REASON_INDEX:
        print('  涨停原因索引：%d 只（覆盖 %d 个交易日）' % (len(REASON_INDEX), len(ds)))
    return REASON_INDEX


# 涨停原因索引（模块级缓存，见 build_reason_index）
REASON_INDEX = {}


def _related(board, own_concepts):
    """新闻点名的板块 与 个股已有概念 是否相关（避免综述误配）"""
    b = re.sub(r'(板块|概念股?)$', '', board)
    for c in own_concepts:
        c2 = re.sub(r'(板块|概念股?)$', '', c)
        if not b or not c2:
            continue
        if b == c2:
            return True
        if len(b) >= 2 and (b in c2 or c2 in b):
            return True
        # 别名映射后比较（CPO ↔ 光通信模块）
        a1 = CONCEPT_ALIAS.get(board) or CONCEPT_ALIAS.get(b)
        a2 = CONCEPT_ALIAS.get(c) or CONCEPT_ALIAS.get(c2)
        if a1 and a2 and a1 == a2:
            return True
    return False


# 「风格 / 属性类」弱题材词：它们在**同花顺概念榜里确实有对应板块**（选中会命中），
# 但**不是真正在炒的题材** —— 一只票挂这些标签只是因为它的属性（次新、国企、破净…）。
# 归类时必须**降权**，否则泛词会把真题材挤掉（实测：锡华科技 因涨停原因首位是「次新股」
# 被归到「科创次新股」，而它真正炒的是「风电齿轮箱」）。
WEAK_THEME_KW = ('次新', '国企改革', '预盈预增', '预亏预减', '贬值受益', '送转', '高送转',
                 '破净', '低价股', '百元股', '微盘', '壳资源', '摘帽', 'AB股', 'AH股',
                 '融资融券', '转债标的', 'MSCI', '富时', '标普', 'GDR', '专精特新',
                 '昨日', '涨停', '跌停', '抱团', '机构重仓', '社保重仓', 'QFII', '基金重仓')


def match_board(cname):
    """概念名 → 命中的板块 `(板块名, 当日涨幅)`；找不到返回 `(None, None)`。

    匹配优先级：**精确同名** > **去后缀后相等**（CPO ↔ CPO概念）> **互相包含**（光通信模块 ↔ 光模块）。
    同一档内取**当日涨幅最高**的那个。

    ⚠️ 分档很重要：若不分档，像「AI」这种泛词会被当天涨得最好的「智谱AI」抢走，
    而它本来该落到更贴切的同义板块上。
    """
    if not BOARD_HEAT or not cname:
        return (None, None)
    if cname in BOARD_HEAT:
        return (cname, BOARD_HEAT[cname])
    base = re.sub(r'(板块|概念股?)$', '', cname)
    if not base:
        return (None, None)
    same, fuzzy = [], []
    for bn, pct in BOARD_HEAT.items():
        b2 = re.sub(r'(板块|概念股?)$', '', bn)
        if not b2:
            continue
        if base == b2:
            same.append((bn, pct))          # 去掉「板块/概念」后缀后完全相等
        elif (len(base) >= 2 and base in b2) or (len(b2) >= 2 and b2 in base):
            fuzzy.append((bn, pct))         # 互相包含
    pool = same or fuzzy
    if not pool:
        return (None, None)
    return max(pool, key=lambda t: t[1])


def concept_heat(cname):
    """概念对应板块的当日涨幅（用于「当下热度」排序），找不到返回 None"""
    return match_board(cname)[1]


def rank_concepts(concepts):
    """概念按「对应板块当日涨幅」降序：涨幅最高的就是当下正在炒的概念。"""
    scored = []
    for c in concepts:
        h = concept_heat(c)
        scored.append((h if h is not None else -999, c))
    scored.sort(key=lambda x: (-x[0],))
    return [c for _, c in scored]


def market_of(code):
    """由证券代码推断东财市场标识：1=沪市(6/9开头)，0=深市/北交所(其余)。
    历史节点里个别记录 market 字段有误（如深市 002084 被记为 1），以代码为准更可靠。"""
    return 1 if str(code)[:1] in ('6', '9') else 0


def _secid(code, market=None):
    return '%d.%s' % (market_of(code), code)


def fetch_stock_tags(code, market, name, cache):
    """返回 {region, concepts, industry, pinyin}。

    数据源：同花顺 F10（`basic.10jqka.com.cn/<码>/concept.html` 概念 + `company.html` 地域/行业）。
    东财 `push2/slist` 与 `datacenter F10` 均已停用。
    """
    key = '%s.%s' % (market_of(code), code)
    cached = cache.get(key) or {}
    if cached.get('concepts') and cached.get('_v') == TAGS_VER:
        c = dict(cached)
        c['pinyin'] = pinyin_abbr(name)
        return c

    f10 = THS.fetch_f10(code)
    raw = list(f10.get('concepts') or [])
    region = (f10.get('region') or '').strip()
    ths_industry = (f10.get('industry') or '').strip()

    # ---- 板块分流：行业 -> industry，题材 -> concepts（噪声/地域已剔除）----
    def _is_industry(bn):
        b = bn.replace('板块', '').strip()
        b2 = re.sub(r'[ⅠⅡⅢⅣⅤ]+$', '', b)
        return b in INDUSTRY_BLOCK or b2 in INDUSTRY_BLOCK

    industries, themes = [], []
    for bn in raw:
        if _is_industry(bn):
            industries.append(re.sub(r'[ⅠⅡⅢⅣⅤ]+$', '', bn.replace('板块', '').strip()))
            continue
        if any(k in bn for k in NOISE_KW):
            continue
        b = bn.replace('板块', '').strip()
        b2 = re.sub(r'[ⅠⅡⅢⅣⅤ]+$', '', b)
        # 地域类概念（如「北京」「江苏」）只填 region，不进 concepts
        if b in PROVINCES or b2 in PROVINCES:
            if not region:
                region = b
            continue
        themes.append(CONCEPT_ALIAS.get(bn) or CONCEPT_ALIAS.get(b) or CONCEPT_ALIAS.get(b2) or b)

    # 去重保序
    seen, clean = set(), []
    for t in themes:
        if t and t not in seen:
            seen.add(t)
            clean.append(t)

    # 人工兜底置顶（东财抓不到该题材时用，如华盛昌=CPO）
    fix = CONCEPT_FIX.get(str(code))
    if fix:
        clean = list(fix) + [c for c in clean if c not in fix]
    clean = clean[:8]

    # 当下热度排序：同一只票每一波走的概念不同，
    # 按「对应板块当日涨幅」降序，把当下正在炒的排到最前。
    if BOARD_HEAT:
        clean = rank_concepts(clean)
    clean = clean[:6]

    industry = industries[0] if industries else ths_industry

    # 网络失败且无新数据时，降级用旧缓存（但按新口径过滤一遍）
    if not clean and cached.get('concepts'):
        for c in cached['concepts']:
            if c in INDUSTRY_BLOCK or any(k in c for k in NOISE_KW):
                continue
            if c not in clean:
                clean.append(c)
        clean = clean[:6]
        if not industry:
            industry = cached.get('industry', '')

    out = {'region': region or cached.get('region') or '—',
           'concepts': clean, 'industry': industry or (clean[0] if clean else ''),
           'pinyin': pinyin_abbr(name), '_v': TAGS_VER}
    cache[key] = out
    return out


# ============ 7. 节点判定 ============
def track_status(code, node_date, days_list, zt_hist, today_lbc, fallback_boards=0):
    """跟踪节点票从「节点日次日」到今天的涨停连续性，返回 (status, boards)。

    status 四种：
      连板中   —— 期间从未断板，今天仍在涨停且 lbc>=2
      首板     —— 期间从未断板，今天涨停但只有 1 板
      断板反包 —— 期间断过板，但在断板后 ≤3 个交易日内重新涨停（今天在涨停池）
      已断板   —— 今天不在涨停池（UI 默认折叠）

    说明：同一节点内的票应属于同一梯队（如国芳穿越节点的票都应是 4 板）。
    若某票板数明显低于梯队（中间断过板又反包上来），则标记「断板反包」。
    """
    after = [d for d in days_list if d > node_date]
    seq = []          # 节点日之后每个交易日的 lbc（0 = 当天未涨停）
    for d in after:
        hit = None
        for x in zt_hist.get(d, []):
            if x['code'] == code:
                hit = x
                break
        seq.append(hit['lbc'] if hit else 0)

    if today_lbc:                       # 今天仍在涨停池
        if 0 not in seq:                # 一路连板未断
            return ('连板中' if today_lbc >= 2 else '首板'), today_lbc
        last_break = max(i for i, v in enumerate(seq) if v == 0)
        gap = len(seq) - 1 - last_break  # 断板后到今天经过的交易日数
        if gap <= 3:
            return '断板反包', today_lbc
        return ('连板中' if today_lbc >= 2 else '首板'), today_lbc
    # 已断板：板数取「**最后一次涨停那天的连板数**」（可从 zt_hist 精确回溯），
    # 而不是沿用上一版 data.json 的旧值 —— 旧值可能是历史错误口径算出来的，
    # 沿用会把它永久固化（本次连板 bug 的连带问题）。
    last = 0
    for d in reversed(after):
        for x in zt_hist.get(d, []):
            if x['code'] == code:
                last = x['lbc']
                break
        if last:
            break
    return '已断板', (last or fallback_boards)


def find_start_volume(zt_hist, days, idx, code):
    """从 idx 往前找该股本轮连板的启动首板（lbc==1）成交额"""
    for j in range(idx, -1, -1):
        for s in zt_hist.get(days[j], []):
            if s['code'] == code and s['lbc'] == 1:
                return s['amount']
    return None


def detect_nodes(zt_hist, days):
    """返回节点列表（未做标签标注）"""
    nodes = []
    for i in range(1, len(days)):
        d_prev, d_today = days[i - 1], days[i]
        prev_pool = zt_hist.get(d_prev, [])
        today_pool = zt_hist.get(d_today, [])
        if not prev_pool or not today_pool:
            continue
        today_map = {s['code']: s for s in today_pool}

        # ---- 1. 最高标断板节点 ----
        max_prev = max((s['lbc'] for s in prev_pool), default=0)
        if max_prev >= 2:
            tops_prev = [s for s in prev_pool if s['lbc'] == max_prev]
            # 同梯队去重：并列最高标中有票今天晋级成功 → 断板方并入穿越节点（如国芳晋级/竞业达断板只留国芳穿越）
            any_advance = any(s['code'] in today_map and today_map[s['code']]['lbc'] > max_prev
                               for s in tops_prev)
            for top in tops_prev:
                if top['code'] in today_map:
                    continue  # 未断板
                if any_advance:
                    continue  # 同梯队有人晋级成功，失败方不单列断板节点
                v_start = find_start_volume(zt_hist, days, i - 1, top['code'])
                ratio = round(top['amount'] / v_start, 2) if v_start else None
                # 断板节点票池：统一取当日首板（用户口径，新龙孵化池）。
                # 注：断板「放量」与否看的是断板日量 vs 涨停日量环比，
                #     温和放量才可能接 2板；缩量/暴量出货均落首板。
                pool_type, stocks = '首板', [s for s in today_pool if s['lbc'] == 1]
                if stocks:
                    nodes.append({
                        'id': 'BREAK_%s_%s' % (d_today.replace('-', ''), top['code']),
                        'type': '最高标断板节点',
                        'date': d_today, 'trigger': top, 'volume_ratio': ratio,
                        'pool_type': pool_type, 'stocks': stocks, 'replaced': [],
                        'desc': '%s %s板断板（涨停日量/启动量=%s）→ 取当日首板' % (
                            top['name'], max_prev, ratio if ratio else '—'),
                    })

        prev_map = {s['code']: s for s in prev_pool}

        # ---- 2/3. 突破节点 & 穿越节点（互斥：突破优先）----
        # 突破：连板高度创 stage 新高（超过回看窗口内历史最高）
        # 穿越：最高标跨越前期天花板 —— 今日最高板 > 昨日最高板，且由昨日最高标晋级而来
        #       例：9/3 最高5板(国芳) → 9/4 国芳5进6断板、龙版接棒5板 → 9/7 龙版5进6，
        #           跨过5板天花板成为6板 = 穿越节点，节点票取 9/7 当日首板。
        hist_max = 0
        for j in range(max(0, i - BREAKOUT_LOOKBACK), i):
            for s in zt_hist.get(days[j], []):
                hist_max = max(hist_max, s['lbc'])
        today_max = max((s['lbc'] for s in today_pool), default=0)
        first_boards = [s for s in today_pool if s['lbc'] == 1]

        if hist_max > 0 and today_max > hist_max:
            # 突破节点：创阶段新高
            if first_boards:
                bt = [s for s in today_pool if s['lbc'] == today_max]
                nodes.append({
                    'id': 'BREAKOUT_%s' % d_today.replace('-', ''),
                    'type': '突破节点',
                    'date': d_today, 'trigger': bt[0] if bt else None,
                    'volume_ratio': None, 'pool_type': '首板', 'stocks': first_boards,
                    'replaced': [],
                    'desc': '连板高度由%d板突破至%d板 → 取当日首板' % (hist_max, today_max),
                })
        elif max_prev >= 2 and today_max > max_prev:
            # 穿越节点：今日最高标由昨日晋级而来，且跨过昨日最高板（前期天花板）
            tops_today = [s for s in today_pool if s['lbc'] == today_max]
            crossed = [s for s in tops_today
                       if s['code'] in prev_map and prev_map[s['code']]['lbc'] < s['lbc']]
            if crossed and first_boards:
                trig = crossed[0]
                # replaced：昨日同处最高板、今日被甩在身后的票（若有）
                replaced = [s['code'] for s in prev_pool
                            if s['lbc'] == max_prev and s['code'] != trig['code']
                            and s['code'] not in today_map]
                nodes.append({
                    'id': 'CROSS_%s' % d_today.replace('-', ''),
                    'type': '穿越节点',
                    'date': d_today, 'trigger': trig,
                    'volume_ratio': None, 'pool_type': '首板', 'stocks': first_boards,
                    'replaced': replaced,
                    'desc': '%s由%d板晋级%d板，跨过前期%d板天花板 → 取当日首板' % (
                        trig['name'], prev_map[trig['code']]['lbc'], today_max, max_prev),
                })
    return nodes


def apply_focus(nodes_out):
    """节点聚焦：只看「当前最高标」的血统链。

    修正（2026-09-08）：旧逻辑取「今日涨停池最高连板票」当下标，
    但当前最高标（龙版传媒）若今日断板则不在涨停池中，会误把当日新晋
    最高板（如爱仕达）当顶点，导致真正的当前最高标周期（9/7 穿越、9/8
    断板）反而被排除，而旧国芳节点被错误聚焦。

    新逻辑：当前最高标 = nodes 中「最近一个节点（按 date 降序）」的 trigger。
    该 trigger 即本轮总龙头（龙版）。血统链 = {龙版} ∪ 龙版各节点 replaced
    （被龙版穿越取代的前任，如国芳）。birth_date 取链上节点最早 date，避免
    把最高标上一轮老节点（深中华A 等）卷进来。
    """
    # 当前最高标 = 降序后首个带 trigger 的节点
    cur_top = None
    for n in nodes_out:
        if (n.get('trigger') or {}).get('code'):
            cur_top = n['trigger']['code']
            break
    top_code = cur_top

    chain_codes = set()
    if top_code:
        chain_codes.add(top_code)
        for n in nodes_out:
            trig = n.get('trigger') or {}
            if trig.get('code') == top_code:
                for rc in (n.get('replaced') or []):
                    chain_codes.add(rc)

    birth_date = None
    if chain_codes:
        bd = [n['date'] for n in nodes_out
              if (n.get('trigger') or {}).get('code') in chain_codes]
        if bd:
            birth_date = min(bd)

    for n in nodes_out:
        trig = n.get('trigger') or {}
        n['top_related'] = bool(top_code and birth_date and n['date'] >= birth_date
                                and trig.get('code') in chain_codes)
    return nodes_out


# 主线主题映射：个股概念(行业大类) 与 板块榜(题材词) 命名体系不同，
# 统一归一化到「主线主题」再做交集，避免 农林牧渔↔猪肉 这类漏匹配导致假0。
THEME_MAP = {
    '农业':   ['农林牧渔', '种植业', '农业', '猪肉', '养鸡', '家禽', '水产品', '玉米', '兽药', '饲料', '养殖', '渔业', '粮食'],
    '消费':   ['零售', '百货', '商贸', '家电', '小家电', '黄酒', '白酒', '啤酒', '食品', '饮料', '商超', '消费', '免税'],
    '稳定币': ['稳定币', 'RWA', '数字货币', '区块链', '数字人民币', '跨境支付'],
    '航运':   ['航运', '港口', '海运', '交通运输'],
    '传媒':   ['传媒', '出版', '教育', '文化', '在线教育', '游戏'],
    '电力':   ['电力', '热电', '公用事业', '能源', '天然气'],
    '军工':   ['中船系', '军工', '船舶', '卫星'],
    '科技':   ['AI', '人工智能', '互联网服务', '大数据', '计算机', 'IT服务', '算力', '机器人'],
    '医药':   ['医药', '创新药', '中药', '医疗器械'],
    '金融':   ['证券', '银行', '保险', '多元金融'],
    '地产':   ['房地产', '地产', '园区'],
    '有色':   ['有色金属', '黄金', '稀土', '铜'],
}

def theme_of(text):
    """返回 text 命中的主线主题列表"""
    out = []
    if not text:
        return out
    for th, kws in THEME_MAP.items():
        for kw in kws:
            if kw and kw in text:
                out.append(th)
                break
    return out

def concept_hit_count(concepts, hot_list):
    """题材共振 = 个股概念命中的「主线主题」数。
    个股概念(行业大类) 与 板块榜(题材词) 命名体系不同，先归一化到同一套
    主线主题再做交集，避免 农林牧渔↔猪肉 漏匹配导致假0。"""
    s_themes = set()
    for c in concepts or []:
        s_themes.update(theme_of(c))
    hot_themes = set()
    for h in hot_list or []:
        hot_themes.update(theme_of(h))
    return len(s_themes & hot_themes)


def ferment_count(concepts, zt_pool, own_industry=''):
    """板块发酵度：候选股题材概念 与 当日涨停池细分行业 模糊匹配命中的涨停家数/连板数。

    注意：东财 zt_pool 的 hybk(细分行业) 被**截断为 ≤4 字**（如「旅游及景区」→「旅游及景」、
    「炼化及贸易」→「炼化及贸」），若只做整串子串匹配会大量假 0（发酵列长期空白）。
    故匹配放宽为：① 整串互相包含；② **前 2 字词干**互相包含。
    另把候选自身的细分行业(own_industry)也纳入比对键，直接统计「同细分行业涨停家数」。
    """
    keys = [c for c in (concepts or []) if len(c) >= 2]
    if own_industry and len(own_industry) >= 2:
        keys.append(own_industry)
    zt, lb = 0, 0
    for s in zt_pool or []:
        ind = s.get('industry') or ''
        if len(ind) < 2:
            continue
        hit = False
        for k in keys:
            if k in ind or ind in k or k[:2] in ind or ind[:2] in k:
                hit = True
                break
        if hit:
            zt += 1
            if s.get('lbc', 0) >= 2:
                lb += 1
    return {'zt': zt, 'lb': lb}


def dedup_candidates(cands):
    """同一只票可能同时属于多个节点，保留命中最高的那条。
    优先级：节点类型(穿越>突破>断板) > 板数 > 题材共振 > 成交额。"""
    best = {}
    for c in cands:
        k = c['code']
        cur = best.get(k)
        score = (_node_type_priority(c.get('node_type', '')), c.get('boards', 0),
                 c.get('concept_hit', 0), c.get('amount_yi', 0))
        if cur is None:
            best[k] = c
        else:
            cur_score = (_node_type_priority(cur.get('node_type', '')), cur.get('boards', 0),
                         cur.get('concept_hit', 0), cur.get('amount_yi', 0))
            if score > cur_score:
                best[k] = c
    out = list(best.values())
    out.sort(key=lambda x: (-_node_type_priority(x.get('node_type', '')),
                            -x['boards'], -x['concept_hit'], -x['amount_yi']))
    return out


def build_candidates(nodes_out, today_pool, hot_concepts, cache):
    """构造次日连板候选池。

    数据源 = 各节点 trigger(连板中的最高标) + 各节点 stocks(连板中的票)。

    🔴 **入池先决条件（2026-09-22 用户明确）**：
    **候选池是「连板」候选池 → 入池票必须是「今天在涨停池、且连板数 ≥ 2」的票。**
    理由：连板 = 自本轮第一个涨停起每个交易日都涨停、**断开即重新起算**（§3 口径）。
    所以「今天涨停」且「连板数 ≥ 2」**必然蕴含「前一交易日也涨停」** —— 这正是候选池
    （次日竞价接力）该有的前提。反过来：
      · 首板（lbc==1）→ 前一交易日没涨停 → **不构成连板，必须剔除**；
      · 断板反包（连板段中途断过、今天重新涨停）→ 今天的 lbc 从 1 重新起算 →
        若 lbc==1 同样**不是连板**，也要剔除；只有它已续成 ≥2 板（lbc>=2）才算连板。
    ⚠️ 曾经的 bug（2026-09-22 用户第二次报）：第二分支只看 `status in ('连板中','断板反包')`，
      **没校验 `boards`** → 一批「节点当日首板」（lbc==1）通过它们在**旧节点**里的
      `断板反包` 记录混进池子（同一票在多个节点出现，dedup 取到旧节点那条），
      表现为「候选池里一堆断板票 / 首板票」。
    trigger 单独入池是为了避免它被旧节点的 stock dedup 覆盖掉（如龙版 6 板
    穿越节点是当日 trigger，但最早它出现在 8/31 断板节点的 stocks 里）。
    """
    today_map = {s.get('code'): s for s in (today_pool or [])}
    cands = []

    def _ok_boards(b):
        """入池门槛：连板数 ≥ 2（⟺ 前一交易日也涨停）。"""
        try:
            return int(b or 0) >= 2
        except (TypeError, ValueError):
            return False

    for n in nodes_out:
        trg = n.get('trigger') or {}
        if not trg.get('code'):
            continue
        cur = today_map.get(trg['code'])
        # ⚠️ 门槛用**当日涨停池的实时 lbc**（权威值），不信节点里存的历史 boards
        if not cur or not _ok_boards(cur.get('lbc')):
            continue
        # ⚠️ nodes 里的 trigger 常缺 market 字段：不能默认 1(沪)，否则深市票会查错
        #    cache key（'1.000993'）→ 概念/地域/行业全空，且 secid 指向别的标的。
        mk = trg.get('market')
        if mk not in (0, 1, '0', '1'):
            mk = 1 if str(trg['code']).startswith('6') else 0
        mk = int(mk)
        ck = '%s.%s' % (mk, trg['code'])
        _c = cache.get(ck) or {}
        # cache 未收录时，回退用节点自身记录的题材/地域/行业（同源数据，非虚构）
        cpt = _c.get('concepts') or trg.get('concepts') or []
        ind = _c.get('industry') or trg.get('industry') or ''
        rgn = _c.get('region') or trg.get('region') or '—'
        hit = concept_hit_count(cpt, hot_concepts)
        ferm = ferment_count(cpt, today_pool, cur.get('industry') or ind)
        cands.append({
            'node_type': n['type'], 'node_date': n['date'], 'node_id': n['id'],
            'code': trg['code'], 'market': mk, 'name': trg['name'],
            'region': rgn, 'pinyin': trg.get('pinyin') or pinyin_abbr(trg.get('name', '')),
            'concepts': cpt, 'industry': ind,
            'boards': cur['lbc'], 'status': '连板中',
            'concept_hit': hit, 'ferment': ferm,
            'zbc': int(cur.get('zbc') or 0),          # 当日炸板次数（风险标记）
            'first_seal': cur.get('first_seal'),
            'amount_yi': round(cur['amount'] / 1e8, 2),
            'total_cap_yi': round(cur['total_cap'] / 1e8, 2),
        })
    for n in nodes_out:
        for s in n.get('stocks', []):
            # 🔴 只收「连板中」且连板数 ≥ 2 的票（见函数头口径说明）。
            #    ⛔ 不再收「断板反包」：它今天已断过板、连板从 1 重新起算，
            #       若已续成 ≥2 板则以 status='连板中' 出现在它**自己的节点**里（会被这里收到）；
            #       status='断板反包' 的 boards 恒等于当日 lbc，可能只有 1 → 不是连板。
            if s.get('status') != '连板中':
                continue
            # ⚠️ 门槛双查：节点里存的 boards 可能滞后；以**当日涨停池的实时 lbc**为准
            cur = today_map.get(s['code'])
            lbc_now = cur.get('lbc') if cur else None
            if not cur or not _ok_boards(lbc_now):
                continue
            hit = concept_hit_count(s.get('concepts', []), hot_concepts)
            ferm = ferment_count(s.get('concepts', []), today_pool,
                                 cur.get('industry') or '')
            cands.append({
                'node_type': n['type'], 'node_date': n['date'], 'node_id': n['id'],
                'code': s['code'], 'market': s['market'], 'name': s['name'],
                'region': s.get('region', '—'), 'pinyin': s.get('pinyin', ''),
                'concepts': s.get('concepts', []), 'industry': s.get('industry', ''),
                'boards': lbc_now, 'status': s.get('status', ''),
                'concept_hit': hit, 'ferment': ferm,
                'zbc': int(cur.get('zbc') or 0),   # 当日炸板次数
                'first_seal': cur.get('first_seal'),
                'amount_yi': round(s.get('amount', 0) / 1e8, 2),
                'total_cap_yi': round(s.get('total_cap', 0) / 1e8, 2),
            })
    out = dedup_candidates(cands)[:12]
    # 🛡️ 收尾自检：出池前再过滤一次，确保**没有任何 1 板票**漏网
    #    （任何分支/未来改动引入的 1 板票都会在这里被挡下）
    bad = [c for c in out if not _ok_boards(c.get('boards'))]
    if bad:
        print('  [warn] 候选池自检剔除 %d 只连板数<2 的票：%s'
              % (len(bad), '、'.join('%s(%s板)' % (c.get('name'), c.get('boards')) for c in bad)))
        out = [c for c in out if _ok_boards(c.get('boards'))]
    return out


def build_reco(cands, today_map, today, verbose=True):
    """给候选池补「上板分时量」（= 次日竞价硬线的基准），并生成次日首选推荐卡。

    返回 (cands, reco)。上板分时量**全量并发**计算（6 线程，逐只 1 个请求，带当日缓存）。
    抽成独立函数是为了让「盘前竞价任务(premarket.py)」与盘后流程共用同一口径。
    """
    def _seal_of(c):
        cur = today_map.get(c['code']) or {}
        return fetch_seal_amount(c['code'], c.get('market', 0), today,
                                 cur.get('first_seal'), bool(cur.get('is_yizi')))

    with ThreadPoolExecutor(max_workers=6) as ex:
        seals = list(ex.map(_seal_of, cands))

    for c, (seal, day_total) in zip(cands, seals):
        if seal:
            c['seal_amount_yi'] = round(seal / 1e8, 2)
            c['bid_required_yi'] = round(seal / 1e8 * 0.5, 2)
            c['bid_basis'] = '上板分时量'
        else:
            c['seal_amount_yi'] = None
            c['bid_required_yi'] = round((c.get('amount_yi') or 0) * 0.5, 2)
            c['bid_basis'] = '全天成交额×50%(分时缺失回退)'
        if day_total:
            c['day_amount_yi'] = round(day_total / 1e8, 2)

    reco = None
    if cands:
        c = cands[0]
        bid = c.get('bid_required_yi') or round(c['amount_yi'] * 0.5, 2)
        reco = {
            'name': c['name'], 'code': c['code'], 'boards': c['boards'],
            'region': c['region'], 'pinyin': c['pinyin'], 'concepts': c['concepts'],
            'from_node': c['node_type'], 'node_date': c['node_date'],
            'concept_hit': c['concept_hit'], 'ferment': c['ferment'],
            'industry': c.get('industry', ''),
            'amount_yi': c['amount_yi'],
            'seal_amount_yi': c.get('seal_amount_yi'),
            'bid_basis': c.get('bid_basis', ''),
            'total_cap_yi': c['total_cap_yi'],
            'bid_required_yi': bid,                              # 竞价额 >= 上板分时量*50%
            'seal_min_yi': round(c['total_cap_yi'] * 0.01, 2),   # 封单 >= 总市值 1%
            'seal_max_yi': round(c['total_cap_yi'] * 0.03, 2),   # 封单 <= 总市值 3%
        }
        if verbose:
            print('  首位候选：%s(%s) %s板 · %s · 来自%s'
                  % (reco['name'], reco['code'], reco['boards'], reco['region'], reco['from_node']))
            print('  竞价量下限 %.2f 亿 | 封单区间 %.2f~%.2f 亿'
                  % (reco['bid_required_yi'], reco['seal_min_yi'], reco['seal_max_yi']))
    return cands, reco


# ============ 8. 主流程 ============
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=20,
                    help='回溯交易日数量（东财涨停池仅保留最近约15个交易日）')
    ap.add_argument('--tag-limit', type=int, default=40, help='单次标注个股数量上限')
    ap.add_argument('--no-macro', action='store_true',
                    help='跳过「必看」页大盘宏观面板（macro.py）；默认盘后一并生成')
    args = ap.parse_args()

    t0 = time.time()
    print('=' * 60)
    print('A股短线仪表盘 · 数据采集  %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print('=' * 60)

    # ⚠️ 依赖自检：pypinyin 决定「股票缩写」，缺了会静默把全站缩写写成空串（2026-09-22 踩过）
    if not PINYIN_OK:
        print('\n' + '!' * 60)
        print('⚠️⚠️  pypinyin 未安装 —— 本次生成的「股票缩写(pinyin)」将全部为空！')
        print('      修复：<python> -m pip install pypinyin   （本机清华源不通，用官方源）')
        print('      装完必须重跑本脚本 + enrich_tags.py 才会把缩写补回来。')
        print('!' * 60)
    else:
        print('  [依赖] pypinyin OK → 股票缩写可正常生成')

    print('\n[1/8] 获取交易日...')
    days = fetch_trade_days(args.days)
    if not days:
        print('  ❌ 交易日获取失败，终止')
        return
    print('  交易日 %d 个：%s ~ %s' % (len(days), days[0], days[-1]))

    print('\n[2/8] 抓取历史涨停池...')
    # 并发抓取（4 线程）→ 对空结果顺序补抓一次（并发可能触发限流，保证不漏交易日）
    with ThreadPoolExecutor(max_workers=4) as ex:
        pairs = list(ex.map(lambda d: (d, fetch_zt_pool(d.replace('-', ''))), days))
    zt_hist = {d: p for d, p in pairs if p}
    miss = [d for d, p in pairs if not p]
    if miss:
        print('  补抓空结果 %d 天...' % len(miss))
        for d in miss:
            p = fetch_zt_pool(d.replace('-', ''))
            if p:
                zt_hist[d] = p
            time.sleep(0.15)
    print('  有效交易日涨停数据：%d 天' % len(zt_hist))

    # 连板数「严格连续」口径自算：交叉校验同花顺连板梯队接口 + 修正其降级值。
    # 必须**在节点/候选池构建之前**执行 —— 下游 build_ladder / track_status / build_candidates
    # 全部读 zt_hist[*]['lbc']，此处不改则错误口径会一路传到前端（本次连板 bug 的教训）。
    recount_lbc_by_continuity(zt_hist, days)

    # 跌停池：与涨停池同步抓（用于「跌停板梯队」——家数趋势 + 连续跌停明细）
    dt_days = days[-DT_LADDER_DAYS:] if len(days) > DT_LADDER_DAYS else days
    with ThreadPoolExecutor(max_workers=4) as ex:
        dt_pairs = list(ex.map(lambda d: (d, fetch_dt_pool(d.replace('-', ''))), dt_days))
    dt_hist = {d: p for d, p in dt_pairs if p}
    print('  跌停池：近 %d 个交易日，有跌停的 %d 天' % (len(dt_days), len(dt_hist)))

    print('\n[3/8] 抓取指数 / 板块 / 新闻 / 量能...')
    # 四项互不依赖 → 并行抓取（原先串行，网络抖动时最耗时）
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_idx = ex.submit(fetch_index)
        f_board = ex.submit(fetch_board_rank, 60)
        f_news = ex.submit(fetch_news)
        f_mv = ex.submit(fetch_market_volume, 15)
        index = f_idx.result()
        brd = f_board.result()
        news = f_news.result()
        mv = f_mv.result()
    board = brd['industry']      # 同花顺行业榜
    concept = brd['concept']     # 同花顺概念榜

    # ---- 产业板块榜：按用户指定清单（SECTOR_WATCH），由同花顺逐条取数 ----
    sector = list(brd.get('watch') or [])
    sector.sort(key=lambda x: -x['pct'])
    _miss = brd.get('watch_missing') or []
    print('  产业板块 %d/%d 个匹配到同花顺（按当日涨幅降序）%s'
          % (len(sector), len(SECTOR_WATCH), ('，缺：' + '、'.join(_miss)) if _miss else ''))
    print('  指数 %d 条 | 行业 %d 条 | 概念 %d 条 | 新闻 宏观%d 利好%d 利空%d 外围%d'
          % (len(index), len(board), len(concept), len(news['macro']),
             len(news['good']), len(news['bad']), len(news['overseas'])))

    # 沪深京量能（近 15 个已收盘交易日）
    if mv:
        print('  沪深京量能 %d 日，最新 %s = %.0f 亿'
              % (len(mv), mv[-1]['date'], mv[-1]['amount_yi']))
    else:
        mv = (load_json(DATA_JSON, {}) or {}).get('market_volume', {}).get('list', [])
        print('  沪深京量能：本次未取到，%s'
              % ('沿用上次 %d 日' % len(mv) if mv else '无数据'))

    # 3 日榜：同花顺榜页无 3日/5日 列 → 按板块指数日K自算（见 fetch_board_rank）
    print('\n[4/8] 生成 3 日榜...')
    today = days[-1]
    board_3d = sorted([b for b in board if b.get('d3') is not None],
                      key=lambda x: -(x.get('d3') or 0))[:10]
    board_3d_note = '3日涨幅（同花顺板块指数自算）'
    # 产业板块 3 日榜（同一份清单，按 3 日涨幅降序）
    sector_3d = sorted(sector, key=lambda x: -(x.get('d3') or 0))
    board_3d_days = 3
    print('  3日榜：%d 条 · %s' % (len(board_3d), board_3d_note))

    # 板块「涨停 / 全部」统计：拉成分股逐只判涨停（行业榜+概念榜+3日榜+产业清单，去重）
    print('\n[4b/7] 统计板块涨停家数...')
    _stat = {}
    for _b in (board[:10] + board_3d + sector):
        _k = _b.get('ths_code') or _b.get('code')
        if _k:
            _stat[_k] = _b
    fetch_board_zt_stats(list(_stat.values()))
    print('  已统计 %d/%d 个板块' % (
        sum(1 for _b in _stat.values() if _b.get('stock_total')), len(_stat)))

    # 板块K线预生成：随 data.js 下发，前端点击即开（避免浏览器跨域取东财失败）
    print('\n[4c/7] 预生成榜单板块的日K/周K...')
    # 只给「页面实际展示的板块」生成K线（= 产业板块清单 28 个）。
    # 注：board_daily/board_3d 已不在页面展示（仅供盘后 module4 补成分股用），故不再预生成其K线，
    #     否则请求数翻倍（同花顺虽宽松，但也没必要）。
    _kl = {}
    for _b in sector:
        if _b.get('em_code'):
            _kl[_b['em_code']] = _b
    # 传入上一版 board_kline：内部会自动合并——新数据残缺/失效的板块沿用老数据。
    _old_kl = (load_json(DATA_JSON, {}) or {}).get('board_kline') or {}
    board_kline = fetch_board_klines(list(_kl.values()), old=_old_kl)
    print('  已生成 %d/%d 个板块的日K/周K（日K120根·周K60根）' % (len(board_kline), len(_kl)))
    # ⚠️ 同花顺日K**盘中不含当日**（当日行缺省或为昨收占位）→ 末条日期落后于今日时明确提示，
    #    避免「板块涨幅是今天、K线却停在昨天」这种静默落后。
    _smp = next((v for v in board_kline.values() if v.get('day')), None)
    if _smp:
        _last = str(_smp['day'][-1]).split(',')[0].replace('-', '')
        _today = datetime.now().strftime('%Y%m%d')
        if _last != _today:
            print('  [warn] 板块K线末条=%s，未含今日(%s) —— 同花顺板块日K当日发布较晚（常到当晚），'
                  '属正常；要补当日须重跑本脚本，或 `python fill_board_kline.py --force`'
                  '（⚠️ 不带 --force 只会补"空"的，不会重取"落后"的）'
                  % (_smp['day'][-1].split(',')[0], _today))

    # ---- 梯队折线图数据 ----
    print('\n[5/8] 构建梯队折线数据...')
    series = build_ladder(zt_hist, days)
    print('  梯队数据点 %d 个' % len(series))

    # ---- 跌停板梯队：与连板高度梯队**完全同构**（最高连跌 / 次高连跌 / 创业板，近 7 日）----
    print('\n[5b/7] 构建跌停板梯队...')
    dt_series = build_dt_ladder(dt_hist, dt_days)
    print('  跌停梯队数据点 %d 个' % len(dt_series))

    # 今日跌停明细 + 题材标签（用于跌停梯队列表、判断杀跌方向）
    dt_today = dt_hist.get(today, [])
    if dt_today:
        _fcache = load_json(STOCK_CACHE, {})
        for s in dt_today[:25]:
            try:
                tg = fetch_stock_tags(s['code'], s['market'], s['name'], _fcache)
                s['region'] = tg.get('region', '—')
                s['concepts'] = (tg.get('concepts') or [])[:5]
                if not s.get('industry'):
                    s['industry'] = tg.get('industry', '')
            except Exception:
                s['region'], s['concepts'] = '—', []
        save_json(STOCK_CACHE, _fcache)
    print('  今日跌停 %d 只（已标注题材）' % len(dt_today))

    # ---- 节点判定 ----
    print('\n[6/8] 节点判定...')
    # 涨停原因索引：必须在节点票标注**之前**整批预热（同花顺口径，替代原东财新闻搜索）
    build_reason_index(days[-12:])
    raw_nodes = detect_nodes(zt_hist, [d for d in days if d in zt_hist])
    print('  检出节点 %d 个' % len(raw_nodes))
    for n in raw_nodes:
        print('   · [%s] %s  %s' % (n['type'], n['date'], n['desc']))

    # 节点票标注 + 跟踪状态
    cache = load_json(STOCK_CACHE, {})
    today_pool = zt_hist.get(today, [])
    today_map = {s['code']: s for s in today_pool}

    old_nodes = load_json(NODES_JSON, {}).get('nodes', [])
    old_index = {n['id']: n for n in old_nodes}

    nodes_out = []
    tagged = 0
    cap_filtered = 0
    # 只为最近 5 个交易日的节点请求标签接口（老节点票多已断板，避免触发东财限流）
    recent_cut = days[-6] if len(days) >= 6 else days[0]
    for n in raw_nodes:
        trg = n.get('trigger') or {}
        is_recent = n['date'] >= recent_cut
        stocks = n['stocks']
        # 节点票保留全部首板（不截断）：最高标诞生节点里封板晚的票可能才是
        # 后续走出来的真龙（如龙版传媒 10:42 封板），截断会漏掉关键票。
        picked = stocks if is_recent else stocks[:40]
        out_stocks = []
        for s in picked:
            prev = None
            for on in old_nodes:
                if on['id'] == n['id']:
                    for os_ in on.get('stocks', []):
                        if os_['code'] == s['code']:
                            prev = os_
                            break
            if prev and prev.get('concepts'):
                tags = {'region': prev.get('region', '—'),
                        'concepts': prev.get('concepts', []),
                        'industry': prev.get('industry', ''),
                        'pinyin': prev.get('pinyin') or pinyin_abbr(s['name'])}
            # 标签一律只读本地缓存（不发网络请求，避免耗尽东财配额）
            # 网络抓取统一交给 enrich_tags.py 慢速补齐
            ck = '%s.%s' % (s['market'], s['code'])
            _c = cache.get(ck) or {}
            _concepts = list(_c.get('concepts', []))
            # 当下热度排序：把「当下正在炒」的概念排到最前（不消耗网络请求）
            if _concepts and BOARD_HEAT:
                _concepts = rank_concepts(_concepts)
            tags = {'region': _c.get('region', '—'), 'concepts': _concepts,
                    'industry': _c.get('industry', ''), 'pinyin': pinyin_abbr(s['name'])}

            cur = today_map.get(s['code'])
            # 涨停连续性跟踪：区分「连板中 / 首板 / 断板反包 / 已断板」
            status, boards = track_status(
                s['code'], n['date'], days, zt_hist,
                cur['lbc'] if cur else 0,
                (prev or {}).get('boards', s['lbc']))            # 成交额/市值：连板中的票用今日最新值（用于竞价量/封单计算），
            # 一字板形态仍按节点诞生当日（s）判定。
            amount = cur['amount'] if cur else s['amount']
            total_cap = cur['total_cap'] if cur else s['total_cap']

            # 节点票池市值过滤：只保留 200 亿以下（数据缺失时不误杀）
            if total_cap and total_cap > NODE_CAP_LIMIT:
                cap_filtered += 1
                continue

            out_stocks.append({
                'code': s['code'], 'market': s['market'], 'name': s['name'],
                'region': tags['region'], 'pinyin': tags['pinyin'],
                'concepts': tags['concepts'], 'industry': tags['industry'],
                'status': status, 'boards': boards,
                'amount': amount, 'total_cap': total_cap,
                'total_cap_yi': round(total_cap / 1e8, 2) if total_cap else None,
                'is_yizi': s.get('is_yizi', False),
                'folded': status == '已断板',   # 已断板默认折叠
            })
            if tags.get('concepts') or tags.get('region') != '—':
                tagged += 1

        # 触发票标签同样只读缓存
        trg_tags = {'region': '—', 'concepts': [], 'industry': trg.get('industry', ''),
                    'pinyin': pinyin_abbr(trg.get('name', ''))}
        if trg.get('code'):
            ck = '%s.%s' % (trg.get('market', 1), trg['code'])
            _c = cache.get(ck) or {}
            if _c.get('concepts') or _c.get('region'):
                trg_tags = {'region': _c.get('region', '—'), 'concepts': _c.get('concepts', []),
                            'industry': _c.get('industry', trg.get('industry', '')),
                            'pinyin': pinyin_abbr(trg.get('name', ''))}

        nodes_out.append({
            'id': n['id'], 'type': n['type'], 'date': n['date'], 'desc': n['desc'],
            'pool_type': n['pool_type'], 'volume_ratio': n['volume_ratio'],
            'replaced': n.get('replaced', []),
            'trigger': {
                'code': trg.get('code', ''), 'name': trg.get('name', ''),
                'boards': trg.get('lbc', 0),
                'region': trg_tags['region'], 'pinyin': trg_tags['pinyin'],
                'concepts': trg_tags['concepts'],
                'industry': trg_tags.get('industry', trg.get('industry', '')),
            },
            'stocks': out_stocks,
        })

    # ---- 节点聚焦：只看当前最高标的血统链 ----
    # 当前最高标 = nodes 中最近节点(按 date 降序)的 trigger（龙版 9/8 断板节点
    # trigger=龙版），不再取「今日涨停池最高连板票」（龙版今日断板不在池中，
    # 会误判为爱仕达等新最高板，并把旧国芳节点错误聚焦）。
    apply_focus(nodes_out)

    # ---- 涨停原因（事件 / 消息驱动）标注 ----
    # 同一只票每一波可能走不同概念：有的是题材轮动，有的是重组/中标/业绩等消息刺激。
    # 只对「未折叠」的活跃票抓新闻（已断板的老票省配额），缓存按天复用。
    evt_cache = load_json(EVENT_CACHE, {})
    stamp = datetime.now().strftime('%Y-%m-%d')
    evt_n = 0

    def _merge_events(obj):
        code, nm = obj.get('code', ''), obj.get('name', '')
        if not code:
            return False
        ent = evt_cache.get(code) or {}
        ev, bd = ent.get('events', []), ent.get('boards', [])   # 已在上方并发预热
        if not (ev or bd):
            return False
        base = list(obj.get('concepts') or [])
        # 只把「真原因」事件（重组/中标/业绩…）放进概念；现象类（龙虎榜/异动）不占概念位
        real_ev = [e for e in ev if e in EVENT_REAL][:1]
        merged = bd[:2] + real_ev + [c for c in base if c not in real_ev and c not in bd]
        obj['concepts'] = merged[:6]
        obj['events'] = ev
        obj['hot_boards'] = bd
        return True

    # 并发预热「未缓存」的涨停原因（网络耗时为主，4 线程）→ 再顺序合并，避免竞态
    def _need_evt(o):
        c = o.get('code', '')
        return bool(c) and (evt_cache.get(c) or {}).get('ts') != stamp

    pending, seen = [], set()
    for n in nodes_out:
        for s in n.get('stocks', []):
            if s.get('folded'):
                continue
            if _need_evt(s) and s.get('code') not in seen:
                seen.add(s['code']); pending.append((s['code'], s.get('name', ''), s.get('concepts')))
        tg = n.get('trigger') or {}
        if _need_evt(tg) and tg.get('code') not in seen:
            seen.add(tg['code']); pending.append((tg['code'], tg.get('name', ''), tg.get('concepts')))
    if pending:
        print('  并发抓取涨停原因 %d 只...' % len(pending))

        def _grab(it):
            code, nm, cc = it
            ev, bd = fetch_stock_events(code, nm, own_concepts=cc)
            return code, ev, bd

        with ThreadPoolExecutor(max_workers=4) as ex:
            for code, ev, bd in ex.map(_grab, pending):
                evt_cache[code] = {'events': ev, 'boards': bd, 'ts': stamp}

    for n in nodes_out:
        for s in n.get('stocks', []):
            if s.get('folded'):
                continue
            if _merge_events(s):
                evt_n += 1
        if _merge_events(n.get('trigger') or {}):
            evt_n += 1
    save_json(EVENT_CACHE, evt_cache)
    print('  涨停原因(事件/消息)标注 %d 只' % evt_n)

    # 只保留最近两周（10 个交易日）的节点：更早的节点票已全部断板，
    # 留在池里只会干扰「当前最高标血统链」判定，且拖慢渲染。
    keep_cut = days[-NODE_KEEP_DAYS] if len(days) >= NODE_KEEP_DAYS else days[0]
    before = len(nodes_out)
    nodes_out = [n for n in nodes_out if n['date'] >= keep_cut]
    dropped = before - len(nodes_out)

    # 再清掉「已经没有连板」的失效节点：该节点的票全部断板、无一首板晋级，
    # 说明这一波没走出来，继续挂着只会污染梯队视图。
    # 例外：当日新节点的票还都是首板（没走完），无条件保留。
    def _node_alive(n):
        if n['date'] >= today:
            return True
        return any(s.get('status') in ('连板中', '断板反包')
                   for s in (n.get('stocks') or []))

    before2 = len(nodes_out)
    nodes_out = [n for n in nodes_out if _node_alive(n)]
    dropped2 = before2 - len(nodes_out)

    nodes_out.sort(key=lambda x: x['date'], reverse=True)
    save_json(NODES_JSON, {'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           'nodes': nodes_out})
    save_json(STOCK_CACHE, cache)
    print('  节点票标注 %d 只' % tagged)
    if dropped:
        print('  已清理 %d 个过期节点（仅保留 %s 起、近 %d 个交易日）'
              % (dropped, keep_cut, NODE_KEEP_DAYS))
    if dropped2:
        print('  已清理 %d 个失效节点（节点内已无连板票，本波未走出来）' % dropped2)

    # ---- 推荐：优先节点票 + 当日 trigger 票（连板中的最高标） ----
    # [8/8] 候选池 / 次日推荐：已移交「盘前竞价任务(premarket.py, 每交易日 9:25 后)」刷新。
    # 盘后只沿用上一版结果，避免盘后重算把当日实战口径覆盖掉。
    print('\n[8/8] 候选池 / 次日推荐：已移交盘前竞价任务刷新，此处沿用上一版')
    _prev = load_json(DATA_JSON, {}) or {}
    cands = _prev.get('candidates') or []
    reco = _prev.get('recommend')
    # ⚠️ 派生字段自愈：candidates / recommend 是**从盘前版本原样沿用**的，其中的 `pinyin`
    #    是「名字的纯函数」→ 每次沿用都按当前名字重算一遍。
    #    2026-09-22 事故：盘前生成它们时 pypinyin 恰好缺失 → 这 13 条缩写永远是空串，
    #    即使后来装了 pypinyin、盘后重跑 fetch_data.py 也不会被修（因为它们是"沿用"的）。
    for _o in (cands or []) + ([reco] if reco else []):
        if isinstance(_o, dict) and _o.get('name'):
            _o['pinyin'] = pinyin_abbr(_o['name'])
    print('  沿用上一版：候选 %d 只%s'
          % (len(cands), ('，首选 ' + reco['name']) if reco else ''))

    # ⛔ 已删（2026-09-20）：厄尔尼诺整块功能（后端 + 前端）已按用户要求删除。**不要再加回来**。

    # ---- 板块K线可用性：同花顺口径下以 ths_code 为准（em_code 仅用于前端 bklink 桥接） ----
    def _k_ok(_b):
        return bool(_b.get('ths_code') or _b.get('em_code'))
    print('\n[板块] 板块K线可用：行业 %d/%d · 概念 %d/%d · 3日 %d/%d'
          % (sum(1 for _b in board[:10] if _k_ok(_b)), len(board[:10]),
             sum(1 for _b in concept[:15] if _k_ok(_b)), len(concept[:15]),
             sum(1 for _b in board_3d if _k_ok(_b)), len(board_3d)))

    # ---- 高标跟踪（近半月 ≥4 连板 → 按当前最贴合题材归类 → 跟到跌停为止）----
    # 依赖 zt_hist / dt_hist（**运行期内存数据，不落盘**）与标签缓存，故必须在本模块内产出。
    def _theme_of(code, market, fb, ind):
        """当前最贴合题材。

        三步取值（顺序即优先级）：
          ① **涨停原因**（`fb`，同花顺标注的「今天为什么涨停」）中能命中在榜板块的 → 用**命中的板块名**；
          ② 退而用 F10 题材（标签缓存）中能命中在榜板块的 → 同样用板块名；
          ③ 都没命中 → 退回原始词（涨停原因 → F10 → 行业），**不塞进「其他」**。

        ⚠️ 为什么「涨停原因」优先于 F10 题材：F10 是**宽泛标签**（一只票挂 5~6 个），
        按"当日涨幅最高"选会严重跑偏 —— 实测 **爱仕达** 被归到「土地流转」(当日 2.84%)，
        而它当天真正的涨停原因是「**人形机器人**」；闽东电力被归到「国企改革」而非「风电」。
        ⚠️ 为什么最终用「**板块名**」做归类键：只有归一化，同题材的票才会落到同一组
        （锡华科技的「风电齿轮箱」与闽东电力的「风电」都会归到「风电」）。
        """
        cpts = [x for x in ((cache.get('%s.%s' % (market, code)) or {}).get('concepts') or []) if x]
        fb = [x for x in (fb or []) if x]

        def _weak(x):
            return any(k in x for k in WEAK_THEME_KW)

        # ① 涨停原因：按**位置**取第一个命中的 —— 同花顺「为什么涨停」的顺序是可靠的
        for x in fb:
            if _weak(x):
                continue
            bn, _ = match_board(x)
            if bn:
                return bn
        # ② F10 题材：位置**不可靠**（一票挂 5~6 个、顺序含泛词），故改按**当日涨幅**取最热的命中板块
        best, best_pct = None, None
        for x in cpts:
            if _weak(x):
                continue
            bn, pct = match_board(x)
            if bn and (best_pct is None or pct > best_pct):
                best, best_pct = bn, pct
        if best:
            return best
        # ③ 只剩「次新 / 国企改革 / 破净」这类属性词 → 降级使用（聊胜于无）
        for x in fb + cpts:
            bn, _ = match_board(x)
            if bn:
                return bn
        # ④ 一个在榜板块都没命中 → 退回原始词，**不要塞进「其他」**
        return (fb or [None])[0] or (cpts or [None])[0] or ind or '其他'

    bigboards = BB.build_bigboards(zt_hist, days, dt_hist, theme_of=_theme_of)

    data = {
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'date': today,
        'index': index,
        'news': news,
        'board_daily': board[:10],
        'board_concept': concept[:15],
        'sector_daily': sector,          # 「产业板块」当日榜（用户指定清单，按涨幅降序）
        'sector_3d': sector_3d,          # 「产业板块」3 日榜
        'board_3d': board_3d,
        'board_3d_note': board_3d_note,
        'board_kline': board_kline,
        'board_3d_days': board_3d_days,
        'ladder': series,
        'zt_pool': today_pool,
        'dt_pool': dt_today,        # 跌停板梯队：今日明细（含题材）
        'dt_ladder': dt_series,     # 跌停板梯队：近 7 日家数/最高连跌趋势
        'bigboards': bigboards,     # 高标跟踪：近半月 ≥4 连板，按当前题材归类，跟到跌停为止
        'nodes': nodes_out[:12],
        'board_perf': build_board_perf(today_pool, days),  # 连板晋级 + 昨日涨停表现（同花顺自算）
        'market_volume': {                           # 沪深京量能（近15个已收盘交易日）
            'list': mv,
            'date': mv[-1]['date'] if mv else None,
            'total_yi': mv[-1]['amount_yi'] if mv else None,
        },
        'recommend': reco,
        'candidates': cands,
        # ---- 盘前竞价任务的标记：15:05 重建 data.json 时必须**原样带过来** ----
        # 这些字段由 premarket.py / auction_check.py 在 9:26 写入；本函数是**整字典重建**，
        # 不显式搬运就会丢 → 前端「竞价口径」标注（auction_is_live）会永远拿不到值。
        # （2026-09-20 修：实测 2026-09-18 的 data.json 里这 4 个键全部缺失，属真 bug）
        'premarket_updated': _prev.get('premarket_updated'),
        'auction_updated': _prev.get('auction_updated'),
        'auction_is_live': _prev.get('auction_is_live'),
        'auction_source': _prev.get('auction_source'),
        'params': {
            'vol_ratio_threshold': VOL_RATIO_THRESHOLD,
            'breakout_lookback': BREAKOUT_LOOKBACK,
        },
    }
    save_json(DATA_JSON, data)
    save_js(os.path.join(BASE, 'data.js'), data)
    print('\n✅ 完成，耗时 %.1fs' % (time.time() - t0))
    print('   输出：data.json / data.js / nodes.json / board_history.json')
    # 股票缩写自检：正常 >90% 非空；几乎全空 = pypinyin 没装（见开头告警）
    _ok, _tot = pinyin_coverage(data)
    _tail = '' if (_tot == 0 or _ok / _tot > 0.9) else \
            '   ⚠️ 覆盖率异常低 → 检查 pypinyin 是否安装，装后重跑本脚本'
    print('   股票缩写(pinyin)：%d/%d 非空%s' % (_ok, _tot, _tail))

    # ---- 「必看」页大盘宏观面板（macro.py）----
    # 与主流程一体：盘后跑完主体数据后，顺手刷新 macro 键；--no-macro 可跳过。
    if not args.no_macro:
        print('\n[7/8] 刷新「必看」页大盘宏观面板（macro.py）...')
        try:
            import macro as _macro
            _macro.run(quiet=True)
        except Exception as _e:
            print('  ⚠️ macro.py 失败（不影响主体数据）：%s' % _e)


if __name__ == '__main__':
    main()
