# -*- coding: utf-8 -*-
"""
B段复盘数据采集 —— 输出 review_data.json

东方财富妙想 MCP / 腾讯自选股 MCP 在当前会话不可用时，改用东财公开行情接口等效取数。
所有数值均为接口原值，不做任何推断或补全。
"""
import json, os, time, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import BASE, http_get, load_json, save_json
from module4 import em_get

TODAY = datetime.now().strftime('%Y-%m-%d')
OUT = os.path.join(BASE, 'review_data.json')

HIS_HOSTS = ['push2his.eastmoney.com', 'push2hisdelay.eastmoney.com']
EX_HOSTS = ['push2ex.eastmoney.com']


def his_get(path):
    for h in HIS_HOSTS:
        t = http_get('https://%s%s' % (h, path), timeout=20, retry=1, silent=True)
        if t:
            return t
    return None


def ex_get(path):
    for h in EX_HOSTS:
        t = http_get('https://%s%s' % (h, path), timeout=20, retry=1, silent=True)
        if t:
            return t
    return None


def js(t):
    try:
        return json.loads(t)
    except Exception:
        return None


# ---------------- 1. 指数 + 涨跌家数 ----------------
def fetch_indices():
    secids = {
        '上证指数': '1.000001', '深证成指': '0.399001', '创业板指': '0.399006',
        '科创50': '1.000688', '北证50': '0.899050', '沪深300': '1.000300',
        '中证500': '1.000905', '中证1000': '1.000852',
    }
    ids = ','.join(secids.values())
    t = em_get('/api/qt/ulist.np/get?fltt=2&secids=%s&fields=f1,f2,f3,f4,f6,f12,f14,f104,f105,f106' % ids)
    d = js(t)
    out = []
    if d:
        for it in (d.get('data') or {}).get('diff') or []:
            out.append({'name': it.get('f14'), 'code': it.get('f12'),
                        'price': it.get('f2'), 'chg': it.get('f3'),
                        'amount_yi': round((it.get('f6') or 0) / 1e8, 2),
                        'up': it.get('f104'), 'down': it.get('f105'), 'flat': it.get('f106')})
    return out


def fetch_amount_hist():
    """沪+深 近5日成交额（亿元），用于环比"""
    res = {}
    for nm, secid in (('sh', '1.000001'), ('sz', '0.399001')):
        t = his_get('/api/qt/stock/kline/get?secid=%s&klt=101&fqt=1&lmt=6&'
                    'fields1=f1,f2,f3&fields2=f51,f56' % secid)
        d = js(t)
        if not d:
            continue
        kl = ((d.get('data') or {}).get('klines') or [])
        for line in kl:
            p = line.split(',')
            if len(p) >= 2:
                try:
                    res.setdefault(p[0], {})[nm] = float(p[1]) / 1e8
                except ValueError:
                    pass
    return {d: {k: round(v, 2) for k, v in m.items()} for d, m in sorted(res.items())}


# ---------------- 2. 板块（行业/概念）主力净流入榜 ----------------
def fetch_board_flow(topn=30):
    out = []
    for fs, kind in (('m:90+t:2+f:!50', '行业'), ('m:90+t:3+f:!50', '概念')):
        t = em_get('/api/qt/clist/get?pn=1&pz=%d&po=1&np=1&fltt=2&invt=2&fid=f62&fs=%s&'
                   'fields=f12,f14,f3,f62,f104,f105,f128,f136' % (topn, fs))
        d = js(t)
        if not d:
            continue
        for it in (d.get('data') or {}).get('diff') or []:
            out.append({'kind': kind, 'name': it.get('f14'), 'code': it.get('f12'),
                        'pct': it.get('f3'),
                        'zjlryi': round((it.get('f62') or 0) / 1e8, 2),
                        'up': it.get('f104'), 'down': it.get('f105'),
                        'leader': it.get('f128'), 'leader_pct': it.get('f136')})
    return out


# ---------------- 3. 个股主力净流入 TOP ----------------
def fetch_stock_flow(topn=15):
    fs = 'm:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2'
    t = em_get('/api/qt/clist/get?pn=1&pz=%d&po=1&np=1&fltt=2&invt=2&fid=f62&fs=%s&'
               'fields=f12,f14,f2,f3,f62' % (topn, fs))
    d = js(t)
    out = []
    if d:
        for it in (d.get('data') or {}).get('diff') or []:
            out.append({'code': it.get('f12'), 'name': it.get('f14'),
                        'price': it.get('f2'), 'chg': it.get('f3'),
                        'zjlryi': round((it.get('f62') or 0) / 1e8, 2)})
    return out


# ---------------- 4. 跌幅榜 / 杀跌方向 ----------------
def fetch_losers(topn=20):
    fs = 'm:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2'
    t = em_get('/api/qt/clist/get?pn=1&pz=%d&po=1&np=1&fltt=2&invt=2&fid=f3&fs=%s&'
               'fields=f12,f14,f2,f3,f8,f6' % (topn, fs))
    d = js(t)
    out = []
    if d:
        for it in (d.get('data') or {}).get('diff') or []:
            out.append({'code': it.get('f12'), 'name': it.get('f14'),
                        'price': it.get('f2'), 'chg': it.get('f3'),
                        'turnover': it.get('f8'),
                        'amount_yi': round((it.get('f6') or 0) / 1e8, 2)})
    return out


# ---------------- 5. 跌停池 ----------------
def fetch_dt_pool(date_yyyymmdd):
    t = ex_get('/getTopicDTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&'
               'Pageindex=0&pagesize=300&sort=fund%%3Aasc&date=%s' % date_yyyymmdd)
    d = js(t)
    out = []
    if d and d.get('data'):
        for it in d['data'].get('pool') or []:
            out.append({'code': it.get('c'), 'name': it.get('n'),
                        'price': round((it.get('p') or 0) / 1000, 2),
                        'chg': round((it.get('zdp') or 0) / 100, 2),
                        'amount_yi': round((it.get('amount') or 0) / 1e8, 2),
                        'lbc': it.get('lbc')})
    return out


# ---------------- 6. 炸板池 ----------------
def fetch_zb_pool(date_yyyymmdd):
    t = ex_get('/getTopicZBPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&'
               'Pageindex=0&pagesize=300&sort=fbt%%3Aasc&date=%s' % date_yyyymmdd)
    d = js(t)
    out = []
    if d and d.get('data'):
        for it in d['data'].get('pool') or []:
            out.append({'code': it.get('c'), 'name': it.get('n'),
                        'price': round((it.get('p') or 0) / 1000, 2),
                        'chg': round((it.get('zdp') or 0) / 100, 2),
                        'lbc': it.get('lbc'),
                        'amount_yi': round((it.get('amount') or 0) / 1e8, 2)})
    return out


# ---------------- 7. 昨日板块表现（东财特色板块） ----------------
def fetch_yesterday_boards():
    codes = {'BK0816': '昨日连板', 'BK1051': '昨日连板_含一字', 'BK0815': '昨日涨停',
             'BK1050': '昨日涨停_含一字', 'BK1630': '昨日首板', 'BK1645': '昨日打二板以上表现',
             'BK1631': '昨日炸板', 'BK0817': '昨日触板'}
    ids = ','.join('90.' + c for c in codes)
    t = em_get('/api/qt/ulist.np/get?fltt=2&secids=%s&fields=f2,f3,f12,f14' % ids)
    d = js(t)
    out = []
    if d:
        for it in (d.get('data') or {}).get('diff') or []:
            out.append({'code': it.get('f12'), 'name': codes.get(it.get('f12'), it.get('f14')),
                        'pct': it.get('f3')})
    return out


def main():
    r = {'date': TODAY, 'source': 'eastmoney_public_api'}

    print('[1] 指数...')
    r['indices'] = fetch_indices()
    print('    %d 条' % len(r['indices']))

    print('[2] 成交额历史...')
    r['amount_hist'] = fetch_amount_hist()
    print('    %s' % list(r['amount_hist'].keys()))

    print('[3] 板块资金流...')
    r['board_flow'] = fetch_board_flow()
    print('    %d 条' % len(r['board_flow']))

    print('[4] 个股主力净流入...')
    r['stock_flow'] = fetch_stock_flow()
    print('    %d 条' % len(r['stock_flow']))

    print('[5] 跌幅榜...')
    r['losers'] = fetch_losers()
    print('    %d 条' % len(r['losers']))

    print('[6] 跌停池...')
    r['dt_pool'] = fetch_dt_pool(TODAY.replace('-', ''))
    print('    %d 条' % len(r['dt_pool']))

    print('[7] 炸板池...')
    r['zb_pool'] = fetch_zb_pool(TODAY.replace('-', ''))
    print('    %d 条' % len(r['zb_pool']))

    print('[8] 昨日板块表现...')
    r['ytd_boards'] = fetch_yesterday_boards()
    print('    %d 条' % len(r['ytd_boards']))

    save_json(OUT, r)
    print('✅ 写入 %s' % OUT)


if __name__ == '__main__':
    main()
