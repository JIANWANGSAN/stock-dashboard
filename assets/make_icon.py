"""生成 Vibe AStock 桌面图标（多尺寸 .ico）+ 横向预览图。

设计意图：深蓝夜色底 = 盘后复盘的屏幕感；红色K线 + 上箭头 = A股上涨语义
（**红涨绿跌**，中国股市惯例，与欧美相反，别搞错）。

改样式后重跑本脚本即可覆盖 ico。8 倍超采样后再 LANCZOS 缩放，边缘才不毛躁。
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
ICO_PATH = os.path.join(OUT_DIR, "vibe-astock.ico")
PREVIEW_PATH = os.path.join(OUT_DIR, "icon-preview.png")

MASTER, SS = 1024, 8          # 设计尺寸 & 超采样倍数
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]

BG_TOP = (16, 24, 48)         # 深蓝夜色
BG_BOTTOM = (32, 44, 82)
UP_RED = (240, 64, 64)        # 涨 = 红
UP_RED_HI = (255, 116, 116)
GRID = (58, 74, 120)
WHITE = (255, 255, 255)
GOLD = (250, 196, 72)


def rounded(draw: ImageDraw.ImageDraw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def draw_master() -> Image.Image:
    S = MASTER * SS

    # 背景：垂直渐变 + 圆角
    bg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        gd.line(
            [(0, y), (S, y)],
            fill=(
                int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t),
                int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t),
                int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t),
                255,
            ),
        )
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
    bg.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(bg)

    # 背景网格（K线图坐标纸的暗示），压低亮度不抢主体
    for i in range(1, 6):
        x = S * i / 6
        d.line([(x, S * 0.16), (x, S * 0.84)], fill=GRID + (70,), width=int(S * 0.006))
    for i in range(1, 4):
        y = S * (0.16 + 0.68 * i / 4)
        d.line([(S * 0.14, y), (S * 0.86, y)], fill=GRID + (70,), width=int(S * 0.006))

    # 四根 K 线，越往右越高（上升趋势）
    base_y = S * 0.70
    bars = [
        (0.24, 0.30, 0.10),   # (中心x比例, 实体高比例, 上影线比例)
        (0.40, 0.20, 0.07),
        (0.56, 0.28, 0.09),
        (0.72, 0.38, 0.11),
    ]
    bw = S * 0.085           # 实体宽度
    for cx, bh, sh in bars:
        x = S * cx
        # 影线
        d.line([(x, base_y - S * bh - S * sh), (x, base_y + S * 0.03)],
               fill=UP_RED_HI + (255,), width=int(S * 0.016))
        # 实体（空心感：深色填充 + 红边，缩到 16px 也能分辨）
        top = base_y - S * bh
        d.rounded_rectangle(
            [x - bw / 2, top, x + bw / 2, base_y],
            radius=int(S * 0.012),
            fill=(58, 26, 32, 255),
            outline=UP_RED + (255,),
            width=int(S * 0.022),
        )

    # 上涨箭头（右上角，金色，压住背景但层级最高）
    ax0, ay0, ax1, ay1 = S * 0.52, S * 0.46, S * 0.84, S * 0.18
    lw = int(S * 0.045)
    d.line([(ax0, ay0), (ax1, ay1)], fill=GOLD + (255,), width=lw)
    # 箭头尖
    d.polygon(
        [(ax1 + lw * 0.15, ay1 - lw * 0.15),
         (ax1 - lw * 1.75, ay1 - lw * 0.15),
         (ax1 - lw * 0.15, ay1 - lw * 1.75)],
        fill=GOLD + (255,),
    )

    # 底部基线（托住K线）
    d.rounded_rectangle(
        [S * 0.16, base_y + S * 0.045, S * 0.86, base_y + S * 0.062],
        radius=int(S * 0.008),
        fill=WHITE + (200,),
    )

    return bg.resize((MASTER, MASTER), Image.LANCZOS)


def main() -> None:
    master = draw_master()

    # 自检：采样关键像素，确认元素真画在画布内（防止两套坐标系混用）
    px = master.load()
    row = int(0.18 * MASTER)   # 箭头所在高度
    arrow_hits = sum(1 for x in range(int(0.70 * MASTER), int(0.90 * MASTER))
                     if px[x, row][:3] == GOLD)
    bar_row = int(0.40 * MASTER)
    bar_hits = sum(1 for x in range(int(0.66 * MASTER), int(0.80 * MASTER))
                   if px[x, bar_row][0] > 120 and px[x, bar_row][1] < 130)
    print(f"自检 箭头像素={arrow_hits} (>0 说明画出来了)  K线红边像素={bar_hits}")

    frames = [master.resize((s, s), Image.LANCZOS) for s in SIZES]
    frames[-1].save(ICO_PATH, format="ICO", sizes=[(s, s) for s in SIZES])
    print("已写出", ICO_PATH, os.path.getsize(ICO_PATH), "bytes")

    # 横向预览图：肉眼确认各尺寸可辨识度
    w = sum(SIZES) + 12 * (len(SIZES) + 1)
    canvas = Image.new("RGB", (w, 268), (232, 232, 232))
    x = 12
    for s in SIZES:
        im = frames[SIZES.index(s)]
        canvas.paste(im, (x, 12 + (256 - s) // 2), im)
        x += s + 12
    canvas.save(PREVIEW_PATH)
    print("已写出", PREVIEW_PATH)


if __name__ == "__main__":
    main()
