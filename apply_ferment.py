# -*- coding: utf-8 -*-
"""
定点补充「板块发酵」字段（零网络 / 零模型请求）。
- industry：从 nodes.json 的连板中票按 code 反查（一级行业）。
- ferment：用 fetch_data.ferment_count 以「题材概念 ↔ 当日涨停池细分行业」模糊匹配，
  得出该题材当天涨停家数(zt)与连板家数(lb)。
写回 data.json 与 data.js，不影响其余字段（含竞价量/封单）。
"""
import json, os
import fetch_data as F

BASE = F.BASE
DATA_JSON = F.DATA_JSON
NODES_JSON = F.NODES_JSON
DATA_JS = os.path.join(BASE, 'data.js')

data = F.load_json(DATA_JSON, {})
nodes = F.load_json(NODES_JSON, {}).get('nodes', [])
zt_pool = data.get('zt_pool', [])

# code -> industry 反查（优先连板中票）
ind_map = {}
for n in nodes:
    for s in n.get('stocks', []):
        if s.get('code'):
            ind_map.setdefault(s['code'], s.get('industry', ''))

def fill(c):
    c['industry'] = ind_map.get(c.get('code'), c.get('industry', ''))
    c['ferment'] = F.ferment_count(c.get('concepts', []), zt_pool)
    return c

cands = data.get('candidates', [])
data['candidates'] = [fill(c) for c in cands]

reco = data.get('recommend')
if reco:
    data['recommend'] = fill(reco)

F.save_json(DATA_JSON, data)
F.save_js(DATA_JS, data)

print('== 板块发酵落盘结果 ==')
if reco:
    f = reco.get('ferment', {})
    print('推荐 %s(%s) %s板 行业=%s 发酵: 涨停%d 连板%d'
          % (reco['name'], reco['code'], reco['boards'], reco.get('industry'),
             f.get('zt', 0), f.get('lb', 0)))
print('\n候选发酵:')
for c in data['candidates']:
    f = c.get('ferment', {})
    print('  %-8s %s板 行业=%-8s 发酵 涨停%d 连板%d'
          % (c['name'], c['boards'], c.get('industry', ''), f.get('zt', 0), f.get('lb', 0)))
print('\nOK -> data.json / data.js 已更新')
