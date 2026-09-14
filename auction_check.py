# -*- coding: utf-8 -*-
"""竞价定格核对（9:25 集合竞价结束后运行）。

用途：自动化每交易日 9:26 运行；看板「梯队」置顶的『竞价核对』卡读取 data['auction']。
口径：竞价额(亿) >= 该票「上板分时量 × 50%」(bid_required_yi) → 合格。
      bid_required_yi 由 fetch_data.py 写入 cands[:5]；其余票回退 round(amount_yi*0.5, 2)。
数据源：东财 push2 快照为主，腾讯 qt 快照兜底。
      f43现价 f46今开 f47量(手) f48额(元) f60昨收（价格均 ×100）

注意：9:26 运行时 data.json 里的 candidates/recommend 是「上一交易日收盘」生成的，
     正是「今日待验证」的那批票 —— 数据天然互通，无需额外传参。
"""
import sys, os, json, time
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

from fetch_data import (BASE, DATA_JSON, http_get, load_json, save_json, save_js,
                        ThreadPoolExecutor)

EM_FIELDS = 'f43,f44,f45,f46,f47,f48,f60,f71'
AUC_MAX = 12


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
    """腾讯快照兜底（字段：3=现价 4=昨收 5=今开 6=量(手) 37=额(万元)）。"""
    mp = 'sh' if str(market) == '1' else 'sz'
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


def _row(c, q):
    """把报价整理成一行核对结果。q 已归一化为 {open, prev, vol, amt}。"""
    line = c.get('bid_required_yi')
    basis = '上板分时量×50%'
    if line is None:
        line = round((c.get('amount_yi') or 0) * 0.5, 2)
        basis = '全天成交额×50%(回退)'
    amt_yi = round((q.get('amt') or 0) / 1e8, 2)
    prev, op = q.get('prev') or 0, q.get('open') or 0
    pct = round((op - prev) / prev * 100, 2) if prev else None
    return {
        'code': c.get('code'), 'name': c.get('name'), 'boards': c.get('boards'),
        'region': c.get('region'), 'concepts': (c.get('concepts') or [])[:3],
        'src': c.get('src', '候选池'),
        'open': op, 'prev': prev, 'pct': pct,
        'vol_shou': q.get('vol') or 0,
        'amt_yi': amt_yi,
        'bid_line_yi': line, 'bid_basis': basis,
        'ok': bool(line and amt_yi >= line),
        'gap': ('高开' if (pct or 0) > 0.2 else ('低开' if (pct or 0) < -0.2 else '平开')) if pct is not None else None,
    }


def run():
    data = load_json(DATA_JSON, {})
    src_date = data.get('date') or ''
    cands = (data.get('candidates') or [])[:AUC_MAX]
    reco = data.get('recommend') or {}

    # 组装待核对的票：推荐票优先，再补候选池（去重）
    picks, seen = [], set()
    if reco.get('code'):
        picks.append({'code': reco['code'], 'name': reco.get('name'), 'boards': reco.get('boards'),
                      'region': reco.get('region'), 'concepts': (reco.get('concepts') or [])[:3],
                      'bid_required_yi': reco.get('bid_required_yi'), 'amount_yi': reco.get('amount_yi'),
                      'src': '次日首选'})
        seen.add(reco['code'])
    for c in cands:
        if c.get('code') and c['code'] not in seen:
            seen.add(c['code'])
            picks.append(dict(c, src='候选池'))

    if not picks:
        print('[竞价] data.json 无 candidates/recommend，跳过')
        return

    def _mkt(c):
        """市场：0=深 1=沪。recommend 无 market 字段，按代码前缀推导。"""
        m = c.get('market')
        if m in (0, 1, '0', '1'):
            return int(m)
        return 1 if str(c.get('code', '')).startswith('6') else 0

    # 并发取快照
    def _fetch(c):
        mk = _mkt(c)
        d = em_quote('%s.%s' % (mk, c['code']))
        if d and d.get('f48'):
            return {'open': (d.get('f46') or 0) / 100.0, 'prev': (d.get('f60') or 0) / 100.0,
                    'vol': (d.get('f47') or 0), 'amt': (d.get('f48') or 0)}
        return tx_quote(c['code'], mk)

    with ThreadPoolExecutor(max_workers=6) as ex:
        quotes = list(ex.map(_fetch, picks))

    rows = [_row(c, q) for c, q in zip(picks, quotes) if q]
    miss = [c['name'] for c, q in zip(picks, quotes) if not q]

    now = datetime.now()
    mins = now.hour * 60 + now.minute
    # 只有 9:15~9:30 抓到的量额才是「竞价口径」；其他时段拿到的是全日成交额，不可与硬线比较
    is_auction = (9 * 60 + 15) <= mins <= (9 * 60 + 30)
    data['auction'] = {
        'date': now.strftime('%Y-%m-%d'),
        'updated': now.strftime('%Y-%m-%d %H:%M:%S'),
        'src_date': src_date,
        'is_auction': is_auction,
        'rows': rows,
        'miss': miss,
    }
    save_json(DATA_JSON, data)
    save_js(os.path.join(BASE, 'data.js'), data)

    okn = sum(1 for r in rows if r['ok'])
    print('[竞价] 核对对象日=%s，共 %d 只（合格 %d）%s'
          % (src_date, len(rows), okn, '' if is_auction else '  ⚠ 非竞价时段，量额为全日口径，不可比'))
    for r in rows:
        print('  %-8s %-6s 开%7.2f %+6.2f%%  竞价额%6.2f亿 / 硬线%5.2f亿  %s'
              % (r['name'], r['code'], r['open'], (r['pct'] or 0), r['amt_yi'],
                 r['bid_line_yi'] or 0, '✅合格' if r['ok'] else '❌不足'))
    if miss:
        print('  [warn] 未取到快照：%s' % '、'.join(miss))


if __name__ == '__main__':
    run()
