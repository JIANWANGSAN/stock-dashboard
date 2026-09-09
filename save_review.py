# -*- coding: utf-8 -*-
"""
A股复盘记录归档脚本
--------------------
把当日复盘文本写入单一滚动文件「A股复盘记录.md」：
  - 最新一天排在最前面
  - 只保留最近 30 个交易日（自动截断更早的内容）
  - 全天内容作为一个完整块写入，不拆分

用法：
    python save_review.py                # 读取 _review_today.md 并归档
    python save_review.py --keep 60      # 自定义保留天数

自动化流程：AI 生成复盘文本 -> 写入 _review_today.md -> 运行本脚本 -> 删除临时文件
"""
import os
import re
import sys
import argparse
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
REVIEW_FILE = os.path.join(BASE, 'A股复盘记录.md')
TODAY_FILE = os.path.join(BASE, '_review_today.md')
BLOCK_MARK = '<!-- REVIEW:%s -->'


def split_blocks(text):
    """按 <!-- REVIEW:YYYY-MM-DD --> 标记切分为 [(date, content), ...]"""
    if not text:
        return []
    parts = re.split(r'<!-- REVIEW:(\d{4}-\d{2}-\d{2}) -->', text)
    # parts[0] 是文件头（标题等），其后 日期、内容 成对出现
    blocks = []
    for i in range(1, len(parts) - 1, 2):
        blocks.append((parts[i], parts[i + 1]))
    return blocks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', type=int, default=30, help='保留最近 N 个交易日')
    args = ap.parse_args()

    if not os.path.exists(TODAY_FILE):
        print('[归档] 未找到 %s，跳过。' % os.path.basename(TODAY_FILE))
        return 1

    with open(TODAY_FILE, encoding='utf-8') as f:
        today_text = f.read().strip()
    if not today_text:
        print('[归档] 今日复盘内容为空，跳过。')
        return 1

    today = datetime.now().strftime('%Y-%m-%d')

    # 已有内容（跳过文件头）
    header = ''
    old_blocks = []
    if os.path.exists(REVIEW_FILE):
        with open(REVIEW_FILE, encoding='utf-8') as f:
            raw = f.read()
        m = re.search(r'<!-- REVIEW:\d{4}-\d{2}-\d{2} -->', raw)
        if m:
            header = raw[:m.start()].strip()
            old_blocks = split_blocks(raw[m.start():])
        else:
            old_blocks = split_blocks(raw)

    # 去重同一天（重跑时覆盖）后按日期降序，确保「最新在前」
    old_blocks = [(d, c) for d, c in old_blocks if d != today]
    blocks = [(today, '\n' + today_text + '\n')] + old_blocks
    blocks.sort(key=lambda x: x[0], reverse=True)
    # 只保留最近 N 天（降序后取前 N 个＝最近的 N 个，不依赖文件内顺序）
    blocks = blocks[:args.keep]

    header = header or '# A股每日盘后复盘\n\n> 最新在最前，自动保留最近 %d 个交易日。\n' % args.keep

    out = [header, '']
    for i, (d, c) in enumerate(blocks):
        out.append(BLOCK_MARK % d)
        # 去掉块内自带的分隔线，避免末尾出现孤立的 ---
        out.append(re.sub(r'\n?---\s*$', '', c.strip()))
        if i < len(blocks) - 1:      # 块之间才加分隔线，末尾不加
            out.append('\n---\n')

    with open(REVIEW_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out).rstrip() + '\n')

    print('[归档] 已写入 %s（本次 %s，现存 %d 天，上限 %d）'
          % (os.path.basename(REVIEW_FILE), today, len(blocks), args.keep))

    # 清理临时文件
    try:
        os.remove(TODAY_FILE)
    except OSError:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
