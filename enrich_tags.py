# -*- coding: utf-8 -*-
"""
补齐节点票标签（地域 / 概念 / 拼音缩写）。

与 fetch_data.py 分离的原因：东财标签接口对请求频率敏感，
前置的涨停池抓取会耗尽配额，独立慢速运行 + 持久化缓存最稳。

用法:
    python enrich_tags.py            # 补全部缺失标签
    python enrich_tags.py --limit 30 # 本次最多补 30 只
    python enrich_tags.py --force    # 忽略缓存，全部重抓
"""

import sys, os, json, time, argparse
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

from fetch_data import (BASE, NODES_JSON, DATA_JSON, STOCK_CACHE, load_json, save_json,
                        fetch_stock_tags, pinyin_abbr, concept_hit_count, dedup_candidates,
                        fetch_seal_amount, ferment_count, build_candidates, TAGS_VER,
                        market_of, fetch_board_rank, BOARD_HEAT)
import os, time
from fetch_data import save_js


def recalc_recommend(nodes, board_daily, zt_pool, date_str='', board_3d=None):
    """依据最新标签重算次日连板候选"""
    # 热点源 = 单日板块榜 + 多日累计榜（覆盖「这几天」主线）
    hot = [b['name'] for b in board_daily[:30]] + [b['name'] for b in (board_3d or [])]
    today_map = {s.get('code'): s for s in (zt_pool or [])}
    cache = load_json(STOCK_CACHE, {})
    cands = build_candidates(nodes, zt_pool, hot, cache)

    # 上板分时量（次日竞价量基准），只对前5只请求，分时接口易限流
    if date_str:
        for c in cands[:5]:
            cur = today_map.get(c['code'])
            fbt = cur.get('first_seal') if cur else None
            seal, day_total = fetch_seal_amount(c['code'], c['market'], date_str, fbt)
            if seal:
                c['seal_amount_yi'] = round(seal / 1e8, 2)
                c['bid_required_yi'] = round(seal / 1e8 * 0.5, 2)
                c['bid_basis'] = '上板分时量'
            else:
                c['seal_amount_yi'] = None
                c['bid_required_yi'] = round(c['amount_yi'] * 0.5, 2)
                c['bid_basis'] = '全天成交额(分时缺失回退)'
            if day_total:
                c['day_amount_yi'] = round(day_total / 1e8, 2)
            time.sleep(0.3)

    reco = None
    if cands:
        c = cands[0]
        reco = {
            'name': c['name'], 'code': c['code'], 'boards': c['boards'],
            'region': c['region'], 'pinyin': c['pinyin'], 'concepts': c['concepts'],
            'from_node': c['node_type'], 'node_date': c['node_date'],
            'concept_hit': c['concept_hit'], 'ferment': c['ferment'],
            'industry': c.get('industry', ''), 'amount_yi': c['amount_yi'],
            'seal_amount_yi': c.get('seal_amount_yi'),
            'bid_basis': c.get('bid_basis', ''),
            'total_cap_yi': c['total_cap_yi'],
            'bid_required_yi': c.get('bid_required_yi') or round(c['amount_yi'] * 0.5, 2),
            'seal_min_yi': round(c['total_cap_yi'] * 0.01, 2),
            'seal_max_yi': round(c['total_cap_yi'] * 0.03, 2),
        }
    return reco, cands


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=200, help='本次最多补多少只')
    ap.add_argument('--force', action='store_true', help='忽略缓存全部重抓')
    args = ap.parse_args()

    nodes_obj = load_json(NODES_JSON, {'nodes': []})
    nodes = nodes_obj.get('nodes', [])
    data = load_json(DATA_JSON, {})
    cache = load_json(STOCK_CACHE, {})
    if args.force:
        cache = {}

    # 抓一次全量板块涨幅，填充 BOARD_HEAT：
    # 个股概念按「对应板块当日涨幅」排序，取当下正在炒的那个
    try:
        fetch_board_rank(60)
        print('板块热度表：%d 个板块' % len(BOARD_HEAT))
    except Exception as e:
        print('板块热度表获取失败（概念将按原顺序）：%s' % e)

    print('=' * 58)
    print('节点票标签补齐  %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print('=' * 58)
    print('节点 %d 个 | 缓存 %d 条' % (len(nodes), len(cache)))

    # 收集待标注（去重）
    todo, seen = [], set()
    def need(o, code=''):
        # 缓存口径版本过期（如概念口径升级：行业名 → 真题材）→ 强制重抓
        if code:
            ck = '%s.%s' % (market_of(code), code)
            cached = cache.get(ck) or {}
            if cached.get('concepts') and cached.get('_v') != TAGS_VER:
                return True
        return o.get('region', '—') in ('—', '', None) or not o.get('concepts')

    for n in nodes:
        t = n.get('trigger') or {}
        if t.get('code') and (args.force or need(t, t.get('code', ''))):
            k = 'T_%s' % t['code']
            if k not in seen:
                seen.add(k)
                todo.append(('trigger', n['id'], t['code'], t.get('market', 1), t.get('name', '')))
        for s in n.get('stocks', []):
            if args.force or need(s, s.get('code', '')):
                k = 'S_%s' % s['code']
                if k not in seen:
                    seen.add(k)
                    todo.append(('stock', n['id'], s['code'], s.get('market', 1), s['name']))

    todo = todo[:args.limit]
    print('待标注 %d 只' % len(todo))
    if not todo:
        print('✅ 标签已齐全，无需补齐')
    else:
        # 并发抓取（网络耗时为主，4 线程）→ 再顺序回写，避免竞态
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as ex:
            fetched = list(ex.map(lambda it: fetch_stock_tags(it[2], it[3], it[4], cache), todo))
        ok, fail = 0, 0
        for i, ((kind, nid, code, market, name), tags) in enumerate(zip(todo, fetched)):
            good = bool(tags.get('concepts')) or tags.get('region', '—') != '—'
            if good:
                ok += 1
            else:
                fail += 1
            # 回写到所有引用该股的位置
            for n in nodes:
                if kind == 'trigger' and n['id'] == nid:
                    t = n.get('trigger') or {}
                    t.update({'region': tags['region'], 'concepts': tags['concepts'],
                              'pinyin': tags['pinyin'],
                              'industry': tags.get('industry') or t.get('industry', '')})
                elif kind == 'stock':
                    for s in n.get('stocks', []):
                        if s['code'] == code:
                            s.update({'region': tags['region'], 'concepts': tags['concepts'],
                                      'pinyin': tags['pinyin'],
                                      'industry': tags.get('industry', '')})
            if (i + 1) % 10 == 0 or i == len(todo) - 1:
                print('  进度 %d/%d（成功%d 失败%d）' % (i + 1, len(todo), ok, fail))

        save_json(STOCK_CACHE, cache)
        nodes_obj['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        nodes_obj['nodes'] = nodes
        save_json(NODES_JSON, nodes_obj)
        print('✅ 标注完成：成功 %d / 失败 %d' % (ok, fail))

    # 重算推荐并回写 data.json
    if data:
        reco, cands = recalc_recommend(nodes, data.get('board_daily', []),
                                       data.get('zt_pool', []), data.get('date', ''),
                                       data.get('board_3d', []))
        data['recommend'] = reco
        data['candidates'] = cands
        data['nodes'] = nodes[:12]
        data['tags_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        save_json(DATA_JSON, data)
        save_js(os.path.join(BASE, 'data.js'), data)
        if reco:
            print('\n推荐同步更新 → %s(%s) %s板 · %s · %s · 概念%s'
                  % (reco['name'], reco['code'], reco['boards'], reco['region'],
                     reco['from_node'], '、'.join(reco['concepts'][:3]) or '—'))
            print('  竞价量 ≥ %.2f 亿 | 封单 %.2f~%.2f 亿'
                  % (reco['bid_required_yi'], reco['seal_min_yi'], reco['seal_max_yi']))
        else:
            print('\n（无有效候选：当前无节点票处于连板中）')


if __name__ == '__main__':
    main()
