#!/usr/bin/env python3
"""生成应用图标 aglauncher/assets/icon.png 与多尺寸 icon.ico（需要 Pillow）。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "aglauncher" / "assets"


def _star_points(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    *,
    control: float = 0.40,
    steps: int = 96,
) -> list[tuple[float, float]]:
    """四角星轮廓：四个方向各一条二次贝塞尔曲线，边线轻微内凹。"""
    quadrants = (
        ((0.0, -1.0), (control, -control), (1.0, 0.0)),
        ((1.0, 0.0), (control, control), (0.0, 1.0)),
        ((0.0, 1.0), (-control, control), (-1.0, 0.0)),
        ((-1.0, 0.0), (-control, -control), (0.0, -1.0)),
    )
    points: list[tuple[float, float]] = []
    for p0, p1, p2 in quadrants:
        for i in range(steps + 1):
            t = i / steps
            x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t**2 * p2[0]
            y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t**2 * p2[1]
            points.append((cx + x * rx, cy + y * ry))
    return points


def render_icon(size: int = 512) -> Image.Image:
    """绘制米白圆角底座 + 黑色四角星图标。"""
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = round(s * 0.035)
    radius = round(s * 0.22)
    # 圆角米白底座，保留参考图的浅色背景
    draw.rounded_rectangle(
        (pad, pad, s - pad, s - pad),
        radius=radius,
        fill=(250, 249, 245, 255),
        outline=(218, 217, 213, 255),
        width=max(2, round(s * 0.006)),
    )

    cx, cy = s / 2, s / 2
    draw.polygon(
        _star_points(cx, cy, s * 0.335, s * 0.315),
        fill=(31, 30, 29, 255),
    )
    return img.resize((size, size), Image.Resampling.LANCZOS)


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
