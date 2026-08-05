#!/usr/bin/env python3
"""生成 Workbench 桌面 App 的图标源图(512x512 PNG)。

用法:
    python make_icon.py               # 输出 ./icon-source.png
    npx tauri icon ./icon-source.png  # 生成 src-tauri/icons/ 全套(CI 里做)

纯 Pillow 实现,无第三方依赖(除 Pillow)。
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 512
ROOT = Path(__file__).resolve().parent


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def main() -> None:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 背景:深紫纵向渐变 + 圆角矩形(紫色主题,与 app/static/style.css 呼应)
    top = (58, 44, 104)     # #3a2c68
    bottom = (124, 77, 255)  # #7c4dff
    for y in range(SIZE):
        t = y / (SIZE - 1)
        color = lerp(top, bottom, t)
        draw.line([(0, y), (SIZE, y)], fill=color + (255,))

    # 圆角遮罩
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, SIZE - 1, SIZE - 1], radius=110, fill=255,
    )
    img.putalpha(mask)

    # 中央白色圆环(仪表盘意象)
    cx, cy, r, w = SIZE // 2, SIZE // 2, 150, 34
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, 255), width=w)

    # 中心 "W" 字母(多平台字体路径探测,找不到就退回 Pillow 内置字体)
    font = None
    for font_path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # Linux
        "/System/Library/Fonts/Helvetica.ttc",                    # macOS
        "C:/Windows/Fonts/arialbd.ttf",                           # Windows
    ]:
        try:
            font = ImageFont.truetype(font_path, 200)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    text = "W"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        (cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]),
        text, font=font, fill=(255, 255, 255, 255),
    )

    out = ROOT / "icon-source.png"
    img.save(out, "PNG")
    print(f"✓ 图标已生成: {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
