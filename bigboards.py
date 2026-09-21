# -*- coding: utf-8 -*-
"""高标跟踪（近半月 ≥4 连板）—— 按「当前最贴合」题材归类，跟到跌停为止。

用户需求（2026-09-21）：
  · 统计近半个月内曾达到 **≥4 连板** 的股票
  · 按「目前最贴合」的题材归类（用**当下板块热度**，不是历史涨停原因）
  · 持续跟踪：直到该票**跌停** → 标「跌停」并停止跟踪
  · ~~每票一个「删除」按钮~~ → ⛔ 已删（2026-09-21 用户第二次要求）

⚠️⚠️ **「不再跟踪」的准确含义（2026-09-21 用户澄清，务必按此理解）**：
  跌停只是让这只票**退出「高标跟踪」这张卡片**（停止跟踪、不再计入跟踪中）。
  它的数据在**别处一律照旧使用** —— 涨停梯队、连板统计、板块涨停家数、复盘文本等
  **该用到它的地方还要照常用**。**所以这里绝不能做任何「永久排除 / 删除」**：
    · 本模块只负责「在 bigboards 这张卡里怎么展示」，**不产生任何跨模块的删除副作用**；
    · `status='limit_down'` 是**展示状态**，不是「把这票删掉」的指令。
  → ⛔ 曾有一段「黑名单机制」（`bigboards_hidden.json` / `load_hidden` / `save_hidden` /
    `hide_bigboard.py`），**已按用户要求整体删除** —— 它把「停止跟踪」错做成了「全栈删除」。
    **不要再以任何形式把它加回来。**

设计要点：
  · **零额外请求**：数据全部来自 `fetch_data.py` 运行时的 zt_hist / dt_hist
    （zt_hist 不落盘，所以本模块只能由 fetch_data 调用）
  · 「≥4 连板」= **窗口内曾达到**（`max_lbc >= 4`），不是「当前正好 4 连板」
  · 跌停是**终态**，口径 = 「**最后一次涨停之后**出现过跌停」——
    这样「跌停 → 又起新一波」不会被上一轮的跌停误判为终止
"""
from __future__ import annotations

from datetime import datetime

BIG_DAYS = 10        # 近半月 ≈ 10 个交易日（与 NODE_KEEP_DAYS 同口径）
BIG_MIN_LBC = 4      # 收录门槛：窗口内曾达到 4 连板及以上


def build_bigboards(zt_hist, days, dt_hist, theme_of=None, now=None):
    """构造「高标跟踪」数据（近 BIG_DAYS 个交易日内曾 ≥BIG_MIN_LBC 连板的个股）。

    Args:
        zt_hist: {YYYY-MM-DD: [stock, ...]} 涨停池；stock 含
                 code / name / market / lbc / price / concepts / industry
        days:    交易日列表（**升序**，YYYY-MM-DD）
        dt_hist: {YYYY-MM-DD: [stock, ...]} 跌停池；stock 含 code / chg
        theme_of: 可选回调 `(code, market, fallback_concepts, industry) -> str`，
                 由调用方注入「按当下板块热度排序取第一个」的逻辑
                 （排序依赖 fetch_data 内部的 BOARD_HEAT，故不在此实现）
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

    # ---- ③ 过滤出门槛 + 判定**展示状态** ----
    # ⚠️ 这里只判断「要不要收进这张卡片」和「卡片上标什么」——
    #    `limit_down` 不产生任何删除 / 排除副作用（跌停票在别处照常用），见文件头说明。
    picked = []
    for code, r in rec.items():
        if r['max_lbc'] < BIG_MIN_LBC:
            continue                        # 没到过 4 板 → 不收录
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

    return {
        'days': len(win), 'min_lbc': BIG_MIN_LBC,
        'date_from': win[0], 'date_to': win[-1],
        'updated': now or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'stats': stats, 'groups': groups,
    }
