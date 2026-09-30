# -*- coding: utf-8 -*-
"""竞价合格判定（并入「连板候选池」，不再单独出卡）。

由 premarket.py 在每交易日 **9:26**（集合竞价 9:25 定格之后）调用：
  · 对 data.json 的 candidates（含 recommend）逐只抓集合竞价快照
  · 结果**直接写回每条候选**：auction_amt_yi / auction_ok / auction_open / auction_pct
  · 口径：竞价额(亿) >= bid_required_yi（= 上板分时量 × 50%）

数据源：**腾讯 `qt.gtimg.cn` 快照为主（今开 / 量 / 额 / 昨收），同花顺 `realhead` 兜底**
   —— 东财已全站停用（push2 快照 / push2his 分时路径均已移除），见 项目约定.md §3.6 / §5 / §9.1。

⚠️ 只有 9:15~9:30 抓到的量额才是**真竞价额**；其他时段拿到的是开盘后的**累计**成交额，
   不可与硬线比较（故写 data['auction_is_live'] 标记）。

【2026-09-16 补取】任务实际常晚于 9:30 触发（如 9:34），此时快照已是累计口径。
   补救：用**同花顺当日分时首根（09:30 = 集合竞价）**的成交额还原真竞价额
   —— 与 fetch_data.fetch_seal_amount 同一口径（首根即集合竞价）。取到则按竞价口径判定，
   写 data['auction_source']='minute_0930' 且 auction_is_live=True；取不到才退回累计口径并标记 false。
   ⚠️ 同花顺分时**必须校验日期**（盘前返回的是上一交易日），防止把昨天的竞价当今天的。
"""
import sys, os, json, time
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

import ths_source as THS
from fetch_data import (BASE, DATA_JSON, http_get, load_json, save_json, save_js,
                        ThreadPoolExecutor)


def ths_quote(code):
    """同花顺 realhead 快照兜底。返回 {'open','prev','vol','amt'} 或 None。

    ⚠️ 竞价时段「最新价」即竞价成交价 → 映射为 open；成交额取 field 19（单位：元）。
       非竞价时段的 open 会偏（那是现价而非今开），但调用方在非竞价时段一律会用
       「分时首根」把 open 覆盖掉（见 run() 的补取分支），故不影响最终口径。
    """
    try:
        r = (THS.fetch_realhead([('hs', code)]) or {}).get(('hs', code)) or {}
    except Exception:
        return None
    if r.get('price') is None:
        return None
    return {'open': r.get('price') or 0, 'prev': r.get('prev') or 0,
            'vol': 0, 'amt': r.get('amount') or 0}


def tx_quote(code, market):
    """腾讯快照（字段：4=昨收 5=今开 6=量(手) 37=额(万元)）。"""
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


def ths_first_bar(code, date_str):
    """同花顺当日分时首根（09:30 = 集合竞价）→ (竞价成交价, 竞价额[元])；失败 (None, None)。

    ⚠️ **日期不符一律丢弃**：同花顺盘前 / 非交易日返回的是**上一交易日**的分时，
       不校验就会把昨天的集合竞价当成今天的（原东财版用 trends 行的日期前缀做同一道防线）。
    """
    d, price, amount = THS.fetch_minute_auction(code)
    want = (date_str or '').replace('-', '')
    if not d or not want or d != want:
        return None, None
    return price, amount


def _mkt(c):
    """市场：0=深 1=沪。recommend 无 market 字段时按代码前缀推导。"""
    m = c.get('market')
    if m in (0, 1, '0', '1'):
        return int(m)
    return 1 if str(c.get('code', '')).startswith('6') else 0


def run():
    now = datetime.now()
    mins = now.hour * 60 + now.minute
    today = now.strftime('%Y-%m-%d')
    is_auction = (9 * 60 + 15) <= mins <= (9 * 60 + 30)

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
        q = tx_quote(c['code'], mk)              # ① 腾讯快照（今开 / 量 / 额 / 昨收）
        if q and q.get('amt'):
            return q
        return ths_quote(c['code']) or q         # ② 同花顺 realhead 兜底

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

    # 补取：错过 9:15~9:30 窗口（快照已是累计口径）→ 用分时首根还原真竞价额
    source = 'live'
    if not is_auction and today > (data.get('date') or ''):
        bars = []                        # 串行 + 慢节奏：分时接口并发易被限流
        for c in picks:
            bars.append(ths_first_bar(c['code'], today))
            time.sleep(0.25)
        got = 0
        for c, (bop, bamt) in zip(picks, bars):
            if bamt is None:
                continue
            r = by.get(c['code'])
            if r is None:                    # 快照也失败 → 仅用分时首根补一条（无昨收，不算涨幅）
                ln = c.get('bid_required_yi')
                if ln is None:
                    ln = round((c.get('amount_yi') or 0) * 0.5, 2)
                r = by[c['code']] = {'amt_yi': None, 'ok': False, 'open': bop or 0,
                                     'prev': 0, 'pct': None, 'line': ln}
                if c.get('name') in miss:
                    miss.remove(c['name'])
            got += 1
            r['amt_yi'] = round(bamt / 1e8, 2)
            r['ok'] = bool(r['line'] and r['amt_yi'] >= r['line'])
            if bop:
                r['open'] = bop
                r['pct'] = round((bop - r['prev']) / r['prev'] * 100, 2) if r['prev'] else None
        if got:
            source = 'minute_0930'
            print('[竞价] 非竞价时段启动 → 已用「当日分时首根(09:30=集合竞价)」还原真竞价额 %d/%d 只'
                  % (got, len(picks)))

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

    ## `auction_is_live` 语义 = 「这批量额是否真竞价口径、可与硬线比较」（见模块 docstring）
    live = is_auction or source == 'minute_0930'
    data['candidates'] = cands
    data['recommend'] = reco
    data.pop('auction', None)          # 旧版独立竞价块已废弃（并入候选池），清掉残留
    data['auction_updated'] = now.strftime('%Y-%m-%d %H:%M:%S')
    data['auction_is_live'] = live
    data['auction_source'] = source    # live=盘内快照 / minute_0930=分时首根还原
    save_json(DATA_JSON, data)
    save_js(os.path.join(BASE, 'data.js'), data)

    tail = ('（' + ('集合竞价口径·分时首根还原' if source == 'minute_0930' else '集合竞价框内快照')
            + '）') if live else '  ⚠ 非竞价时段，量额为开盘后累计口径，不可与硬线比较'
    print('[竞价] 核对 %d 只 · 合格 %d · 对象日=%s%s'
          % (len(by), n_ok, data.get('date') or '', tail))
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
