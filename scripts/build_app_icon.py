"""生成应用图标 assets/app.ico（从 ui/assets/avatar.png 圆形裁剪 + 缩放）。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "ui" / "assets" / "avatar.png"
OUT = ROOT / "ui" / "assets" / "app.ico"


def main() -> None:
    img = Image.open(SRC).convert("RGBA")
    # 居中方形裁剪
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))

    # 圆形蒙版
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, img.size[0] - 1, img.size[1] - 1), fill=255)
    img.putalpha(mask)

    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(OUT, format="ICO", sizes=sizes)
    print(f"icon written: {OUT}")


if __name__ == "__main__":
    main()
