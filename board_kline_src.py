# -*- coding: utf-8 -*-
"""板块/指数 K线 取数（供 `fetch_data.py` 盘后预生成 与 `fill_board_kline.py` 手动补齐共用）。

数据源（**整批单一来源**，不可逐板块混用——两家指数基期不同）：
  ① 东财 `push2his`  —— 首选。与页面「板块涨幅」同源，指数口径完全一致。
  ② 同花顺 `d.10jqka.com.cn` —— 兜底。东财 K线路径会对高频 IP **整段封禁**
     （RemoteDisconnected / curl exit 56，而同主机分时接口仍正常），此时整批改用同花顺。

映射表见 `ths_board_map.py`（由 `build_ths_map.py` 重建）。
字段统一为 `'日期,开,收,高,低'`（同花顺原始序为 日期,开,高,低,收，此处已换序）。
"""
import json, time, ssl, urllib.request
from concurrent.futures import ThreadPoolExecutor
from ths_board_map import THS_MAP

UA = {'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                     '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
      'Accept': '*/*',
      'Accept-Language': 'zh-CN,zh;q=0.9'}

# 数据源偏好（用户 2026-09-17 明确要求：**东财已停用，以后只用同花顺**）
#   'ths'  = 只用同花顺（默认）
#   'em'   = 只用东财
#   'auto' = 先探测东财、不通再切同花顺（东财已停用，勿再用）
SOURCE_PREF = 'ths'

# 同花顺没有对应板块时（目前只有 BK1652「新消费」），是否允许用东财补这一条。
# True  = 补（覆盖率优先；该板块单独走东财，与其它板块不同源，但每条自身连续）
# False = 留空（严格只用同花顺）
THS_MISSING_FALLBACK_EM = True

# 沪深量能用的指数（东财 secid → 同花顺指数码，前缀 zs_）
THS_INDEX = {
    '1.000001': 'zs_1A0001',    # 上证指数
    '0.399106': 'zs_399106',    # 深证综指
}

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def http_get(url, timeout=6, retry=2, headers=None):
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers=dict(UA, **(headers or {})))
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
                return r.read().decode('utf-8', errors='ignore')
        except Exception:
            if i < retry - 1:
                time.sleep(0.5)
    return None


# ---------- 东财 ----------
def fetch_em_kline(code, klt, n, hosts=('push2his.eastmoney.com', '1.push2his.eastmoney.com')):
    """klt: 101=日K / 102=周K；code 为东财 secid 后半段（板块 90.BKxxxx 里的 BKxxxx，或 1.000001 里的 000001）。"""
    secid = code if '.' in code else '90.%s' % code
    for host in hosts:
        url = ('https://%s/api/qt/stock/kline/get?secid=%s'
               '&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56&klt=%d&fqt=1'
               '&end=20500101&lmt=%d&_=%d' % (host, secid, klt, n, int(time.time() * 1000)))
        t = http_get(url, retry=1)
        if t:
            try:
                ks = (json.loads(t).get('data') or {}).get('klines') or []
            except Exception:
                ks = []
            if ks:
                return [','.join(k.split(',')[:5]) for k in ks]     # 东财字段已是 日期,开,收,高,低
    return []


# ---------- 同花顺 ----------
def ths_json(code, per):
    """code 形如 `bk_886033`（板块）或 `zs_1A0001`（指数）；per: 01=日 11/12=周
    返回 {name, data} 或 None"""
    url = 'https://d.10jqka.com.cn/v6/line/%s/%s/last.js?_=%d' % (code, per, int(time.time() * 1000))
    t = http_get(url, retry=1, headers={'Referer': 'http://q.10jqka.com.cn/'})
    if not t or '(' not in t:
        return None
    try:
        return json.loads(t[t.index('(') + 1:t.rindex(')')])
    except Exception:
        return None


def fetch_ths_kline(ths_code, per, n):
    """板块K线（ths_code 为 6 位板块指数码）。

    日K 用 01；周K 用 11 或 12 —— 同一份周K数据分片在不同 host 上，
    部分板块只有其一可访问（'11' 报 HTTPError 时 '12' 往往可用），故依次尝试。
    """
    periods = ('01',) if per == '01' else ('11', '12')
    for p in periods:
        for _ in range(2):
            j = ths_json('bk_' + ths_code, p)
            if not j:
                time.sleep(0.5)
                continue
            rows = [x for x in (j.get('data') or '').split(';') if x]
            out = []
            for r in rows:
                a = r.split(',')
                if len(a) < 5:
                    continue
                out.append('%s,%s,%s,%s,%s' % (a[0], a[1], a[4], a[2], a[3]))   # 开,高,低,收 → 开,收,高,低
            if out:
                return out[-n:]
            break
    return []


def fetch_ths_amount(ths_code, n=25):
    """同花顺指数日K → {日期: 成交额(元)}，用于东财被封时兜底「沪深量能」。

    ths_code 形如 `zs_1A0001`。行字段：`日期,开,高,低,收,成交量,成交额,…`（第 7 列＝成交额，单位元）。
    日期统一格式化为 `YYYY-MM-DD`（同花顺给的是 YYYYMMDD，与东财口径不一致，混用会打乱排序/当日剔除）。
    """
    j = ths_json(ths_code, '01')
    if not j:
        return {}
    rows = [x for x in (j.get('data') or '').split(';') if x]
    out = {}
    for r in rows[-n:]:
        a = r.split(',')
        if len(a) > 6 and a[6]:
            d = a[0]
            if len(d) == 8 and d.isdigit():
                d = '%s-%s-%s' % (d[:4], d[4:6], d[6:8])
            try:
                out[d] = float(a[6])
            except Exception:
                pass
    return out


# ---------- 数据源探测 ----------
def probe_source(sample_code, tries=3, need=2):
    """一律「先东财、通则整批用东财」；不通则整批用同花顺。返回 'em' / 'ths'。

    ⚠️ 必须多探几次：东财被限流时会**偶发放行**（实测连续 12 次全空，但个别时刻能过 1 次）。
    只探 1 次的话，撞上一次放行就整批走东财 → 其余全失败 → 把整批好数据覆盖成个位数。
    """
    ok = 0
    for _ in range(max(1, tries)):
        if fetch_em_kline(sample_code, 101, 5):
            ok += 1
            if ok >= need:
                return 'em'
        time.sleep(0.5)
    return 'ths'


def merge_board_kline(new, old, day_min=100, week_min=50):
    """合并新老 K线，**防止「抽风」把好数据覆盖成残缺**。

    · 新取到的板块若日K/周K 任一项不达标，且老数据里有 → 整条沿用老数据（不混两家口径）；
    · 新批次完全没取到的板块 → 沿用老数据（宁缺勿假）。
    """
    out = {}
    for c, r in (new or {}).items():
        o = (old or {}).get(c)
        complete = (len(r.get('day') or []) >= day_min and len(r.get('week') or []) >= week_min)
        out[c] = r if (complete or not o) else o
    for c, o in (old or {}).items():
        if c not in out and (o.get('day') or o.get('week')):
            out[c] = o
    return out


def fetch_board_klines(boards, day_n=120, week_n=60, max_workers=None, src=None, old=None,
                       day_min=100, week_min=50):
    """预生成板块日K/周K。

    boards: [{'code': 东财BK代码, 'name': 展示名}, ...]
    old:    上一版 board_kline —— 传入后会自动合并（新数据残缺的板块沿用老数据，见 merge_board_kline）
    返回 {code: {'name':.., 'day': [...], 'week': [...], 'src': 'em'|'ths'}}
    """
    out = {}
    codes = [b.get('code') or b.get('em_code') for b in boards]
    codes = [c for c in codes if c]
    if not codes:
        return out
    if src is None:
        src = SOURCE_PREF if SOURCE_PREF in ('em', 'ths') else probe_source(codes[0])
    if src == 'ths':
        lack = [b.get('name') for b in boards if (b.get('code') or '') not in THS_MAP]
        if lack:
            print('  [ths] 同花顺无对应板块：%s%s' % ('、'.join(x for x in lack if x),
                  '→ 改用东财单独补' if THS_MISSING_FALLBACK_EM else '→ 跳过'))
    # 东财对并发敏感 → 压低；同花顺宽松
    if max_workers is None:
        max_workers = 3 if src == 'em' else 5

    def _one(b):
        code = b.get('code') or b.get('em_code')
        if not code:
            return None
        rsrc = src
        if src == 'em':
            day = fetch_em_kline(code, 101, day_n)
            week = fetch_em_kline(code, 102, week_n)
        else:
            t = THS_MAP.get(code)
            if t:
                day = fetch_ths_kline(t[0], '01', day_n)
                week = fetch_ths_kline(t[0], '11', week_n)
            elif THS_MISSING_FALLBACK_EM:
                # 同花顺无此板块 → 单独用东财补（如 BK1652 新消费）
                day = fetch_em_kline(code, 101, day_n)
                week = fetch_em_kline(code, 102, week_n)
                rsrc = 'em'
            else:
                return None
        if day or week:
            return code, {'name': b.get('name', ''), 'day': day, 'week': week, 'src': rsrc}
        return None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for r in ex.map(_one, boards):
            if r:
                out[r[0]] = r[1]
    print('  板块K线数据源=%s，新取到 %d/%d 个' % ('东财' if src == 'em' else '同花顺', len(out), len(codes)))
    if old:
        fresh = len(out)
        out = merge_board_kline(out, old, day_min=day_min, week_min=week_min)
        print('  新取到 %d 个，与上一版合并后共 %d 个（残缺/失效的沿用老数据）' % (fresh, len(out)))
    return out
