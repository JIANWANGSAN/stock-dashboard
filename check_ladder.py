# -*- coding: utf-8 -*-
"""连板梯队回归检查 —— 自建梯队 vs 同花顺「连板梯队」接口，逐日对账。

背景（2026-09-21）：`fetch_zt_pool()` 曾从涨停池的 `high_days_value` 自己推连板数，
错了两层（取错半段 + 「N天M板」的 M 含断板），导致「连板高度梯队」折线长期偏高
—— 例如 09-17 把断板回封的众泰汽车（8天5板）画成当日最高 **8 板**，而真实最高只有 5 板。
详见 `项目约定.md` §9.2 第 12 条。

本脚本含**两道检查**：
  ① `check()`    — 自建梯队的 top/second 必须逐日等于 `continuous_limit_up` 的 height 前两项。
  ② `check_n_neq_m()` — 纯数学断言：「N天M板」且 **N ≠ M** 的票，连板数必须 ≤ **M-1**。
     推理：N 个交易日里只有 M 次涨停（N > M）→ 至少有一天没涨停 → M 次涨停被切成 ≥2 段
           → 任何一段的连续长度 ≤ M-1。**故 M（次数）和 N（天数）都不可能是连板数。**
     这条能**同时抓住「取 M」和「取 N」两种取值错误**，不依赖任何接口。
     实例：远望谷 002161 @2026-09-18「6天3板」→ 连板数必须 ≤ 2（真值 1；取 M 得 3、取 N 得 6）。

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

    return 1 if (bad or bad2) else 0


if __name__ == '__main__':
    sys.exit(main())
