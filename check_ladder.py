# -*- coding: utf-8 -*-
"""连板梯队回归检查 —— 自建梯队 vs 同花顺「连板梯队」接口，逐日对账。

背景（2026-09-21）：`fetch_zt_pool()` 曾从涨停池的 `high_days_value` 自己推连板数，
错了两层（取错半段 + 「N天M板」的 M 含断板），导致「连板高度梯队」折线长期偏高
—— 例如 09-17 把断板回封的众泰汽车（8天5板）画成当日最高 **8 板**，而真实最高只有 5 板。
详见 `项目约定.md` §9.2 第 12 条。

本脚本含**三道检查**：
  ① `check()`    — 自建梯队的 top/second 必须逐日等于 `continuous_limit_up` 的 height 前两项。
  ② `check_n_neq_m()` — 纯数学断言：「N天M板」且 **N ≠ M** 的票，连板数必须 ≤ **M-1**。
     推理：N 个交易日里只有 M 次涨停（N > M）→ 至少有一天没涨停 → M 次涨停被切成 ≥2 段
           → 任何一段的连续长度 ≤ M-1。**故 M（次数）和 N（天数）都不可能是连板数。**
     这条能**同时抓住「取 M」和「取 N」两种取值错误**，不依赖任何接口。
     实例：远望谷 002161 @2026-09-18「6天3板」→ 连板数必须 ≤ 2（真值 1；取 M 得 3、取 N 得 6）。
  ③ `check_candidates()` — 候选池**入池先决条件**：每只票的 `boards` 必须 ≥ 2。
     「连板候选池」的票必须**前一交易日也涨停**（⟺ 连板数 ≥ 2，见 `项目约定.md` §3.6）。
     2026-09-22 用户第二次报此错：池里混进一批首板（1 板）/ 断板反包票。

**改任何与连板数相关的代码后都该跑一次。**

⚠️ 为什么必须挑「N ≠ M」的样本才会暴露：像闽东电力「6天6板」这种 N == M 的票，
无论取高 16 位还是低 16 位结果都一样 —— 正是它掩盖了这个 bug 好几天。

用法：
    PY="D:/软件/WorkBuddytwo/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
    "$PY" check_ladder.py            # 默认查最近 10 个交易日
    "$PY" check_ladder.py --days 20  # 查最近 20 个

退出码：0 = 全部通过；1 = 有不一致（可接 CI）。
"""
import argparse
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

import ths_source as THS


def check(days: int, end: str = None, verbose: bool = False) -> int:
    """逐日对账。

    Args:
        days: 检查最近多少个交易日。
        end: 截止日 `YYYYMMDD`（含）；None = 用最新交易日。
        verbose: 不一致时打印各档成员，便于定位。

    Returns:
        不一致的天数（0 表示全部通过）。
    """
    td = [d.replace('-', '') for d in THS.fetch_trade_days(limit=days + 10)]
    if end:
        td = [d for d in td if d <= end]
    td = td[-days:]

    print('对账区间：%s ~ %s（%d 个交易日）' % (td[0], td[-1], len(td)))
    print()
    print('%-12s %-16s %-16s %s' % ('日期', '自建(最高/次高)', '同花顺 height', '一致?'))
    bad = 0
    for d in td:
        pool, _ = THS.fetch_zt_pool(d)
        lbcs = sorted({s['lbc'] for s in pool}, reverse=True)
        mine = (lbcs[0] if lbcs else 0, lbcs[1] if len(lbcs) > 1 else 0)

        theirs = sorted({g['height'] for g in THS.fetch_lianban_ladder(d)}, reverse=True)
        ok = list(mine) == theirs[:2]
        if not ok:
            bad += 1
        # 格式化日期便于阅读
        pretty = '%s-%s-%s' % (d[:4], d[4:6], d[6:])
        print('%-12s %-16s %-16s %s' % (pretty, mine, theirs, '✅' if ok else '❌'))
        if not ok and verbose:
            for h in lbcs[:3]:
                print('        自建 %s 板：%s'
                      % (h, '、'.join(s['name'] for s in pool if s['lbc'] == h)))
            for g in THS.fetch_lianban_ladder(d)[:3]:
                print('        同花顺 %s 板：%s'
                      % (g['height'], '、'.join(x['name'] for x in g['stocks'])))

    print()
    if bad:
        print('❌ 有 %d/%d 天不一致 —— 连板数解析有问题，先看 `项目约定.md` §9.2 第 12 条' % (bad, len(td)))
    else:
        print('✅ 全部一致（%d/%d）' % (len(td), len(td)))
    return bad


def check_n_neq_m(td, verbose: bool = False) -> int:
    """专项断言：「N天M板」且 N ≠ M 的票，连板数必须 ≤ M-1。

    纯数学推理，**不依赖任何接口**（见模块 docstring）：
        N 个交易日里只有 M 次涨停（N > M）→ 至少一天没涨停 → M 次涨停分成 ≥2 段
        → 最长连续段 ≤ M-1。
    因此：
        · 若代码取 **M**（`high_days_value >> 16`）→ lbc = M > M-1 → ❌ 被抓
        · 若代码取 **N**（`high_days_value & 0xFFFF`）→ lbc = N > M-1 → ❌ 被抓
        · 正确口径（`continuous_limit_up` / 历史池自算）→ lbc ≤ M-1 → ✅

    Args:
        td: 交易日列表（`YYYYMMDD`）。
        verbose: 通过时也打印部分样本（便于确认断言真的在跑）。

    Returns:
        违反断言的票数（0 表示通过）。
    """
    bad = checked = skipped_boundary = 0
    for d in td:
        pool, _ = THS.fetch_zt_pool(d)
        for s in pool:
            m = re.match(r'(\d+)天(\d+)板', s.get('high_days') or '')
            if not m:
                continue
            n_days, m_boards = int(m.group(1)), int(m.group(2))
            if n_days == m_boards:
                # N == M → 最近 N 天全部涨停 → 连板数恰为 M，无从判别（正是它掩盖过 bug）
                skipped_boundary += 1
                continue
            checked += 1
            # 涨停池的历史窗口可能短于 high_days 的 N 天，此时若 lbc 一路连到窗口边界，
            # 上界仍成立（N>M 必有中断），故不需要额外放宽 —— 保留严格判定。
            if s['lbc'] > m_boards - 1:
                bad += 1
                if bad <= 10 or verbose:
                    print('   ❌ %s %s(%s) high_days=%s → lbc=%s，上界应为 %s'
                          % (d, s['name'], s['code'], s['high_days'], s['lbc'], m_boards - 1))
            elif verbose and checked <= 5:
                print('   ✅ %s %s(%s) high_days=%s → lbc=%s ≤ %s'
                      % (d, s['name'], s['code'], s['high_days'], s['lbc'], m_boards - 1))
    print('N≠M 上界断言：检查 %d 只（N==M 的 %d 只已跳过）→ %s'
          % (checked, skipped_boundary, '全部通过 ✅' if not bad else '违反 %d 只 ❌' % bad))
    return bad


def _candidate_base_date(d):
    """候选池的**基准交易日**（YYYYMMDD 字符串）。

    ⚠️ 候选池是「盘前」任务生成的，基准 = **生成日的前一个交易日**的涨停池
       （premarket.py 在 T 日 09:26 读的 data.json 还是 T-1 收盘版，见 §2 / §3.6b）。
    所以**不能拿 `data.date` 去比对**：15:05 盘后重建后 `data.date` 已前进到 T，
    而候选池仍基于 T-1 → 会造成 8/12 "不在当日涨停池" 的**假违规**（2026-09-22 实测踩到）。

    判据：以 `premarket_updated`（其次 `updated`）的日期为「生成日」，取梯队日期里 < 生成日的最后一天。
    """
    days = sorted({str(s.get('date', '')).replace('-', '')
                   for s in (d.get('ladder') or []) if s.get('date')})
    stamp = str(d.get('premarket_updated') or d.get('updated') or '')
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', stamp)
    if m and days:
        gen = '%s%s%s' % m.groups()
        prevs = [x for x in days if x < gen]
        if prevs:
            return prevs[-1]
    cur = str(d.get('date') or '').replace('-', '')
    prevs = [x for x in days if x < cur]
    return prevs[-1] if prevs else cur


def check_candidates(verbose: bool = False) -> int:
    """第三道检查：候选池的每只票 `boards` 必须 ≥ 2，且在该基准日涨停池里、池内 `lbc` ≥ 2。

    「连板候选池」的语义是「次日可能继续连板」→ 前提是**基准日已经是连板**，
    即连板数 ≥ 2（⟺ 再前一交易日也涨停，见 §3.6b 口径）。
    若池里出现 `boards < 2` 的票（首板 / 断板反包），说明 `build_candidates` 的
    分支过滤被改坏（2026-09-22 的真实事故：只看 `status`、没校验 `boards`）。

    ⚠️ 比对用的涨停池是**基准日**（盘前生成日的前一交易日）的池，不是 `data.date` 当日的池
       —— 详见 `_candidate_base_date()`。池取不到（网络）时降级为只断言 `boards ≥ 2`。

    Returns:
        违规条数（0 表示通过）。
    """
    import json
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, 'data.json')
    if not os.path.exists(p):
        print('候选池检查：跳过（找不到 data.json）')
        return 0
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    cands = d.get('candidates') or []
    if not cands:
        print('候选池检查：跳过（data.json 无 candidates）')
        return 0

    base = _candidate_base_date(d)
    zt, pool_ok = {}, False
    try:
        pool, _stat = THS.fetch_zt_pool(base)
        zt = {s.get('code'): s for s in (pool or [])}
        pool_ok = bool(zt)
    except Exception as e:
        print('   候选池检查：基准日 %s 涨停池取数失败（%s）→ 降级为只断言 boards ≥ 2' % (base, e))

    bad = 0
    print('候选池检查：%d 只（基准日 %s，data.date=%s%s）'
          % (len(cands), base, d.get('date'), '' if pool_ok else '，池未取到'))
    for c in cands:
        b = c.get('boards')
        ok_boards = isinstance(b, int) and b >= 2
        in_pool = (c.get('code') in zt) if pool_ok else None
        # 在池内时，用池里的权威 lbc 再核一遍（防节点里存的旧值）
        lbc_now = (zt.get(c.get('code')) or {}).get('lbc') if pool_ok else None
        ok_lbc = (not pool_ok) or (lbc_now is None) or (isinstance(lbc_now, int) and lbc_now >= 2)
        ok_pool = (not pool_ok) or in_pool
        if not (ok_boards and ok_pool and ok_lbc):
            bad += 1
            print('   ❌ %s(%s) boards=%s 在基准日涨停池=%s 池内lbc=%s'
                  % (c.get('name'), c.get('code'), b, in_pool, lbc_now))
        elif verbose:
            print('   ✅ %s(%s) boards=%s%s'
                  % (c.get('name'), c.get('code'), b, '' if not pool_ok else ' 池内lbc=%s' % lbc_now))
    print('候选池入池门槛：%d 只 → %s'
          % (len(cands), '全部满足 boards ≥ 2 ✅' if not bad else '违规 %d 只 ❌' % bad))
    return bad


def check_node_cap_gate(days, verbose=False):
    """第四道：节点票池的「≤200 亿市值」门槛**必须按节点诞生当日市值判定**。

    2026-09-23 修的真实 bug：原写法用「今日市值」过滤节点池，把
    「节点日合格、之后涨上去」的票**追溯错杀** ——
      · 新华文轩 09-18 市值 171.5 亿（合格）→ 09-23 涨到 228.3 亿
      → 09-18 节点池被误删，用户从图上发现「09-18 节点少了 4 板的新华文轩」。
    本检查：对每个节点，用**节点日涨停池**重算「合格票」（节点日市值 ≤ 200 亿 且
    属于该节点票池类型：断板/突破/穿越节点均为当日首板 lbc==1），
    与 data.json 里的池内票比对，报出「该在但不在」的漏票。

    返回不一致的节点数（0 = 全对）。
    """
    import json as _json
    LIMIT = 200 * 1e8
    NODE_CAP = 200.0
    try:
        with open('data.json', encoding='utf-8') as f:
            d = _json.load(f)
    except Exception as e:
        print('  [skip] 读不到 data.json：%s' % e)
        return 0

    nodes = d.get('nodes') or []
    if not nodes:
        print('  [skip] data.json 无节点')
        return 0

    bad = 0
    total_lost = 0
    for n in nodes:
        dt = n['date']
        dtc = dt.replace('-', '')
        pool, _ = THS.fetch_zt_pool(dtc)
        if not pool:
            continue
        # 节点票池口径：全部节点类型的池子都是「当日首板」（见 detect_nodes）
        cand = {s['code']: s for s in pool if s['lbc'] == 1}
        # 节点日市值合格者
        ok = {c: s for c, s in cand.items()
              if (s.get('total_cap') or 0) and (s['total_cap'] / 1e8) <= NODE_CAP}
        inpool = {s['code'] for s in n.get('stocks', [])}
        missing = {c: s for c, s in ok.items() if c not in inpool}
        if missing:
            bad += 1
            total_lost += len(missing)
            print('  ❌ [%s] %s 漏掉 %d 只节点日合格的票：' % (dt, n['type'], len(missing)))
            for c, s in sorted(missing.items(), key=lambda kv: kv[1]['name']):
                cur = next((x for x in pool if x['code'] == c), None)
                print('       %-10s %s  节点日市值=%.1f 亿（今日若涨过 200 亿会被旧写法误杀）'
                      % (s['name'], c, s['total_cap'] / 1e8))

    if not bad:
        print('  节点池市值门槛：%d 个节点全部按「节点日市值」判定 ✅' % len(nodes))
    else:
        print('  ⛔ %d 个节点存在漏票，合计 %d 只 —— 检查 fetch_data.py 的 cap_gate 是否用了今日市值'
              % (bad, total_lost))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description='连板梯队回归检查')
    ap.add_argument('--days', type=int, default=10, help='检查最近多少个交易日（默认 10）')
    ap.add_argument('--end', default=None, help='截止日 YYYYMMDD（默认最新交易日）')
    ap.add_argument('-v', '--verbose', action='store_true', help='不一致时打印各档成员')
    a = ap.parse_args()

    bad = check(a.days, a.end, a.verbose)

    # 交易日列表（与 check 同口径）
    td = [d.replace('-', '') for d in THS.fetch_trade_days(limit=a.days + 10)]
    if a.end:
        td = [d for d in td if d <= a.end]
    td = td[-a.days:]

    print()
    print('── 第二道检查：N≠M 上界断言 ──')
    bad2 = check_n_neq_m(td, a.verbose)

    print()
    print('── 第三道检查：候选池入池门槛（boards ≥ 2）──')
    bad3 = check_candidates(a.verbose)

    print()
    print('── 第四道检查：节点池「市值门槛」须按节点日判定 ──')
    bad4 = check_node_cap_gate(td, a.verbose)

    return 1 if (bad or bad2 or bad3 or bad4) else 0


if __name__ == '__main__':
    sys.exit(main())
