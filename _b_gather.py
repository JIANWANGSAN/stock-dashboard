# -*- coding: utf-8 -*-
"""B段补充取数：批量行情/涨停价核对、指数K线缺口量能、行业跌幅榜、板块资金流。"""
import json, os, sys, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import BASE, http_get, load_json, save_json, market_of
from module4 import em_get

TODAY = datetime.now().strftime('%Y-%m-%d')
D = TODAY.replace('-', '')


def js(t):
    try:
        return json.loads(t)
    except Exception:
        return None


def limit_pct(code):
    c = str(code)
    if c.startswith('300') or c.startswith('301') or c.startswith('688') or c.startswith('689'):
        return 20.0
    if c.startswith('8') or c.startswith('4') or c.startswith('92'):
        return 30.0
    return 10.0


def quotes(codes):
    """批量 ulist.np 取行情"""
    out = {}
    for i in range(0, len(codes), 50):
        chunk = codes[i:i + 50]
        secs = ','.join('%d.%s' % (market_of(c), c) for c in chunk)
        t = em_get('/api/qt/ulist.np/get?fltt=2&invt=2&secids=%s&fields=f12,f14,f2,f3,f6,f8,f17,f18,f15,f16,f20,f21' % secs)
        o = js(t) or {}
        for it in (o.get('data') or {}).get('diff') or []:
            out[str(it.get('f12'))] = it
        time.sleep(0.2)
    return out


def ex_get(path):
    return http_get('https://push2ex.eastmoney.com%s' % path, timeout=20, retry=1, silent=True)


UT = '7eea3edcaed734bea9cbfc24409ed989'


def zt_pool_of(d):
    t = ex_get('/getTopicZTPool?ut=%s&dpt=wz.ztzt&Pageindex=0&pagesize=600&sort=fbt%%3Aasc&date=%s' % (UT, d))
    o = js(t) or {}
    out = []
    for it in (o.get('data') or {}).get('pool') or []:
        out.append({'code': it.get('c'), 'name': it.get('n'),
                    'price': round((it.get('p') or 0) / 1000, 2),
                    'chg': round(it.get('zdp') or 0, 2),
                    'lbc': it.get('lbc'), 'zbc': it.get('zbc'),
                    'amount_yi': round((it.get('amount') or 0) / 1e8, 2),
                    'first_seal': it.get('fbt'), 'last_seal': it.get('lbt'),
                    'seal_fund_yi': round((it.get('fund') or 0) / 1e8, 2)})
    return out


def main():
    res = {}
    # ---- 1. 今日涨停池（含成交额/首封时间）----
    today_zt = zt_pool_of(D)
    res['today_zt'] = today_zt
    print('[1] 今日涨停池 %d 只' % len(today_zt))

    # ---- 2. T-1 涨停池 ----
    prev_zt = zt_pool_of('20260915')
    res['prev_zt'] = prev_zt
    print('[2] T-1(09-15) 涨停池 %d 只' % len(prev_zt))

    # ---- 3. T-2 涨停池（算T-1断板）----
    prev2_zt = zt_pool_of('20260914')
    res['prev2_zt'] = prev2_zt
    print('[3] T-2(09-14) 涨停池 %d 只' % len(prev2_zt))

    # ---- 4. T-1 名单今日行情核对 ----
    pz_codes = [x['code'] for x in prev_zt]
    p2_codes = [x['code'] for x in prev2_zt]
    allc = list(dict.fromkeys(pz_codes + p2_codes))
    q = quotes(allc)
    res['t1_quotes'] = q
    print('[4] 行情核对 %d 只' % len(q))

    # ---- 5. 指数日K（腾讯）----
    klines = {}
    for sym, nm in [('sh000001', '上证指数'), ('sz399001', '深证成指'), ('sz399006', '创业板指'),
                    ('sh000688', '科创50'), ('sz399905', '中证500')]:
        t = http_get('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,15,qfq' % sym,
                     timeout=20, retry=2, silent=True)
        try:
            o = json.loads(t)
            arr = o['data'][sym].get('day') or o['data'][sym].get('qfqday') or []
            klines[nm] = [{'d': a[0], 'o': float(a[1]), 'c': float(a[2]), 'h': float(a[3]),
                           'l': float(a[4]), 'v': float(a[5])} for a in arr][-12:]
        except Exception as e:
            klines[nm] = []
            print('   kline fail', nm, e)
    res['klines'] = klines
    print('[5] 指数K线 %s' % {k: len(v) for k, v in klines.items()})

    # ---- 6. 行业跌幅榜（杀跌方向）----
    t = em_get('/api/qt/clist/get?pn=1&pz=25&po=0&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f3,f62,f104,f105,f128,f140')
    o = js(t) or {}
    res['ind_drop'] = [{'code': i.get('f12'), 'name': i.get('f14'), 'pct': i.get('f3'),
                        'zjlryi': round((i.get('f62') or 0) / 1e8, 2)} for i in (o.get('data') or {}).get('diff') or []]
    print('[6] 行业跌幅榜 %d' % len(res['ind_drop']))

    # ---- 7. 行业/概念涨幅榜 ----
    for fs, key in [('m:90+t:2', 'ind_rise'), ('m:90+t:3', 'con_rise')]:
        t = em_get('/api/qt/clist/get?pn=1&pz=25&po=1&np=1&fltt=2&invt=2&fid=f3&fs=%s&fields=f12,f14,f3,f62,f128,f140' % fs)
        o = js(t) or {}
        res[key] = [{'code': i.get('f12'), 'name': i.get('f14'), 'pct': i.get('f3'),
                     'zjlryi': round((i.get('f62') or 0) / 1e8, 2),
                     'leader': i.get('f128')} for i in (o.get('data') or {}).get('diff') or []]
        print('[7] %s %d' % (key, len(res[key])))
        time.sleep(0.2)

    # ---- 8. 板块资金流 TOP/BOTTOM ----
    t = em_get('/api/qt/clist/get?pn=1&pz=15&po=1&np=1&fltt=2&invt=2&fid=f62&fs=m:90+t:2,m:90+t:3&fields=f12,f14,f3,f62')
    o = js(t) or {}
    res['flow_top'] = [{'name': i.get('f14'), 'pct': i.get('f3'), 'zjlryi': round((i.get('f62') or 0) / 1e8, 2)}
                       for i in (o.get('data') or {}).get('diff') or []]
    t = em_get('/api/qt/clist/get?pn=1&pz=15&po=0&np=1&fltt=2&invt=2&fid=f62&fs=m:90+t:2,m:90+t:3&fields=f12,f14,f3,f62')
    o = js(t) or {}
    res['flow_bot'] = [{'name': i.get('f14'), 'pct': i.get('f3'), 'zjlryi': round((i.get('f62') or 0) / 1e8, 2)}
                       for i in (o.get('data') or {}).get('diff') or []]
    print('[8] 板块资金流 top/bot %d/%d' % (len(res['flow_top']), len(res['flow_bot'])))

    save_json(os.path.join(BASE, '_b_gather.json'), res)
    print('✅ 写入 _b_gather.json')


if __name__ == '__main__':
    main()
