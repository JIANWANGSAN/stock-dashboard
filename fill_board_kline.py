# -*- coding: utf-8 -*-
"""增量补齐板块K线：按「日K / 周K」分别检查完整性，缺哪补哪，合并写回（可反复运行）。

数据源（自动切换，整批单一来源）：
  ① 东财 push2his  —— 首选。与页面「板块涨幅」同源，指数口径完全一致。
  ② 同花顺 d.10jqka.com.cn —— 兜底。东财 K线路径对单一 IP 高频请求会封禁
     （RemoteDisconnected / curl exit 56；同主机分时接口仍正常），此时自动切到同花顺。
     两家指数基期不同，点位不可混用，故不按单个板块切换——先探测东财，通则全东财，不通则全同花顺。

用法：
  python fill_board_kline.py              # 补齐（缺哪补哪）
  python fill_board_kline.py --force      # 全部重取
  python fill_board_kline.py --src ths    # 强制指定数据源（em/ths）
"""
import sys, os, json, time, ssl, urllib.request
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding='utf-8')
import fetch_data as F
from ths_board_map import THS_MAP

DAY_MIN, WEEK_MIN = 100, 50          # 低于此根数视为缺失
DAY_N, WEEK_N = 120, 60              # 目标根数

FORCE = '--force' in sys.argv
SRC = None
if '--src' in sys.argv:
    SRC = sys.argv[sys.argv.index('--src') + 1]

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE
THS_UA = {'User-Agent': F.UA['User-Agent'], 'Referer': 'http://q.10jqka.com.cn/'}


# ---------- 东财 ----------
def fetch_em(code, klt):
    n = DAY_N if klt == 101 else WEEK_N
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=90.%s'
           '&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56&klt=%d&fqt=1'
           '&end=20500101&lmt=%d&_=%d' % (code, klt, n, int(time.time() * 1000)))
    t = F.http_get(url, timeout=6, retry=2, silent=True)
    if not t:
        return []
    try:
        ks = (json.loads(t).get('data') or {}).get('klines') or []
    except Exception:
        return []
    return [','.join(k.split(',')[:5]) for k in ks]     # 东财字段已是 日期,开,收,高,低


# ---------- 同花顺 ----------
def _ths_raw(ths_code, per):
    url = 'https://d.10jqka.com.cn/v6/line/bk_%s/%s/last.js?_=%d' % (ths_code, per, int(time.time() * 1000))
    req = urllib.request.Request(url, headers=THS_UA)
    with urllib.request.urlopen(req, timeout=8, context=_ctx) as r:
        b = r.read().decode('utf-8', 'ignore')
    if '(' not in b:
        return None
    j = json.loads(b[b.index('(') + 1:b.rindex(')')])
    return j


def _ths_rows(j, n):
    """同花顺 K线 字段序为 日期,开,高,低,收 → 换序为 日期,开,收,高,低（对齐东财口径）"""
    rows = [x for x in (j.get('data') or '').split(';') if x]
    out = []
    for r in rows:
        p = r.split(',')
        if len(p) < 5:
            continue
        out.append('%s,%s,%s,%s,%s' % (p[0], p[1], p[4], p[2], p[3]))
    return out[-n:]


def fetch_ths(ths_code, per, n):
    """同花顺日K用 01；周K 用 11 或 12 —— 同一份周K数据由不同分片提供，
    部分板块只有其中一个分片可访问（'11' 报 HTTPError 时 '12' 往往可用），故依次尝试。"""
    periods = ('01',) if per == '01' else ('11', '12')
    for p in periods:
        for _ in range(2):
            try:
                j = _ths_raw(ths_code, p)
                if not j:
                    continue
                out = _ths_rows(j, n)
                if out:
                    return out
                break
            except Exception:
                time.sleep(0.6)
    return []


# ---------- 目标清单 ----------
data = F.load_json(F.DATA_JSON, {})
have = data.get('board_kline') or {}
codes = {}
for b in ((data.get('sector_daily') or []) + (data.get('sector_3d') or [])):
    if b.get('code'):
        codes[b['code']] = b.get('name', '')
if not codes:
    print('⚠️ data.json 里没有产业板块清单（sector_daily/sector_3d），先跑 fetch_data.py')
    raise SystemExit(1)

# ---------- 数据源探测（整批单一来源）----------
if not SRC:
    probe = codes and fetch_em(sorted(codes)[0], 101)
    SRC = 'em' if probe else 'ths'
    print('数据源探测：东财 %s → 本次使用「%s」' % ('可用' if probe else '不可用（K线路径被封）', '东财' if SRC == 'em' else '同花顺'))
if SRC == 'ths':
    miss = [n for c, n in codes.items() if c not in THS_MAP]
    if miss:
        print('⚠️ 同花顺无对应板块，本次跳过：%s' % '、'.join(miss))

# ---------- 待补清单 ----------
todo = []
for c, name in codes.items():
    v = have.get(c) or {}
    need_d = FORCE or len(v.get('day') or []) < DAY_MIN
    need_w = FORCE or len(v.get('week') or []) < WEEK_MIN
    if need_d or need_w:
        todo.append((c, name, need_d, need_w, v))

print('待补 %d 个：%s' % (len(todo),
      '、'.join('%s(%s%s)' % (n, '日' if d else '', '周' if w else '') for _, n, d, w, _ in todo) or '无'))
if not todo:
    print('✅ 已全部齐备')
    raise SystemExit(0)


def work(item):
    c, name, need_d, need_w, v = item
    rec = dict(v) if v else {'name': name}
    rec['name'] = rec.get('name') or name
    rec['src'] = SRC
    if SRC == 'em':
        if need_d: rec['day'] = fetch_em(c, 101)
        if need_w: rec['week'] = fetch_em(c, 102)
    else:
        t = THS_MAP.get(c)
        if t:
            if need_d: rec['day'] = fetch_ths(t[0], '01', DAY_N)
            if need_w: rec['week'] = fetch_ths(t[0], '11', WEEK_N)
    return c, name, rec


# 同行并发（东财限流敏感 → 并发压到 3；同花顺可到 5）
with ThreadPoolExecutor(max_workers=3 if SRC == 'em' else 5) as ex:
    for c, name, rec in ex.map(work, todo):
        have[c] = rec
        ok = len(rec.get('day') or []) >= DAY_MIN and len(rec.get('week') or []) >= WEEK_MIN
        print('  %s %-12s 日K%3d 周K%3d  [%s]' % ('✅' if ok else '⚠️', name,
              len(rec.get('day') or []), len(rec.get('week') or []), SRC))

data['board_kline'] = have
F.save_json(F.DATA_JSON, data)
F.save_js(os.path.join(F.BASE, 'data.js'), data)
full = sum(1 for c in codes if len((have.get(c) or {}).get('day') or []) >= DAY_MIN
           and len((have.get(c) or {}).get('week') or []) >= WEEK_MIN)
print('累计 %d 个板块有K线（齐备 %d/%d）' % (len(have), full, len(codes)))
