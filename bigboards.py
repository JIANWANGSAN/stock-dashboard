# -*- coding: utf-8 -*-
"""高标跟踪（近半月 ≥4 连板）—— 按「当前最贴合」题材归类，跟到跌停为止。

用户需求（2026-09-21）：
  · 统计近半个月内曾达到 **≥4 连板** 的股票
  · 按「目前最贴合」的题材归类（用**当下板块热度**，不是历史涨停原因）
  · 持续跟踪：直到该票**跌停** → 标「跌停」并停止跟踪
  · 每票一个「删除」按钮 → 删掉该票的全部跟踪数据

设计要点：
  · **零额外请求**：数据全部来自 `fetch_data.py` 运行时的 zt_hist / dt_hist
    （zt_hist 不落盘，所以本模块只能由 fetch_data 调用）
  · 「≥4 连板」= **窗口内曾达到**（`max_lbc >= 4`），不是「当前正好 4 连板」
  · 跌停是**终态**，口径 = 「**最后一次涨停之后**出现过跌停」——
    这样「跌停 → 又起新一波」不会被上一轮的跌停误判为终止
  · 删除 = 写 `bigboards_hidden.json` 黑名单 → **生成时直接排除**（真正全栈删除：
    跨设备一致，且不会明天又被自动加回来）
"""
from __future__ import annotations

import json
import os
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
HIDDEN_FILE = os.path.join(BASE, 'bigboards_hidden.json')

# 黑名单文件里的说明字段 —— `save_hidden()` 必须原样保留，否则重跑一次就把人看的说明抹掉了
_HIDDEN_NOTE = ('高标跟踪的后端删除黑名单（codes = 证券代码，升序）。此文件入库 → 重跑 fetch_data.py '
                '并 push 后，所有设备都会永久排除这些票。前端「删除」按钮只写本机 localStorage'
                '(bb_deleted_v1)，换设备会重现，故真正的「全栈删除」必须走本文件。')

BIG_DAYS = 10        # 近半月 ≈ 10 个交易日（与 NODE_KEEP_DAYS 同口径）
BIG_MIN_LBC = 4      # 收录门槛：窗口内曾达到 4 连板及以上


def load_hidden() -> set:
    """读取「已删除」黑名单（证券代码集合）。

    ⚠️ 键名是 **`codes`**（与 `save_hidden()` 写入的一致）。历史上有一次误写成 `hidden`
       → 黑名单**静默失效**（读不到 → 票又冒出来，且不报错）。改这里务必同步改 `save_hidden()`。

    Returns:
        set[str]: 代码集合；文件不存在 / 解析失败一律返回空集（绝不让管线挂掉）。
    """
    try:
        with open(HIDDEN_FILE, encoding='utf-8') as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return set()
        # 兼容两种键名（`codes` 为准，`hidden` 为历史误写），任一命中即生效
        raw = d.get('codes')
        if raw is None:
            raw = d.get('hidden')
        return {str(c).strip() for c in (raw or []) if str(c).strip()}
    except Exception:
        return set()


def save_hidden(codes) -> None:
    """写回黑名单（代码升序 + 更新时间 + 保留说明，便于人工审阅 git diff）。

    Args:
        codes: 可迭代的证券代码。写入后，这些票在下次生成时会被完全排除。
    """
    out = {'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
           'codes': sorted({str(c).strip() for c in codes if str(c).strip()}),
           '_note': _HIDDEN_NOTE}
    with open(HIDDEN_FILE, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write('\n')


def build_bigboards(zt_hist, days, dt_hist, theme_of=None, hidden=None, now=None):
    """构造「高标跟踪」数据（近 BIG_DAYS 个交易日内曾 ≥BIG_MIN_LBC 连板的个股）。

    Args:
        zt_hist: {YYYY-MM-DD: [stock, ...]} 涨停池；stock 含
                 code / name / market / lbc / price / concepts / industry
        days:    交易日列表（**升序**，YYYY-MM-DD）
        dt_hist: {YYYY-MM-DD: [stock, ...]} 跌停池；stock 含 code / chg
        theme_of: 可选回调 `(code, market, fallback_concepts, industry) -> str`，
                 由调用方注入「按当下板块热度排序取第一个」的逻辑
                 （排序依赖 fetch_data 内部的 BOARD_HEAT，故不在此实现）
        hidden:  已删除的代码集合；None → 自动读 `bigboards_hidden.json`
        now:     更新时间字符串；默认取当前时间

    Returns:
        dict: {
            'days', 'min_lbc', 'date_from', 'date_to', 'updated',
            'stats': {'total', 'tracking', 'limit_down'},
            'groups': [{'theme', 'n', 'tracking', 'max_lbc',
                        'stocks': [{'code','name','market','theme','max_lbc','cur_lbc',
                                    'first_big','last_zt','last_price','status',
                                    'ld_date','ld_chg','concepts','industry','lbc_track'}]}],
        }
        无数据时 `groups` 为空列表。
    """
    days = [str(d) for d in (days or [])]
    empty = {'days': BIG_DAYS, 'min_lbc': BIG_MIN_LBC, 'groups': [],
             'stats': {'total': 0, 'tracking': 0, 'limit_down': 0}}
    if not days or not zt_hist:
        return empty

    hidden = load_hidden() if hidden is None else {str(c).strip() for c in hidden}
    win = days[-BIG_DAYS:]          # 近半月窗口（不足则全用）

    # ---- ① 汇总窗口内每只票的连板轨迹 ----
    rec = {}
    for d in win:
        for s in (zt_hist.get(d) or []):
            code = s.get('code') or ''
            if not code:
                continue
            r = rec.get(code)
            if r is None:
                r = rec[code] = {
                    'code': code, 'name': s.get('name') or '',
                    'market': s.get('market', 0),
                    'max_lbc': 0, 'first_big': None, 'last_zt': None,
                    'last_price': None, 'concepts': [], 'industry': '',
                    'lbc_track': [],
                }
            lbc = int(s.get('lbc') or 0)
            r['lbc_track'].append([d, lbc])
            if lbc > r['max_lbc']:
                r['max_lbc'] = lbc
            if lbc >= BIG_MIN_LBC and r['first_big'] is None:
                r['first_big'] = d      # 首次达到门槛的日期（= 本轮高标的起点）
            r['last_zt'] = d
            r['last_price'] = s.get('price')
            # 概念 / 行业一律取**最后一次涨停那天**的 —— 「目前最贴合」要用最新的炒作原因，
            # 而不是窗口首日那次的（同票每一波可能炒不同题材）。win 是升序，覆盖赋值即最新。
            r['concepts'] = list(s.get('concepts') or []) or r['concepts']
            r['industry'] = s.get('industry') or r['industry']

    # ---- ② 跌停池：每票在窗口内的「最后一次跌停」 ----
    last_ld = {}
    for d in win:
        for s in (dt_hist.get(d) or []):
            last_ld[s.get('code')] = (d, s.get('chg'))

    # ---- ③ 过滤出门槛 + 判定跟踪状态 ----
    picked = []
    for code, r in rec.items():
        if r['max_lbc'] < BIG_MIN_LBC:
            continue                        # 没到过 4 板 → 不收录
        if code in hidden:
            continue                        # 用户已删除 → 全栈排除（不会再回来）
        hit = last_ld.get(code)
        status, ld_date, ld_chg = 'tracking', None, None
        # 跌停必须发生在「最后一次涨停之后」才算这轮终止（防止上一轮的跌停误杀新一波）
        if hit and r['last_zt'] and hit[0] > r['last_zt']:
            status, ld_date, ld_chg = 'limit_down', hit[0], hit[1]
        r['status'], r['ld_date'], r['ld_chg'] = status, ld_date, ld_chg
        # 当前连板：只有窗口末日仍在涨停池才算「还在走」
        r['cur_lbc'] = r['lbc_track'][-1][1] if r['last_zt'] == win[-1] else 0
        picked.append(r)

    # ---- ④ 归类：当前最贴合题材 ----
    for r in picked:
        if theme_of:
            r['theme'] = theme_of(r['code'], r['market'], r['concepts'], r['industry']) or '其他'
        else:
            r['theme'] = (r['concepts'] or [r['industry'] or '其他'])[0]

    # ---- ⑤ 按题材分组 + 排序 ----
    gmap = {}
    for r in picked:
        gmap.setdefault(r['theme'], []).append(r)
    groups = []
    for theme, ss in gmap.items():
        # 稳定排序两次：先「最近涨停降序」，再「活口优先 + 最高板降序」
        ss.sort(key=lambda x: x['last_zt'] or '', reverse=True)
        ss.sort(key=lambda x: (x['status'] == 'limit_down', -x['max_lbc']))
        groups.append({
            'theme': theme, 'n': len(ss),
            'tracking': sum(1 for s in ss if s['status'] == 'tracking'),
            'max_lbc': max(s['max_lbc'] for s in ss),
            'stocks': ss,
        })
    # 组间：有活口的组优先 → 组内最高板降序 → 票数降序
    groups.sort(key=lambda g: (g['tracking'] > 0, g['max_lbc'], g['n']), reverse=True)

    stats = {'total': len(picked),
             'tracking': sum(1 for r in picked if r['status'] == 'tracking'),
             'limit_down': sum(1 for r in picked if r['status'] == 'limit_down')}
    print('  [高标跟踪] 近%d日 ≥%d板：%d 只（跟踪中 %d · 跌停 %d）→ 归入 %d 个题材'
          % (len(win), BIG_MIN_LBC, stats['total'], stats['tracking'],
             stats['limit_down'], len(groups)))
    if hidden:
        print('     （黑名单已排除 %d 只）' % len(hidden))

    return {
        'days': len(win), 'min_lbc': BIG_MIN_LBC,
        'date_from': win[0], 'date_to': win[-1],
        'updated': now or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'stats': stats, 'groups': groups,
    }
