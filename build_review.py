# -*- coding: utf-8 -*-
"""B段复盘素材整理 —— 输出 review_dump.txt（供 AI 一次性取用）"""
import json, os, sys, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import BASE, http_get, load_json, save_json
from module4 import em_get

TODAY = datetime.now().strftime('%Y-%m-%d')
D = TODAY.replace('-', '')


def js(t):
    try:
        return json.loads(t)
    except Exception:
        return None


def clist(params):
    t = em_get('/api/qt/clist/get?' + params)
    d = js(t)
    if not d:
        return []
    return (d.get('data') or {}).get('diff') or []


# ---------- 跌幅榜（升序） ----------
def losers(n=20):
    fs = 'm:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2'
    rows = clist('pn=1&pz=%d&po=0&np=1&fltt=2&invt=2&fid=f3&fs=%s&fields=f12,f14,f2,f3,f8,f6' % (n, fs))
    return [{'code': r.get('f12'), 'name': r.get('f14'), 'chg': r.get('f3'),
             'turnover': r.get('f8'), 'amount_yi': round((r.get('f6') or 0) / 1e8, 2)} for r in rows]


# ---------- 昨日连板成分股 + 今日表现（晋级/断板） ----------
def ytd_lianban():
    rows = clist('pn=1&pz=200&po=1&np=1&fltt=2&invt=2&fid=f3&fs=b:BK0816&fields=f12,f14,f3,f2')
    return [{'code': r.get('f12'), 'name': r.get('f14'), 'chg_today': r.get('f3'),
             'price': r.get('f2')} for r in rows]


# ---------- 炸板池 ----------
def zb_pool():
    t = http_get('https://push2ex.eastmoney.com/getTopicZBPool?ut=7eea3edcaed734bea9cbfc24409ed989'
                 '&dpt=wz.ztzt&Pageindex=0&pagesize=300&sort=fbt%%3Aasc&date=%s' % D, timeout=20, retry=1)
    d = js(t)
    out = []
    if d and d.get('data'):
        for it in d['data'].get('pool') or []:
            zt = it.get('zttj') or {}
            out.append({'code': it.get('c'), 'name': it.get('n'),
                        'price': round((it.get('p') or 0) / 1000, 2),
                        'chg': round(it.get('zdp') or 0, 2),
                        'zbc': it.get('zbc'), 'hybk': it.get('hybk'),
                        'days': zt.get('days'), 'ct': zt.get('ct'),
                        'amount_yi': round((it.get('amount') or 0) / 1e8, 2)})
    return out


# ---------- 跌停池 ----------
def dt_pool():
    t = http_get('https://push2ex.eastmoney.com/getTopicDTPool?ut=7eea3edcaed734bea9cbfc24409ed989'
                 '&dpt=wz.ztzt&Pageindex=0&pagesize=300&sort=fund%%3Aasc&date=%s' % D, timeout=20, retry=1)
    d = js(t)
    out = []
    if d and d.get('data'):
        for it in d['data'].get('pool') or []:
            out.append({'code': it.get('c'), 'name': it.get('n'),
                        'price': round((it.get('p') or 0) / 1000, 2),
                        'chg': round(it.get('zdp') or 0, 2),
                        'amount_yi': round((it.get('amount') or 0) / 1e8, 2)})
    return out


def main():
    data = load_json(os.path.join(BASE, 'data.json'), {})
    rv = load_json(os.path.join(BASE, 'review_data.json'), {})
    cache = load_json(os.path.join(BASE, '.stock_cache.json'), {})

    zt = data.get('zt_pool', []) or []
    for s in zt:
        c = cache.get('%d.%s' % (s.get('market', 0), s.get('code'))) or {}
        s['concepts'] = c.get('concepts') or []
        s['region'] = c.get('region') or ''

    out = {
        'date': TODAY,
        'indices': rv.get('indices', []),
        'ytd_boards': rv.get('ytd_boards', []),
        'board_flow': rv.get('board_flow', []),
        'stock_flow': rv.get('stock_flow', []),
        'board_daily': data.get('board_daily', []),
        'board_3d': data.get('board_3d', []),
        'ladder': data.get('ladder', [])[-3:],
        'nodes': data.get('nodes', [])[-3:],
        'recommend': data.get('recommend'),
        'zt_all': zt,
        'zt_lb': [s for s in zt if s.get('lbc', 1) >= 2],
        'losers': losers(),
        'ytd_lianban': ytd_lianban(),
        'zb_pool': zb_pool(),
        'dt_pool': dt_pool(),
    }
    save_json(os.path.join(BASE, 'review_dump.json'), out)

    # ---- 控制台精简视图 ----
    print('=== 连板梯队（今日涨停池 lbc>=2） ===')
    byb = {}
    for s in out['zt_lb']:
        byb.setdefault(s.get('lbc'), []).append(s)
    for b in sorted(byb, reverse=True):
        for s in byb[b]:
            print('%d板 %s(%s) chg=%.2f 换手%.2f 额%.2f亿 一字=%s 行业=%s 概念=%s'
                  % (b, s['name'], s['code'], s.get('chg', 0), s.get('turnover', 0),
                     s.get('amount', 0) / 1e8, s.get('is_yizi'), s.get('industry'),
                     '、'.join((s.get('concepts') or [])[:4])))
    print()
    print('=== 首板（lbc=1）共 %d 只 ===' % len([s for s in zt if s.get('lbc', 1) == 1]))
    for s in [x for x in zt if x.get('lbc', 1) == 1][:40]:
        print('  %s(%s) chg=%.2f 换手%.2f 额%.2f亿 行业=%s 概念=%s'
              % (s['name'], s['code'], s.get('chg', 0), s.get('turnover', 0),
                 s.get('amount', 0) / 1e8, s.get('industry'), '、'.join((s.get('concepts') or [])[:4])))
    print()
    print('=== 昨日连板股今日表现 ===')
    ztmap = {s['code']: s for s in zt}
    for y in out['ytd_lianban']:
        st = '晋级' if y['code'] in ztmap else '未涨停'
        print('  %s(%s) 今日%.2f%% %s' % (y['name'], y['code'], y.get('chg_today') or 0, st))
    print()
    print('=== 跌停 %d ===' % len(out['dt_pool']))
    for x in out['dt_pool']:
        print('  %s(%s) %.2f%%' % (x['name'], x['code'], x['chg']))
    print()
    print('=== 跌幅榜 ===')
    for x in out['losers'][:15]:
        print('  %s(%s) %.2f%% 换手%s 额%s亿' % (x['name'], x['code'], x['chg'], x['turnover'], x['amount_yi']))
    print()
    print('=== 炸板 %d ===' % len(out['zb_pool']))
    for x in out['zb_pool']:
        print('  %s(%s) %.2f%% 炸板%s次 %s天%s板 行业=%s' % (x['name'], x['code'], x['chg'], x['zbc'], x['days'], x['ct'], x['hybk']))
    print()
    print('=== 板块日榜 ===')
    for b in out['board_daily']:
        print('  %-12s %6s%% 涨停%d/%d 龙头%s(%.2f%%) 主力%.2f亿'
              % (b['name'], b.get('pct'), b.get('up', 0), b.get('total', 0),
                 b.get('leader'), b.get('leader_pct', 0), (b.get('zljlr_wan') or 0) / 1e4))
    print()
    print('=== 板块资金流入TOP(概念) ===')
    for b in [x for x in out['board_flow'] if x['kind'] == '概念'][:12]:
        print('  %-14s %6s%% 主力%+8.2f亿 龙头%s(%s%%)' % (b['name'], b['pct'], b['zjlryi'], b['leader'], b['leader_pct']))
    print('=== 板块资金流出TOP(概念) ===')
    for b in sorted([x for x in out['board_flow'] if x['kind'] == '概念'], key=lambda x: x['zjlryi'])[:8]:
        print('  %-14s %6s%% 主力%+8.2f亿 龙头%s(%s%%)' % (b['name'], b['pct'], b['zjlryi'], b['leader'], b['leader_pct']))
    print('✅ dump -> review_dump.json')


if __name__ == '__main__':
    main()
