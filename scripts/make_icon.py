#!/usr/bin/env python3
"""生成应用图标 aglauncher/assets/icon.png 与多尺寸 icon.ico（需要 Pillow）。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "aglauncher" / "assets"


def render_icon(size: int = 512) -> Image.Image:
    """绘制深色底座 + 代理节点脉冲环 + 上升箭头矢量图标。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = int(size * 0.04)
    radius = int(size * 0.22)
    # 圆角深色底座
    draw.rounded_rectangle(
        (pad, pad, size - pad, size - pad),
        radius=radius,
        fill=(15, 17, 21, 255),
        outline=(54, 65, 86, 255),
        width=max(2, size // 64),
    )

    # 内层微光环
    cx, cy = size // 2, size // 2
    ring_r = int(size * 0.34)
    draw.ellipse(
        (cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r),
        outline=(76, 141, 255, 190),
        width=max(4, size // 28),
    )

    # 右上角绿色节点状态点
    dot_r = max(5, int(size * 0.065))
    dot_x = cx + int(ring_r * 0.72)
    dot_y = cy - int(ring_r * 0.72)
    draw.ellipse(
        (dot_x - dot_r - 3, dot_y - dot_r - 3, dot_x + dot_r + 3, dot_y + dot_r + 3),
        fill=(15, 17, 21, 255),
    )
    draw.ellipse(
        (dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r),
        fill=(61, 220, 132, 255),
    )

    # 中心向上“反重力 / 隧道穿透”箭头
    s = size
    arrow = [
        (s * 0.50, s * 0.23),
        (s * 0.72, s * 0.48),
        (s * 0.58, s * 0.48),
        (s * 0.58, s * 0.75),
        (s * 0.42, s * 0.75),
        (s * 0.42, s * 0.48),
        (s * 0.28, s * 0.48),
    ]
    draw.polygon(arrow, fill=(76, 141, 255, 255))

    # 底座横条（代表进程边界/隔离层）
    bar_w = int(s * 0.30)
    bar_h = max(4, int(s * 0.045))
    draw.rounded_rectangle(
        (cx - bar_w // 2, int(s * 0.79), cx + bar_w // 2, int(s * 0.79) + bar_h),
        radius=bar_h // 2,
        fill=(61, 220, 132, 240),
    )
    return img


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = render_icon(512)
    png_path = ASSETS / "icon.png"
    ico_path = ASSETS / "icon.ico"
    master.save(png_path, format="PNG")
    # 保存 16..256 多尺寸 ICO
    master.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Generated {png_path} and {ico_path}")


if __name__ == "__main__":
    main()
