# -*- coding: utf-8 -*-
"""临时：为当日涨停池/炸板池/资金流TOP 补齐概念标签并打印"""
import sys, os, json, time
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import (BASE, load_json, save_json, fetch_stock_tags, market_of,
                        fetch_board_rank, fetch_seal_amount)
from module4 import em_get

d = load_json(os.path.join(BASE, 'data.json'), {})
r = load_json(os.path.join(BASE, 'review_data.json'), {})
cache = load_json(os.path.join(BASE, '.stock_cache.json'), {})

# 板块热度表：概念排序用
heat = fetch_board_rank() or []
hrank = {}
for i, b in enumerate(heat):
    hrank[b.get('name')] = len(heat) - i

def tags(code, market, name):
    t = fetch_stock_tags(code, market, name, cache) or {}
    cs = t.get('concepts') or []
    cs2 = sorted(cs, key=lambda c: -hrank.get(c, 0))
    return cs2

out = {}
zp = d.get('zt_pool') or []
for x in zp:
    out.setdefault(x['code'], []).append(('zt', x, tags(x['code'], x.get('market'), x.get('name'))))
    time.sleep(0.1)

for x in (r.get('zb_pool') or []):
    out.setdefault(x['code'], []).append(('zb', x, tags(x['code'], 1 if x['code'][0] in '6' else 0, x.get('name'))))
    time.sleep(0.1)

for x in (r.get('stock_flow') or [])[:15]:
    if x['code'] not in out:
        out.setdefault(x['code'], []).append(('flow', x, tags(x['code'], 1 if x['code'][0] in '6' else 0, x.get('name'))))
        time.sleep(0.1)

save_json(os.path.join(BASE, '.stock_cache.json'), cache)

res = {'zt': [], 'zb': [], 'flow': []}
for x in zp:
    cs = out.get(x['code'], [('zt', x, [])])[0][2]
    res['zt'].append({'code': x['code'], 'name': x['name'], 'lbc': x.get('lbc'),
                      'chg': x.get('chg'), 'amt': round((x.get('amount') or 0)/1e8, 2),
                      'turn': x.get('turnover'), 'seal': round((x.get('seal_fund') or 0)/1e8, 2),
                      'yizi': x.get('is_yizi'), 'zbc': x.get('zbc'),
                      'ind': x.get('industry'), 'cs': cs[:4]})
for x in (r.get('zb_pool') or []):
    cs = out.get(x['code'], [('zb', x, [])])[0][2]
    res['zb'].append({'code': x['code'], 'name': x['name'], **{k: v for k, v in x.items() if k not in ('code', 'name')}, 'cs': cs[:4]})
for x in (r.get('stock_flow') or [])[:15]:
    cs = out.get(x['code'], [('flow', x, [])])[0][2]
    res['flow'].append({**x, 'cs': cs[:4]})

save_json(os.path.join(BASE, '_tags_dump.json'), res)
print(json.dumps(res, ensure_ascii=False, indent=1))
