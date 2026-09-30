# -*- coding: utf-8 -*-
"""增量补齐板块K线：按「日K / 周K」分别检查完整性，缺哪补哪，合并写回（可反复运行）。

⚠️ **本脚本默认只补「空」的，不会重取「非空但落后」的K线**
   （判定见下方 `need_d = FORCE or not (v.get('day') or [])`，第 49-51 行的注释即为原因）。
   所以：遇到「末条停在上一交易日」这种**落后**，默认模式会直接报 `待补 0 个 / ✅ 已全部齐备`——
   **那是假安心**，必须用 `--force` 整批重取。

取数实现见 `board_kline_src.py`：**先探测东财，通则整批走东财；东财 K线路径被封时整批切同花顺**
（两家板块指数基期不同、点位不可混用，故不允许按单个板块切换来源）。

用法：
  python fill_board_kline.py              # 只补空的（缺哪补哪）
  python fill_board_kline.py --force      # 全部重取（★ 补「当日/落后」用这个）
  python fill_board_kline.py --src ths    # 强制指定数据源（em / ths）
"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import fetch_data as F
from board_kline_src import (fetch_em_kline, fetch_ths_kline, probe_source,
                             SOURCE_PREF, THS_MISSING_FALLBACK_EM)
from ths_board_map import THS_MAP

DAY_MIN, WEEK_MIN = 100, 50          # 「齐备」口径（仅用于展示）
DAY_N, WEEK_N = 120, 60              # 目标根数

FORCE = '--force' in sys.argv
SRC = sys.argv[sys.argv.index('--src') + 1] if '--src' in sys.argv else None

data = F.load_json(F.DATA_JSON, {})
have = data.get('board_kline') or {}
codes = {}
# 只处理「页面展示的产业板块」（board_daily/board_3d 已不在页面展示）
for b in ((data.get('sector_daily') or []) + (data.get('sector_3d') or [])):
    if b.get('code'):
        codes[b['code']] = b.get('name', '')
if not codes:
    print('⚠️ data.json 里没有产业板块清单（sector_daily/sector_3d），先跑 fetch_data.py')
    raise SystemExit(1)

if not SRC:
    # 默认跟 board_kline_src.SOURCE_PREF（现为 'ths'：用户已停用东财）；仅当偏好设为 auto 时才探测
    SRC = SOURCE_PREF if SOURCE_PREF in ('em', 'ths') else probe_source(sorted(codes)[0])
    print('数据源＝%s（SOURCE_PREF=%s）' % ('东财' if SRC == 'em' else '同花顺', SOURCE_PREF))
if SRC == 'ths':
    miss = [n for c, n in codes.items() if c not in THS_MAP]
    if miss:
        print('⚠️ 同花顺无对应板块：%s%s' % ('、'.join(miss),
              '→ 改用东财单独补' if THS_MISSING_FALLBACK_EM else '→ 跳过'))

todo = []
for c, name in codes.items():
    v = have.get(c) or {}
    # 只补「空」的：有的板块（如东财「新消费」）本身历史就短，非空即视为已拿到，不再反复重试
    need_d = FORCE or not (v.get('day') or [])
    need_w = FORCE or not (v.get('week') or [])
    if need_d or need_w:
        todo.append((c, name, need_d, need_w, v))

print('待补 %d 个：%s' % (len(todo),
      '、'.join('%s(%s%s)' % (n, '日' if d else '', '周' if w else '') for _, n, d, w, _ in todo) or '无'))
if not todo:
    print('✅ 已全部齐备')
    raise SystemExit(0)

def _pick(c, rsrc, need_d, need_w):
    """按来源取日K/周K；同花顺无对应板块时（若允许）单独用东财补。"""
    t = THS_MAP.get(c) if rsrc == 'ths' else None
    if rsrc == 'ths' and not t and not THS_MISSING_FALLBACK_EM:
        return None, None, None
    src = 'ths' if t else 'em'
    day = week = None
    if need_d:
        day = fetch_ths_kline(t[0], '01', DAY_N) if t else fetch_em_kline(c, 101, DAY_N)
    if need_w:
        week = fetch_ths_kline(t[0], '11', WEEK_N) if t else fetch_em_kline(c, 102, WEEK_N)
    return day, week, src


for c, name, need_d, need_w, v in todo:
    rec = dict(v) if v else {'name': name}
    rec['name'] = rec.get('name') or name
    day, week, src = _pick(c, SRC, need_d, need_w)
    if day is None and week is None:
        print('  ⏭  %-12s 同花顺无对应板块，跳过' % name)
        continue
    # ⚠️ 只在取回非空时覆盖：避免数据源抽风（偶发空返回）把已有K线清空
    if day:
        rec['day'] = day
    if week:
        rec['week'] = week
    if src:
        rec['src'] = src
    have[c] = rec
    d_n, w_n = len(rec.get('day') or []), len(rec.get('week') or [])
    if not d_n and not w_n:
        tag = '⚠️ 无数据'
    elif d_n >= DAY_MIN and w_n >= WEEK_MIN:
        tag = '✅'
    else:
        tag = '✅' if d_n and w_n else '⚠️'      # 非空但偏短 → 多为该板块历史本就短
    print('  %s %-12s 日K%3d 周K%3d  [%s]%s' % (tag, name, d_n, w_n, rec.get('src'),
          '' if (day or week) else '（本次空返回，沿用旧值）'))

data['board_kline'] = have
F.save_json(F.DATA_JSON, data)
F.save_js(os.path.join(F.BASE, 'data.js'), data)
full = sum(1 for c in codes if (have.get(c) or {}).get('day') and (have.get(c) or {}).get('week'))
print('累计 %d 个板块有K线（日K/周K 均非空 %d/%d）' % (len(have), full, len(codes)))
