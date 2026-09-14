# -*- coding: utf-8 -*-
"""竞价合格判定（并入「连板候选池」，不再单独出卡）。

由 premarket.py 在每交易日 9:25 集合竞价结束后调用：
  · 对 data.json 的 candidates（含 recommend）逐只抓集合竞价快照
  · 结果**直接写回每条候选**：auction_amt_yi / auction_ok / auction_open / auction_pct
  · 口径：竞价额(亿) >= bid_required_yi（= 上板分时量 × 50%）

数据源：东财 push2 快照为主（f46今开 / f47量 / f48额 / f60昨收），腾讯 qt.gtimg.cn 兜底。

⚠️ 只有 9:15~9:30 抓到的量额才是**真竞价额**；其他时段拿到的是全天成交额，不可与硬线比较
   （故写 data['auction_is_live'] 标记）。
"""
import sys, os, json, time
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

from fetch_data import (BASE, DATA_JSON, http_get, load_json, save_json, save_js,
                        ThreadPoolExecutor)

EM_FIELDS = 'f43,f44,f45,f46,f47,f48,f60,f71'


def em_quote(secid):
    """东财实时快照。返回 dict 或 None。"""
    url = ('https://push2.eastmoney.com/api/qt/stock/get?secid=%s&fields=%s'
           % (secid, EM_FIELDS))
    for _ in range(3):
        t = http_get(url, timeout=8, retry=1, silent=True)
        if t:
            try:
                d = (json.loads(t) or {}).get('data')
                if d:
                    return d
            except Exception:
                pass
        time.sleep(0.7)
    return None


def tx_quote(code, market):
    """腾讯快照兜底（字段：4=昨收 5=今开 6=量(手) 37=额(万元)）。"""
    mp = 'sh' if int(market or 0) == 1 else 'sz'
    t = http_get('https://qt.gtimg.cn/q=%s%s' % (mp, code), timeout=8, retry=1, silent=True)
    if not t:
        return None
    s = t.split('~')
    if len(s) < 38:
        return None
    try:
        return {'open': float(s[5] or 0), 'prev': float(s[4] or 0),
                'vol': float(s[6] or 0), 'amt': float(s[37] or 0) * 1e4}
    except Exception:
        return None


def _mkt(c):
    """市场：0=深 1=沪。recommend 无 market 字段时按代码前缀推导。"""
    m = c.get('market')
    if m in (0, 1, '0', '1'):
        return int(m)
    return 1 if str(c.get('code', '')).startswith('6') else 0


def run():
    data = load_json(DATA_JSON, {})
    cands = data.get('candidates') or []
    reco = data.get('recommend') or {}

    picks, seen = [], set()
    if reco.get('code'):
        picks.append({'code': reco['code'], 'name': reco.get('name'),
                      'market': _mkt(reco), 'bid_required_yi': reco.get('bid_required_yi'),
                      'amount_yi': reco.get('amount_yi')})
        seen.add(reco['code'])
    for c in cands:
        if c.get('code') and c['code'] not in seen:
            seen.add(c['code'])
            picks.append(c)

    if not picks:
        print('[竞价] data.json 无 candidates/recommend，跳过')
        return

    def _fetch(c):
        mk = _mkt(c)
        d = em_quote('%s.%s' % (mk, c['code']))
        if d and d.get('f48'):
            return {'open': (d.get('f46') or 0) / 100.0, 'prev': (d.get('f60') or 0) / 100.0,
                    'vol': (d.get('f47') or 0), 'amt': (d.get('f48') or 0)}
        return tx_quote(c['code'], mk)

    with ThreadPoolExecutor(max_workers=6) as ex:
        quotes = list(ex.map(_fetch, picks))

    by, miss = {}, []
    for c, q in zip(picks, quotes):
        if not q:
            miss.append(c.get('name'))
            continue
        line = c.get('bid_required_yi')
        if line is None:
            line = round((c.get('amount_yi') or 0) * 0.5, 2)
        amt_yi = round((q.get('amt') or 0) / 1e8, 2)
        prev, op = q.get('prev') or 0, q.get('open') or 0
        by[c['code']] = {'amt_yi': amt_yi, 'ok': bool(line and amt_yi >= line),
                         'open': op, 'prev': prev,
                         'pct': round((op - prev) / prev * 100, 2) if prev else None,
                         'line': line}

    # 写回候选池每条票（前端「竞价结论」列直接读这几个字段）
    n_ok = 0
    for c in cands:
        r = by.get(c.get('code'))
        if not r:
            continue
        c['auction_amt_yi'] = r['amt_yi']
        c['auction_ok'] = r['ok']
        c['auction_open'] = r['open']
        c['auction_pct'] = r['pct']
        if r['ok']:
            n_ok += 1
    if reco.get('code') in by:
        r = by[reco['code']]
        reco['auction_amt_yi'] = r['amt_yi']
        reco['auction_ok'] = r['ok']
        reco['auction_open'] = r['open']
        reco['auction_pct'] = r['pct']

    now = datetime.now()
    mins = now.hour * 60 + now.minute
    is_auction = (9 * 60 + 15) <= mins <= (9 * 60 + 30)
    data['candidates'] = cands
    data['recommend'] = reco
    data.pop('auction', None)          # 旧版独立竞价块已废弃（并入候选池），清掉残留
    data['auction_updated'] = now.strftime('%Y-%m-%d %H:%M:%S')
    data['auction_is_live'] = is_auction
    save_json(DATA_JSON, data)
    save_js(os.path.join(BASE, 'data.js'), data)

    print('[竞价] 核对 %d 只 · 合格 %d · 对象日=%s%s'
          % (len(by), n_ok, data.get('date') or '',
             '' if is_auction else '  ⚠ 非竞价时段，量额为全日口径不可比'))
    for c in cands:
        r = by.get(c.get('code'))
        if not r:
            continue
        print('  %-8s %-6s 开%7.2f %+6.2f%%  竞价额%6.2f亿 / 硬线%5.2f亿  %s'
              % (c['name'], c['code'], r['open'], (r['pct'] or 0), r['amt_yi'],
                 r['line'] or 0, '✅合格' if r['ok'] else '❌不足'))
    if miss:
        print('  [warn] 未取到快照：%s' % '、'.join(miss))


if __name__ == '__main__':
    run()
