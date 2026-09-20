# -*- coding: utf-8 -*-
"""盘前竞价任务（自动化实际触发时间：**每交易日 9:26**，集合竞价 9:25 定格之后）。

⚠️ 时间口径：定时任务是 **9:26**（见 项目约定.md §2 / §6）；本文件早前的注释写「9:25」是笔误 ——
   9:25 只是集合竞价定格时刻，脚本**在那之后**才跑，否则拿不到定格后的竞价量额。

刷新「梯队」菜单顶部三块（数据互通、同一批候选票）：
  ① 竞价核对            —— 抓候选票的竞价快照，比对硬线（上板分时量×50%）
  ② 我的想法·次日连板推荐 —— 由候选池第 1 名生成
  ③ 连板候选池          —— 由最近一次盘后刷新的 nodes + 涨停池 重新计算

为什么放盘前：这三块是「当日实战」内容。盘后 fetch_data.py 已不再重算，只沿用上一版
（避免盘后覆盖掉当日实战口径）。计算输入 nodes/zt_pool/board_daily 来自最近一次盘后刷新
（=上一交易日收盘），正是「今日待验证」的那批票 —— 因此与盘后数据天然互通。

竞价硬线：上板分时量用 fetch_seal_amount(ndays=5) 按 date_str 取上一交易日的分钟数据，
盘前也能正确取到（不会退化成全天成交额）。

用法：python premarket.py
"""
import sys, os
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

from fetch_data import (BASE, DATA_JSON, STOCK_CACHE, load_json, save_json, save_js,
                        build_candidates, build_reco)
import auction_check


def main():
    t0 = datetime.now()
    print('盘前竞价刷新  %s' % t0.strftime('%Y-%m-%d %H:%M:%S'))
    print('=' * 56)

    data = load_json(DATA_JSON, {})
    src_date = data.get('date') or ''
    nodes = data.get('nodes') or []
    zt = data.get('zt_pool') or []

    if not nodes or not zt:
        print('[盘前] data.json 缺 nodes/zt_pool，无法重算候选池，跳过')
        return

    cache = load_json(STOCK_CACHE, {})
    # 题材共振的热点池：以**概念榜**为主（候选题材本身就是概念名），再叠加行业榜/3日榜
    hot = ([b['name'] for b in (data.get('board_concept') or [])[:30]]
           + [b['name'] for b in (data.get('board_daily') or [])]
           + [b['name'] for b in (data.get('board_3d') or [])])

    print('[盘前] 基于 %s 收盘数据重算候选池...' % src_date)
    cands = build_candidates(nodes, zt, hot, cache)
    today_map = {s.get('code'): s for s in zt}
    cands, reco = build_reco(cands, today_map, src_date)

    data['candidates'] = cands
    data['recommend'] = reco
    data['premarket_updated'] = t0.strftime('%Y-%m-%d %H:%M:%S')
    save_json(DATA_JSON, data)
    save_js(os.path.join(BASE, 'data.js'), data)
    print('[盘前] 候选 %d 只已写回%s' % (len(cands), ('，首选 ' + reco['name']) if reco else ''))

    # 竞价核对（读刚写回的候选池 → 抓竞价快照 → 写 data['auction']）
    print('-' * 56)
    auction_check.run()

    print('=' * 56)
    print('[盘前] 完成，耗时 %.1fs' % (datetime.now() - t0).total_seconds())


if __name__ == '__main__':
    main()
