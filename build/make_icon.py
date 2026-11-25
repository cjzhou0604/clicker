"""生成连点器图标（纯标准库，无需 Pillow）。

图标为「光标 + 点击波纹」造型，按 4x 超采样后盒式降采样以消除锯齿，
再用 struct 直接拼出 Windows .ico（32 位 BGRA + AND 掩码）。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

SS = 4                      # 超采样倍数
SIZES = (16, 24, 32, 48, 64)
TOP = (0x3E, 0x7B, 0xF5)    # 渐变上端
BOTTOM = (0x14, 0x2E, 0x7A)  # 渐变下端

# 经典鼠标箭头轮廓，尖端位于原点，指向左上方。
ARROW = [
    (0.0, 0.0), (0.0, 16.0), (3.8, 12.2), (6.6, 18.6),
    (9.2, 17.5), (6.4, 11.2), (12.0, 11.2),
]


def inside(points: list[tuple[float, float]], x: float, y: float) -> bool:
    hit = False
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def sample(px: float, py: float, size: int) -> tuple[int, int, int, int] | None:
    """返回归一化坐标 (px, py) 处的颜色；size 为最终图标边长。"""
    radius = size * 0.22
    # 圆角矩形：角外区域需落在角圆内。
    lo, hi = radius, size - radius
    cx = min(max(px, lo), hi)
    cy = min(max(py, lo), hi)
    if (px - cx) ** 2 + (py - cy) ** 2 > radius ** 2:
        return None

    t = max(0.0, min(1.0, py / size))
    color = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM))
    red, green, blue = color

    tip_x, tip_y = size * 0.30, size * 0.24
    dist = ((px - tip_x) ** 2 + (py - tip_y) ** 2) ** 0.5
    # 两道向外扩散的点击波纹。
    for ring, band in ((size * 0.20, size * 0.035), (size * 0.34, size * 0.028)):
        edge = max(0.0, 1.0 - abs(dist - ring) / band)
        if edge:
            mix = edge * 0.75
            red = round(red + (255 - red) * mix)
            green = round(green + (255 - green) * mix)
            blue = round(blue + (255 - blue) * mix)

    scale = size / 26.0
    arrow = [(tip_x + x * scale, tip_y + y * scale) for x, y in ARROW]
    if inside(arrow, px, py):
        return 255, 255, 255, 255
    step = size / 90.0
    for dx, dy in ((x, y) for x in (-1, 0, 1) for y in (-1, 0, 1)):
        shifted = [(x + dx * step, y + dy * step) for x, y in arrow]
        if inside(shifted, px, py):
            return 12, 26, 62, 255
    return red, green, blue, 255


def render(size: int) -> bytes:
    rows = []
    for y in range(size - 1, -1, -1):          # ICO 自下而上存放
        row = bytearray()
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                for sx in range(SS):
                    color = sample(x + (sx + 0.5) / SS, y + (sy + 0.5) / SS, size)
                    if color is None:
                        continue
                    for i in range(4):
                        acc[i] += color[i]
            n = SS * SS
            alpha = acc[3] // n
            blue, green, red = acc[2] // n, acc[1] // n, acc[0] // n
            # 预乘 alpha，Windows 对非预乘 32 位图标处理更稳妥。
            row += bytes((blue * alpha // 255, green * alpha // 255,
                          red * alpha // 255, alpha))
        rows.append(bytes(row))
    pixels = b"".join(rows)
    mask_row = ((size + 31) // 32) * 4
    mask = b"\x00" * (mask_row * size)         # 全 0：形状由 alpha 决定
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         len(pixels) + len(mask), 0, 0, 0, 0)
    return header + pixels + mask


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "clicker.ico")
    images = [(size, render(size)) for size in SIZES]
    directory = b""
    offset = 6 + 16 * len(images)
    for size, data in images:
        directory += struct.pack("<BBBBHHII", size % 256, 0, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    body = b"".join(data for _, data in images)
    out.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + directory + body)
    print(f"已生成 {out}（{out.stat().st_size} 字节，含 {'/'.join(str(s) for s, _ in images)} 像素）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
