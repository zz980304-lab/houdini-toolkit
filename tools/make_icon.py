#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成工具架的缩略图 icon.png（Houdini 的 Download Shelf 列表里显示的那张图）。

    python tools/make_icon.py

纯标准库手写 PNG，不依赖 Pillow。画面：深色圆角方块 + 橙紫渐变圆环。
"""

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "icon.png"

W = H = 128
R = 26          # 圆角半径
RING_OUT = 46   # 环外径
RING_IN = 32    # 环内径


def lerp(a, b, t):
    return int(round(a + (b - a) * t))


def inside_round_rect(x, y, w, h, r):
    cx = min(max(x, r), w - r)
    cy = min(max(y, r), h - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def build_pixels():
    rows = []
    cx = cy = (W - 1) / 2.0
    for y in range(H):
        row = bytearray()
        row.append(0)  # PNG 每行的 filter type
        for x in range(W):
            px = x + 0.5
            py = y + 0.5
            if not inside_round_rect(px, py, W, H, R):
                row.extend((0, 0, 0, 0))
                continue
            t = (px + py) / (W + H)
            # 背景：深灰渐变
            br = lerp(38, 24, t)
            bg = lerp(40, 26, t)
            bb = lerp(46, 30, t)
            d = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
            if RING_IN <= d <= RING_OUT:
                # 环：橙 → 紫
                rr = lerp(255, 168, t)
                gg = lerp(150, 70, t)
                bb2 = lerp(60, 200, t)
                a = 255
                if d > RING_OUT - 1.2:      # 边缘抗锯齿
                    a = int(255 * max(0.0, (RING_OUT - d) / 1.2))
                elif d < RING_IN + 1.2:
                    a = int(255 * max(0.0, (d - RING_IN) / 1.2))
                row.extend((rr, gg, bb2, a))
            else:
                row.extend((br, bg, bb, 255))
        rows.append(bytes(row))
    return b"".join(rows)


def chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def main():
    raw = build_pixels()
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    OUT.write_bytes(png)
    print("已生成 %s（%d 字节）" % (OUT, len(png)))


if __name__ == "__main__":
    main()
