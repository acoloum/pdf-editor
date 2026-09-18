"""產生應用程式圖示 resources/icons/app.ico。

圖示語意：深色圓角底 ＋ 白色 PDF 文件頁 ＋ 青色鋼筆尖（呼應「墨頁」的墨水意象），
代表「編輯 PDF」。小尺寸（16～24 px）改畫簡化版本，避免細節糊成一團。
執行方式：python scripts/make_icon.py
"""
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "resources" / "icons" / "app.ico"
SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4

BACKGROUND_TOP = (16, 28, 46)
BACKGROUND_BOTTOM = (8, 14, 24)
BORDER = (34, 211, 238)
PAGE = (247, 250, 252)
PAGE_EDGE = (198, 212, 226)
FOLD = (205, 218, 232)
LINE = (126, 144, 166)
NIB_LIGHT = (103, 232, 249)
NIB_BASE = (34, 211, 238)
NIB_DARK = (14, 116, 144)
GRIP = (226, 236, 245)
PDF_LABEL = (220, 38, 38)
LABEL_FONTS = ("arialbd.ttf", "seguisb.ttf", "segoeuib.ttf")


def _gradient_background(size):
    image = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        blend = y / max(1, size - 1)
        draw.line([(0, y), (size, y)], fill=tuple(round(top + (bottom - top) * blend)
            for top, bottom in zip(BACKGROUND_TOP, BACKGROUND_BOTTOM)))
    return image


def _label_font(size):
    """畫「PDF」用的粗體字型；系統找不到時回傳 None，改為不畫標籤。"""
    folder = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in LABEL_FONTS:
        candidate = folder / name
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), round(size * 0.145))
            except OSError:
                continue
    return None


def _draw_page(draw, size, detailed, labelled):
    left, top = size * 0.15, size * 0.09
    right, bottom = size * 0.66, size * 0.86
    fold = size * 0.17
    draw.polygon([(left, top), (right - fold, top), (right, top + fold),
        (right, bottom), (left, bottom)], fill=PAGE, outline=PAGE_EDGE,
        width=max(1, round(size * 0.012)))
    draw.polygon([(right - fold, top), (right, top + fold), (right - fold, top + fold)],
        fill=FOLD)
    if not detailed:
        return
    line_left, line_right = left + size * 0.06, right - size * 0.08
    for index in range(3):
        y = top + size * (0.22 + index * 0.115)
        end = line_right if index % 2 == 0 else line_right - size * 0.13
        draw.line([(line_left, y), (end, y)], fill=LINE, width=max(1, round(size * 0.036)))
    font = _label_font(size) if labelled else None
    if font is not None:
        # 「PDF」置於文字行下方、筆尖左側，兩者不重疊。
        draw.text((line_left, top + size * 0.50), "PDF", font=font, fill=PDF_LABEL)


def _nib_shape(tip, angle, length, half_width):
    """以筆尖為原點，回傳鋼筆尖的輪廓點；angle 為筆桿指向（度）。"""
    radians = math.radians(angle)
    along = (math.cos(radians), math.sin(radians))
    across = (-along[1], along[0])

    def point(distance, offset):
        return (tip[0] + along[0] * distance + across[0] * offset,
            tip[1] + along[1] * distance + across[1] * offset)

    shoulder = length * 0.46
    return {
        "outline": [tip, point(shoulder, -half_width), point(length, -half_width * 0.86),
            point(length, half_width * 0.86), point(shoulder, half_width)],
        "slit": [tip, point(length * 0.62, 0)],
        "vent": point(length * 0.66, 0),
        "grip": [point(length, -half_width * 0.86), point(length * 1.42, -half_width * 0.7),
            point(length * 1.42, half_width * 0.7), point(length, half_width * 0.86)],
        "barrel": [point(length * 1.42, -half_width * 0.62), point(length * 2.35, -half_width * 0.34),
            point(length * 2.35, half_width * 0.34), point(length * 1.42, half_width * 0.62)],
    }


def _draw_nib(draw, size, detailed):
    tip = (size * 0.56, size * 0.88)
    parts = _nib_shape(tip, angle=-50, length=size * 0.28, half_width=size * 0.082)
    if detailed:
        draw.polygon(parts["barrel"], fill=NIB_DARK)
        draw.polygon(parts["grip"], fill=GRIP)
    draw.polygon(parts["outline"], fill=NIB_BASE)
    if not detailed:
        return
    # 筆尖左半以較亮的色調表現金屬反光，中線為筆縫、圓孔為氣孔。
    highlight = parts["outline"][:3] + [parts["slit"][1]]
    draw.polygon(highlight, fill=NIB_LIGHT)
    draw.line(parts["slit"], fill=NIB_DARK, width=max(1, round(size * 0.018)))
    radius = size * 0.028
    draw.ellipse([parts["vent"][0] - radius, parts["vent"][1] - radius,
        parts["vent"][0] + radius, parts["vent"][1] + radius], fill=NIB_DARK)


def _render(size):
    canvas = size * SUPERSAMPLE
    detailed, labelled = size >= 32, size >= 48
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, canvas - 1, canvas - 1],
        radius=canvas * 0.22, fill=255)
    image.paste(_gradient_background(canvas), (0, 0), mask)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([1, 1, canvas - 2, canvas - 2], radius=canvas * 0.22,
        outline=BORDER, width=max(SUPERSAMPLE, round(canvas * (0.025 if detailed else 0.015))))
    _draw_page(draw, canvas, detailed, labelled)
    _draw_nib(draw, canvas, detailed)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def build(target=TARGET):
    target.parent.mkdir(parents=True, exist_ok=True)
    images = [_render(size) for size in SIZES]
    images[-1].save(target, format="ICO", sizes=[(size, size) for size in SIZES])
    return target


if __name__ == "__main__":
    print(build())
