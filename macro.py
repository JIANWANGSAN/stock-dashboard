# -*- coding: utf-8 -*-
"""「必看」页 · 大盘宏观面板取数（同花顺口径）

产出 data.json / data.js 的 `macro` 键，供 index.html「必看」面板渲染 5 个区块：
  ① 大盘概览 + 今日资金主线     ② 27 个产业板块涨跌热力网格
  ③ 主力资金行业净流入排行（双向条形）  ④ 风格轮动强弱   ⑤ 行业配置研判

数据源（全部同花顺 / 腾讯，均标注 src）：
  · 指数行情/成交额：同花顺 realhead（`10`现价 `264648`涨跌额 `199112`涨跌幅 `19`成交额元）
    小盘宽基（中证1000/国证2000/北证50）同花顺 zs_ 码不可用 → 腾讯 qt.gtimg.cn 兜底
  · 行业/概念资金流：`data.10jqka.com.cn/funds/<hy|gn>zjl/`（单位：亿）
  · 产业板块涨跌：同花顺 realhead（SECTOR_WATCH 27 个）
  · 涨停/跌停/封板率：同花顺涨停池

用法：
  python macro.py             # 盘中/盘后均可跑，实时口径
  python macro.py --quiet
"""
import os
import sys
import json
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ths_source as THS
from fetch_data import (BASE, DATA_JSON, SECTOR_WATCH, load_json, save_json, save_js)
from ths_board_map import THS_MAP

DATA_JS = os.path.join(BASE, 'data.js')

# ── ① 大盘概览：主要指数（大盘/小盘/价值/成长各维度）────────────────────────
# (显示名, 同花顺 zs 码 or None, 腾讯码 or None, 归类)
INDEX_SPEC = [
    ('上证指数', 'zs_1A0001', 'sh000001', 'core'),
    ('深证成指', 'zs_399001', 'sz399001', 'core'),
    ('创业板指', 'zs_399006', 'sz399006', 'core'),
    ('科创50',  'zs_1B0688', 'sh000688', 'core'),
    ('沪深300', 'zs_399300', 'sh000300', 'core'),
    ('北证50',  None,        'bj899050', 'core'),
    # 风格轮动专用
    ('上证50',  'zs_1B0016', 'sh000016', 'style'),
    ('中证500', 'zs_1B0905', 'sh000905', 'style'),
    ('中证1000', None,       'sh000852', 'style'),
    ('国证2000', 'zs_399303', 'sz399303', 'style'),
]

# ── ④ 风格轮动：四组对立维度（左=defensive，右=offensive）────────────────
STYLE_DIMS = [
    ('市值风格', '大盘', '沪深300', '小盘', '中证1000'),
    ('估值风格', '价值', '上证50',  '成长', '创业板指'),
    ('板块属性', '主板', '上证指数', '科创', '科创50'),
    ('弹性/情绪', '深市主板', '深证成指', '北证/小盘', '国证2000'),
]


# 产业板块「显示简称」→ 同花顺榜上的名字。
# 用户清单用的是行情软件里的短名，同花顺概念榜有时用全称（且把英文缩写放在括号里，
# 会被 _stem_name 剥掉 → 纯简称匹配不上）→ 这里手工桥接。
SECTOR_ALIAS = {
    'CPO': '共封装光学(CPO)',
    '存储': '存储芯片',
}


def _tx_quote(codes):
    """腾讯行情兜底（GBK）。返回 {腾讯码: {name, price, pct, chg}}。"""
    import urllib.request
    import ssl
    if not codes:
        return {}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = 'https://qt.gtimg.cn/q=' + ','.join(codes)
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Referer': 'https://gu.qq.com/'})
        h = urllib.request.urlopen(req, timeout=8, context=ctx).read().decode('gbk', 'ignore')
    except Exception:
        return {}
    out = {}
    for seg in h.split(';'):
        seg = seg.strip()
        if '=' not in seg:
            continue
        code = seg.split('=')[0].replace('v_', '').strip()
        try:
            f = seg.split('="')[1].strip('"').split('~')
        except Exception:
            continue
        if len(f) < 33:
            continue
        def _n(i):
            try:
                return float(f[i])
            except Exception:
                return None
        out[code] = {'name': f[1], 'price': _n(3), 'chg': _n(31), 'pct': _n(32)}
    return out


def fetch_indices():
    """主要指数（同花顺优先 + 腾讯兜底），返回 (indices, amount_yi_hu_shen)。"""
    ths_specs = [(s[1], s[0]) for s in INDEX_SPEC if s[1]]
    snap = THS.fetch_realhead([tuple(c.split('_', 1)) for c, _ in ths_specs],
                              max_workers=8) if ths_specs else {}
    tx_codes = [s[2] for s in INDEX_SPEC if not s[1] or True]
    tx = _tx_quote(tx_codes)

    out = []
    for name, ths_code, tx_code, kind in INDEX_SPEC:
        v = None
        if ths_code:
            v = snap.get(tuple(ths_code.split('_', 1)))
        src = 'ths'
        if v and (v.get('price') or v.get('pct') is not None):
            row = {
                'name': name, 'price': v.get('price'), 'pct': v.get('pct'),
                'chg': v.get('chg'), 'amount_yi': round((v.get('amount') or 0) / 1e8, 2),
                'kind': kind, 'src': src,
            }
        else:
            t = tx.get(tx_code) or {}
            row = {
                'name': name, 'price': t.get('price'), 'pct': t.get('pct'),
                'chg': t.get('chg'), 'amount_yi': None, 'kind': kind, 'src': 'tx',
            }
        out.append(row)

    # 沪深两市成交额（上证 + 深证综指 实时成交额，同花顺 realhead 的 19 字段）
    amt = 0.0
    got = 0
    extra = THS.fetch_realhead([('zs', '1A0001'), ('zs', '399106')], max_workers=2)
    for k in [('zs', '1A0001'), ('zs', '399106')]:
        a = (extra.get(k) or {}).get('amount')
        if a:
            amt += a
            got += 1
    return out, (round(amt / 1e8, 2) if got == 2 else None)


def fetch_heat():
    """27 个产业板块当日涨跌（热力网格用）。"""
    secs, meta = [], []
    for disp, em in SECTOR_WATCH:
        t = THS_MAP.get(em)
        if not t:
            continue
        secs.append(('bk', t[0]))
        meta.append({'name': disp, 'em_code': em, 'ths_code': t[0], 'ts_name': t[1]})
    snap = THS.fetch_realhead(secs, max_workers=10) if secs else {}
    out = []
    for m in meta:
        v = snap.get(('bk', m['ths_code'])) or {}
        out.append({
            'name': m['name'],
            'em_code': m['em_code'],
            'ths_code': m['ths_code'],
            'pct': v.get('pct') if v.get('pct') is not None else None,
            'amount_yi': round((v.get('amount') or 0) / 1e8, 2),
            'leader': '',
            'leader_pct': None,
            'net_in_yi': None,
        })
    return out


def fetch_fund():
    """行业资金流榜 → 净额升/降序各取前 N，并回填 heat 的净额与领涨股。"""
    hy = THS.fetch_fund_rank('hy', pages=2)
    gn = THS.fetch_fund_rank('gn', pages=9)
    return hy, gn


def build_style(indices):
    """④ 风格轮动：四组对立维度的涨跌幅差。"""
    m = {i['name']: i for i in indices}
    out = []
    for dim, ln, lcode, rn, rcode in STYLE_DIMS:
        L, R = m.get(lcode) or {}, m.get(rcode) or {}
        lp, rp = L.get('pct'), R.get('pct')
        if lp is None or rp is None:
            continue
        out.append({
            'dim': dim,
            'left': {'label': ln, 'name': lcode, 'pct': lp},
            'right': {'label': rn, 'name': rcode, 'pct': rp},
            'spread': round(rp - lp, 2),          # >0 = 右侧（进攻）占优
        })
    return out


def _fmt(v, unit='', nd=2):
    if v is None:
        return '—'
    return ('%+.' + str(nd) + 'f') % v if v >= 0 else ('%.' + str(nd) + 'f') % v


def build_notes(indices, heat, fund_in, fund_out, breadth, stat, hy):
    """⑤ 3 条行业配置研判 —— 全部由当日客观数据推出，不含涨跌预测。"""
    notes = []
    m = {i['name']: i for i in indices}

    # 研判1：资金集中度 —— 头部净流入占全部净流入之和的比重
    pos = sum(b['net_in_yi'] for b in fund_in if (b.get('net_in_yi') or 0) > 0)
    top3 = sum((b.get('net_in_yi') or 0) for b in fund_in[:3])
    if pos > 0 and fund_in:
        share = top3 / pos * 100
        names = '、'.join(b['name'] for b in fund_in[:3])
        notes.append({
            'tag': '资金集中度',
            'level': '高' if share >= 45 else ('中' if share >= 25 else '低'),
            'title': '主力资金向「%s」集中' % names,
            'body': ('全部 %d 个行业中净流入为正者合计 %.0f 亿，其中前 3 大行业（%s）合计 %.0f 亿、'
                     '占比 %.0f%%；%s 以 %.0f 亿居首。'
                     % (len(hy), pos, names, top3, share,
                        fund_in[0]['name'], fund_in[0]['net_in_yi'])),
            'data': '同花顺行业资金流 · %s' % datetime.now().strftime('%Y-%m-%d'),
        })

    # 研判2：量价背离 —— 涨幅为正但主力净流出
    diverge = [b for b in heat if (b.get('pct') or 0) > 0.5 and (b.get('net_in_yi') or 0) < -1]
    if diverge:
        d = diverge[0]
        notes.append({
            'tag': '量价背离',
            'level': '留意',
            'title': '%d 个板块「价升量减」' % len(diverge),
            'body': ('%s。这类板块涨幅由情绪或小单推动，主力资金实为净流出，'
                     '持续性弱于「价量同向」的板块。'
                     % '、'.join('%s(涨%.2f%%/净流出%.2f亿)' % (x['name'], x['pct'], x['net_in_yi'])
                                 for x in diverge[:3])),
            'data': '同花顺板块快照 + 行业资金流 · %s' % datetime.now().strftime('%Y-%m-%d'),
        })

    # 研判3：风格 —— 小盘成长 vs 大盘价值
    grow = m.get('创业板指') or {}
    value = m.get('上证50') or {}
    small = m.get('中证1000') or {}
    big = m.get('沪深300') or {}
    if grow.get('pct') is not None and value.get('pct') is not None:
        spread = (grow.get('pct') or 0) - (value.get('pct') or 0)
        sm = ((small.get('pct') or 0) - (big.get('pct') or 0)) if small.get('pct') is not None else None
        notes.append({
            'tag': '风格取向',
            'level': '小盘成长占优' if spread > 0 else '大盘价值占优',
            'title': '成长跑赢价值 %.2f 个百分点' % abs(spread),
            'body': ('创业板指 %s%% vs 上证50 %s%%；%s市值端 中证1000 %s%% vs 沪深300 %s%%。'
                     '全市涨停 %d 家 / 跌停 %d 家、封板率 %.0f%%，弹性品种强于权重蓝筹。'
                     % (_fmt(grow.get('pct')), _fmt(value.get('pct')),
                        ('小盘占优（差 %+.2f）· ' % sm) if sm is not None else '',
                        _fmt(small.get('pct')), _fmt(big.get('pct')),
                        stat.get('zt') or 0, stat.get('dt') or 0,
                        (stat.get('zt_rate') or 0) * 100)),
            'data': '同花顺指数 + 涨停池 · %s' % datetime.now().strftime('%Y-%m-%d'),
        })
    return notes[:3]


def run(quiet=False):
    t0 = datetime.now()
    print('=' * 58)
    print('必看页 · 大盘宏观面板   %s' % t0.strftime('%Y-%m-%d %H:%M:%S'))
    print('=' * 58)

    indices, hs_amount = fetch_indices()
    print('[指数] %d 条（沪深成交额 %s 亿）'
          % (len(indices), ('%.0f' % hs_amount) if hs_amount else '—'))

    heat = fetch_heat()
    print('[产业板块] %d 个' % len(heat))

    hy, gn = fetch_fund()
    print('[资金流] 行业 %d 个 · 概念 %d 个' % (len(hy), len(gn)))

    # 回填 heat 的净额/领涨股：产业板块名可能是「行业」也可能是「概念」
    # （CPO/存储/核聚变/机器人 等都在概念榜里）→ 先行业榜，再概念榜
    _hy_by_name = {b['name']: b for b in hy}
    _gn_by_name = {b['name']: b for b in gn}
    _hit_h = _hit_g = 0
    for h in heat:
        q = SECTOR_ALIAS.get(h['name'], h['name'])
        r = THS.match_fund_row(_hy_by_name, q) or {}
        kind = 'industry'
        if not r:
            r = THS.match_fund_row(_gn_by_name, q) or {}
            kind = 'concept' if r else ''
            if r:
                _hit_g += 1
        else:
            _hit_h += 1
        h['net_in_yi'] = r.get('net_in_yi')
        h['leader'] = r.get('leader') or ''
        h['leader_pct'] = r.get('leader_pct')
        h['stock_total'] = int(r.get('stock_total') or 0)
        h['src_kind'] = kind
        if h['pct'] is None and r.get('pct') is not None:
            h['pct'] = r['pct']
    print('        匹配：行业榜 %d · 概念榜 %d · 未匹配 %d'
          % (_hit_h, _hit_g, len(heat) - _hit_h - _hit_g))

    fund_in = sorted([b for b in hy if (b.get('net_in_yi') or 0) > 0],
                     key=lambda x: -(x['net_in_yi'] or 0))
    fund_out = sorted([b for b in hy if (b.get('net_in_yi') or 0) < 0],
                      key=lambda x: (x['net_in_yi'] or 0))

    # 概念资金流 TOP（资金主线补充）
    gn_in = sorted([b for b in gn if (b.get('net_in_yi') or 0) > 0],
                   key=lambda x: -(x['net_in_yi'] or 0))[:12]

    # 市场宽度：90 个行业的涨跌分布
    up_i = sum(1 for b in hy if (b.get('pct') or 0) > 0)
    dn_i = sum(1 for b in hy if (b.get('pct') or 0) < 0)
    breadth = {'up_industry': up_i, 'down_industry': dn_i, 'total_industry': len(hy)}

    stat = {}
    try:
        _, stat = THS.fetch_zt_pool(datetime.now().strftime('%Y%m%d'))
    except Exception:
        stat = {}
    print('[情绪] 涨停 %s · 跌停 %s · 封板率 %s · %s'
          % (stat.get('zt'), stat.get('dt'),
             ('%.0f%%' % ((stat.get('zt_rate') or 0) * 100)) if stat.get('zt_rate') else '—',
             stat.get('trade_status') or ''))

    style = build_style(indices)
    print('[风格] %d 组维度' % len(style))

    notes = build_notes(indices, heat, fund_in, fund_out, breadth, stat, hy)
    print('[研判] %d 条' % len(notes))

    macro = {
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'date': datetime.now().strftime('%Y-%m-%d'),
        'session': stat.get('trade_status') or '盘中',
        'src': '同花顺（iFinD 同源）',
        'indices': indices,
        'amount_yi': hs_amount,
        'breadth': breadth,
        'mood': {
            'zt': stat.get('zt'), 'dt': stat.get('dt'),
            'zt_open': stat.get('zt_open'), 'zt_rate': stat.get('zt_rate'),
            'y_zt': stat.get('y_zt'), 'y_zt_rate': stat.get('y_zt_rate'),
        },
        'mainline': [
            {'name': b['name'], 'pct': b['pct'], 'net_in_yi': b['net_in_yi'],
             'stock_total': b['stock_total'], 'leader': b['leader']}
            for b in fund_in[:6]
        ],
        'heat': heat,
        'fund_rank': {
            'in': [{'name': b['name'], 'pct': b['pct'], 'net_in_yi': b['net_in_yi'],
                    'stock_total': b['stock_total'], 'leader': b['leader'],
                    'leader_pct': b['leader_pct']} for b in fund_in[:10]],
            'out': [{'name': b['name'], 'pct': b['pct'], 'net_in_yi': b['net_in_yi'],
                     'stock_total': b['stock_total'], 'leader': b['leader'],
                     'leader_pct': b['leader_pct']} for b in fund_out[:10]],
        },
        'concept_in': [{'name': b['name'], 'pct': b['pct'], 'net_in_yi': b['net_in_yi'],
                        'leader': b['leader']} for b in gn_in],
        'style': style,
        'notes': notes,
    }

    data = load_json(DATA_JSON, {}) or {}
    data['macro'] = macro
    save_json(DATA_JSON, data)
    save_js(DATA_JS, data)
    print('\n✅ 完成，耗时 %.1fs → data.json / data.js 的 macro 键' % (datetime.now() - t0).total_seconds())


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    run(quiet=a.quiet)
