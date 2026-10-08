"""节点票池（核心 IP）—— 三类节点检测 + 当前最高标聚焦 + 节点票状态追踪。

移植自用户 stock_dashboard/fetch_data.py 的 detect_nodes 系列，跑在 Vibe AStock
原生 akshare 涨停池（market_facts.pools）之上：规则只维护一份，不再双系统漂移。

三类节点：
  1) 最高标断板节点：昨日最高板今日断板 → 取当日首板（新龙孵化池）
  2) 突破节点：连板高度创回看窗口（BREAKOUT_LOOKBACK）内阶段新高
  3) 穿越节点：今日最高标由昨日晋级而来、跨过昨日天花板，且「不是自己往上打」
     🔴 穿越自己不产生新节点（self_only 守卫）

硬性规则（来自用户约定 §3.2 / 记忆卡）：
  · 排除 北交所 / 科创板 / ST（创业板保留）
  · 市值门槛按「节点诞生日快照」判定，节点票池只留 ≤200 亿
  · 节点票池只保留近 NODE_KEEP_DAYS 个交易日
  · 节点内无票（空节点）即删
  · 一字板判据：首封 ≤09:30:05 且 全天未开板（仅展示，不影响节点/连板/涨停标黄）
"""
from __future__ import annotations

import json
import os
from typing import Optional

from duanxian import market_facts, stock_tags, trade_calendar
from duanxian.paths import data_path
from duanxian.cache_policy import fresh as cache_fresh, write as write_cache
from duanxian.util import china_today

# ---------------------------------------------------------------- 参数（用户确认）
NODE_CAP_LIMIT = 200 * 1e8        # 节点票池总市值上限（元），只保留 200 亿以下
BREAKOUT_LOOKBACK = 20            # 突破节点：回看多少个交易日的最高板高度
NODE_KEEP_DAYS = 10              # 节点票池只保留最近约 10 个交易日的节点
WINDOW = 24                       # 取数窗口（>= BREAKOUT_LOOKBACK + 缓冲）
NODE_SCHEMA = 3                  # v3：标签(pinyin/region/concepts) + 已断板过滤
KLINE_DAYS = 60                  # 附带日K的交易日数（点击股票名查看）
HIDE_BROKEN = True                # 已断板不再显示在节点票里
_NODE_SCHEMA = NODE_SCHEMA        # 向后兼容旧引用

# 用户核对后的总市值修正（元），按 (code, YYYYMMDD) 锁定，避免污染未来交易日。
# 例：('605577', '20260904'): 7604000000,
CAP_OVERRIDE: dict[tuple, float] = {}


# ---------------------------------------------------------------- 基础工具
def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None   # NaN→None
    except (TypeError, ValueError):
        return None


def _hhmmss(t) -> str:
    return str(t or "").strip()


def _sealtime_num(t) -> str:
    """'09:30:05' → '093005'，便于固定宽度字符串比较。"""
    return "".join(ch for ch in (t or "") if ch.isdigit())


def is_yizi(first_seal, last_seal) -> bool:
    """一字板判据：开盘即封 且 全天未开板。

    唯一判据：首次封板 ≤09:30:05 且 最后封板==首次封板（即全天从未开板）。
    旧写法（仅 fbt<=93005）会把「竞价封板→盘中炸板→尾盘回封」误判一字板。
    仅展示层使用，不参与节点/连板/涨停标黄判定。
    """
    fbt = _sealtime_num(first_seal)
    lbt = _sealtime_num(last_seal)
    return bool(fbt and fbt <= "093005" and lbt == fbt)


def is_excluded(code, name: str = "") -> bool:
    """剔除 北交所 / 科创板 / ST / 退市。创业板保留（全站口径，仅连板梯队才剔创业板）。"""
    c = str(code or "")
    if c.startswith(("688", "689")):          # 科创板
        return True
    if c[:1] in ("4", "8") or c.startswith("92"):   # 北交所 43/83/87/88/92
        return True
    n = str(name or "").upper().replace(" ", "").replace("　", "")
    if "ST" in n:
        return True
    if n.endswith("退") or "退市" in n:
        return True
    return False


def _norm(s: dict) -> dict:
    """market_facts 的 zt 行 → 用户算法期望的键名。"""
    return {
        "code": s.get("code"),
        "name": s.get("name") or "",
        "lbc": int(s.get("boards") or 1),
        "amount": _num(s.get("amount")),
        "total_cap": _num(s.get("total_cap")),
        "first_seal": _hhmmss(s.get("first_seal")),
        "last_seal": _hhmmss(s.get("last_seal")),
        "broken_times": int(s.get("broken_times") or 0),
        "sector": s.get("sector") or "",
        "zt_stat": s.get("zt_stat") or "",
        "turnover": s.get("turnover"),
    }


# ---------------------------------------------------------------- 取数：窗口内逐日涨停池
def build_history(end_date: str, window: int = WINDOW):
    """返回 (zt_hist, days)。

    zt_hist: {date: [归一化后的涨停股]}（已剔除 北交所/科创板/ST）。
    每个股票的 total_cap 即**该日的节点诞生日快照**，市值门槛据此判定。
    """
    days = trade_calendar.trade_dates_ending_at(end_date, n=window)
    zt_hist: dict[str, list] = {}
    for d in days:
        p = market_facts.pools(d)
        rows = []
        if isinstance(p, dict):
            dd = d.replace("-", "")
            for s in (p.get("zt") or []):
                code = s.get("code", "")
                name = s.get("name", "")
                if is_excluded(code, name):
                    continue
                row = _norm(s)
                key = (str(row["code"]), dd)
                if key in CAP_OVERRIDE:
                    row["total_cap"] = CAP_OVERRIDE[key]
                rows.append(row)
        zt_hist[d] = rows
    return zt_hist, days


# ---------------------------------------------------------------- 核心：三类节点检测
def detect_nodes(zt_hist: dict, days: list) -> list:
    """返回节点列表（未做标签/状态标注）。逻辑与用户 fetch_data.detect_nodes 一致。"""
    nodes = []
    for i in range(1, len(days)):
        d_prev, d_today = days[i - 1], days[i]
        prev_pool = zt_hist.get(d_prev, [])
        today_pool = zt_hist.get(d_today, [])
        if not prev_pool or not today_pool:
            continue
        today_map = {s["code"]: s for s in today_pool}

        # ---- 1. 最高标断板节点 ----
        max_prev = max((s["lbc"] for s in prev_pool), default=0)
        if max_prev >= 2:
            tops_prev = [s for s in prev_pool if s["lbc"] == max_prev]
            any_advance = any(
                s["code"] in today_map and today_map[s["code"]]["lbc"] > max_prev
                for s in tops_prev
            )
            for top in tops_prev:
                if top["code"] in today_map:
                    continue  # 未断板
                if any_advance:
                    continue  # 同梯队有人晋级成功，失败方不单列断板节点
                v_start = find_start_volume(zt_hist, days, i - 1, top["code"])
                ratio = round(top["amount"] / v_start, 2) if (v_start and top.get("amount")) else None
                stocks = [s for s in today_pool if s["lbc"] == 1]
                if stocks:
                    nodes.append({
                        "id": "BREAK_%s_%s" % (d_today.replace("-", ""), top["code"]),
                        "type": "最高标断板节点",
                        "date": d_today, "trigger": top, "volume_ratio": ratio,
                        "pool_type": "首板", "stocks": stocks, "replaced": [],
                        "desc": "%s %s板断板（涨停日量/启动量=%s）→ 取当日首板" % (
                            top["name"], max_prev, ratio if ratio else "—"),
                    })

        prev_map = {s["code"]: s for s in prev_pool}

        # ---- 2/3. 突破节点 & 穿越节点（互斥：突破优先）----
        hist_max = 0
        for j in range(max(0, i - BREAKOUT_LOOKBACK), i):
            for s in zt_hist.get(days[j], []):
                hist_max = max(hist_max, s["lbc"])
        today_max = max((s["lbc"] for s in today_pool), default=0)
        first_boards = [s for s in today_pool if s["lbc"] == 1]
        top_codes = {s["code"] for s in today_pool if s["lbc"] == today_max}

        if hist_max > 0 and today_max > hist_max:
            bt = [s for s in today_pool if s["lbc"] == today_max]
            if first_boards:
                nodes.append({
                    "id": "BREAKOUT_%s" % d_today.replace("-", ""),
                    "type": "突破节点",
                    "date": d_today, "trigger": bt[0] if bt else None,
                    "volume_ratio": None, "pool_type": "首板", "stocks": first_boards,
                    "replaced": [],
                    "desc": "连板高度由%d板突破至%d板 → 取当日首板" % (hist_max, today_max),
                })
        elif max_prev >= 2 and today_max > max_prev:
            tops_today = [s for s in today_pool if s["lbc"] == today_max]
            crossed = [s for s in tops_today
                       if s["code"] in prev_map and prev_map[s["code"]]["lbc"] < s["lbc"]]
            # 🔴 穿越自己不产生新节点 —— 但「自己」指**这轮梯队里该天花板高度的首创者**。
            #   判法：从昨日往回走，只要当日最高板仍 ≥ max_prev（本轮梯队未塌），就把
            #   到达过该高度（≥max_prev）的票都记入 ceiling_setters；一塌（<max_prev）即停，
            #   更早旧周期的高度不算。setters 里除今天最高标外还有别人（新华文轩先到 5 板、
            #   新华传媒后到再破）→ 是跨别人的旗 → 出穿越节点；只有它自己（华瓷 5→6，
            #   5 板从头到尾自己爬的）→ 自己往上打，不出。
            prev_top_codes = {s["code"] for s in prev_pool if s["lbc"] == max_prev}
            ceiling_setters = set(prev_top_codes)
            for j in range(i - 1, max(0, i - BREAKOUT_LOOKBACK) - 1, -1):
                day_pool = zt_hist.get(days[j], [])
                day_max = max((s["lbc"] for s in day_pool), default=0)
                if day_max < max_prev:
                    break  # 本轮梯队在此塌到天花板以下
                ceiling_setters |= {s["code"] for s in day_pool if s["lbc"] >= max_prev}
            self_only = bool(ceiling_setters) and ceiling_setters == top_codes
            if crossed and first_boards and not self_only:
                trig = crossed[0]
                replaced = [s["code"] for s in prev_pool
                            if s["lbc"] == max_prev and s["code"] != trig["code"]
                            and s["code"] not in today_map]
                nodes.append({
                    "id": "CROSS_%s" % d_today.replace("-", ""),
                    "type": "穿越节点",
                    "date": d_today, "trigger": trig,
                    "volume_ratio": None, "pool_type": "首板", "stocks": first_boards,
                    "replaced": replaced,
                    "desc": "%s由%d板晋级%d板，跨过前期%d板天花板 → 取当日首板" % (
                        trig["name"], prev_map[trig["code"]]["lbc"], today_max, max_prev),
                })
    return nodes


def find_start_volume(zt_hist, days, idx, code):
    """从 idx 往前找该股本轮连板的启动首板（lbc==1）成交额。"""
    for j in range(idx, -1, -1):
        for s in zt_hist.get(days[j], []):
            if s["code"] == code and s["lbc"] == 1:
                return s["amount"]
    return None


def track_status(code, node_date, days_list, zt_hist, today_lbc, fallback_boards=0):
    """跟踪节点票从「节点日次日」到今天的涨停连续性，返回 (status, boards)。

    status ∈ {连板中, 首板, 断板反包, 已断板}
    """
    after = [d for d in days_list if d > node_date]
    seq = []
    for d in after:
        hit = None
        for x in zt_hist.get(d, []):
            if x["code"] == code:
                hit = x
                break
        seq.append(hit["lbc"] if hit else 0)

    if today_lbc:                       # 今天仍在涨停池
        if 0 not in seq:                # 一路连板未断
            return ("连板中" if today_lbc >= 2 else "首板"), today_lbc
        last_break = max(i for i, v in enumerate(seq) if v == 0)
        gap = len(seq) - 1 - last_break
        if gap <= 3:
            return "断板反包", today_lbc
        return ("连板中" if today_lbc >= 2 else "首板"), today_lbc
    last = 0
    for d in reversed(after):
        for x in zt_hist.get(d, []):
            if x["code"] == code:
                last = x["lbc"]
                break
        if last:
            break
    return "已断板", (last or fallback_boards)


def apply_focus(nodes_out):
    """节点聚焦：只看「当前最高标」的血统链（与用户 apply_focus 一致）。"""
    cur_top = None
    for n in nodes_out:
        if (n.get("trigger") or {}).get("code"):
            cur_top = n["trigger"]["code"]
            break
    top_code = cur_top

    chain_codes = set()
    if top_code:
        chain_codes.add(top_code)
        for n in nodes_out:
            trig = n.get("trigger") or {}
            if trig.get("code") == top_code:
                for rc in (n.get("replaced") or []):
                    chain_codes.add(rc)

    birth_date = None
    if chain_codes:
        bd = [n["date"] for n in nodes_out
              if (n.get("trigger") or {}).get("code") in chain_codes]
        if bd:
            birth_date = min(bd)

    for n in nodes_out:
        trig = n.get("trigger") or {}
        n["top_related"] = bool(top_code and birth_date and n["date"] >= birth_date
                                and trig.get("code") in chain_codes)
    return nodes_out


# ---------------------------------------------------------------- 标注辅助
def _annotate_trigger(trig, today_pool, tags):
    if not trig:
        return trig
    trig = dict(trig)
    cur = today_pool.get(trig.get("code"))
    trig["today_lbc"] = cur["lbc"] if cur else 0
    trig["total_cap_yi"] = round(trig["total_cap"] / 1e8, 2) if trig.get("total_cap") else None
    trig["amount_yi"] = round((trig.get("amount") or 0) / 1e8, 2)
    trig["is_yizi"] = is_yizi(trig.get("first_seal"), trig.get("last_seal"))
    trig.update(stock_tags.annotate(trig.get("code"), trig.get("name"), tags))
    return trig


def _annotate_stock(s, today_pool, node_date, days, zt_hist, tags):
    cur = today_pool.get(s["code"])
    today_lbc = cur["lbc"] if cur else 0
    status, boards = track_status(s["code"], node_date, days, zt_hist, today_lbc)
    row = {
        "code": s["code"], "name": s["name"],
        "lbc": s["lbc"], "boards": boards, "status": status,
        "amount_yi": round((s.get("amount") or 0) / 1e8, 2),
        "total_cap_yi": round(s["total_cap"] / 1e8, 2) if s.get("total_cap") else None,
        "sector": s.get("sector", ""),
        "is_yizi": is_yizi(s.get("first_seal"), s.get("last_seal")),
        "first_seal": s.get("first_seal"), "last_seal": s.get("last_seal"),
    }
    row.update(stock_tags.annotate(s["code"], s["name"], tags))
    return row


# ---------------------------------------------------------------- 对外入口
def _trigger_ohlc(code: str, start: str, end: str, cache_dir: str):
    """触发票 [start, end] 日K的开/收（腾讯 hist 源，与本仓库其他日K同源）。

    落盘永久缓存（历史事实不会变）。取数失败返回 None —— 上层如实标 unknown，
    绝不把"取不到"当成"没收阴"。
    """
    p = os.path.join(cache_dir, "kline_%s_%s_%s.json" % (code, start, end))
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as fh:
                rows = json.load(fh)
            if isinstance(rows, list):
                return rows
        except Exception:  # noqa: BLE001  坏缓存当没缓存
            pass
    try:
        import akshare as ak

        sym = ("sh" if str(code).startswith(("6", "9")) else "sz") + str(code).zfill(6)
        df = ak.stock_zh_a_hist_tx(symbol=sym, start_date=start.replace("-", ""),
                                   end_date=end.replace("-", ""))
        rows = [{"date": str(r["date"]), "open": float(r["open"]), "close": float(r["close"])}
                for _, r in df.iterrows()] if df is not None and len(df) else []
    except Exception:  # noqa: BLE001
        return None
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass
    return rows


def build_node_pool(end_date: Optional[str] = None, force: bool = False) -> dict:
    """生成节点票池。失败（数据不可用）时返回 available=False，不抛异常。"""
    if not end_date:
        try:
            ds = trade_calendar.trade_dates_ending_at(china_today(), 1)
            end_date = ds[-1] if ds else None
        except Exception:
            end_date = None
    if not end_date:
        return {"available": False, "reason": "无法确定交易日", "nodes": [], "meta": {}}

    cache_dir = data_path("cache/node_pool")
    os.makedirs(cache_dir, exist_ok=True)
    cpath = os.path.join(cache_dir, "%s.json" % end_date)
    if not force and cache_fresh(cpath, end_date):
        try:
            with open(cpath, encoding="utf-8") as fh:
                cached = json.load(fh)
            if isinstance(cached, dict) and cached.get("schema") == _NODE_SCHEMA:
                return cached
        except Exception:
            pass

    zt_hist, days = build_history(end_date)
    if not days:
        return {"available": False, "reason": "%s 前后无交易日" % end_date,
                "nodes": [], "meta": {"end_date": end_date}}

    raw_nodes = detect_nodes(zt_hist, days)
    today_pool = {s["code"]: s for s in zt_hist.get(end_date, [])}

    # ---- 标签（同花顺 F10：拼音缩写 / 地域 / 概念）一次性批量补，命中缓存不发请求 ----
    all_codes = []
    for n in raw_nodes:
        t = n.get("trigger") or {}
        if t.get("code"):
            all_codes.append(str(t["code"]))
        for s in n.get("stocks", []):
            all_codes.append(str(s["code"]))
    try:
        tags = stock_tags.enrich(all_codes)
    except Exception:  # noqa: BLE001  标签失败不拖垮节点池，只是标签为空
        tags = {}

    nodes = []
    for n in raw_nodes:
        # 市值门槛：按节点诞生日快照 total_cap；缺失则保留（不误删）
        stocks = [s for s in n.get("stocks", [])
                  if s.get("total_cap") is None or s["total_cap"] <= NODE_CAP_LIMIT]
        if not stocks:
            continue  # 剔除零连板 / 空节点
        annotated = [_annotate_stock(s, today_pool, n["date"], days, zt_hist, tags)
                     for s in stocks]
        n = dict(n)
        n["stocks"] = annotated
        n["trigger"] = _annotate_trigger(n.get("trigger"), today_pool, tags)
        nodes.append(n)

    # 触发票在节点日收阴（收<开）→ 该节点减分。取不到日K如实标 None，不当成没收阴。
    for n in nodes:
        trig = n.get("trigger") or {}
        n["trigger_yin"] = None
        if trig.get("code"):
            rows = _trigger_ohlc(str(trig["code"]), days[0], end_date, cache_dir)
            for r in rows or []:
                if str(r.get("date", "")).replace("-", "") == n["date"].replace("-", ""):
                    n["trigger_yin"] = bool(r["close"] < r["open"])
                    break
        if n["trigger_yin"]:
            n["penalty"] = "减分"
            n["desc"] = (n.get("desc") or "") + "｜触发票节点日收阴，减分"

    apply_focus(nodes)

    # 近 NODE_KEEP_DAYS 过滤
    keep_from = days[max(0, len(days) - NODE_KEEP_DAYS)] if days else None
    if keep_from:
        nodes = [n for n in nodes if n["date"] >= keep_from]

    # 已断板不再显示（数据层仍留 hidden 计数，便于核对"剔掉了多少"）
    if HIDE_BROKEN:
        for n in nodes:
            hidden = [s for s in n.get("stocks", []) if s.get("status") == "已断板"]
            n["broken_hidden"] = len(hidden)
            n["stocks"] = [s for s in n.get("stocks", []) if s.get("status") != "已断板"]
        # 沿用既有「空节点即删」口径：全断板的节点没有可看的票，不渲染空卡片
        nodes = [n for n in nodes if n.get("stocks")]

    nodes.sort(key=lambda n: n["date"], reverse=True)

    out = {
        "schema": _NODE_SCHEMA,
        "available": True,
        "end_date": end_date,
        "window_days": len(days),
        "node_count": len(nodes),
        "cap_limit_yi": NODE_CAP_LIMIT / 1e8,
        "keep_days": NODE_KEEP_DAYS,
        "nodes": nodes,
    }
    try:
        write_cache(cpath, out)
    except Exception:
        pass
    return out


if __name__ == "__main__":
    import sys
    _d = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(build_node_pool(_d, force=True), ensure_ascii=False, indent=2))
