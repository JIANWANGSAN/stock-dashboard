# -*- coding: utf-8 -*-
"""
盘前轻量刷新：只更新「每日必看」的新闻（宏观政策 / 行业利好 / 行业利空 / 外围行情）。

与 fetch_data.py 的区别：
  不跑梯队、不判节点、不动板块累计榜（board_history.json）、不改推荐候选。
  盘前/盘中这些都没有有效数据，重跑只会把不完整的盘中数据写进去造成污染。

用法:
    python refresh_news.py
"""

import sys, os, json, argparse
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

from fetch_data import (BASE, DATA_JSON, load_json, save_json, save_js, fetch_news)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=3,
                    help='抓取最近 N 天的新闻（默认3，覆盖周末与隔夜外围行情）')
    args = ap.parse_args()

    data = load_json(DATA_JSON, {})
    if not data:
        print('未找到 data.json，请先运行 fetch_data.py 做一次完整采集')
        return

    print('=' * 56)
    print('盘前轻量刷新（仅新闻）  %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print('=' * 56)
    old = {k: len(v) for k, v in (data.get('news') or {}).items()}
    print('刷新前:', old, '共%d条' % sum(old.values()))

    news = fetch_news(days=args.days)
    total = sum(len(v) for v in (news or {}).values())
    if total == 0:
        print('本次未抓到新闻（非交易日 / 接口限流 / 时段过早），保持原数据不变')
        return

    data['news'] = news
    data['news_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    save_json(DATA_JSON, data)
    save_js(os.path.join(BASE, 'data.js'), data)

    print('刷新后:', {k: len(v) for k, v in news.items()}, '共%d条' % total)
    print('已更新 data.json / data.js')
    print('梯队、节点、板块累计榜、推荐候选均未改动（保持上一交易日收盘口径）')


if __name__ == '__main__':
    main()
