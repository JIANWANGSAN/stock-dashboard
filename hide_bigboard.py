# -*- coding: utf-8 -*-
"""高标跟踪「已跌停 / 不再跟踪」票的全栈删除工具。

用法：
    python hide_bigboard.py --list                 # 只列出「已跌停（不再跟踪）」的候选，不写任何文件
    python hide_bigboard.py --hide-limit-down      # 把 data.json 里所有 status='limit_down' 的票写入黑名单
    python hide_bigboard.py --add 600865,002403    # 手工追加指定代码
    python hide_bigboard.py --remove 600865        # 手工移出黑名单（票又能回来了）
    python hide_bigboard.py --show                 # 显示当前黑名单

⚠️ 写黑名单后**必须重跑 fetch_data.py**（让 data.json 重新生成、把票真正剔掉）**并 push**，
   否则只是改了本地文件，线上/别的设备看不到。
⚠️ 这是唯一推荐的改黑名单方式 —— 手改 bigboards_hidden.json 很容易把键名写成 `hidden`（历史踩过），
   而 `load_hidden()` 只认 `codes` → 会**静默失效**。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import bigboards as BB  # noqa: E402

DATA_JSON = os.path.join(BASE, 'data.json')


def load_data() -> dict:
    """读 data.json（缺失/损坏 → 空字典）。"""
    try:
        with open(DATA_JSON, encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def iter_stocks(d: dict):
    """遍历 bigboards 里所有票，产出 (stock, theme)。"""
    for g in ((d.get('bigboards') or {}).get('groups') or []):
        for s in (g.get('stocks') or []):
            yield s, g.get('theme') or s.get('theme') or ''


def cmd_list() -> int:
    """列出「已跌停（不再跟踪）」的候选票（只读）。"""
    d = load_data()
    bb = d.get('bigboards') or {}
    rows = [(s, t) for s, t in iter_stocks(d) if s.get('status') == 'limit_down']
    print('data.json date = %s | 高标跟踪 stats = %s' % (d.get('date'), bb.get('stats')))
    if not rows:
        print('\n（当前没有 status=limit_down 的票）')
        return 0
    hidden = BB.load_hidden()
    print('\n已跌停（不再跟踪）共 %d 只：\n' % len(rows))
    print('  %-8s %-10s %-12s %-6s %-11s %s' % ('代码', '名称', '题材', '最高板', '跌停日', '已在本机黑名单'))
    for s, t in rows:
        print('  %-8s %-10s %-12s %-6s %-11s %s'
              % (s.get('code'), s.get('name'), t, s.get('max_lbc'),
                 s.get('ld_date') or '—', '是' if s.get('code') in hidden else '否'))
    return 0


def cmd_show() -> int:
    """显示当前黑名单。"""
    hidden = BB.load_hidden()
    print('黑名单文件：%s' % BB.HIDDEN_FILE)
    if not hidden:
        print('（空）')
        return 0
    print('共 %d 只：%s' % (len(hidden), '、'.join(sorted(hidden))))
    # 顺带核对：黑名单里的票是否确实已从 bigboards 消失
    still = {s.get('code') for s, _ in iter_stocks(load_data())}
    leaked = sorted(hidden & still)
    if leaked:
        print('⚠️  以下代码仍在 data.json 的 bigboards 里（需重跑 fetch_data.py）：%s' % '、'.join(leaked))
    else:
        print('✅ 黑名单里的票均已从 data.json 的 bigboards 中排除')
    return 0


def _write(codes: set, action: str) -> int:
    """写黑名单并提示后续动作。"""
    BB.save_hidden(codes)
    print('✅ %s → 黑名单现有 %d 只：%s' % (action, len(codes), '、'.join(sorted(codes)) or '（空）'))
    print('⚠️  下一步必须：① 重跑 `python fetch_data.py`；② `git commit` + `python push_api.py`')
    print('    不重跑 → data.json 里这些票还在；不 push → 别的设备和线上看不到删除。')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description='高标跟踪黑名单维护（全栈删除不再跟踪的票）')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--list', action='store_true', help='列出 status=limit_down 的候选票（只读）')
    g.add_argument('--show', action='store_true', help='显示当前黑名单')
    g.add_argument('--hide-limit-down', action='store_true', help='把所有已跌停的票写入黑名单')
    g.add_argument('--add', metavar='CODES', help='追加代码（逗号分隔）')
    g.add_argument('--remove', metavar='CODES', help='移出代码（逗号分隔）')
    a = ap.parse_args()

    if a.list:
        return cmd_list()
    if a.show:
        return cmd_show()

    cur = BB.load_hidden()
    if a.hide_limit_down:
        rows = [s.get('code') for s, _ in iter_stocks(load_data()) if s.get('status') == 'limit_down']
        if not rows:
            print('（当前没有 status=limit_down 的票，黑名单不变）')
            return 0
        return _write(cur | set(rows), '已把 %d 只已跌停票加入' % len(rows))
    if a.add:
        return _write(cur | {c.strip() for c in a.add.split(',') if c.strip()}, '已追加')
    if a.remove:
        return _write(cur - {c.strip() for c in a.remove.split(',') if c.strip()}, '已移出')
    return 0


if __name__ == '__main__':
    sys.exit(main())
