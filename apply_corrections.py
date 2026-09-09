# -*- coding: utf-8 -*-
"""应用用户核对修正：龙版传媒 9/4 上板分时量 1.32 亿 / 总市值 76.04 亿。

只对 2026-09-04 这一天的视图做定点修正（不影响未来交易日），
并用 fetch_data.SEAL_OVERRIDE / CAP_OVERRIDE 把这两个值固化进代码，
保证后续定时任务重算后仍然正确。
"""
import os, json
from datetime import datetime

import fetch_data as fd
from enrich_tags import recalc_recommend, load_json, save_json, save_js

BASE = fd.BASE
NODES_JSON = fd.NODES_JSON
DATA_JSON = fd.DATA_JSON
SEAL_CACHE = fd.SEAL_CACHE

TARGET_CODE = '605577'
TARGET_DATE = '2026-09-04'
NEW_CAP = 7604000000          # 76.04 亿
SEAL_AMT = 132000000         # 1.32 亿
DAY_AMT = 495051040          # 全天 4.95 亿

# 1) 修正 nodes.json 里龙版的总市值（stocks + trigger）
nodes_obj = load_json(NODES_JSON, {'nodes': []})
nodes = nodes_obj.get('nodes', [])
for n in nodes:
    for s in n.get('stocks', []):
        if s.get('code') == TARGET_CODE:
            s['total_cap'] = NEW_CAP
    t = n.get('trigger') or {}
    if t.get('code') == TARGET_CODE:
        t['total_cap'] = NEW_CAP
save_json(NODES_JSON, nodes_obj)
print('[1] nodes.json 龙版总市值 -> 76.04 亿')

# 2) 种子化 seal 缓存（上板分时量 1.32 亿），与 SEAL_OVERRIDE 保持一致
sc = load_json(SEAL_CACHE, {})
sc['%s_%s' % (TARGET_CODE, TARGET_DATE)] = {'seal': SEAL_AMT, 'total': DAY_AMT}
save_json(SEAL_CACHE, sc)
print('[2] .seal_cache.json 种子化 龙版上板分时量 1.32 亿')

# 3) 复用现有推荐逻辑重算（只走东财分时接口，不触发 LLM）
data = load_json(DATA_JSON, {})
reco, cands = recalc_recommend(nodes, data.get('board_daily', []),
                               data.get('zt_pool', []), data.get('date', ''))
data['recommend'] = reco
data['candidates'] = cands
data['nodes'] = nodes[:12]
data['tags_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

# 4) 同步 zt_pool 里龙版总市值（历史快照对齐用户核对值）
for s in data.get('zt_pool', []):
    if s.get('code') == TARGET_CODE:
        s['total_cap'] = NEW_CAP

save_json(DATA_JSON, data)
save_js(os.path.join(BASE, 'data.js'), data)
print('[3] data.json / data.js 重算完成')

print('\n推荐卡:')
print(json.dumps(reco, ensure_ascii=False, indent=2))
print('\n候选前 3:')
for c in cands[:3]:
    print('  %s(%s) %s板 上板分时量=%s 竞价>=%s 封单%s~%s' % (
        c['name'], c['code'], c['boards'],
        c.get('seal_amount_yi'), c.get('bid_required_yi'),
        c.get('seal_min_yi'), c.get('seal_max_yi')))
