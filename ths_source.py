# -*- coding: utf-8 -*-
"""同花顺（THS）统一取数层 —— 东财已停用，全站数据源收敛到这里。

为什么单独成模块：`fetch_data.py` / `board_kline_src.py` / `fill_board_kline.py` 三处都要取数，
接口路径、回调名、字段序、字段码一旦写两套就必然走偏。**K线类**统一在 `board_kline_src.py`，
**其余全部**统一在这里。

────────────────────────────────────────────────────────────────────────
一、接口清单（2026-09-17 逐条实测）
────────────────────────────────────────────────────────────────────────
· 涨停池        data.10jqka.com.cn/dataapi/limit_up/limit_up_pool
                一次请求同时返回 data.limit_up_count / data.limit_down_count（含封板率/炸板数）
                → 涨停家数、跌停家数、封板率 全部由此得来，**不再需要任何东财情绪接口**
· 板块涨停排行  data.10jqka.com.cn/dataapi/limit_up/block_top
                20 个板块 × {涨幅 change, 涨停数 limit_up_num, 最高板 high, 成分股 stock_list}
                stock_list 内含 first_limit_up_time / last_limit_up_time（Unix 秒）→ 封板时间
· 炸板池        data.10jqka.com.cn/dataapi/limit_up/open_limit_pool
· 连板梯队      data.10jqka.com.cn/dataapi/limit_up/continuous_limit_up
· 分时          d.10jqka.com.cn/v6/time/hs_<码>/last.js      （仅当日；字段=时间,价,成交额,均价,量）
· 日K/周K       d.10jqka.com.cn/v6/line/<bk_码|zs_码|hs_码>/<01|11>/last.js
                （见 board_kline_src.py；字段序 日期,开,高,低,收,量,额 → 须换序）
· 实时快照      d.10jqka.com.cn/v6/realhead/<bk_码|zs_码|hs_码>/defer/last.js
                字段码：10=最新价 7=昨收 8=最高 9=最低 19=成交额(元)
                        264648=涨跌幅(%) 199112=涨跌额 1968584=换手(%) 526792=量比
· 7×24 快讯     news.10jqka.com.cn/tapp/news/push/stock/
· 个股 F10      basic.10jqka.com.cn/<码>/concept.html   → 概念（td.gnName + clid=概念指数码）
                basic.10jqka.com.cn/<码>/company.html   → 所属地域 / 所属申万行业
· 板块成分股    q.10jqka.com.cn/thshy/detail/code/<881xxx>/        （行业）
                q.10jqka.com.cn/gn/detail/code/<概念ID>/           （概念）

二、字段码（涨停池 field 参数，2026-09-17 实测）
────────────────────────────────────────────────────────────────────────
  199112=涨跌幅(%)   10=最新价        19=成交额(元)     1968584=换手率(%)
  133970=封单额(元)  3475914=流通市值   3541450=总市值     9001=涨停原因
  330329=涨停统计
  → 响应里另有结构化键名（无需码）：code/name/latest/change_rate/turnover/
    turnover_rate/order_amount/currency_value/sum_market_value/reason_type/
    high_days(「6天6板」)/high_days_value(hex: 高8位=天数, 低16位=板数)/
    change_tag(FIRST_LIMIT|LIMIT_BACK|…)/is_again_limit/market_id(17=沪 33=深)/is_new

三、THS 唯一缺口
────────────────────────────────────────────────────────────────────────
**跌停个股池明细**（连续跌停天数 / 跌停封单额）：THS 无公开接口。
→ 跌停**家数**用涨停池的 `limit_down_count`；跌停**明细**仍走东财 push2ex `getTopicDTPool`
  （该域不在被封的 push2his 路径下，实测可用），并在 `fetch_data.fetch_dt_pool` 处标注。
"""
import json
import os
import re
import ssl
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))

THS_UA = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
    'Accept': '*/*',
    'Referer': 'http://q.10jqka.com.cn/',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}
# 数据接口专用 Referer（dataapi 需要来源页，否则 403）
REF_DATACENTER = 'https://data.10jqka.com.cn/datacenterph/limitup/limtupInfo.html'
REF_F10 = 'http://basic.10jqka.com.cn/'

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# ════════════════════════ 底层请求 ════════════════════════
def ths_get(url, timeout=10, retry=3, enc='utf-8', silent=True, ref=None):
    """GET 一个 THS 接口，失败返回 None（绝不抛异常，保住管线）。"""
    headers = dict(THS_UA)
    if ref:
        headers['Referer'] = ref
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
                return r.read().decode(enc, errors='ignore')
        except Exception as e:
            if i == retry - 1:
                if not silent:
                    print('  [ths][warn] %s %s' % (type(e).__name__, url[:80]))
                return None
            time.sleep(0.5 + i * 0.6)
    return None


def ths_quote_json(url, timeout=10, silent=True, ref=None):
    """解析 quotebridge 回调包裹：`quotebridge_v6_xxx(...)` 或纯 JSON → dict。"""
    t = ths_get(url, timeout=timeout, silent=silent, ref=ref)
    if not t:
        return None
    try:
        t = t.strip()
        if t.startswith('{'):
            return json.loads(t)
        return json.loads(t[t.index('(') + 1:t.rindex(')')])
    except Exception:
        return None


def _api(url, timeout=15, silent=True):
    """THS dataapi（返回纯 JSON）。"""
    t = ths_get(url, timeout=timeout, silent=silent, ref=REF_DATACENTER)
    if not t:
        return None
    try:
        return json.loads(t)
    except Exception:
        return None


# ════════════════════════ 代码 / 板块工具 ════════════════════════
def is_excluded(code, name=''):
    """北交所 / 科创板 / ST / 退市 —— 全站统一剔除口径（与 fetch_data.is_excluded 保持一致）。"""
    c = str(code or '')
    if c.startswith(('688', '689')):
        return True
    if c[:1] in ('4', '8') or c.startswith('92'):
        return True
    n = str(name or '').upper().replace(' ', '').replace('\u3000', '')
    if 'ST' in n or n.endswith('退') or '退市' in n:
        return True
    return False


def market_of(code):
    """1=沪市(6/9 开头)，0=深市。与 fetch_data.market_of 同口径。"""
    return 1 if str(code)[:1] in ('6', '9') else 0


# THS market_id → 项目内 market（0=深 1=沪）
_MARKET_ID = {17: 1, 33: 0, 16: 1, 32: 0}


def market_from_id(mid):
    try:
        return _MARKET_ID.get(int(mid))
    except Exception:
        return None


# ════════════════════════ ① 涨停池（含涨跌停家数）════════════════════════
_ZT_FIELDS = '199112,10,19,1968584,133970,3475914,3541450,330329,9001'
_POOL_MAX = 200          # ⚠️ 接口硬限：limit > 200 直接返回 status_code=-1


def fetch_zt_pool(date_yyyymmdd):
    """当日涨停池。

    返回 (pool, stat)：
      pool —— 列表，字段与旧东财口径**完全兼容**（code/market/name/price/chg/amount/
              float_cap/total_cap/turnover/lbc/first_seal/last_seal/seal_fund/zbc/
              is_yizi/industry），便于 fetch_data.py 其余逻辑零改动。
      stat —— {'zt': 89, 'dt': 4, 'zt_open': 11, 'zt_rate': 0.89, 'y_zt': 100, …}

    封板时间说明：limit_up_pool 不返回封板时间，用 `fetch_block_top()` 的
    first_limit_up_time 回填；回填不到时留 0（后续由分时反推，见 fetch_seal_amount）。
    """
    url = ('https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool'
           '?page=1&limit=%d&field=%s&filter=HS,GEM2STAR'
           '&order_field=330329&order_type=0&date=%s' % (_POOL_MAX, _ZT_FIELDS, date_yyyymmdd))
    j = _api(url)
    if not j:
        return [], {}
    d = j.get('data') or {}
    info = d.get('info') or []

    # 封板时间回填表（block_top 覆盖约 87%，其余靠分时反推）
    seal_time = {}
    for blk in (fetch_block_top(date_yyyymmdd) or []):
        for s in (blk.get('stock_list') or []):
            c = s.get('code')
            if c and c not in seal_time:
                seal_time[c] = (s.get('first_limit_up_time'),
                                s.get('last_limit_up_time'),
                                s.get('continue_num'),
                                s.get('reason_type') or '',
                                s.get('reason_info') or '')

    out = []
    for s in info:
        code = s.get('code') or ''
        name = s.get('name') or ''
        if not code or is_excluded(code, name):
            continue
        fst, lst, cnum, rtype, rinfo = seal_time.get(code, (None, None, None, '', ''))
        # 连板数：优先 high_days_value（hex 低16位=板数），退回 block_top 的 continue_num
        lbc = 0
        hdv = s.get('high_days_value')
        if isinstance(hdv, int) and hdv:
            lbc = hdv & 0xFFFF
        if not lbc and cnum:
            lbc = int(cnum)
        if not lbc:
            lbc = _parse_high_days(s.get('high_days'))
        fbt = _ts_to_hhmmss(fst)
        lbt = _ts_to_hhmmss(lst)
        out.append({
            'code': code,
            'market': market_from_id(s.get('market_id')) if market_from_id(s.get('market_id')) is not None
                      else market_of(code),
            'name': name,
            'price': round(float(s.get('latest') or 0), 2),
            'chg': round(float(s.get('change_rate') or 0), 2),
            'amount': float(s.get('turnover') or 0),            # 成交额（元）
            'float_cap': float(s.get('currency_value') or 0),   # 流通市值（元）
            'total_cap': float(s.get('sum_market_value') or 0),  # 总市值（元）
            'turnover': round(float(s.get('turnover_rate') or 0), 2),
            'lbc': int(lbc),
            'first_seal': fbt,                                  # 首次封板时间 HHMMSS
            'last_seal': lbt,                                   # 最后封板时间 HHMMSS
            'seal_fund': float(s.get('order_amount') or 0),      # 封单额（元）
            'zbc': 0,                                            # 炸板次数（THS 在炸板池，不在此）
            'is_yizi': bool(fbt and fbt <= 93005),
            'industry': '',                                       # THS 无截断行业；题材见 concepts
            'concepts': [x for x in (rtype or '').split('+') if x],
            'reason_info': rinfo,
            'change_tag': s.get('change_tag') or '',
            'is_again_limit': s.get('is_again_limit') or 0,
            'high_days': s.get('high_days') or '',
            'src': 'ths',
        })

    stat = _market_stat(d)
    return out, stat


def _parse_high_days(hd):
    """'6天6板' → 6；'首板' → 1；'3天2板' → 2。"""
    if not hd:
        return 0
    m = re.search(r'(\d+)\s*板', str(hd))
    if m:
        return int(m.group(1))
    if '首板' in str(hd):
        return 1
    return 0


def _market_stat(d):
    """从涨停池响应里取「涨停/跌停家数、封板率、炸板数」—— 一次请求拿全市场情绪。"""
    up = d.get('limit_up_count') or {}
    dn = d.get('limit_down_count') or {}
    tu, yu = up.get('today') or {}, up.get('yesterday') or {}
    td, yd = dn.get('today') or {}, dn.get('yesterday') or {}
    return {
        'zt': tu.get('num'),                 # 今日涨停家数
        'dt': td.get('num'),                 # 今日跌停家数
        'zt_open': tu.get('open_num'),        # 今日炸板家数
        'zt_rate': tu.get('rate'),            # 今日封板率
        'y_zt': yu.get('num'),                # 昨日涨停家数
        'y_zt_open': yu.get('open_num'),
        'y_zt_rate': yu.get('rate'),
        'y_dt': yd.get('num'),                # 昨日跌停家数
        'dt_open': td.get('open_num'),
        'trade_status': (d.get('trade_status') or {}).get('name') or '',
        'date': d.get('date'),
        'src': 'ths',
    }


# ════════════════════════ ② 板块涨停排行 ════════════════════════
def fetch_block_top(date_yyyymmdd, limit=20):
    """板块涨停排行（涨停池热度视角）：20 个板块 × 涨幅/涨停数/最高板/成分股。

    这是**「题材共振」与「板块发酵」的第一手口径** —— 比东财的行业/概念涨幅榜更贴短线，
    因为它自带「该板块今日涨停几家」。
    """
    url = ('https://data.10jqka.com.cn/dataapi/limit_up/block_top'
           '?filter=HS,GEM2STAR&date=%s&limit=%d' % (date_yyyymmdd, limit))
    j = _api(url)
    if not j:
        return []
    return [b for b in (j.get('data') or []) if b.get('code')]


# ════════════════════════ ③ 炸板池 ════════════════════════
def fetch_zb_pool(date_yyyymmdd, limit=_POOL_MAX):
    """炸板池（当日封板后打开）。"""
    url = ('https://data.10jqka.com.cn/dataapi/limit_up/open_limit_pool'
           '?page=1&limit=%d&field=%s&filter=HS,GEM2STAR'
           '&order_field=199112&order_type=0&date=%s' % (limit, _ZT_FIELDS, date_yyyymmdd))
    j = _api(url)
    if not j:
        return []
    out = []
    for s in ((j.get('data') or {}).get('info') or []):
        code, name = s.get('code') or '', s.get('name') or ''
        if not code or is_excluded(code, name):
            continue
        out.append({
            'code': code, 'name': name,
            'market': market_from_id(s.get('market_id')) if market_from_id(s.get('market_id')) is not None
                      else market_of(code),
            'price': round(float(s.get('latest') or 0), 2),
            'chg': round(float(s.get('change_rate') or 0), 2),
            'amount': float(s.get('turnover') or 0),
            'float_cap': float(s.get('currency_value') or 0),
            'seal_fund': float(s.get('order_amount') or 0),
            'open_cnt': int(s.get('open_num') or s.get('open_cnt') or 0),
            'lbc': _parse_high_days(s.get('high_days')),
            'concepts': [x for x in (s.get('reason_type') or '').split('+') if x],
            'src': 'ths',
        })
    return out


# ════════════════════════ ④ 连板梯队 ════════════════════════
def fetch_lianban_ladder(date_yyyymmdd):
    """连板梯队：按高度分组 [{height, stocks:[{code,name,market}]}]。

    接口返回 `{"data":[{"height":6,"code_list":[{code,name}...]}...]}`，与东财口径一致。
    """
    url = ('https://data.10jqka.com.cn/dataapi/limit_up/continuous_limit_up'
           '?filter=HS,GEM2STAR&date=%s' % date_yyyymmdd)
    j = _api(url)
    if not j:
        return []
    out = []
    for g in (j.get('data') or []):
        h = int(g.get('height') or 0)
        if h < 2:
            continue
        lst = []
        for s in (g.get('code_list') or []):
            code, name = s.get('code') or '', s.get('name') or ''
            if not code or is_excluded(code, name):
                continue
            lst.append({'code': code, 'name': name, 'market': market_of(code)})
        if lst:
            out.append({'height': h, 'stocks': lst})
    out.sort(key=lambda x: -x['height'])
    return out


# ════════════════════════ ⑤ 分时（当日）════════════════════════
def fetch_minute(code, silent=True):
    """个股当日分时。返回 [(hhmm, price, amount, avg, vol), ...]。

    字段序（**与东财不同**）：时间, 现价, 该分钟成交额(元), 均价, 该分钟成交量(股)
    —— 首根 0930 = 集合竞价那根，其余各行为**该分钟增量**（不是累计）。
    """
    t = ths_get('http://d.10jqka.com.cn/v6/time/hs_%s/last.js' % code,
                silent=silent, ref='http://stockpage.10jqka.com.cn/')
    if not t:
        return []
    try:
        j = json.loads(t[t.index('(') + 1:t.rindex(')')])
        node = j.get('hs_%s' % code) or {}
        rows = []
        for line in (node.get('data') or '').split(';'):
            p = line.split(',')
            if len(p) < 4:
                continue
            # ⚠️ 收盘后 15:05–15:30 的「盘后固定价格交易」行尾列是空串，
            #    必须**逐行**容错，否则一根坏行会把整只票的分时全丢掉。
            try:
                rows.append((p[0], float(p[1]), float(p[2] or 0),
                             float(p[3] or 0), float(p[4]) if len(p) > 4 and p[4] else 0.0))
            except Exception:
                continue
        return rows
    except Exception:
        return []


# ════════════════════════ ⑥ 实时快照 ════════════════════════
def _realhead_key(prefix, code):
    return '%s%s' % (prefix, code)


def fetch_realhead(secs, max_workers=8):
    """批量实时快照。secs = [('bk'|'zs'|'hs', '881121'), ...]

    返回 {(kind, code): {'name','price','prev','pct','amount','turnover','vol_ratio'}}
    """
    from concurrent.futures import ThreadPoolExecutor
    out = {}

    def _one(sec):
        kind, code = sec
        key = _realhead_key(('%s_' % kind) if kind != 'hs' else 'hs_', code)
        u = 'http://d.10jqka.com.cn/v6/realhead/%s/defer/last.js' % key
        t = ths_get(u, silent=True, ref='http://q.10jqka.com.cn/')
        if not t:
            return None
        try:
            it = (json.loads(t[t.index('(') + 1:t.rindex(')')]) or {}).get('items') or {}
        except Exception:
            return None

        def _f(k):
            try:
                v = it.get(k)
                return float(v) if v not in (None, '', '--') else None
            except Exception:
                return None
        # ⚠️ 字段码实测结论（2026-09-17）：
        #   199112 = 涨跌幅(%)   264648 = 涨跌额      → 昨收须用「现价 − 涨跌额」反推。
        #   注意 realhead 的 `7` 对**个股**是昨收，对**指数/板块**却不是（是开盘价），
        #   故统一不用 `7`，只用 (10, 264648, 199112) 三者，口径跨品种一致。
        price, chg = _f('10'), _f('264648')
        prev = round(price - chg, 4) if (price is not None and chg is not None) else None
        return (sec, {
            'name': it.get('name') or '',
            'price': price,
            'prev': prev,
            'high': _f('8'),
            'low': _f('9'),
            'pct': _f('199112'),
            'chg': chg,
            'amount': _f('19'),
            'turnover': _f('1968584'),
            'vol_ratio': _f('526792'),
        })

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for r in ex.map(_one, list(secs)):
            if r:
                out[r[0]] = r[1]
    return out


# ════════════════════════ ⑦ 交易日 ════════════════════════
def fetch_trade_days(limit=90):
    """交易日列表：同花顺上证指数日K的日期列（升序）。"""
    t = ths_get('http://d.10jqka.com.cn/v6/line/zs_1A0001/01/last.js',
                silent=True, ref='http://q.10jqka.com.cn/')
    if not t:
        return []
    try:
        j = json.loads(t[t.index('(') + 1:t.rindex(')')])
        days = []
        for line in (j.get('data') or '').split(';'):
            p = line.split(',')
            if len(p) >= 2 and len(p[0]) == 8:
                days.append('%s-%s-%s' % (p[0][:4], p[0][4:6], p[0][6:]))
        return days[-limit:]
    except Exception:
        return []


# ════════════════════════ ⑧ 7×24 快讯 ════════════════════════
def fetch_news(pagesize=200, page=1, pages=1):
    """同花顺 7×24 快讯。返回 [{'time','text','title','digest','url'}, ...]（时间 desc）。

    `pages>1` 时连续翻页（用于跨周末/隔夜的早盘口径）；每页之间节流 0.3s。
    """
    out, seen = [], set()
    for p in range(page, page + max(1, pages)):
        u = ('https://news.10jqka.com.cn/tapp/news/push/stock/'
             '?page=%d&tag=&track=website&pagesize=%d' % (p, pagesize))
        raw = ths_get(u, silent=True, ref='https://news.10jqka.com.cn/')
        if not raw:
            break
        try:
            d = json.loads(raw)
        except Exception:
            break
        lst = (d.get('data') or {}).get('list') or []
        if not lst:
            break
        for x in lst:
            ct = x.get('ctime')
            try:
                ts = time.strftime('%Y-%m-%d %H:%M', time.localtime(int(ct)))
            except Exception:
                ts = ''
            title = (x.get('title') or '').strip()
            digest = (x.get('digest') or '').strip()
            key = (ts, title or digest)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                'time': ts,
                'title': title,
                'digest': digest,
                'url': x.get('url') or x.get('appurl') or '',
            })
        if pages > 1:
            time.sleep(0.3)
    return out


# ════════════════════════ ⑨ 个股 F10（概念/行业/地域）════════════════════════
_F10_CACHE = {}


def fetch_f10(code, silent=True):
    """个股 F10：{'concepts':[名], 'concept_codes':[(名, 概念指数码)],
                 'industry':'', 'region':''}"""
    code = str(code)
    if code in _F10_CACHE:
        return _F10_CACHE[code]
    out = {'concepts': [], 'concept_codes': [], 'industry': '', 'region': ''}
    # 概念（td.gnName + clid=同花顺概念指数码）
    h = ths_get('http://basic.10jqka.com.cn/%s/concept.html' % code, enc='gbk',
                silent=silent, ref=REF_F10)
    if h:
        for m in re.finditer(r'class="gnName"[^>]*clid="(\d+)"[^>]*>(.*?)</td>', h, re.S):
            nm = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            if nm:
                out['concept_codes'].append((nm, m.group(1)))
                out['concepts'].append(nm)
        if not out['concepts']:
            for m in re.finditer(r'class="gnName"[^>]*>(.*?)</td>', h, re.S):
                nm = re.sub(r'<[^>]+>', '', m.group(1)).strip()
                if nm:
                    out['concepts'].append(nm)
    # 所属地域 / 所属申万行业
    c = ths_get('http://basic.10jqka.com.cn/%s/company.html' % code, enc='gbk',
                silent=silent, ref=REF_F10)
    if c:
        flat = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', c[:400000]))
        m = re.search(r'所属地域[：:]\s*(\S+?)(?:\s|$)', flat)
        if m:
            out['region'] = m.group(1).strip()
        m2 = re.search(r'所属申万行业[：:]\s*(\S+?)(?:\s|$)', flat)
        if m2:
            out['industry'] = m2.group(1).strip()
    _F10_CACHE[code] = out
    return out


# ════════════════════════ ⑩ 板块成分股 ════════════════════════
def fetch_board_members(ths_code, concept_id=None, max_page=3, silent=True):
    """板块成分股（含现价/涨跌幅），**按涨跌幅降序**（涨停股必然在最前）。

    ths_code  —— 行业指数码 881xxx → 走 thshy/detail
    concept_id —— 概念板块ID（6 位，如 309049=CPO）→ 走 gn/detail

    返回 (members, total)：
      members —— 逐页抓到的成分股（最多 max_page 页）
      total   —— 板块**总家数**（由页面 `page_info` 的「1/N」× 每页行数推出；取不到为 0）

    ⚠️ 同花顺对连续翻页有**严格速率限制**（第 2 页起常直接 HTTPError），
       故只当作「取前列涨幅股」用：**涨停股必然排在最前**，
       取到第 1 页即可覆盖该板块当日绝大多数涨停股。
    """
    if concept_id:
        base = 'http://q.10jqka.com.cn/gn/detail/field/199112/order/desc/page/%d/ajax/1/code/%s/'
        arg = concept_id
    else:
        base = 'http://q.10jqka.com.cn/thshy/detail/field/199112/order/desc/page/%d/ajax/1/code/%s/'
        arg = ths_code
    out, seen, total_pages, per_page, total = [], set(), 0, 0, 0
    for pn in range(1, max_page + 1):
        if pn > 1:
            time.sleep(0.6)          # 翻页节流：连发会被同花顺直接拒（第 2 页起 403）
        # 并发场景下同花顺会偶发 403/空响应（瞬时，非永久）→ 第 1 页重试 2 次；
        # 不重试会导致该板块 stock_total 偏小、zt_count 直接为 None（假缺失）。
        h = ''
        for _try in range(3 if pn == 1 else 1):
            h = ths_get(base % (pn, arg), enc='gbk', silent=silent,
                        ref='http://q.10jqka.com.cn/')
            if h and '<tr>' in h:
                break
            time.sleep(0.35 + 0.25 * _try)
        if not h:
            break
        rows = re.findall(r'<tr>(.*?)</tr>', h, re.S)
        if not rows:
            break
        n0 = len(out)
        n_parsed = 0
        for r in rows:
            cells = [re.sub(r'<[^>]+>', '', x).replace('&nbsp;', '').strip()
                     for x in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', r, re.S)]
            if len(cells) < 6 or not re.fullmatch(r'\d{6}', cells[1] or ''):
                continue
            n_parsed += 1
            c = cells[1]
            if c in seen:
                continue
            seen.add(c)
            if is_excluded(c, cells[2]):
                continue
            out.append({'code': c, 'name': cells[2],
                        'price': _numify(cells[3]), 'pct': _numify(cells[4])})
        if per_page == 0:
            per_page = n_parsed or len(seen)
            m = re.search(r'page_info[^>]*>\s*(\d+)\s*/\s*(\d+)', h)
            if m and per_page:
                try:
                    total_pages = int(m.group(2))
                    total = total_pages * per_page
                except Exception:
                    total = 0
        if len(out) == n0:
            break
    return out, total


def _numify(v):
    try:
        return float(v)
    except Exception:
        return None


def _ts_to_hhmmss(ts):
    """Unix 秒 → HHMMSS int（封板时间口径，与旧东财 fbt 一致）。"""
    try:
        if not ts:
            return 0
        return int(time.strftime('%H%M%S', time.localtime(int(ts))))
    except Exception:
        return 0


def _hhmmss_to_ts(hhmmss, date_yyyymmdd):
    """HHMMSS → Unix 秒（按给定日期），用于与分时对齐。"""
    try:
        s = '%06d' % int(hhmmss)
        t = time.strptime('%s %s:%s:%s' % (date_yyyymmdd, s[:2], s[2:4], s[4:6]),
                          '%Y%m%d %H:%M:%S')
        return int(time.mktime(t))
    except Exception:
        return 0


# ════════════════════════ ⑪ 行业涨幅榜（单请求）════════════════════════
# 同花顺「行业」页一次性给出 51 个行业 + **净流入** + 上涨/下跌家数 + 领涨股，
# 字段与东财 clist 板块榜 1:1，是板块榜最省事、最稳的替代。
_THSHY_URL = 'http://q.10jqka.com.cn/thshy/'


def fetch_industry_rank(silent=True):
    """同花顺行业涨幅榜。返回 [{name, code, pct, amount_yi, net_in_yi, up, down, leader, leader_pct}]"""
    h = ths_get(_THSHY_URL, enc='gbk', silent=silent, ref='http://q.10jqka.com.cn/')
    if not h:
        return []
    out = []
    for r in re.findall(r'<tr>(.*?)</tr>', h, re.S):
        cells = [re.sub(r'<[^>]+>', '', x).replace('&nbsp;', '').strip()
                 for x in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', r, re.S)]
        if len(cells) < 12 or not cells[0].isdigit():
            continue
        code = ''
        m = re.search(r'/thshy/detail/code/(\d+)', r)
        if m:
            code = m.group(1)
        out.append({
            'name': cells[1],
            'code': code,
            'pct': _numify(cells[2]) or 0.0,
            'amount_yi': _numify(cells[4]) or 0.0,
            'net_in_yi': _numify(cells[5]) or 0.0,
            'up': int(_numify(cells[6]) or 0),
            'down': int(_numify(cells[7]) or 0),
            'leader': cells[9],
            'leader_pct': _numify(cells[11]) or 0.0,
            'kind': 'industry',
        })
    for b in out:
        b['total'] = b['up'] + b['down']
    out.sort(key=lambda x: -x['pct'])
    return out


# ════════════════════════ ⑫ 板块指数表 & 概念涨幅榜 ════════════════════════
_INDEX_TABLE = None


def index_table():
    """同花顺板块指数表 {code: name}（885xxx/886xxx=概念，881xxx=行业）。

    优先读仓库里 `_ths_index_names.json`（build_ths_map.py 扫描产出）；
    缺失时现扫码段重建（费时，仅在首次或文件损坏时发生）。
    """
    global _INDEX_TABLE
    if _INDEX_TABLE is not None:
        return _INDEX_TABLE
    p = os.path.join(BASE, '_ths_index_names.json')
    tbl = {}
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            tbl = {str(k): (v.get('name') if isinstance(v, dict) else v)
                   for k, v in raw.items()}
        except Exception:
            tbl = {}
    _INDEX_TABLE = tbl
    return tbl


def concept_codes():
    """全部概念指数码（885xxx/886xxx）。"""
    return [c for c in index_table() if c.startswith(('885', '886'))]


def fetch_concept_rank(codes=None, max_workers=10, top=0):
    """概念涨幅榜：对概念指数批量取 realhead，按当日涨幅降序。

    概念数量多（~360），故并发拉取；`top>0` 时只返回前 top 个。
    与行业榜字段对齐（net_in_yi 概念指数无此字段，留 None）。
    """
    tbl = index_table()
    codes = list(codes) if codes else concept_codes()
    if not codes:
        return []
    secs = [('bk', c) for c in codes]
    snap = fetch_realhead(secs, max_workers=max_workers)
    out = []
    for (_, c), v in snap.items():
        out.append({
            'name': v.get('name') or tbl.get(c) or c,
            'code': c,
            'pct': v.get('pct') or 0.0,
            'amount_yi': round((v.get('amount') or 0) / 1e8, 2),
            'net_in_yi': None,
            'up': 0, 'down': 0, 'total': 0,
            'leader': '', 'leader_pct': 0.0,
            'kind': 'concept',
        })
    out.sort(key=lambda x: -x['pct'])
    return out[:top] if top else out


# ════════════════════════ ⑬ 板块内涨停家数（成分股口径）════════════════════════
def _is_limit_up(code, name, pct):
    """按板块归属判定涨停：主板 10%、创业板(300/301) 20%、ST 5%。"""
    if pct is None:
        return False
    n = str(name or '')
    c = str(code or '')
    lim = 20.0 if c.startswith(('300', '301')) else 10.0
    if 'ST' in n.upper():
        lim = 5.0
    return pct >= lim - 0.35


def count_board_zt(ths_code, concept_id=None, max_page=3):
    """板块成分股里当日涨停家数 / 成分股总数（用于板块榜「涨停/全部」列）。

    入参可用 **THS 板块指数码（881/885/886xxx）** 或 **概念ID**；
    指数码会自动按「名称桥接」解析成 gn/detail 需要的概念ID（见 board_ref）。

    ⚠️ `fetch_board_members` 返回 (members, total_pages) 二元组；
       `total_pages`（页面 page_info 的分母）是该板块**真实总家数**（≈总页数×每页数），
       比「抓到几页」准确得多 —— 故 stock_total 优先用它，抓不到才退回已抓条数。
    """
    if concept_id is None and ths_code and not str(ths_code).startswith('881'):
        kind, arg = board_ref(ths_code)
        # 桥接不到概念ID 不直接放弃：`fetch_board_members` 还能用「指数码 → 同花顺内部反查」兜底
        # （例：885927 CRO概念 无 gn/detail 页，但按指数码仍能取到成分股）。
        concept_id = arg if (kind == 'gn' and arg) else None
    mem, total = fetch_board_members(ths_code, concept_id=concept_id, max_page=max_page)
    if not mem:
        return None
    return {'zt_count': sum(1 for m in mem if _is_limit_up(m['code'], m['name'], m.get('pct'))),
            'stock_total': total or len(mem),
            'leader': (mem[0]['name'] if mem else ''),
            'leader_pct': (mem[0].get('pct') or 0.0) if mem else 0.0}


# ════════════════════════ ⑰ 板块指数码 ↔ 概念ID 桥接 ════════════════════════
# 同花顺有**两套并行的板块编码**，极易混用（2026-09-17 实测）：
#   · 板块**指数码** 881xxx(行业) / 885xxx|886xxx(概念)
#       → 用于 `d.10jqka.com.cn` 的 K线(`line/bk_*`)与快照(`realhead/bk_*`)
#   · 概念**板块ID**   6 位（如 309049=共封装光学(CPO)、307940=存储芯片）
#       → 用于 `q.10jqka.com.cn/gn/detail/.../code/<ID>/` 取**成分股**
#   两套名称完全一致 → 用「名称」做桥接（`_ths_boards.json` 由 build_ths_map.py 产出）。
_BOARDS_TBL = None


def boards_table():
    """`_ths_boards.json` → {'行业': {881xxx: 名}, '概念': {概念ID: 名}}（带缓存）。"""
    global _BOARDS_TBL
    if _BOARDS_TBL is not None:
        return _BOARDS_TBL
    tbl = {'行业': {}, '概念': {}}
    for fn in ('_ths_boards.json',):
        p = os.path.join(BASE, fn)
        if not os.path.exists(p):
            continue
        try:
            with open(p, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            for k in ('行业', '概念'):
                if isinstance(raw.get(k), dict):
                    tbl[k].update({str(a): b for a, b in raw[k].items()})
        except Exception:
            pass
    _BOARDS_TBL = tbl
    return tbl


def index_name(ths_code):
    """板块指数码 → 板块名（取 `_ths_index_names.json` 的 name 字段）。"""
    v = index_table().get(str(ths_code))
    if isinstance(v, dict):
        return v.get('name') or ''
    return v or ''


# 概念ID 别名：同花顺概念表里没有精确同名条目时的桥接（人工确认）
CONCEPT_BOARD_ALIAS = {
    'AIGC概念': '302035',        # → 人工智能
}
# 已知无同花顺概念页的板块（成分股/涨停家数取不到，其余指标正常）：
#   885927 CRO概念 —— 同花顺概念表无「CRO/医药外包」；用行业「医疗服务」(881157 附近) 也不等价，
#   故留空（对应板块的「涨停/全部」列显示「—」，其余列照常）。


def concept_id_by_name(name):
    """概念名 → 概念ID（gn/detail 成分股接口用）。

    `CONCEPT_ALIAS` 用于同花顺两套命名不一致的情况（2026-09-17 实测）：
      · AIGC概念(886019)   → 同花顺概念表里没有「AIGC」，最接近的是「人工智能」(302035)
      · CRO概念(885927)    → 概念表无「CRO」，用「医疗服务」行业页兜底（'THS:<码>'）
    """
    if not name:
        return None
    con = boards_table()['概念']
    if name in con.values():
        for cid, nm in con.items():
            if nm == name:
                return cid
    # 显式别名（概念ID）优先
    alias = CONCEPT_BOARD_ALIAS.get(name)

    # 宽松匹配：去掉「概念/板块/(CPO)」等修饰后按词干比对
    def _stem(s):
        return re.sub(r'(概念|板块|\(.*?\)|（.*?）)', '', str(s)).strip()
    tgt = _stem(name)
    if tgt:
        for cid, nm in con.items():
            if _stem(nm) == tgt:
                return cid
    return alias


def board_ref(ths_code):
    """THS 板块指数码 → 取成分股所需的 (接口类型, 参数)。

    返回 ('ths', 881xxx)  → 走行业页 thshy/detail
         ('gn',  概念ID)   → 走概念页 gn/detail
         (None,  None)    → 无法解析（该板块拿不到成分股）
    """
    c = str(ths_code or '')
    if c.startswith('881'):
        return 'ths', c
    if c.startswith(('885', '886')):
        cid = concept_id_by_name(index_name(c))
        return ('gn', cid) if cid else (None, None)
    return None, None


# ════════════════════════ ⑱ 板块资金流榜（行业/概念，一站式）════════════════════════
# `data.10jqka.com.cn/funds/<gn|hy>zjl/` 的 ajax 分页表一次给全：
#   序号 | 名称 | 指数点位 | 涨跌幅% | 流入(亿) | 流出(亿) | 净额(亿) | 公司家数 | 领涨股 | 领涨股涨跌幅% | 当前价
# 字段与东财板块榜(f3/f62/f104/f128/f136)一一对应，且**公司家数=成分股总数**（与 realhead 的 37 字段同值），
# 是「板块榜」最省事、最稳的替代源（概念约 8 页、行业约 2 页，共 ~430 行）。
_FUND_URL = ('http://data.10jqka.com.cn/funds/%szjl/field/zdf/order/desc/page/%d/ajax/1/')
_FUND_REF = 'http://data.10jqka.com.cn/funds/gnzjl/'


def _fund_rows(h):
    out = []
    for r in re.findall(r'<tr[^>]*>(.*?)</tr>', h, re.S):
        c = [re.sub(r'<[^>]+>', '', x).replace('&nbsp;', '').strip()
             for x in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', r, re.S)]
        if len(c) >= 11 and c[0].isdigit():
            out.append(c)
    return out


def fetch_fund_rank(kind='gn', pages=8, max_workers=6, silent=True):
    """板块资金流榜。kind='gn'(概念) / 'hy'(行业)。

    返回 [{name, pct, amount_yi, net_in_yi, stock_total, leader, leader_pct, index_val, kind}]
    —— 按当日涨跌幅降序（页面即按 zdf 排好，此处不改序）。
    """
    from concurrent.futures import ThreadPoolExecutor

    def _one(p):
        h = ths_get(_FUND_URL % (kind, p), enc='gbk', silent=silent, ref=_FUND_REF)
        return _fund_rows(h or '')

    rows, seen = [], set()
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for R in ex.map(_one, range(1, pages + 1)):
            for c in R:
                nm = c[1]
                if not nm or nm in seen:
                    continue
                seen.add(nm)
                inflow, outflow = _numify(c[4]), _numify(c[5])
                rows.append({
                    'name': nm,
                    'pct': _numify(str(c[3]).replace('%', '')) or 0.0,
                    'amount_yi': round((inflow or 0) + (outflow or 0), 2),
                    'net_in_yi': _numify(c[6]),
                    'stock_total': int(_numify(c[7]) or 0),
                    'leader': c[8] if len(c) > 8 else '',
                    'leader_pct': _numify(str(c[9]).replace('%', '')) if len(c) > 9 else None,
                    'index_val': _numify(c[2]),
                    'kind': 'concept' if kind == 'gn' else 'industry',
                })
    return rows


def fetch_all_fund_rank(max_workers=6, silent=True):
    """概念 + 行业资金流榜合并 → {名称: row}（**概念优先**，同名以概念为准）。"""
    out = {}
    for b in fetch_fund_rank('hy', pages=2, max_workers=max_workers, silent=silent):
        out[b['name']] = b
    for b in fetch_fund_rank('gn', pages=8, max_workers=max_workers, silent=silent):
        out[b['name']] = b
    return out


def _stem_name(s):
    """板块名归一化：去「概念/板块/(CPO)」等修饰，用于宽松匹配。"""
    return re.sub(r'(概念|板块|\(.*?\)|（.*?）)', '', str(s or '')).strip()


def match_fund_row(rows_by_name, name):
    """在资金流榜里按名称匹配（先精确、后词干、再反向包含）。"""
    if not name:
        return None
    r = rows_by_name.get(name)
    if r:
        return r
    st = _stem_name(name)
    if not st:
        return None
    for nm, row in rows_by_name.items():
        if _stem_name(nm) == st:
            return row
    for nm, row in rows_by_name.items():
        s2 = _stem_name(nm)
        if s2 and (s2 in st or st in s2):
            return row
    return None


# ════════════════════════ ⑭ 通用板块/指数日K ════════════════════════
def fetch_daily_kline(ths_code, n=10, prefix='bk_'):
    """板块/指数**日K** → [(日期 YYYYMMDD, 开, 收, 高, 低), ...]（升序）。

    与 `board_kline_src.fetch_ths_kline` 同一接口，此处不重复依赖（避免模块耦合），
    主要用于**算 3日/5日涨幅**（同花顺板块榜页只有当日一列，中期涨幅须自算）。
    """
    t = ths_get('https://d.10jqka.com.cn/v6/line/%s%s/01/last.js' % (prefix, ths_code),
                silent=True, ref='http://q.10jqka.com.cn/')
    if not t or '(' not in t:
        return []
    try:
        j = json.loads(t[t.index('(') + 1:t.rindex(')')])
    except Exception:
        return []
    out = []
    for r in (j.get('data') or '').split(';'):
        a = r.split(',')
        if len(a) < 5:
            continue
        out.append((a[0], _numify(a[1]), _numify(a[4]), _numify(a[2]), _numify(a[3])))
    return out[-n:]


def pct_over(ths_code, days, prefix='bk_'):
    """近 days 个自然K线日的累计涨幅(%)：`最新收盘 / days 根之前的收盘 − 1`。"""
    rows = fetch_daily_kline(ths_code, n=days + 1, prefix=prefix)
    if len(rows) < 2:
        return None
    first = rows[0][2]
    last = rows[-1][2]
    if not first or not last:
        return None
    return round((last / first - 1) * 100, 2)


# ════════════════════════ ⑮ 指数快照 ════════════════════════
# 同花顺指数码（`zs_` 前缀）。北证50(899050) 同花顺无此指数 → 由调用方用腾讯兜底。
THS_INDEX = [
    ('zs_1A0001', 'sh000001', '上证指数'),
    ('zs_399001', 'sz399001', '深证成指'),
    ('zs_399006', 'sz399006', '创业板指'),
    ('zs_1B0688', 'sh000688', '科创50'),
    ('zs_399300', 'sh000300', '沪深300'),
]


def fetch_index(silent=True):
    """主要指数快照：返回 [{'name','code','price','chg_pct','amount_yi'}]（同花顺 realhead）。"""
    secs = [('zs', c.replace('zs_', '')) for c, _, _ in THS_INDEX]
    snap = fetch_realhead(secs, max_workers=6)
    out = []
    for zs_code, tx_code, disp in THS_INDEX:
        v = snap.get(('zs', zs_code.replace('zs_', '')))
        if not v:
            continue
        out.append({
            'name': v.get('name') or disp,
            'code': tx_code,
            'price': round(v['price'], 2) if v.get('price') is not None else None,
            'chg_pct': v.get('pct'),
            'amount_yi': round((v.get('amount') or 0) / 1e8, 2),
            'src': 'ths',
        })
    return out


# ════════════════════════ ⑯ 涨停原因索引（事件驱动标签）════════════════════════
def fetch_reason_index(dates, limit=60, max_workers=6):
    """跨日「涨停原因」索引 {code: {'concepts': [..], 'info': str, 'date': 'YYYYMMDD'}}。

    数据源：`limit_up/block_top`（逐日一次请求，并发）—— 每条 stock_list 自带
      `reason_type`（如「存储芯片+电子元器件分销+资产重组」）与 `reason_info`（行业/事件说明）。
    这是**同花顺对「涨停原因」的第一手口径**，比抓个股新闻更准、更省请求，且完全在同一数据源内。
    后出现的日期覆盖先前日期 → 自动只留该股**最近一次**涨停的原因。
    """
    from concurrent.futures import ThreadPoolExecutor
    out = {}

    def _one(d):
        try:
            return d, fetch_block_top(d, limit=limit)
        except Exception:
            return d, []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for d, blocks in ex.map(_one, list(dates)):
            for blk in blocks:
                for s in (blk.get('stock_list') or []):
                    c = s.get('code')
                    if not c:
                        continue
                    out[c] = {
                        'concepts': [x for x in (s.get('reason_type') or '').split('+') if x],
                        'info': s.get('reason_info') or '',
                        'date': d,
                    }
    return out
