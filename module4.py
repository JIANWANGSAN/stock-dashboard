# -*- coding: utf-8 -*-
"""
模块4 · 均线共振动态股票池

逻辑：
  1. 取模块3「两榜重合板块」（board_daily ∩ board_3d，即带★的持续性方向）
  2. 取这些板块的成分股（主源：东方财富板块成分股；兜底：本池已有涨停/连板票按题材匹配）
  3. 对每只成分股拉日K（腾讯源，稳定），算 MA5/MA10/MA20，筛选：
        - MA5 > MA10 > MA20            （多头排列，顺序正确）
        - MA5_t>MA5_{t-1} 且 MA10_t>MA10_{t-1} 且 MA20_t>MA20_{t-1}  （三线斜率向上）
        - (MA5-MA10) >= (MA5-MA10)_{t-1} 且 (MA10-MA20) >= (MA10-MA20)_{t-1}  （向上发散，差值扩大）
        - 收盘价 > MA20                 （站上20日线）
  4. 动态股票池：每日增量更新，符合条件进、不符出，记录新增/剔除
  5. 客观评分排序 + 买入策略框架（非收益承诺）

依赖：fetch_data.py 的 http_get / load_json / save_json / save_js / BASE / DATA_JSON / theme_of
用法：python module4.py     # 依赖 data.json 中已有的 board_daily / board_3d
"""
import os, json, time, re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from fetch_data import (BASE, DATA_JSON, http_get, load_json, save_json, save_js, theme_of)

MODULE4_POOL = os.path.join(BASE, 'module4_pool.json')
KLINE_CACHE  = os.path.join(BASE, '.module4_kline_cache.json')

# 妙想（东方财富连接器）成分股缓存：{日期: {妙想板块名: [[code,name], ...]}}
# 由每日自动化运行时 AI 调用 mx_index_block_finance_data 填充，Python 侧只读
MX_MEMBERS  = os.path.join(BASE, '.mx_members_cache.json')

# 腾讯板块名(模块3口径) -> 妙想板块名(东财口径)。两套命名体系不同，需显式映射。
# 已实测：妙想有「猪肉概念」「CPO概念」「PCB」；无「覆铜板」「博通概念」「光芯片」「谷歌概念」
MX_NAME_MAP = {
    '覆铜板': 'PCB', 'PCB概念': 'PCB', '覆铜板概念': 'PCB',
    '光芯片': 'CPO概念', '光芯片概念': 'CPO概念',
    '博通概念': 'CPO概念', '博通': 'CPO概念',
    '谷歌概念': 'CPO概念', '谷歌': 'CPO概念',
    '共封装光模块(CPO）': 'CPO概念', '共封装光模块(CPO)': 'CPO概念',
    'CPO概念': 'CPO概念', '光通信': 'CPO概念', '光模块': 'CPO概念',
    '猪肉': '猪肉概念', '猪肉概念': '猪肉概念',
}

# 腾讯板块名 -> 东财实际存在的板块名（东财列表里查不到原名时的兜底别名）
# 已实测：东财无「白糖」「玉米」「燃料乙醇」「光芯片」「博通概念」，命中不了就跳过，不重试
EM_ALIAS = {
    '棉花': '棉纺', '棉花概念': '棉纺', '棉纺': '棉纺',
    '玉米': '粮食种植', '玉米概念': '粮食种植',
    '光芯片': 'CPO', '博通概念': 'CPO', '谷歌概念': 'CPO', '光通信': '光通信模块',
    '覆铜板': 'PCB', 'PCB概念': 'PCB',
    '猪肉': '猪肉概念',
}

TODAY = datetime.now().strftime('%Y-%m-%d')

# 东财 push2 主机冗余：部分网络环境下 push2.eastmoney.com 会 RemoteDisconnected，
# push2delay.eastmoney.com 可正常返回，逐个尝试直到成功。
EM_HOSTS = ['push2.eastmoney.com', 'push2delay.eastmoney.com', '82.push2.eastmoney.com']


def em_get(path, timeout=15):
    """东财接口请求，带主机冗余。path 形如 /api/qt/clist/get?..."""
    for host in EM_HOSTS:
        t = http_get('https://%s%s' % (host, path), timeout=timeout, retry=1, silent=True)
        if t:
            return t
    return None


def load_mx_members(board_name, date_str=None):
    """读妙想成分股缓存。返回 [[code, name], ...]，未命中返回 []"""
    cache = load_json(MX_MEMBERS, {})
    if not isinstance(cache, dict):
        return []
    day = cache.get(date_str or TODAY) or cache.get('latest') or {}
    if not isinstance(day, dict):
        return []
    hit = day.get(board_name) or day.get(MX_NAME_MAP.get(board_name, ''))
    if not hit:
        # 归一化后再试一轮（去「概念/板块」等后缀）
        nb = norm_board(board_name)
        for k, v in day.items():
            if norm_board(k) == nb:
                hit = v
                break
    if not hit:
        return []
    out = []
    for it in hit:
        if isinstance(it, (list, tuple)) and len(it) >= 2:
            out.append((str(it[0]), str(it[1])))
        elif isinstance(it, dict):
            out.append((str(it.get('code', '')), str(it.get('name', ''))))
    return out


# ---------------- 板块名归一化 / 东财映射 ----------------
def norm_board(name):
    if not name:
        return ''
    return re.sub(r'(概念|板块|行业|产业|指数|股)$', '', name.strip())


def build_em_board_map():
    """东方财富 概念(t:3) + 行业(t:2) 板块列表 → {归一名: code, 原名: code}（分页并发拉取）

    并发不影响结果：先并发取各页，再按「原遍历顺序」合并，保持后者覆盖前者的语义。
    """
    jobs = [(fs, pn) for fs in ('m:90+t:3+f:!50', 'm:90+t:2+f:!50') for pn in range(1, 13)]

    def _page(job):
        fs, pn = job
        path = ('/api/qt/clist/get?pn=%d&pz=100&po=1&np=1'
                '&fltt=2&invt=2&fid=f3&fs=%s&fields=f12,f14' % (pn, fs))
        t = em_get(path)
        if not t:
            return []
        try:
            diff = (json.loads(t).get('data') or {}).get('diff') or []
        except Exception:
            return []
        return [(it.get('f12'), it.get('f14')) for it in diff]

    with ThreadPoolExecutor(max_workers=6) as ex:
        pages = list(ex.map(_page, jobs))

    m = {}
    for rows in pages:
        for c, n in rows:
            if c and n:
                m[n] = c
                m[norm_board(n)] = c
    return m


def match_em_code(name, em_map):
    if name in em_map:
        return em_map[name]
    n = norm_board(name)
    if n and n in em_map:
        return em_map[n]
    alias = EM_ALIAS.get(name) or EM_ALIAS.get(n)
    if alias:
        if alias in em_map:
            return em_map[alias]
        na = norm_board(alias)
        if na and na in em_map:
            return em_map[na]
    for k, v in em_map.items():
        if len(k) >= 2 and (k in name or name in k):
            return v
    return None


def fetch_board_members_em(code):
    """东财板块成分股 → [(stock_code, stock_name), ...]"""
    out = []
    for pn in range(1, 6):
        path = ('/api/qt/clist/get?pn=%d&pz=500&po=1&np=1'
                '&fltt=2&invt=2&fid=f3&fs=b:%s&fields=f12,f14' % (pn, code))
        t = em_get(path)
        if not t:
            break
        try:
            d = json.loads(t)
            diff = (d.get('data') or {}).get('diff') or []
        except Exception:
            break
        if not diff:
            break
        for it in diff:
            c = it.get('f12')
            nm = it.get('f14')
            if c and nm:
                out.append((c, nm))
        if len(diff) < 500:
            break
        time.sleep(0.15)
    return out


# ---------------- 腾讯 K线 ----------------
def tencent_kline(code, cache):
    """返回 (closes, vols) 或 None。带当日缓存。"""
    if code in cache:
        c = cache[code]
        if c.get('last') == TODAY and c.get('closes'):
            return c['closes'], c['vols']
    if code.startswith('6'):
        mp = 'sh'
    elif code.startswith(('8', '4')):
        mp = 'bj'
    else:
        mp = 'sz'
    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s%s,day,,,60,qfq' % (mp, code)
    t = http_get(url, silent=True)
    if not t:
        return None
    try:
        d = json.loads(t)
        node = (d.get('data') or {}).get(mp + code) or (d.get('data') or {}).get(code) or {}
        kl = node.get('qfqday') or node.get('day') or []
    except Exception:
        return None
    if len(kl) < 21:
        return None
    try:
        closes = [float(x[2]) for x in kl]
        vols = [float(x[5]) for x in kl]
    except (ValueError, IndexError):
        return None
    cache[code] = {'last': kl[-1][0], 'closes': closes, 'vols': vols}
    return closes, vols


# ---------------- 均线判定 ----------------
def ma(arr, n):
    return sum(arr[-n:]) / n


def eval_stock(closes, vols):
    """均线多头 + 向上发散 + 站上20日线判定，返回 None 或 指标字典"""
    if len(closes) < 21:
        return None
    ma5, ma10, ma20 = ma(closes, 5), ma(closes, 10), ma(closes, 20)
    ma5p, ma10p, ma20p = ma(closes[:-1], 5), ma(closes[:-1], 10), ma(closes[:-1], 20)
    close = closes[-1]
    if not (ma5 > ma10 > ma20):
        return None
    if not (ma5 > ma5p and ma10 > ma10p and ma20 > ma20p):
        return None
    if not ((ma5 - ma10) >= (ma5p - ma10p) and (ma10 - ma20) >= (ma10p - ma20p)):
        return None
    if not (close > ma20):
        return None
    bias20 = (close - ma20) / ma20 * 100
    div_str = (ma5 - ma20) / ma20 * 100
    vma5 = ma(vols, 5)
    vol_ratio = (vols[-1] / vma5) if vma5 else 0
    ret5 = (close / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
    return {
        'ma5': round(ma5, 3), 'ma10': round(ma10, 3), 'ma20': round(ma20, 3),
        'close': round(close, 3), 'bias20': round(bias20, 2),
        'div_strength': round(div_str, 2), 'vol_ratio': round(vol_ratio, 2),
        'recent5_ret': round(ret5, 2),
    }


# ---------------- 评分 ----------------
def sector_rank_score(rank):
    return {1: 20, 2: 16, 3: 12, 4: 8}.get(rank, 4)


def score_stock(m, sector_rank, multi_board):
    s = 0.0
    s += min(m['div_strength'], 25) * 1.2
    # 乖离率：0~15% 给满分；>20% 视为超买，反向扣分（越偏离越不优先）
    if m['bias20'] <= 15:
        s += m['bias20'] * 1.0
    else:
        s += 15 - max(0, m['bias20'] - 20) * 1.0
    s += min(m['vol_ratio'], 4) * 2.5
    s += sector_rank_score(sector_rank)
    s += 8 if multi_board > 1 else 0
    s += min(m['recent5_ret'], 20) * 0.5
    return round(s, 1)


def is_overbought(m):
    """乖离率 > 20% 视为短期超买（追高风险大）"""
    return m['bias20'] > 20


# ---------------- 兜底：本池已有票按题材匹配 ----------------
def get_fallback_members(theme, data):
    out = {}
    seen = set()
    srcs = [data.get('zt_pool', []) or [], data.get('candidates', []) or []]
    if data.get('recommend'):
        srcs.append([data['recommend']])
    for src in srcs:
        for s in src:
            if not s:
                continue
            code = str(s.get('code', ''))
            if not code or code in seen:
                continue
            concepts = s.get('concepts') or []
            industry = s.get('industry') or ''
            themes = set(theme_of(industry))
            for c in concepts:
                themes.update(theme_of(c))
            if theme in themes:
                seen.add(code)
                out[code] = {'name': s.get('name', ''), 'boards': [theme]}
    return out


# ---------------- 主流程 ----------------
def run():
    data = load_json(DATA_JSON, {})
    daily = data.get('board_daily', []) or []
    d3 = data.get('board_3d', []) or []
    d3names = {b.get('name') for b in d3}
    overlap = [b.get('name') for b in daily if b.get('name') in d3names]
    overlap = list(dict.fromkeys(overlap))
    if not overlap:
        print('[模块4] 两榜无重合板块，跳过。')
        return

    daily_rank = {b.get('name'): i + 1 for i, b in enumerate(daily)}
    overlap_themes = {b: set(theme_of(b)) for b in overlap}

    em = build_em_board_map()
    print('[模块4] 东财板块映射 %d 条' % len(em))

    members = {}     # code -> {name, boards[]}
    em_used = False
    mx_used = 0
    for bname in overlap:
        # 1) 妙想（东方财富连接器）缓存优先 —— 命名体系与腾讯不同，走 MX_NAME_MAP 映射
        board_members = load_mx_members(bname)
        src = 'MX' if board_members else ''
        # 2) 东财板块成分股接口
        if not board_members:
            code = match_em_code(bname, em)
            board_members = fetch_board_members_em(code) if code else []
            time.sleep(0.2)
            src = 'EM' if board_members else ''
        if board_members:
            if src == 'EM':
                em_used = True
            else:
                mx_used += 1
            for mc, mn in board_members:
                if mc not in members:
                    members[mc] = {'name': mn, 'boards': []}
                if bname not in members[mc]['boards']:
                    members[mc]['boards'].append(bname)
        else:
            th = list(overlap_themes.get(bname, set()))
            fb = get_fallback_members(th[0], data) if th else {}
            for mc, info in fb.items():
                if mc not in members:
                    members[mc] = {'name': info['name'], 'boards': []}
                if bname not in members[mc]['boards']:
                    members[mc]['boards'].append(bname)
    print('[模块4] 候选成分股 %d 只（妙想源板块%d个 / 东财主源=%s）'
          % (len(members), mx_used, em_used))

    cache = load_json(KLINE_CACHE, {})

    def _cached(c):
        v = cache.get(c) or {}
        return v.get('last') == TODAY and bool(v.get('closes'))

    # 并发拉取 K 线（腾讯源稳定）：只请求未命中缓存的，8 线程 → 大幅缩短耗时
    todo = [c for c in members if not _cached(c)]
    print('  拉取 K线：需请求 %d 只 / 缓存命中 %d 只' % (len(todo), len(members) - len(todo)))
    if todo:
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(lambda c: tencent_kline(c, cache), todo))

    qual = []
    for i, (code, info) in enumerate(members.items()):
        kv = tencent_kline(code, cache)     # 此时基本都命中缓存，不再发网络请求
        if not kv:
            continue
        closes, vols = kv
        m = eval_stock(closes, vols)
        if not m:
            continue
        ranks = [daily_rank.get(b, 99) for b in info['boards']]
        best_rank = min(ranks)
        sc = score_stock(m, best_rank, len(info['boards']))
        qual.append({
            'code': code, 'name': info['name'], 'market': ('1' if code.startswith('6') else '0'),
            'boards': info['boards'], 'best_rank': best_rank,
            'multi_board': len(info['boards']),
            'ma5': m['ma5'], 'ma10': m['ma10'], 'ma20': m['ma20'], 'close': m['close'],
            'bias20': m['bias20'], 'div_strength': m['div_strength'],
            'vol_ratio': m['vol_ratio'], 'recent5_ret': m['recent5_ret'],
            'overbought': is_overbought(m), 'score': sc,
        })
    save_json(KLINE_CACHE, cache)

    qual.sort(key=lambda x: -x['score'])
    for i, q in enumerate(qual, 1):
        q['rank'] = i

    # ---- 动态池：增量更新 ----
    prev = load_json(MODULE4_POOL, {})
    prev_codes = {s.get('code') for s in prev.get('stocks', [])}
    new_codes = {q['code'] for q in qual}
    added = [q for q in qual if q['code'] in (new_codes - prev_codes)]
    removed_codes = prev_codes - new_codes
    removed_names = [s.get('name', s.get('code', '')) for s in prev.get('stocks', []) if s.get('code') in removed_codes]

    history = prev.get('history', [])
    history.append({'date': TODAY, 'added': [q['name'] for q in added], 'removed': removed_names})
    history = history[-30:]

    pool = {
        'date': TODAY, 'overlap_boards': overlap, 'stocks': qual,
        'added_today': [q['name'] for q in added], 'removed_today': removed_names,
        'history': history,
    }
    save_json(MODULE4_POOL, pool)

    # ---- 写入 data.json / data.js ----
    strategy = {
        'criteria': [
            '入选条件：MA5>MA10>MA20（多头排列）且三线斜率向上、差值扩大（向上发散），收盘价站上MA20',
            '选股范围：仅限模块3「两榜重合板块」（当日领涨且连续走强的持续性主线）成分股',
        ],
        'entry': '介入时机：缩量回踩 MA5 / MA10 不破为第一买点；放量突破近5日新高为追涨买点。优先选回踩不破者，风险收益比更优。',
        'overbought': '超买提示：乖离率（收盘价/MA20-1）> 20% 视为短期超买，追高风险大，仅适合激进打板；稳健者等回踩 MA5/MA10 缩量不破再介入。',
        'position': '仓位：单票不超过总仓 15%~20%，分 2~3 批建仓（首仓 1/2，回踩确认加 1/3，突破加 1/6）。',
        'stop': '止损：收盘价跌破 MA20，或买入价回撤 -5%~-7%，二者先到为准，严格离场。',
        'turnover': '轮动：本池每日更新。触发剔除条件（均线死叉 / 跌破MA20 / 发散收敛）即离场信号，不恋战。',
        'disclaimer': '以上为客观技术信号整理与策略框架，不构成投资建议，亦不对收益作任何承诺。市场有风险，决策需自担。',
    }
    data['module4'] = {
        'date': TODAY, 'overlap_boards': overlap, 'pool_count': len(qual),
        'added_today': [q['name'] for q in added], 'removed_today': removed_names,
        'em_used': em_used, 'mx_used': mx_used, 'stocks': qual[:25], 'strategy': strategy,
    }
    save_json(DATA_JSON, data)
    save_js(os.path.join(BASE, 'data.js'), data)

    print('[模块4] 完成：入选 %d 只 | 今日新增 %d | 今日剔除 %d（东财主源=%s）'
          % (len(qual), len(added), len(removed_names), em_used))
    print('[模块4] Top5（按评分）：')
    for q in qual[:5]:
        print('  %2d. %-8s %s | 发散%.1f%% 乖离%.1f%% 量比%.2f 评分%.1f | %s'
              % (q['rank'], q['name'], q['code'], q['div_strength'], q['bias20'],
                 q['vol_ratio'], q['score'], '、'.join(q['boards'])))


if __name__ == '__main__':
    run()
