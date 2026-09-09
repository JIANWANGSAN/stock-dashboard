# -*- coding: utf-8 -*-
"""
A0 补板块成分股缓存（.mx_members_cache.json）

东方财富妙想 MCP 连接器在当前会话不可用时，使用东财公开 push2 接口等效补齐，
写入格式与连接器口径一致：{日期: {板块名: [[code,name], ...]}}
"""
import json, os, time, re
from datetime import datetime

from fetch_data import BASE, http_get, load_json, save_json
from module4 import build_em_board_map, match_em_code, fetch_board_members_em, MX_MEMBERS, MX_NAME_MAP

TODAY = datetime.now().strftime('%Y-%m-%d')


def main():
    data = load_json(os.path.join(BASE, 'data.json'), {})
    daily = data.get('board_daily', []) or []
    d3 = data.get('board_3d', []) or []
    d3names = [b.get('name') for b in d3[:10]]
    overlap = [b.get('name') for b in daily[:15] if b.get('name') in d3names]
    overlap = list(dict.fromkeys(overlap))
    print('[A0] 两榜重合板块：', overlap)

    em = build_em_board_map()
    print('[A0] 东财板块映射 %d 条' % len(em))

    cache = load_json(MX_MEMBERS, {})
    if not isinstance(cache, dict):
        cache = {}
    day = cache.get(TODAY)
    if not isinstance(day, dict):
        day = {}

    for bname in overlap:
        mx_name = MX_NAME_MAP.get(bname, bname)
        code = match_em_code(bname, em) or match_em_code(mx_name, em)
        mem = fetch_board_members_em(code) if code else []
        time.sleep(0.25)
        if not mem:
            print('  [A0] %-10s (妙想名:%s) -> 无成分股，跳过' % (bname, mx_name))
            continue
        day[bname] = [[c, n] for c, n in mem]
        print('  [A0] %-10s (妙想名:%s, em=%s) -> %d 只' % (bname, mx_name, code, len(mem)))

    cache[TODAY] = day
    cache['latest'] = day
    save_json(MX_MEMBERS, cache)
    print('[A0] 已写入 %s -> %s' % (MX_MEMBERS, list(day.keys())))


if __name__ == '__main__':
    main()
