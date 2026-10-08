"""个股标签：拼音缩写（pinyin）+ 所属概念 + 地域。

数据源：同花顺 F10
  · 概念`basic.10jqka.com.cn/<code>/concept.html` → td.gnName
  · 地域/行业 `basic.10jqka.com.cn/<code>/company.html` → 「所属地域」「所属申万行业」

口径与用户 stock_dashboard/enrich_tags.py 一致：
  · 板块分流——行业名进 industry，题材名进 concepts，地域类概念只填 region；
  · 剔除财务/交易属性/指数成分等噪声标签（NOISE_KW），避免「东方财富」「沪股通」
    这类和短线题材无关的标签混进概念列；
  · 概念按同花顺原始顺序取前若干个（**当下热度排序在 stock_dashboard 侧做**，
    本模块不拉板块涨幅榜，避免多一次重网络调用）。

⚠️ 拼音缩写依赖 pypinyin（全站唯一第三方依赖）。**绝不允许 ImportError 静默降级**
—— 2026-09-22 仪表盘踩过：环境重建后 pypinyin 丢失 → 全站缩写变空串、脚本一声不响跑完。
这里改成 import 即失败（FastAPI 启动即报错），并在取数时做覆盖率自检。
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pypinyin import lazy_pinyin

from duanxian.paths import data_path

TAGS_VER = 3# 口径版本：变更标签口径时 +1，缓存自动重抓
TAG_TTL_DAYS = 7                # 概念/地域会变，给 7 天重抓窗口；历史 K 线才永久固化
MAX_CONCEPTS = 6
_UA = {"User-Agent": "Mozilla/5.0", "Referer": "http://basic.10jqka.com.cn/"}

# 交易属性 / 财务标签 / 指数成分 —— 不是短线题材，剔除
NOISE_KW = (
    "昨日", "近期", "百日", "最近", "东方财富", "精准诊断", "趋势股", "题材股",
    "百元股", "高价股", "低价股", "破净股", "破发股", "破增发价", "高市净率",
    "低市盈率", "高质押", "低估值", "高股息", "中报", "年报", "季报", "预增",
    "预减", "首亏", "扭亏", "续亏", "预盈", "业绩", "含一字", "高换手",
    "高振幅", "融资融券", "沪股通", "深股通", "转融券", "转债标的", "可转债",
    "标准普尔", "MSCI", "富时罗素", "AH股", "HS300", "上证180", "上证50",
    "中证500", "中证1000", "创业板综", "创业成份", "深成500", "深证100R",
    "机构重仓", "基金重仓", "社保重仓", "QFII重仓", "参股银行", "参股券商",
    "参股保险", "举牌", "增持", "回购", "送转", "高送转", "分红",
    "融资", "定增", "新股与次新股", "次新股", "注册制次新股", "ST股",
    "壳资源", "重组", "股权转让", "要约", "同花顺", "大智慧",
    "标普", "道琼斯", "沪企改革", "AB股", "B股", "H股", "科创板", "北交所",
    "专精特新", "独角兽", "超级品牌", "央视50", "茅指数", "宁组合",
    "茅概念", "宁概念", "社保基金", "险资", "私募", "游资",
    "龙虎榜", "大宗交易", "股权激励", "员工持股", "限售股", "解禁",
    "首发", "打新", "市值", "流通", "总市值", "微盘股", "小盘股", "大盘股",
    "中盘股", "权重股", "蓝筹", "白马", "黑马", "妖股", "牛股", "强势股",
    "弱势股", "活跃股", "冷门股", "热门股", "人气股", "龙头", "涨停",
    "连板", "首板", "炸板", "跌停", "ST", "退市", "风险", "警示",
)

PROVINCES = (
    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海",
    "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
)

# 行业大类（申万口径），从 concepts 里剔除，只保留真题材
INDUSTRY_BLOCK = set("""
农林牧渔 种植业 渔业 林业 养殖 种子 饲料 农产品加工 食品加工 动物保健 农药 化肥
化学原料 化学制品 化学制药 原料药 化纤 橡胶 塑料 非金属材料 民爆 炼化 石油
钢铁 特钢 有色金属 工业金属 贵金属 小金属 能源金属 金属新材料 稀土
电子 半导体 元件 光学光电子 消费电子 电子化学品 面板 LED 印制电路板 被动元件
汽车 汽车零部件 汽车电子 汽车服务 商用货车 商用客车 乘用车 摩托车 轮胎 汽车销售
家电 白色家电 黑色家电 小家电 厨卫电器 家电零部件 照明设备 家居用品 文娱用品
食品饮料 白酒 啤酒 饮料乳品 休闲食品 调味品 预加工食品 保健食品 化妆品 珠宝 玩具 造纸 包装
纺织服饰 纺织制造 服装家纺 鞋帽 钟表 轻工制造 文具
医药生物 医疗器械 医疗服务 CRO 体外诊断 诊断服务 医院 中药 疫苗 血液制品 医药流通 医疗设备 医美
公用事业 电力 燃气 水务 火电 水电 风电 光伏 核电 热电
交通运输 物流 铁路公路 港口 机场 航空 航运 高速公路 快递 电商
房地产 建筑 装修 建材 水泥 玻璃 钢铁制品 工程机械 电器设备
计算机 软件 硬件 IT 互联网 通信 通信设备 通信服务 电子元件 消费电子
传媒 出版 影视 院线 广告 营销 文化 旅游 酒店 餐饮 教育 培训
银行 证券 保险 多元金融 房地产开发 园区开发
""".split())


def pinyin_abbr(name: str) -> str:
    """股票名 → 拼音首字母（大写）。新华传媒 → XHCM。"""
    return "".join(p[:1].upper() for p in lazy_pinyin(str(name or "")) if p[:1].isalpha())


def _strip_industry(bn: str) -> str:
    return re.sub(r"[ⅠⅡⅢⅣⅤ]+$", "", bn.replace("板块", "").strip())


def _is_industry(bn: str) -> bool:
    b = _strip_industry(bn)
    return b in INDUSTRY_BLOCK


def _text(html_bytes: bytes) -> str:
    return html_bytes.decode("gbk", "ignore")


def _fetch_tags(code: str) -> dict:
    """单只票的 F10 标签。任一环节失败返回空标签（不伪造）。"""
    import requests

    out = {"region": "", "industry": "", "concepts": []}
    session = requests.Session()
    session.trust_env = False          # 同花顺是国内站，系统代理常把路由挂掉
    try:
        h = session.get("http://basic.10jqka.com.cn/%s/concept.html" % code,
                        headers=_UA, timeout=10)
        if h.status_code == 200:
            html = _text(h.content)
            for m in re.finditer(r'class="gnName"[^>]*>(.*?)</td>', html, re.S):
                nm = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                if nm:
                    out["concepts"].append(nm)
    except Exception:  # noqa: BLE001
        pass
    try:
        c = session.get("http://basic.10jqka.com.cn/%s/company.html" % code,
                        headers=_UA, timeout=10)
        if c.status_code == 200:
            flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _text(c.content)[:400000]))
            m = re.search(r"所属地域[：:]\s*(\S+?)(?:\s|$)", flat)
            if m:
                out["region"] = m.group(1).strip()
            m2 = re.search(r"所属申万行业[：:]\s*(\S+?)(?:\s|$)", flat)
            if m2:
                out["industry"] = m2.group(1).strip()
    except Exception:  # noqa: BLE001
        pass
    return out


def _split_concepts(raw: list, region: str) -> tuple:
    """板块分流：行业 → industry；地域 → region（仅在 region 为空时兜底）；其余 → concepts。"""
    industries, themes = [], []
    for bn in raw:
        if _is_industry(bn):
            industries.append(_strip_industry(bn))
            continue
        if any(k in bn for k in NOISE_KW):
            continue
        b = _strip_industry(bn)
        if b in PROVINCES:
            if not region:
                region = b
            continue
        themes.append(b)
    seen, clean = set(), []
    for t in themes:
        if t and t not in seen:
            seen.add(t)
            clean.append(t)
    return (industries[0] if industries else ""), region, clean[:MAX_CONCEPTS]


# ---------------------------------------------------------------- 缓存
def _cache_path() -> str:
    return data_path("cache/stock_tags.json")


def _load_cache() -> dict:
    p = _cache_path()
    try:
        with open(p, encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    p = _cache_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError:
        pass


def enrich(codes, timeout: float = 90.0, force: bool = False) -> dict:
    """批量补标签。返回 {code: {region, industry, concepts}}，拼音缩写在调用侧拼。

    带落盘缓存（7 天 TTL + 口径版本）：命中缓存的不发请求。
    网络不可用时返回缓存里的旧值，不把「取不到」伪装成「没有」。
    """
    codes = [str(c) for c in dict.fromkeys(codes) if c]
    if not codes:
        return {}
    cache = _load_cache()
    now = time.time()
    todo = []
    for c in codes:
        hit = cache.get(c)
        if (not force and isinstance(hit, dict) and hit.get("_v") == TAGS_VER
                and isinstance(hit.get("_ts"), (int, float))
                and now - hit["_ts"] < TAG_TTL_DAYS * 86400):
            continue
        todo.append(c)

    if todo:
        workers =min(4, len(todo))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_fetch_tags, c): c for c in todo}
            done = 0
            for fut, code in futs.items():
                try:
                    r = fut.result(timeout=timeout)
                except Exception:  # noqa: BLE001
                    r = {}
                if r:
                    r["_v"] = TAGS_VER
                    r["_ts"] = int(now)
                    cache[code] = r
                done += 1
                if done % 10 == 0:
                    time.sleep(0.3)
        _save_cache(cache)

    out = {}
    for c in codes:
        raw = cache.get(c) or {}
        concepts = list(raw.get("concepts") or [])
        region = (raw.get("region") or "").strip()
        industry, region, clean = _split_concepts(concepts, region)
        out[c] = {
            "region": region or (raw.get("region") or "").strip(),
            "industry": industry or (raw.get("industry") or "").strip(),
            "concepts": clean,
        }
    return out


def annotate(code: str, name: str, tags: dict) -> dict:
    """给单只票贴上 pinyin / region / concepts（tags 来自 enrich）。"""
    t = (tags or {}).get(str(code)) or {}
    return {
        "pinyin": pinyin_abbr(name),
        "region": t.get("region") or "",
        "industry": t.get("industry") or "",
        "concepts": list(t.get("concepts") or []),
    }


if __name__ == "__main__":
    import sys
    _codes = sys.argv[1:] or ["600977", "600096", "000902"]
    _t = enrich(_codes, force=True)
    for _c in _codes:
        print(_c, json.dumps(annotate(_c, _c, _t), ensure_ascii=False))