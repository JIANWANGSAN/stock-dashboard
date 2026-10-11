"""市场总览数据层 —— 市场情绪 + 板块资金流（板块/大盘级公开数据，不涉个股推荐）。

省流量：全站共享一份缓存（TTL 默认 5 分钟），多个用户/多次打开只抓一次；
盘中 5 分钟刷新足够，非交易时段数据本就不变。数据源全免费、无 key。
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timezone, timedelta

import astock
import gstock

BEIJING = timezone(timedelta(hours=8))
_CACHE: dict = {}
_TTL = 300  # 5 分钟；全站共享，省数据源压力


def _cached(key: str, fn, valid=bool):
    """TTL 缓存。数据源故障的空结果不缓存（valid 判否），下次请求直接重试。"""
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    val = fn()
    if valid(val):
        _CACHE[key] = (now, val)
    return val


def _num(v) -> int:
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return 0


def _sentiment() -> dict:
    """市场情绪：涨跌家数/涨停跌停/活跃度 + 大盘宽度、题材投机（客观数据机械分档）。"""
    try:
        # akshare 惰性导入（同 astock 模式）：未装时降级返回空，不挡整个服务启动
        df = astock._akshare().stock_market_activity_legu()
        d = {row["item"]: row["value"] for _, row in df.iterrows()}
    except Exception:
        return {}
    up, down, flat = _num(d.get("上涨")), _num(d.get("下跌")), _num(d.get("平盘"))
    zt, zt_real = _num(d.get("涨停")), _num(d.get("真实涨停"))
    dt, dt_real = _num(d.get("跌停")), _num(d.get("真实跌停"))
    r = up / max(down, 1)
    if up < 600:
        breadth = "冰点"
    elif r < 0.7:
        breadth = "偏弱"
    elif r < 1.2:
        breadth = "中性"
    elif r < 2.5:
        breadth = "偏强"
    else:
        breadth = "普涨"
    speculation = "亢奋" if zt_real >= 100 else "活跃" if zt_real >= 60 else "普通" if zt_real >= 30 else "冰点"
    # 炸板率：乐咕不给炸板数（涨停-真实涨停=ST 拆分，不是炸板），改走东财
    # 涨停池/炸板池口径（与复盘端 zb/(zt+zb) 一致，push2ex 域当前可用）。
    # 日期必须对齐乐咕统计日（周末/盘前乐咕停在上一交易日，用 now() 会查到空池）；
    # 非交易日/接口失败 → None，仓位规则只按跌停家数判断，不臆造。
    zb_rate = None
    try:
        stat_day = str(d.get("统计日期") or "")[:10]
        day = stat_day.replace("-", "") if len(stat_day) == 10 \
            else datetime.now(BEIJING).strftime("%Y%m%d")
        ak = astock._akshare()
        n_zt_pool = len(ak.stock_zt_pool_em(date=day))
        n_zb_pool = len(ak.stock_zb_pool_em(date=day))
        if n_zt_pool + n_zb_pool:
            zb_rate = n_zb_pool / (n_zt_pool + n_zb_pool)
    except Exception:  # noqa: BLE001 - 缺这一项只影响仓位建议，不挡情绪区
        pass
    # 仓位建议：纯机械规则，非投资建议。跌停取乐咕口径（含 ST）。
    position_suggest = "空仓" if (zb_rate is not None and zb_rate > 0.75) or dt > 10 else ""
    return {
        "up": up, "down": down, "flat": flat,
        "zt": zt, "zt_real": zt_real, "dt": dt, "dt_real": dt_real,
        "active": str(d.get("活跃度", "")),
        "breadth": breadth, "speculation": speculation,
        "zb_rate": zb_rate, "position_suggest": position_suggest,
        "date": str(d.get("统计日期", "")),
    }


def _sectors() -> list[dict]:
    """行业资金流（按净额降序）。不含领涨股等个股字段。"""
    try:
        f = astock._akshare().stock_fund_flow_industry(symbol="即时")
        f = f.sort_values("净额", ascending=False)
    except Exception:
        return []
    out = []
    for _, row in f.iterrows():
        out.append({
            "name": str(row["行业"]),
            "pct": round(float(row.get("行业-涨跌幅", 0) or 0), 2),
            "net": round(float(row.get("净额", 0) or 0), 2),
            "inflow": round(float(row.get("流入资金", 0) or 0), 2),
            "outflow": round(float(row.get("流出资金", 0) or 0), 2),
            "firms": _num(row.get("公司家数")),
        })
    return out


def _float(v) -> float:
    """宽松数值解析：容忍 '3.04%'、'1,234.5'、'-' 等字符串形态，解析失败按 0。"""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _sectors_3d() -> list[dict]:
    """行业资金流「3 日排行」页签（同源换页签）：阶段涨跌幅 = 三日涨幅，按涨幅降序。

    与「即时」页签列名不同：3 日的涨跌幅列叫「阶段涨跌幅」**且是带 % 的字符串**
    （实测 "3.04%"），所以必须走 _float 宽松解析（akshare 90 个行业）。
    """
    try:
        f = astock._akshare().stock_fund_flow_industry(symbol="3日排行")
        f = f.sort_values("阶段涨跌幅", ascending=False)
    except Exception:
        return []
    out = []
    for _, row in f.iterrows():
        out.append({
            "name": str(row["行业"]),
            "pct": round(_float(row.get("阶段涨跌幅")), 2),
            "net": round(_float(row.get("净额")), 2),
            "inflow": round(_float(row.get("流入资金")), 2),
            "outflow": round(_float(row.get("流出资金")), 2),
            "firms": _num(row.get("公司家数")),
        })
    return out


def get_overview() -> dict:
    """市场情绪 + 板块资金（含缓存）。资金轮动由前端从 sectors 头尾取。"""
    def build():
        return {
            "sentiment": _sentiment(),
            "sectors": _sectors(),
            "sectors_3d": _sectors_3d(),
            "updated": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
        }
    return _cached("overview", build, valid=lambda v: bool(v.get("sentiment") or v.get("sectors")))


def _emotion() -> dict:
    """短线情绪（聚合口径，**零个股名**）：连板梯队 / 最高连板 / 炸板率 / 封板率 / 晋级率 / 涨跌停家数。

    数据源＝东财涨停板四池（push2ex）。只把池子聚合成计数与比率，
    **不输出任何个股 code/name**——守产品「零标的」红线（个股清单是甩名单，不做）。
    """
    # 定位最近交易日：从今天往前回溯，第一日有涨停池即取（非交易日/盘前返空则继续回溯）。
    today = datetime.now(BEIJING).date()
    resolved, zt = "", []
    for back in range(8):
        d = (today - timedelta(days=back)).strftime("%Y%m%d")
        zt = astock.em_zt_topic_pool("getTopicZTPool", d, "fbt:asc")
        if zt:
            resolved = d
            break
    if not resolved:
        return {}

    zb = astock.em_zt_topic_pool("getTopicZBPool", resolved, "fbt:asc")    # 炸板池
    dt = astock.em_zt_topic_pool("getTopicDTPool", resolved, "fund:asc")   # 跌停池
    yzt = astock.em_zt_topic_pool("getYesterdayZTPool", resolved, "zs:desc")  # 昨涨停池

    boards = [_num(p.get("lbc")) or 1 for p in zt]      # 每只连板数（缺省按 1 板）
    lianban = [b for b in boards if b >= 2]             # 2 板及以上（连板）
    # 连板梯队：2/3/4/5+ 各多少家（5 代表 5 板及以上），只保留有家数的档
    tiers = Counter(min(b, 5) for b in lianban)
    ladder = [{"boards": b, "count": tiers[b], "plus": b >= 5} for b in sorted(tiers)]

    # 连板股清单（2 板+，客观公开榜单数据；按连板数、成交额降序）。
    # 产品定位调整（2026-07-05）：从「零标的」→「展示客观榜单但不推荐/不预测/不评分」。
    # 涨停原因题材串（问财，与首板页共用缓存；缺 key/失败 → 空串，不影响主数据）。
    try:
        import firstboard  # 函数内导入：firstboard 顶部 import market，避免循环依赖

        reasons, _reason_err = firstboard.get_reasons(resolved)
    except Exception:  # noqa: BLE001
        reasons = {}
    lianban_stocks = sorted(
        ({
            "code": str(p.get("c", "")), "name": p.get("n", ""),
            "boards": _num(p.get("lbc")) or 1,
            "price": round((astock._numf(p.get("p")) or 0) / 1000, 2),
            "pct": round(astock._numf(p.get("zdp")) or 0, 2),
            "amount": astock._numf(p.get("amount")),      # 成交额,元（'-' 占位归一为 None，防排序对 str 取负崩溃）
            "float_cap": astock._numf(p.get("ltsz")),     # 流通市值,元
            "industry": p.get("hybk", ""),  # 概念/行业
            "reason": reasons.get(str(p.get("c", "")), ""),  # 涨停原因题材串
        } for p in zt if (_num(p.get("lbc")) or 1) >= 2),
        key=lambda x: (-x["boards"], -(x["amount"] or 0)),
    )

    zt_count, zb_count, yzt_count = len(zt), len(zb), len(yzt)
    attempts = zt_count + zb_count                       # 尝试涨停 = 封住 + 炸板
    seal_rate = round(zt_count / attempts, 3) if attempts else None      # 封板率
    break_rate = round(zb_count / attempts, 3) if attempts else None     # 炸板率
    # 晋级率＝今日 2 板+（＝昨涨停今又停）÷ 昨日涨停家数
    promotion_rate = round(len(lianban) / yzt_count, 3) if yzt_count else None

    return {
        "date": f"{resolved[:4]}-{resolved[4:6]}-{resolved[6:]}",
        "zt_count": zt_count,
        "dt_count": len(dt),
        "zb_count": zb_count,
        "max_boards": max(boards) if boards else 0,
        "lianban_count": len(lianban),
        "ladder": ladder,
        "lianban_stocks": lianban_stocks,
        "seal_rate": seal_rate,
        "break_rate": break_rate,
        "promotion_rate": promotion_rate,
        "yzt_count": yzt_count,
    }


def get_short_term_emotion() -> dict:
    """短线情绪（含缓存，5 分钟）。"""
    return _cached("emotion", _emotion)


def get_turnover_top() -> dict:
    """全市场成交额榜 Top20（客观公开榜单，含缓存 5 分钟）。"""
    def build():
        import math
        rows = astock.market_turnover_rank(20)
        rows = [r for r in rows if isinstance(r.get("amount"), (int, float))
                and math.isfinite(r["amount"]) and r["amount"] > 0]
        rows.sort(key=lambda r: r["amount"], reverse=True)
        return {
            "reason": ("" if quote_day == datetime.now(BEIJING).strftime("%Y-%m-%d") else f"参考行情日为 {quote_day}，请勿当作今日成交榜") if rows else "尚无有效成交额，暂不排名",
            "quote_date": quote_day,
            "stocks": rows,
            "updated": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
        }
    from duanxian import trade_calendar
    quote_day = trade_calendar.quote_trade_day()
    phase = trade_calendar.session_phase(datetime.now(BEIJING), quote_day)
    if phase["phase_key"] in {"auction", "wait", "unknown"}:
        return {"stocks": [], "updated": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M"),
                "reason": "竞价阶段不展示连续交易成交额排行"}
    return _cached("turnover_top:" + str(quote_day) + ":" + phase["phase_key"], build, valid=lambda v: bool(v.get("stocks")))


def get_global_indices() -> list[dict]:
    """全球指数快照（美股 / 港股，含缓存 5 分钟）。空结果不缓存。"""
    return _cached("global_indices", gstock.global_indices, valid=bool)
