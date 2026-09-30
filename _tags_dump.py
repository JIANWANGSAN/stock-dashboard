# -*- coding: utf-8 -*-
"""
B段辅助：批量给涨停池 / 炸板池 / 跌停池 / 资金流TOP 补东财概念标签，落盘 _tags_dump.json

复用 fetch_data.fetch_stock_tags 的「所属板块 + 人工概念修正 + 当下热度排序」口径，
与仪表盘节点票标签保持同一套命名，避免复盘里出现两套概念叫法。
"""
import json, os, sys, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import BASE, load_json, save_json, fetch_stock_tags, market_of, http_get
from module4 import em_get

# 涨停/跌停/炸板池在 push2ex 域，与 push2 域不同，必须单独拼 URL
EX_HOST = 'push2ex.eastmoney.com'
UT = '7eea3edcaed734bea9cbfc24409ed989'


def ex_get(path):
    return http_get('https://%s%s' % (EX_HOST, path), timeout=20, retry=1, silent=True)

TODAY = datetime.now().strftime('%Y-%m-%d')
OUT = os.path.join(BASE, '_tags_dump.json')
TAG_CACHE = os.path.join(BASE, '.stock_cache.json')


def js(t):
    try:
        return json.loads(t)
    except Exception:
        return None


def fetch_zb_pool(d):
    t = ex_get('/getTopicZBPool?ut=%s&dpt=wz.ztzt&'
               'Pageindex=0&pagesize=300&sort=fbt%%3Aasc&date=%s' % (UT, d))
    o = js(t) or {}
    out = []
    for it in (o.get('data') or {}).get('pool') or []:
        out.append({'code': it.get('c'), 'name': it.get('n'),
                    'price': round((it.get('p') or 0) / 1000, 2),
                    'chg': round(it.get('zdp') or 0, 2),
                    'lbc': it.get('lbc'),
                    'amount_yi': round((it.get('amount') or 0) / 1e8, 2),
                    'zbc': it.get('zbc')})
    return out


def fetch_dt_pool(d):
    t = ex_get('/getTopicDTPool?ut=%s&dpt=wz.ztzt&'
               'Pageindex=0&pagesize=300&sort=fund%%3Aasc&date=%s' % (UT, d))
    o = js(t) or {}
    out = []
    for it in (o.get('data') or {}).get('pool') or []:
        out.append({'code': it.get('c'), 'name': it.get('n'),
                    'price': round((it.get('p') or 0) / 1000, 2),
                    'chg': round(it.get('zdp') or 0, 2),
                    'lbc': it.get('lbc'),
                    'amount_yi': round((it.get('amount') or 0) / 1e8, 2)})
    return out


def fetch_stock_flow(topn=12):
    fs = ('m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,'
          'm:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2')
    t = em_get('/api/qt/clist/get?pn=1&pz=%d&po=1&np=1&fltt=2&invt=2&fid=f62&fs=%s&'
               'fields=f12,f14,f2,f3,f62,f8,f6' % (topn, fs))
    o = js(t) or {}
    out = []
    for it in (o.get('data') or {}).get('diff') or []:
        out.append({'code': it.get('f12'), 'name': it.get('f14'),
                    'price': it.get('f2'), 'chg': it.get('f3'),
                    'turnover': it.get('f8'),
                    'amount_yi': round((it.get('f6') or 0) / 1e8, 2),
                    'zjlryi': round((it.get('f62') or 0) / 1e8, 2)})
    return out


def tag_batch(items, cache):
    """就地补 region/concepts/industry/pinyin，带节流"""
    n = 0
    for it in items:
        code, name = str(it.get('code') or ''), it.get('name') or ''
        if not code:
            continue
        try:
            t = fetch_stock_tags(code, market_of(code), name, cache)
        except Exception:
            t = {}
        it['region'] = t.get('region', '—')
        it['concepts'] = t.get('concepts', []) or []
        it['industry'] = t.get('industry', '')
        it['top_concepts'] = (it['concepts'] or [])[:4]
        n += 1
        if n % 15 == 0:
            print('   ...tags %d' % n)
        time.sleep(0.12)
    return items


def main():
    data = load_json(os.path.join(BASE, 'data.json'), {})
    cache = load_json(TAG_CACHE, {})
    d = TODAY.replace('-', '')

    print('[1] 涨停池 %d 只' % len(data.get('zt_pool') or []))
    zt = [dict(x) for x in (data.get('zt_pool') or [])]
    tag_batch(zt, cache)

    print('[2] 炸板池')
    zb = fetch_zb_pool(d)
    print('    %d 只' % len(zb))
    tag_batch(zb, cache)

    print('[3] 跌停池')
    dt = fetch_dt_pool(d)
    print('    %d 只' % len(dt))
    tag_batch(dt, cache)

    print('[4] 资金流TOP')
    fl = fetch_stock_flow()
    print('    %d 只' % len(fl))
    tag_batch(fl, cache)

    save_json(TAG_CACHE, cache)
    save_json(OUT, {'date': TODAY, 'zt': zt, 'zb': zb, 'dt': dt, 'flow': fl})
    print('✅ 写入 %s' % OUT)


if __name__ == '__main__':
    main()
