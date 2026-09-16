# -*- coding: utf-8 -*-
"""增量补齐板块K线：按「日K / 周K」分别检查完整性，缺哪补哪，合并写回（可反复运行）。"""
import sys, os, json, time
sys.stdout.reconfigure(encoding='utf-8')
import fetch_data as F

DAY_MIN, WEEK_MIN = 100, 50          # 低于此根数视为缺失
data = F.load_json(F.DATA_JSON, {})
have = data.get('board_kline') or {}

codes = {}
for b in ((data.get('board_daily') or []) + (data.get('board_3d') or [])
          + (data.get('sector_daily') or []) + (data.get('sector_3d') or [])):
    if b.get('code'):
        codes[b['code']] = b.get('name', '')


def fetch(code, klt, tries=4, gap=2.0):
    n = 120 if klt == 101 else 60
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=90.%s'
           '&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56&klt=%d&fqt=1'
           '&end=20500101&lmt=%d&_=%d' % (code, klt, n, int(time.time() * 1000)))
    for _ in range(tries):
        t = F.http_get(url, silent=True)
        if t:
            try:
                ks = (json.loads(t).get('data') or {}).get('klines') or []
                if ks:
                    return [','.join(k.split(',')[:5]) for k in ks]
            except Exception:
                pass
        time.sleep(gap)
    return []


todo = []
for c, name in codes.items():
    v = have.get(c) or {}
    need_d = len(v.get('day') or []) < DAY_MIN
    need_w = len(v.get('week') or []) < WEEK_MIN
    if need_d or need_w:
        todo.append((c, name, need_d, need_w, v))

print('待补 %d 个：%s' % (len(todo), '、'.join('%s(%s%s)' % (n, '日' if d else '', '周' if w else '') for _, n, d, w, _ in todo)))
if not todo:
    print('✅ 已全部齐备')
    raise SystemExit(0)

for c, name, need_d, need_w, v in todo:
    rec = dict(v) if v else {'name': name}
    rec['name'] = rec.get('name') or name
    if need_d:
        rec['day'] = fetch(c, 101)
        time.sleep(1.2)
    if need_w:
        rec['week'] = fetch(c, 102)
        time.sleep(1.2)
    have[c] = rec
    print('  %s %-12s 日K%3d 周K%3d' % ('✅' if (len(rec.get('day') or []) >= DAY_MIN and len(rec.get('week') or []) >= WEEK_MIN) else '⚠️',
                                        name, len(rec.get('day') or []), len(rec.get('week') or [])))

data['board_kline'] = have
F.save_json(F.DATA_JSON, data)
F.save_js(os.path.join(F.BASE, 'data.js'), data)
print('累计 %d 个板块有K线' % len(have))
