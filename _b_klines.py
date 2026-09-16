# -*- coding: utf-8 -*-
"""B段：指数K线（新浪源）+ 缺口/量能核对"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import BASE, http_get, load_json, save_json

SYMS = [('sh000001', '上证指数'), ('sz399001', '深证成指'), ('sz399006', '创业板指'),
        ('sh000688', '科创50'), ('sh000300', '沪深300'), ('sh000905', '中证500'),
        ('sh000852', '中证1000'), ('bj899050', '北证50')]


def get_kline(sym, n=15):
    url = ('https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           'CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=%d' % (sym, n))
    t = http_get(url, timeout=20, retry=2, silent=True)
    if not t:
        return []
    try:
        arr = json.loads(t)
    except Exception:
        return []
    return [{'d': a['day'], 'o': float(a['open']), 'c': float(a['close']),
             'h': float(a['high']), 'l': float(a['low']), 'v': float(a['volume'])} for a in arr]


def main():
    out = {}
    for sym, nm in SYMS:
        k = get_kline(sym)
        out[nm] = k
        print('%s %s %d根' % (nm, k[-1]['d'] if k else 'FAIL', len(k)))
        time.sleep(0.3)
    save_json(os.path.join(BASE, '_b_klines.json'), out)

    # 缺口/量能核对
    print('\n=== 缺口与量能核对 ===')
    for nm, k in out.items():
        if len(k) < 3:
            continue
        t, y = k[-1], k[-2]
        gap = None
        if y['l'] > t['h']:
            gap = ('向下跳空 %.2f~%.2f' % (t['h'], y['l']), t['h'] >= y['l'])
        print('%-8s 收%.2f 涨跌%.2f%% | 今高%.2f 昨低%.2f | 量比(今/昨) %.3f | %s' % (
            nm, t['c'], (t['c'] / y['c'] - 1) * 100, t['h'], y['l'], t['v'] / y['v'],
            gap[0] if gap else '无跳空'))


if __name__ == '__main__':
    main()
