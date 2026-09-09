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

import sys, os, json, time, re, argparse, copy
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')
import urllib.request

try:
    from pypinyin import lazy_pinyin
    def pinyin_abbr(name):
        return ''.join([w[0].upper() for w in lazy_pinyin(name) if w and w[0].isalpha()])
except ImportError:
    def pinyin_abbr(name):
        return ''

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_JSON      = os.path.join(BASE, 'data.json')
NODES_JSON     = os.path.join(BASE, 'nodes.json')
BOARD_HIST     = os.path.join(BASE, 'board_history.json')
STOCK_CACHE    = os.path.join(BASE, '.stock_cache.json')
SEAL_CACHE     = os.path.join(BASE, '.seal_cache.json')
EVENT_CACHE    = os.path.join(BASE, '.event_cache.json')

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
# 在 fetch_zt_pool() 源头过滤，下游（节点/候选/厄尔尼诺/模块4）自动生效。
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

# ============ 厄尔尼诺事件轮动（静态主题模块）============
# 数据来源：NOAA 2026-08 + 用户提供的轮动逻辑图 + 2018 历史回放
# 当前阶段 current_stage 由 calc_elnino_stage() 依据当日连板梯队自动判定
# （规则：某条线龙头股出现 ≥4 连板 → 该线激活；取连板最高的线高亮）。
# 若当日无 ≥4 连板龙头，则回退到 default_stage 手动兜底值。
EL_NINO_DATA = {
    'updated': '2026-09',
    'intensity': '1950 年以来最强（NOAA 2026-08）',
    'probability': '秋季发生概率 > 50% / 69%',
    'default_stage': 1,            # 无 ≥4 连板激活时的兜底阶段（首阶段·糖橡胶）
    'current_stage': 1,            # 运行时被 calc_elnino_stage() 覆写
    'current_stage_label': '第①阶段·热带软商品（糖/橡胶）',
    'auto_rule': '某条线龙头股出现 ≥4 连板 → 自动高亮该线（连板越高优先级越高）',
    'stages': [
        {
            'idx': 1,
            'title': '第一条线·热带软商品（防主线）',
            'highlight': False,
            'desc': '产区集中于赤道带（印度、泰国、印尼、马来、巴西），厄尔尼诺→干旱/暴风雨→供给受冲击；弹性最大、最先发动。',
            'products': ['白糖', '天然橡胶', '棕榈油', '棉花', '咖啡', '可可', '种业'],
            'sectors': [
                {'name': '制糖', 'leaders': [
                    {'code': '000911', 'name': '南宁糖业', 'pinyin': 'NNTY',
                     'reason': '广西国资委控股 · 国内糖业老牌龙头'},
                    {'code': '600737', 'name': '中粮糖业', 'pinyin': 'ZLTY',
                     'reason': '中粮系 · 国内最大原糖进口商'},
                    {'code': '000833', 'name': '粤桂股份', 'pinyin': 'YGGF',
                     'reason': '广东国资 · 糖业+硫铁矿双主业'},
                    {'code': '600251', 'name': '冠农股份', 'pinyin': 'GNGF',
                     'reason': '新疆建设兵团糖业'}
                ]},
                {'name': '橡胶种植加工', 'leaders': [
                    {'code': '601118', 'name': '海南橡胶', 'pinyin': 'HNXJ',
                     'reason': '海垦集团 · 国内最大橡胶种植企业'}
                ]},
                {'name': '油脂油料', 'leaders': [
                    {'code': '300999', 'name': '金龙鱼', 'pinyin': 'JLY',
                     'reason': '益海嘉里 · 国内食用油龙头 / 棕榈油'},
                    {'code': '002637', 'name': '赞宇科技', 'pinyin': 'ZYKJ',
                     'reason': '棕榈油加工 + 表面活性剂龙头'},
                    {'code': '000505', 'name': '京粮控股', 'pinyin': 'JLK',
                     'reason': '京粮集团 · 油脂加工'}
                ]},
                {'name': '棉花·种植业', 'leaders': [
                    {'code': '601339', 'name': '百隆东方', 'pinyin': 'BLDF',
                     'reason': '国内大型棉纺 · 棉花涨价直接受益'},
                    {'code': '600598', 'name': '北大荒', 'pinyin': 'BDH',
                     'reason': '黑龙江农垦 · 种植业龙头（防御属性）'},
                    {'code': '601952', 'name': '苏垦农发', 'pinyin': 'SKNF',
                     'reason': '江苏农垦 · 大宗农产品种植'}
                ]},
                {'name': '种业（优先级提升 · 半月持续）', 'leaders': [
                    {'code': '002041', 'name': '登海种业', 'pinyin': 'DHZY',
                     'reason': '玉米种子龙头 · 粮食安全+厄尔尼诺减产双逻辑'},
                    {'code': '000998', 'name': '隆平高科', 'pinyin': 'LPGK',
                     'reason': '水稻/玉米种业龙头'},
                    {'code': '600313', 'name': '农发种业', 'pinyin': 'NFZY',
                     'reason': '农发集团 · 国资种业平台'}
                ]},
                {'name': '粮食·米业（本波先锋）', 'leaders': [
                    {'code': '600127', 'name': '金健米业', 'pinyin': 'JJMY',
                     'reason': '湖南粮食集团控股 · 国内米业龙头 · 本波粮食线先锋（4连板开启）'}
                ]}
            ]
        },
        {
            'idx': 2,
            'title': '第二条线·高温线（火电与煤炭）',
            'highlight': False,
            'desc': '极端高温→居民+工业用电需求拉升→火电利用小时数走高→2014/2016 复刻链（电力板块独立行情）。',
            'products': ['电力（火电）', '动力煤'],
            'sectors': [
                {'name': '火电', 'leaders': [
                    {'code': '600011', 'name': '华能国际', 'pinyin': 'HNGJ',
                     'reason': '国内最大火电运营商'},
                    {'code': '600027', 'name': '华电国际', 'pinyin': 'HDGJ',
                     'reason': '华电集团旗下火电平台'},
                    {'code': '601991', 'name': '大唐发电', 'pinyin': 'DTFD',
                     'reason': '大唐集团旗下火电平台'},
                    {'code': '600795', 'name': '国电电力', 'pinyin': 'GDDL',
                     'reason': '国电集团核心上市平台'}
                ]},
                {'name': '动力煤', 'leaders': [
                    {'code': '601088', 'name': '中国神华', 'pinyin': 'ZGSH',
                     'reason': '煤电一体化龙头 · 高温煤炭首选'},
                    {'code': '601225', 'name': '陕西煤业', 'pinyin': 'SXMY',
                     'reason': '陕煤集团 · 优质动力煤'},
                    {'code': '600188', 'name': '兖矿能源', 'pinyin': 'YKNY',
                     'reason': '山东能源旗下 · 动力煤主力'}
                ]}
            ]
        },
        {
            'idx': 3,
            'title': '第三条线·航运物流（优先级提升 · 半月持续）',
            'highlight': False,
            'desc': '厄尔尼诺→全球降水带偏移 / 南美主要河道水位异常→航运周期与物流扰动，事件驱动型、防御转强。',
            'products': ['航运', '物流'],
            'sectors': [
                {'name': '航运', 'leaders': [
                    {'code': '601919', 'name': '中远海控', 'pinyin': 'ZYHK',
                     'reason': '全球集运龙头 · 雨季影响海运周期'},
                    {'code': '601872', 'name': '招商轮船', 'pinyin': 'ZSSL',
                     'reason': '招商局旗下 · 干散/油运综合船队'}
                ]}
            ]
        },
        {
            'idx': 4,
            'title': '第四条线·有色金属（事件扰动）',
            'highlight': False,
            'desc': '铜/镍/锡/锌 — 极端气候影响矿区运营+物流风险，事件驱动型脉冲，逻辑弱于①②③。',
            'products': ['铜', '镍', '锡', '锌'],
            'sectors': [
                {'name': '工业金属·铜', 'leaders': [
                    {'code': '601899', 'name': '紫金矿业', 'pinyin': 'ZJKY',
                     'reason': '全球铜矿龙头'},
                    {'code': '600362', 'name': '江西铜业', 'pinyin': 'JXTY',
                     'reason': '国内最大铜冶炼'}
                ]},
                {'name': '能源金属·镍', 'leaders': [
                    {'code': '603799', 'name': '华友钴业', 'pinyin': 'HYGY',
                     'reason': '镍/钴一体化龙头'},
                    {'code': '300919', 'name': '中伟股份', 'pinyin': 'ZWGF',
                     'reason': '三元前驱体全球龙头'}
                ]},
                {'name': '小金属·锡/锌', 'leaders': [
                    {'code': '000960', 'name': '锡业股份', 'pinyin': 'XYGF',
                     'reason': '全球锡/铟双龙头'},
                    {'code': '000060', 'name': '中金岭南', 'pinyin': 'ZJLN',
                     'reason': '铅锌冶炼龙头'}
                ]}
            ]
        }
    ],
    'history_2018': [
        {'period': '2018.04-05', 'theme': '糖业板块（首阶段复刻）',
         'leaders': '南宁糖业、保龄宝', 'note': '糖价上行 + 厄尔尼诺减产预期'},
        {'period': '2018.06-07', 'theme': '种业/农业',
         'leaders': '登海种业、隆平高科', 'note': '干旱减产 → 粮食安全题材扩散'},
        {'period': '2018.07-08', 'theme': '电力/煤炭（高温复刻）',
         'leaders': '中国神华、华能国际、长江电力', 'note': '用电峰值 · 火电利用小时数走高'},
        {'period': '2018.09-10', 'theme': '有色/稀土（事件扰动复刻）',
         'leaders': '北方稀土、广晟有色、紫金矿业', 'note': '供给担忧 + 稀土打黑叠加'}
    ]
}

# 题材关键词映射：把涨停池里的"新龙头"按 industry/name 匹配到对应线。
# 用于轮动发生时自动发现"不在静态名单里 / 新崛起的情绪龙"并收录。
EL_NINO_THEME_KW = {
    1: ['农产品', '种植', '农业', '橡胶', '粮油', '饲料', '糖', '棉', '种业',
        '米业', '粮食', '农垦', '氮肥', '钾肥', '磷肥', '渔业', '白糖'],
    2: ['电力', '火电', '发电', '热电', '供电', '煤炭', '煤', '能源', '煤电'],
    3: ['航运', '海运', '港口', '物流', '运输', '集运', '干散', '油运'],
    4: ['有色', '金属', '铜', '铝', '镍', '锡', '锌', '铅', '黄金', '稀土',
        '矿业', '采掘', '钴', '锂', '小金属'],
}
# 新龙头收录阈值：连板 >= 该值才自动收录（贴合"4连板带队主升"规律；
# 设为 3 可更早捕捉刚冒头的情绪龙，按需下调）
ELNINO_NEW_LEADER_MIN_LBC = 3

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
def http_get(url, timeout=15, retry=3, silent=False, enc='utf-8'):
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
            time.sleep(1.0 + i * 0.8)
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


# ============ 厄尔尼诺当前阶段自动识别 ============
# 规则：当日涨停池(zt_pool)中，某条线对应龙头股出现 ≥4 连板 → 该线"激活"；
#       取连板最高的激活线作为当前高亮阶段。无激活线则回退 default_stage。
# 依据：连板梯队是短线持续性的硬信号，4 连板是题材从脉冲走向主线的分水岭。
def calc_elnino_stage(zt_pool, elnino, threshold=4):
    # code -> 当日最高连板数
    code_lbc = {}
    for s in (zt_pool or []):
        c = s.get('code')
        if c:
            code_lbc[c] = max(code_lbc.get(c, 0), s.get('lbc', 0))
    # 每条线最高连板 + 触发股（连板最高的龙头）
    best = {}       # idx -> 最高连板
    trigger = {}    # idx -> {'code','name','lbc'} 触发该线高亮的龙头股
    for stg in elnino['stages']:
        mx = 0
        trig = None
        for sc in stg.get('sectors', []):
            for l in sc.get('leaders', []):
                v = code_lbc.get(l['code'], 0)
                if v > mx:
                    mx = v
                    trig = {'code': l['code'], 'name': l['name'], 'lbc': v}
        best[stg['idx']] = mx
        trigger[stg['idx']] = trig
    # 激活线：连板 >= 阈值
    active = [i for i, v in best.items() if v >= threshold]
    if not active:
        return None, 0, best, trigger   # 无激活，回退默认
    # 取连板最高的线；并列时取 idx 最小（主线优先）
    top = max(active, key=lambda i: (best[i], -i))
    return top, best[top], best, trigger


# ============ 厄尔尼诺轮动切换检测（跨交易日缓存）============
# 思路：每次跑全量把"当前激活线"写入缓存；下一交易日若激活线变化，
# 即判定为"换细分/换方向"，前端第一时间高亮 🔄 轮动切换。
ELNINO_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '.elnino_stage_cache.json')


def load_elnino_cache():
    try:
        with open(ELNINO_CACHE, encoding='utf-8') as f:
            return json.load(f).get('stage')
    except Exception:
        return None


def save_elnino_cache(stage):
    try:
        with open(ELNINO_CACHE, 'w', encoding='utf-8') as f:
            json.dump({'stage': stage,
                       'updated': datetime.now().strftime('%Y-%m-%d')},
                      f, ensure_ascii=False)
    except Exception:
        pass


# ============ 厄尔尼诺新龙头自动发现 + 收录 ============
# 轮动发生时，静态名单之外可能冒出新龙头 / 新情绪龙。
# 这里扫描当日涨停池：连板 >= 阈值 且 题材关键词命中某条线 且 不在已收录名单中
# → 自动写入 .elnino_dynamic_leaders.json，并合并进该线展示，做到"第一时间收录"。
ELNINO_DYN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '.elnino_dynamic_leaders.json')


def _match_elnino_line(stock, kwmap):
    hay = ((stock.get('industry') or '') + ' ' + (stock.get('name') or ''))
    for idx, kws in kwmap.items():
        for kw in kws:
            if kw in hay:
                return idx
    return None


def discover_elnino_leaders(zt_pool, elnino, kwmap, dyn_path, add_threshold):
    # 已收录代码（静态名单 + 已动态收录），避免重复
    curated = {stg['idx']: set() for stg in elnino['stages']}
    for stg in elnino['stages']:
        for sc in stg.get('sectors', []):
            for l in sc.get('leaders', []):
                curated[stg['idx']].add(l['code'])
    dyn = {}
    try:
        with open(dyn_path, encoding='utf-8') as f:
            dyn = json.load(f) or {}
    except Exception:
        dyn = {}
    for k, arr in dyn.items():
        curated.setdefault(int(k), set()).update(e.get('code') for e in arr)
    today = datetime.now().strftime('%Y-%m-%d')
    new_added = []
    for s in (zt_pool or []):
        lbc = s.get('lbc', 0)
        if lbc < add_threshold:
            continue
        idx = _match_elnino_line(s, kwmap)
        if not idx:
            continue
        code = s.get('code')
        if not code or code in curated.get(idx, set()):
            continue
        entry = {
            'code': code,
            'name': s.get('name', ''),
            'pinyin': '',
            'reason': '轮动自动收录·%s·%d连板·新晋龙头/情绪龙' % (today, lbc),
            'first_seen': today,
            'boards': lbc,
        }
        dyn.setdefault(str(idx), []).append(entry)
        curated[idx].add(code)
        new_added.append(entry)
    try:
        with open(dyn_path, 'w', encoding='utf-8') as f:
            json.dump(dyn, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return dyn, new_added


def merge_elnino_dynamic(elnino_out, dyn):
    """把动态收录的新龙头合并进各线（追加『新晋龙头』板块）"""
    for stg in elnino_out['stages']:
        arr = dyn.get(str(stg['idx']))
        if arr:
            stg.setdefault('sectors', []).append({
                'name': '新晋龙头（轮动自动收录）',
                'leaders': arr,
                'auto': True,
            })


# ============ 1. 交易日列表 ============
def fetch_trade_days(limit=90):
    """交易日列表：优先腾讯K线，东财为备胎（push2his 常被限流）"""
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
                return days
        except Exception:
            pass
    # 备胎：东财
    url2 = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?'
            'secid=1.000001&fields1=f1,f2,f3&fields2=f51&klt=101&fqt=0&end=20500101&lmt=%d' % limit)
    txt2 = http_get(url2, silent=True)
    if txt2:
        try:
            d2 = json.loads(txt2)
            return [k.split(',')[0] for k in d2['data']['klines']]
        except Exception:
            pass
    print('  [err] 交易日接口全部失败')
    return []


# ============ 市场情绪（全市场涨跌/涨跌停家数）============
def fetch_dt_count():
    """跌停家数：东方财富 push2ex 跌停池（与涨停池同域，稳定）。"""
    d = datetime.now().strftime('%Y%m%d')
    url = ('https://push2ex.eastmoney.com/getTopicDTPool?ut=%s&dpt=wz.ztzt&'
           'Pageindex=0&pagesize=500&sort=fbt%%3Aasc&date=%s' % (UT, d))
    try:
        raw = http_get(url, timeout=15, retry=1, silent=True)
        if raw:
            obj = json.loads(raw)
            pool = (obj.get('data') or {}).get('pool')
            if pool is None:
                pool = obj.get('data') or []
            if isinstance(pool, dict):
                pool = pool.get('pool', [])
            return len(pool)
    except Exception:
        pass
    return None


def build_mood(today_pool):
    """市场情绪：涨停/跌停/连板家数（今日涨停池口径，含 20% 涨停）。
    注：已按用户要求删除全市场涨跌家数（环境对东财 clist 断连，且用户不需要）。"""
    pool = today_pool or []
    return {
        'zt_pool': len(pool),
        'dt': fetch_dt_count(),
        'lbc': len([s for s in pool if s.get('lbc', 0) >= 2]),
        'max_board': max((s.get('lbc', 0) for s in pool), default=0),
        'ok': True,
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


def fetch_ytd_board_pct():
    """拉取「昨日涨停/连板/首板/打二板以上/炸板」板块指数当日涨幅。"""
    ids = ','.join('90.' + c for c in YTD_BOARD_CODES)
    t = em_get('/api/qt/ulist.np/get?fltt=2&secids=%s&fields=f2,f3,f12,f14' % ids)
    out = {}
    if not t:
        return out
    try:
        d = json.loads(t)
    except Exception:
        return out
    for it in (d.get('data') or {}).get('diff') or []:
        nm = YTD_BOARD_CODES.get(it.get('f12'))
        if nm:
            out[nm] = it.get('f3')
    return out


def fetch_ytd_lianban_codes():
    """「昨日连板」板块(BK0816) 成分股代码列表 = 昨日连板股。"""
    t = em_get('/api/qt/clist/get?pn=1&pz=200&po=1&np=1&fltt=2&invt=2&fid=f3'
               '&fs=b:BK0816&fields=f12,f14,f3,f2')
    out = []
    if not t:
        return out
    try:
        d = json.loads(t)
    except Exception:
        return out
    for it in (d.get('data') or {}).get('diff') or []:
        c = it.get('f12')
        if c:
            out.append(c)
    return out


def build_board_perf(today_pool):
    """连板晋级 + 昨日涨停表现。返回 dict 供前端渲染。"""
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
    ytd = fetch_ytd_board_pct()
    km = {'昨日涨停': 'y_zt', '昨日连板': 'y_lb', '昨日首板': 'y_fb',
          '昨日打二板以上': 'y_2plus', '昨日炸板': 'y_broken'}
    for nm, key in km.items():
        bp[key] = ytd.get(nm)
    # 晋级：昨日连板成分股中今日仍在涨停池的
    lb_codes = fetch_ytd_lianban_codes()
    bp['y_lb_total'] = len(lb_codes)
    bp['y_lb_promo'] = len([c for c in lb_codes if c in zt_codes])
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
def fetch_zt_pool(date_yyyymmdd):
    """返回当日涨停股列表（含连板数、成交额、封单、行业）"""
    url = ('https://push2ex.eastmoney.com/getTopicZTPool?ut=%s&dpt=wz.ztzt&'
           'Pageindex=0&pagesize=500&sort=fbt%%3Aasc&date=%s' % (UT, date_yyyymmdd))
    txt = http_get(url, silent=True)
    if not txt:
        return []
    try:
        d = json.loads(txt)
        pool = (d.get('data') or {}).get('pool') or []
    except Exception:
        return []
    out = []
    skipped = 0
    for s in pool:
        try:
            # 全局剔除：北交所 / 科创板 / ST（用户要求所有股票池一律不收）
            if is_excluded(s.get('c', ''), s.get('n', '')):
                skipped += 1
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
                'lbc':    s.get('lbc') or 0,             # 连板数
                'first_seal': s.get('fbt') or 0,         # 首次封板时间
                'last_seal':  s.get('lbt') or 0,         # 最后(回)封板时间
                'seal_fund': s.get('fund') or 0,         # 封单资金
                'zbc':    s.get('zbc') or 0,             # 炸板次数
                'is_yizi': (s.get('fbt') or 999999) <= 93005,  # 一字板：9:30:05 前首次封板
                'industry': s.get('hybk') or '',         # 行业板块
            })
        except Exception:
            continue
    # 总市值修正（用户核对的当前值优先，按日期锁定）
    for s in out:
        k = (s['code'], date_yyyymmdd)
        if k in CAP_OVERRIDE:
            s['total_cap'] = CAP_OVERRIDE[k]
    return out


def _node_type_priority(nt):
    """节点类型优先级（穿越 > 突破 > 断板）：同一只票同时出现在多节点时优先穿越场景"""
    return {'穿越节点': 3, '突破节点': 2, '最高标断板节点': 1}.get(nt, 0)


def fetch_seal_amount(code, market, date_str, fbt, is_yizi=False):
    """上板分时量 = 首次封板时刻的累计成交额（元）。

    注意与「全天成交额(amount)」区分：次日竞价量的基准是上板那一刻的分时量，
    不是全天成交额（炸板/回封会把全天额撑得远大于上板量）。

    口径：
      · 一字板(first_seal<=93005)：封板发生在 9:25 集合竞价，取首根分时累计额
        （含集合竞价成交）= 上板分时量。切勿取 9:35 累计，那会把盘中量算进去。
      · T字板/自然板：取封板时刻(fbt)所在分钟末的累计成交额。

    返回 (seal_amount, day_amount)，失败返回 (None, None)
    """
    key = '%s_%s' % (code, date_str)
    if key in SEAL_OVERRIDE:
        sv = SEAL_OVERRIDE[key]
        return sv[0], sv[1]
    cache = load_json(SEAL_CACHE, {})
    if key in cache:
        c = cache[key]
        return c.get('seal'), c.get('total')

    # 主源：东财分时 trends2（f57=逐分钟增量额，累计需自加）
    secid = _secid(code, market)
    url = ('https://push2his.eastmoney.com/api/qt/stock/trends2/get?'
           'secid=%s&fields1=f1,f2,f3,f4,f5,f6,f7,f8'
           '&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=5' % secid)
    txt = http_get(url, silent=True)
    seal, total = None, None
    if txt:
        try:
            d = json.loads(txt).get('data') or {}
            trends = d.get('trends') or []
        except Exception:
            trends = []
        day = [t for t in trends if t.startswith(date_str)]
        if day:
            hhmm = None
            if fbt:
                try:
                    hhmm = '%02d:%02d' % (int(fbt) // 10000, (int(fbt) % 10000) // 100)
                except Exception:
                    hhmm = None
            hhmm_comp = hhmm.replace(':', '') if hhmm else None   # 东财时刻 '09:35'
            cum = 0.0
            first_cum = None
            seal_at = None
            for t in day:
                p = t.split(',')
                tm = p[0][11:16]
                try:
                    amt = float(p[6])
                except Exception:
                    amt = 0.0
                cum += amt
                if first_cum is None:
                    first_cum = cum                       # 首分钟累计（含集合竞价）
                if seal_at is None and hhmm and tm >= hhmm:
                    seal_at = cum                          # 首次封板时刻累计（仅认第一次）
            total = cum
            if is_yizi and first_cum is not None:
                seal = first_cum          # 一字板：集合竞价首分钟累计
            elif seal_at is not None:
                seal = seal_at            # 自然/T板：首次封板时刻累计，炸板回封一律不计

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
                    first_cum = float(rows[0][3]) if len(rows[0]) > 3 else None
                    if is_yizi and first_cum is not None:
                        # 一字板：首根分时累计（含集合竞价）= 上板分时量
                        seal = first_cum
                    else:
                        # 自然/T板：取「首次封板时刻」所在分钟末累计（统一为 HHMM 比较，修原 bug）
                        target_idx = None
                        for i, r in enumerate(rows):
                            if hhmm_comp and r[0] >= hhmm_comp:
                                target_idx = i
                                break
                        if target_idx is None and rows:
                            target_idx = len(rows) - 1
                        seal = float(rows[target_idx][3])
                    total = float(rows[-1][3])
            except Exception:
                pass

    # 仅在取到有效上板量时落缓存；None（接口缺失/限流）不缓存，下次运行可重试
    if seal is not None:
        cache[key] = {'seal': seal, 'total': total}
        save_json(SEAL_CACHE, cache)
    return seal, total


# ============ 3. 板块涨幅榜 ============
# 板块名噪声前缀（非题材类板块）
BOARD_SKIP = ('昨日', '近期', '百日', '东方财富', '融资', '沪股通', '深股通', '转融',
              'MSCI', '标普', '富时', '创业成份', '深证', '上证', '中证', 'AH股')


def fetch_board_rank(top=60):
    """概念板块涨幅榜：腾讯源（东财 push2 常被限流）"""
    # 注意：sort_type 只支持 price（zdf 会返回空），排序在下方本地完成
    # 板块总数约 800+，单次最多 100 条，需分页抓全
    rows = []
    for offset in range(0, 810, 100):
        url = ('https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank?'
               'board_type=gn&sort_type=price&direct=down&offset=%d&count=100' % offset)
        txt = http_get(url, silent=True)
        if not txt:
            break
        try:
            d = json.loads(txt)
            part = (d.get('data') or {}).get('rank_list') or []
        except Exception:
            part = []
        if not part:
            break
        rows.extend(part)
        if len(part) < 100:
            break
        time.sleep(0.22)
    if not rows:
        # 备胎：东财
        url2 = ('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2'
                '&fid=f3&fs=m:90+t:3+f:!50&fields=f3,f12,f14,f104,f105')
        txt2 = http_get(url2, silent=True)
        if txt2:
            try:
                d2 = json.loads(txt2)
                return [{'name': it['f14'], 'code': it['f12'], 'pct': it['f3'],
                         'up': it.get('f104', 0), 'total': it.get('f104', 0) + it.get('f105', 0),
                         'leader': '', 'leader_pct': 0, 'zdf_d5': 0, 'zljlr_wan': 0}
                        for it in d2['data']['diff']][:top]
            except Exception:
                pass
        return []

    out = []
    for it in rows:
        name = it.get('name', '')
        if not name or any(name.startswith(s) for s in BOARD_SKIP):
            continue
        try:
            pct = float(it.get('zdf') or 0)
        except Exception:
            continue
        zgb = str(it.get('zgb') or '0/0')
        up_s, tot_s = (zgb.split('/') + ['0', '0'])[:2]
        lzg = it.get('lzg') or {}
        try:
            lpct = float(lzg.get('zdf') or 0)
        except Exception:
            lpct = 0.0
        try:
            d5 = float(it.get('zdf_d5') or 0)
        except Exception:
            d5 = 0.0
        try:
            zljlr = float(it.get('zljlr') or 0)
        except Exception:
            zljlr = 0.0
        out.append({
            'name': name, 'code': it.get('code', ''), 'pct': pct,
            'up': int(up_s) if up_s.isdigit() else 0,
            'total': int(tot_s) if tot_s.isdigit() else 0,
            'leader': lzg.get('name', ''), 'leader_pct': lpct,
            'zdf_d5': d5, 'zljlr_wan': zljlr,
        })
    out.sort(key=lambda x: -x['pct'])
    # 全量板块热度留档：个股概念按「对应板块当日涨幅」排序，
    # 从而实现「同一个票每一波走不同概念 → 取当下正在炒的那个」。
    try:
        BOARD_HEAT.clear()
        for b in out:
            if b['name']:
                BOARD_HEAT[b['name']] = b['pct']
    except Exception:
        pass
    return out[:top]


# ============ 4. 指数行情 ============
def fetch_index():
    codes = ['sh000001', 'sz399001', 'sz399006', 'sh000688', 'bj899050', 'sh000300']
    url = 'https://qt.gtimg.cn/q=' + ','.join(codes)
    txt = http_get(url, enc='gbk')  # 腾讯行情接口返回 GBK，按 UTF-8 解会产生乱码
    out = []
    if not txt:
        return out
    for seg in txt.split(';'):
        if '~' not in seg:
            continue
        parts = seg.split('~')
        if len(parts) < 45:
            continue
        try:
            out.append({
                'name': parts[1],
                'code': parts[2],
                'price': float(parts[3]),
                'chg_pct': float(parts[32]),
                'amount_yi': round(float(parts[37]) / 10000.0, 2) if parts[37] else 0,
            })
        except Exception:
            continue
    return out


# ============ 5. 新闻 ============
def fetch_news(days=1):
    """抓取每日必看新闻。
    days=1 只取当日（盘后完整复盘口径）；
    days>1 取最近 N 天（早盘口径，用于覆盖周末与隔夜外围行情）。
    """
    url = ('https://np-listapi.eastmoney.com/comm/web/getFastNewsList?client=web&biz=web_724'
           '&fastColumn=102&sortEnd=&pageSize=%d&req_trace=1' % NEWS_PAGE_SIZE)
    txt = http_get(url)
    if not txt:
        return {'macro': [], 'good': [], 'bad': [], 'overseas': []}
    try:
        d = json.loads(txt)
        items = (d.get('data') or {}).get('fastNewsList') or []
    except Exception:
        return {'macro': [], 'good': [], 'bad': [], 'overseas': []}

    today = datetime.now().strftime('%Y-%m-%d')
    # 允许的最早日期：days=1 时只看今天；days>1 时往前覆盖（周末/隔夜）
    earliest = (datetime.now() - timedelta(days=days - 1)).strftime('%Y-%m-%d')
    multi = days > 1
    res = {'macro': [], 'good': [], 'bad': [], 'overseas': []}
    for it in items:
        show_time = it.get('showTime', '')
        if not show_time or show_time[:10] < earliest or show_time[:10] > today:
            continue
        title = (it.get('title') or '').strip()
        summary = (it.get('summary') or '').strip()
        text = summary if summary else title
        # 一句话极简：截到第一个句号
        m = re.split(r'[。；;]', text)
        brief = m[0].strip() if m and m[0].strip() else title
        brief = re.sub(r'^【[^】]*】', '', brief).strip()
        brief = brief[:90]
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
    """从个股近期新闻标题中提取「涨停原因」。

    返回 (events, hot_boards)：
      events     —— 事件标签，如 重组 / 中标 / 业绩预增 / 政策利好 …
      hot_boards —— 新闻里点名的板块，如「CPO板块短线走高」→ ['CPO']
    这两项都是「当下驱动」，优先级高于静态所属概念。

    own_concepts：该股已有的概念列表。新闻里点名的板块必须与之相关才采纳，
    否则市场综述（如"农业、化肥板块全线爆发"）会把不相干的板块误配给个股。
    """
    if not name:
        return [], []
    from urllib.parse import quote
    import datetime as _dt
    param = {
        "uid": "", "keyword": name,
        "type": ["cmsArticleWebOld"], "client": "web", "clientType": "web",
        "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "time",
                                       "pageIndex": 1, "pageSize": 20}},
    }
    url = ('https://search-api-web.eastmoney.com/search/jsonp?cb=&param=%s'
           % quote(json.dumps(param, ensure_ascii=False), safe=''))
    txt = http_get(url, silent=True)
    if not txt:
        return [], []
    try:
        t = txt.strip()
        if t.startswith('('):
            t = t[1:-1]
        d = json.loads(t)
        arr = (d.get('result') or {}).get('cmsArticleWebOld') or []
    except Exception:
        return [], []

    cutoff = (_dt.date.today() - _dt.timedelta(days=days)).strftime('%Y-%m-%d')
    own = set(own_concepts or [])
    events, boards = [], []
    for it in arr[:20]:
        title = re.sub(r'<[^>]+>', '', it.get('title') or '')
        dt = (it.get('date') or '')[:10]
        if not title:
            continue
        if dt and dt < cutoff:
            continue          # 只看最近 N 天，避免拿旧闻当当下原因
        if any(k in title for k in NEWS_SKIP):
            continue          # 市场综述类：提到个股只是碰巧，不作数
        if name not in title:
            continue          # 标题必须点名该股
        for ev, kws in EVENT_KW.items():
            if ev in events:
                continue
            if any(k in title for k in kws):
                events.append(ev)
        for rgx in (NEWS_BOARD_RE, NEWS_BOARD_RE2):
            for m in rgx.finditer(title):
                b = m.group(1).strip()
                if not (2 <= len(b) <= 8) or b == name or b in boards:
                    continue
                if own and not _related(b, own):
                    continue      # 与个股已有概念无关的板块，判定为综述误配
                boards.append(b)
    # 真原因在前，现象在后
    events.sort(key=lambda e: 0 if e in EVENT_REAL else 1)
    return events, boards


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


def concept_heat(cname):
    """概念对应板块的当日涨幅（用于「当下热度」排序），找不到返回 None"""
    if not BOARD_HEAT:
        return None
    if cname in BOARD_HEAT:
        return BOARD_HEAT[cname]
    # 模糊：板块名 与 概念名 互相包含（如 CPO ↔ CPO概念，光通信模块 ↔ 光模块）
    base = re.sub(r'(板块|概念股?)$', '', cname)
    for bn, pct in BOARD_HEAT.items():
        b2 = re.sub(r'(板块|概念股?)$', '', bn)
        if not base or not b2:
            continue
        if base == b2 or (len(base) >= 2 and base in b2) or (len(b2) >= 2 and b2 in base):
            return pct
    return None


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
    """返回 {region, concepts, industry, pinyin}"""
    key = '%s.%s' % (market_of(code), code)
    cached = cache.get(key) or {}
    if cached.get('concepts') and cached.get('_v') == TAGS_VER:
        c = dict(cached)
        c['pinyin'] = pinyin_abbr(name)
        return c

    sid = _secid(code, market)
    # 所属板块（概念 + 行业 + 地域板块），全量抓取不截断
    raw, region = [], ''
    url = ('https://push2.eastmoney.com/api/qt/slist/get?spt=3&fltt=2&invt=2&secid=%s'
           '&fields=f12,f13,f14,f3&pn=1&np=1&pz=60' % sid)
    txt = http_get(url, silent=True)
    if txt:
        try:
            d = json.loads(txt)
            for it in (d.get('data') or {}).get('diff') or []:
                bn = (it.get('f14') or '').strip()
                if not bn:
                    continue
                base = bn.replace('板块', '').strip()
                if base in PROVINCES or (bn.endswith('板块') and base in PROVINCES):
                    if not region:
                        region = base
                    continue        # 地域板块只用于填 region，不进 concepts
                raw.append(bn)
        except Exception:
            pass

    # F10 兜底取地域
    if not region:
        sufix = '%s.SH' % code if market_of(code) == 1 else '%s.SZ' % code
        url2 = ('https://datacenter.eastmoney.com/securities/api/data/v1/get?'
                'reportName=RPT_F10_BASIC_ORGINFO&columns=ALL&filter=(SECUCODE%%3D%%22%s%%22)'
                '&pageNumber=1&pageSize=1&source=HSF10&client=PC' % sufix)
        txt2 = http_get(url2, silent=True)
        if txt2:
            try:
                d2 = json.loads(txt2)
                rows = (d2.get('result') or {}).get('data') or []
                if rows:
                    region = (rows[0].get('PROVINCE') or '').strip()
            except Exception:
                pass

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

    industry = industries[0] if industries else ''

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
    return '已断板', fallback_boards


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


def ferment_count(concepts, zt_pool):
    """板块发酵度：候选股题材概念 与 当日涨停池细分行业 模糊匹配命中的涨停家数/连板数。
    说明：nodes 里 industry 是一级行业(传媒)，zt_pool 里 industry 是细分行业(出版)，
    粒度不一致，故用 concepts(含出版/传媒等多级) 去模糊匹配 zt_pool 的细分行业更稳。"""
    zt, lb = 0, 0
    for s in zt_pool or []:
        ind = s.get('industry') or ''
        if not ind:
            continue
        for c in concepts or []:
            if len(c) >= 2 and (c in ind or ind in c):
                zt += 1
                if s.get('lbc', 0) >= 2:
                    lb += 1
                break
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
    数据源 = 各节点 stocks(连板中的首板) + 各节点 trigger(连板中的最高标)。
    trigger 单独入池是为了避免它被旧节点的 stock dedup 覆盖掉（如龙版 6 板
    穿越节点是当日 trigger，但最早它出现在 8/31 断板节点的 stocks 里）。
    """
    today_map = {s.get('code'): s for s in (today_pool or [])}
    cands = []
    for n in nodes_out:
        trg = n.get('trigger') or {}
        if not trg.get('code'):
            continue
        cur = today_map.get(trg['code'])
        if not cur or cur.get('lbc', 0) < 2:
            continue
        ck = '%s.%s' % (trg.get('market', 1), trg['code'])
        _c = cache.get(ck) or {}
        hit = concept_hit_count(_c.get('concepts', []), hot_concepts)
        ferm = ferment_count(_c.get('concepts', []), today_pool)
        cands.append({
            'node_type': n['type'], 'node_date': n['date'], 'node_id': n['id'],
            'code': trg['code'], 'market': trg.get('market', 1), 'name': trg['name'],
            'region': _c.get('region', '—'), 'pinyin': pinyin_abbr(trg.get('name', '')),
            'concepts': _c.get('concepts', []), 'industry': _c.get('industry', ''),
            'boards': cur['lbc'], 'status': '连板中',
            'concept_hit': hit, 'ferment': ferm,
            'amount_yi': round(cur['amount'] / 1e8, 2),
            'total_cap_yi': round(cur['total_cap'] / 1e8, 2),
        })
    for n in nodes_out:
        for s in n.get('stocks', []):
            # 连板中 + 断板反包：后者虽中途断板，但 3 日内重新封板，仍是活口
            if s.get('status') not in ('连板中', '断板反包'):
                continue
            hit = concept_hit_count(s.get('concepts', []), hot_concepts)
            ferm = ferment_count(s.get('concepts', []), today_pool)
            cands.append({
                'node_type': n['type'], 'node_date': n['date'], 'node_id': n['id'],
                'code': s['code'], 'market': s['market'], 'name': s['name'],
                'region': s.get('region', '—'), 'pinyin': s.get('pinyin', ''),
                'concepts': s.get('concepts', []), 'industry': s.get('industry', ''),
                'boards': s.get('boards', 0), 'status': s.get('status', ''),
                'concept_hit': hit, 'ferment': ferm,
                'amount_yi': round(s.get('amount', 0) / 1e8, 2),
                'total_cap_yi': round(s.get('total_cap', 0) / 1e8, 2),
            })
    return dedup_candidates(cands)[:12]


# ============ 8. 主流程 ============
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=20,
                    help='回溯交易日数量（东财涨停池仅保留最近约15个交易日）')
    ap.add_argument('--tag-limit', type=int, default=40, help='单次标注个股数量上限')
    args = ap.parse_args()

    t0 = time.time()
    print('=' * 60)
    print('A股短线仪表盘 · 数据采集  %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print('=' * 60)

    print('\n[1/7] 获取交易日...')
    days = fetch_trade_days(args.days)
    if not days:
        print('  ❌ 交易日获取失败，终止')
        return
    print('  交易日 %d 个：%s ~ %s' % (len(days), days[0], days[-1]))

    print('\n[2/7] 抓取历史涨停池...')
    zt_hist = {}
    for i, d in enumerate(days):
        pool = fetch_zt_pool(d.replace('-', ''))
        if pool:
            zt_hist[d] = pool
        if (i + 1) % 10 == 0 or i == len(days) - 1:
            print('  进度 %d/%d' % (i + 1, len(days)))
        time.sleep(0.12)
    print('  有效交易日涨停数据：%d 天' % len(zt_hist))

    print('\n[3/7] 抓取指数 / 板块 / 新闻...')
    index = fetch_index()
    board = fetch_board_rank(60)
    news = fetch_news()
    print('  指数 %d 条 | 板块 %d 条 | 新闻 宏观%d 利好%d 利空%d 外围%d'
          % (len(index), len(board), len(news['macro']), len(news['good']),
             len(news['bad']), len(news['overseas'])))

    # 板块历史累积（3日榜用）
    print('\n[4/7] 更新板块涨幅历史...')
    today = days[-1]
    bhist = load_json(BOARD_HIST, {})
    if board:
        bhist[today] = {b['name']: b['pct'] for b in board}
        # 只保留最近 10 个交易日
        for k in sorted(bhist.keys())[:-10]:
            bhist.pop(k, None)
        save_json(BOARD_HIST, bhist)
    recent_days = sorted(bhist.keys())[-3:]
    print('  已累积 %d 个交易日，最近：%s' % (len(bhist), recent_days))

    board_3d, board_3d_note = [], ''
    if len(recent_days) >= 2:
        acc = {}
        for dd in recent_days:
            for name, pct in bhist[dd].items():
                acc.setdefault(name, []).append(pct)
        for name, plist in acc.items():
            cum = 1.0
            for p in plist:
                cum *= (1 + p / 100.0)
            board_3d.append({'name': name, 'pct': round((cum - 1) * 100, 2), 'days': len(plist)})
        board_3d.sort(key=lambda x: -x['pct'])
        board_3d = board_3d[:10]
        board_3d_note = '%d日累计' % len(recent_days)
    if not board_3d and board:
        # 冷启动：用腾讯板块自带的 5 日涨幅顶替
        board_3d = [{'name': b['name'], 'pct': b['zdf_d5'], 'days': 5} for b in board[:10]]
        board_3d.sort(key=lambda x: -x['pct'])
        board_3d_note = '5日累计（3日历史积累中）'
    print('  累计榜：%d 条 · %s' % (len(board_3d), board_3d_note))

    # ---- 梯队折线图数据 ----
    print('\n[5/7] 构建梯队折线数据...')
    series = []
    for d in days:
        pool = zt_hist.get(d, [])
        if not pool:
            continue
        lbcs = sorted({s['lbc'] for s in pool}, reverse=True)
        top = lbcs[0] if lbcs else 0
        second = lbcs[1] if len(lbcs) > 1 else 0
        # 创业板高度：300/301 开头
        cyb = [s['lbc'] for s in pool if s['code'].startswith(('300', '301'))]
        series.append({
            'date': d,
            'top': top,
            'second': second,
            'cyb': max(cyb) if cyb else 0,
            'top_names': '、'.join([s['name'] for s in pool if s['lbc'] == top][:3]),
            'second_names': '、'.join([s['name'] for s in pool if s['lbc'] == second][:3]),
            'cyb_names': '、'.join([s['name'] for s in pool
                                   if s['code'].startswith(('300', '301'))
                                   and s['lbc'] == (max(cyb) if cyb else 0)][:2]),
        })
    print('  梯队数据点 %d 个' % len(series))

    # ---- 节点判定 ----
    print('\n[6/7] 节点判定...')
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
        if ent.get('ts') == stamp:
            ev, bd = ent.get('events', []), ent.get('boards', [])
        else:
            ev, bd = fetch_stock_events(code, nm, own_concepts=obj.get('concepts'))
            evt_cache[code] = {'events': ev, 'boards': bd, 'ts': stamp}
            time.sleep(0.22)
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
    print('\n[7/7] 计算次日连板候选...')
    # 热点源 = 单日板块榜 + 多日累计榜（覆盖「这几天」主线，不只看单日）
    hot_concepts = [b['name'] for b in board[:30]] + [b['name'] for b in board_3d]
    cands = build_candidates(nodes_out, today_pool, hot_concepts, cache)

    # 上板分时量：次日竞价量的基准是「上板那一刻的分时量 × 50%」，
    # 不是全天成交额（炸板/回封会把全天额撑大数倍）。只对前5只请求，分时接口易限流。
    for c in cands[:5]:
        cur = today_map.get(c['code'])
        fbt = cur.get('first_seal') if cur else None
        is_yizi = bool(cur.get('is_yizi')) if cur else False
        seal, day_total = fetch_seal_amount(c['code'], c['market'], today, fbt, is_yizi)
        if seal:
            c['seal_amount_yi'] = round(seal / 1e8, 2)
            c['bid_required_yi'] = round(seal / 1e8 * 0.5, 2)
            c['bid_basis'] = '上板分时量'
        else:
            c['seal_amount_yi'] = None
            c['bid_required_yi'] = round(c['amount_yi'] * 0.5, 2)
            c['bid_basis'] = '全天成交额(分时缺失回退)'
        if day_total:
            c['day_amount_yi'] = round(day_total / 1e8, 2)
        time.sleep(0.3)

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
            'bid_required_yi': bid,                              # 竞价量 >= 上板分时量*50%
            'seal_min_yi': round(c['total_cap_yi'] * 0.01, 2),   # 封单 >= 总市值 1%
            'seal_max_yi': round(c['total_cap_yi'] * 0.03, 2),   # 封单 <= 总市值 3%
        }
        print('  首位候选：%s(%s) %s板 · %s · 来自%s'
              % (reco['name'], reco['code'], reco['boards'], reco['region'], reco['from_node']))
        print('  竞价量下限 %.2f 亿 | 封单区间 %.2f~%.2f 亿'
              % (reco['bid_required_yi'], reco['seal_min_yi'], reco['seal_max_yi']))

    # 厄尔尼诺当前阶段自动识别（基于当日连板梯队：≥4 连板龙头 → 激活该线）
    elnino_out = copy.deepcopy(EL_NINO_DATA)
    auto_idx, auto_lbc, _best, _trig = calc_elnino_stage(today_pool, EL_NINO_DATA, threshold=4)
    if auto_idx:
        elnino_out['current_stage'] = auto_idx
        elnino_out['stage_auto'] = True
        elnino_out['stage_auto_lbc'] = auto_lbc
        trig = _trig.get(auto_idx)
        if trig:
            elnino_out['stage_auto_trigger'] = trig   # 触发该线高亮的龙头股
        print('\n[厄尔尼诺] 自动识别第 %d 阶段（%d 连板激活 · 触发：%s）：%s'
              % (auto_idx, auto_lbc, (trig or {}).get('name', '—'),
                 elnino_out['stages'][auto_idx-1]['title']))
    else:
        elnino_out['current_stage'] = EL_NINO_DATA['default_stage']
        elnino_out['stage_auto'] = False
        print('\n[厄尔尼诺] 无 ≥4 连板激活线，回退默认第 %d 阶段' % EL_NINO_DATA['default_stage'])

    # 轮动切换检测：与上一交易日激活线对比，第一时间发现"换细分/换方向"
    prev_stage = load_elnino_cache()
    rotation = None
    if auto_idx is not None and prev_stage is not None and auto_idx != prev_stage:
        rotation = {
            'from': prev_stage,
            'from_label': EL_NINO_DATA['stages'][prev_stage-1]['title'],
            'to': auto_idx,
            'to_label': EL_NINO_DATA['stages'][auto_idx-1]['title'],
            'trigger': elnino_out.get('stage_auto_trigger'),
        }
        print('\n[厄尔尼诺] 🔄 轮动切换检测：第 %d 阶段 → 第 %d 阶段（触发：%s）'
              % (prev_stage, auto_idx, (rotation['trigger'] or {}).get('name', '—')))
    elnino_out['rotation'] = rotation
    # 缓存当前激活线（无激活则保留上一次，避免误判为"切换"）
    save_elnino_cache(auto_idx if auto_idx is not None else prev_stage)

    # 新龙头自动发现 + 收录：轮动发生时密切留意不在静态名单里 / 新崛起的情绪龙
    dyn, new_leaders = discover_elnino_leaders(
        today_pool, EL_NINO_DATA, EL_NINO_THEME_KW,
        ELNINO_DYN, ELNINO_NEW_LEADER_MIN_LBC)
    merge_elnino_dynamic(elnino_out, dyn)
    if new_leaders:
        print('\n[厄尔尼诺] 自动收录新龙头：' + '，'.join(
            '%s(%s)%d板' % (e['name'], e['code'], e['boards']) for e in new_leaders))

    data = {
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'date': today,
        'index': index,
        'news': news,
        'board_daily': board[:15],
        'board_3d': board_3d,
        'board_3d_note': board_3d_note,
        'board_3d_days': len(recent_days),
        'ladder': series,
        'zt_pool': today_pool,
        'nodes': nodes_out[:12],
        'board_perf': build_board_perf(today_pool),  # 连板晋级 + 昨日涨停表现
        'recommend': reco,
        'candidates': cands,
        'elnino': elnino_out,    # 厄尔尼诺事件静态主题（置顶于模块3，阶段自动识别）
        'params': {
            'vol_ratio_threshold': VOL_RATIO_THRESHOLD,
            'breakout_lookback': BREAKOUT_LOOKBACK,
        },
    }
    save_json(DATA_JSON, data)
    save_js(os.path.join(BASE, 'data.js'), data)
    print('\n✅ 完成，耗时 %.1fs' % (time.time() - t0))
    print('   输出：data.json / data.js / nodes.json / board_history.json')


if __name__ == '__main__':
    main()
