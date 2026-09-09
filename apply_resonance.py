# -*- coding: utf-8 -*-
"""
定点重算「主线共振」(concept_hit)，零网络 / 零模型。
用新的 THEME_MAP 主题匹配：个股概念(行业大类) 归一化到主线主题，
与 单日榜+累计榜 命中的主题取交集。写回 data.json / data.js。
"""
import json
import fetch_data as F

DATA_JSON = F.DATA_JSON
DATA_JS = F.os.path.join(F.BASE, 'data.js')

data = F.load_json(DATA_JSON, {})
bd = data.get('board_daily', [])
b3 = data.get('board_3d', [])
hot = [b['name'] for b in bd[:30]] + [b['name'] for b in b3]

print('热点源：单日%d + 累计%d 个板块名' % (min(30, len(bd)), len(b3)))
print('热点主题：', sorted({t for h in hot for t in F.theme_of(h)}))

def recur(c):
    c['concept_hit'] = F.concept_hit_count(c.get('concepts', []), hot)
    # 顺带记录命中的主线主题，便于核对
    s_themes = set()
    for x in c.get('concepts', []):
        s_themes.update(F.theme_of(x))
    hot_themes = set()
    for h in hot:
        hot_themes.update(F.theme_of(h))
    c['_themes'] = sorted(s_themes & hot_themes)
    return c

for c in data.get('candidates', []):
    recur(c)
if data.get('recommend'):
    recur(data['recommend'])

F.save_json(DATA_JSON, data)
F.save_js(DATA_JS, data)

print('\n== 主线共振落盘结果（9/4）==')
r = data['recommend']
print('推荐 %s(%s) %d板 主线共振=%d %s' % (r['name'], r['code'], r['boards'], r['concept_hit'], r.get('_themes')))
for c in data['candidates']:
    print('  %-7s %d板 共振=%d %s' % (c['name'], c['boards'], c['concept_hit'], c.get('_themes')))
print('\nOK -> data.json / data.js 已更新')
