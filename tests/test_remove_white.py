"""圖層去除白底：顯示與輸出共用同一套處理。"""

from dataclasses import replace
import io

import pymupdf
from PIL import Image

from pdf_editor.engine.overlay import flatten_overlays, transformed_png
from pdf_editor.model import Overlay


def _row_png(pixels):
    image = Image.new("RGBA", (len(pixels), 1))
    for x, pixel in enumerate(pixels):
        image.putpixel((x, 0), pixel)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _alphas(png):
    with Image.open(io.BytesIO(png)) as image:
        rgba = image.convert("RGBA")
        return [rgba.getpixel((x, 0))[3] for x in range(rgba.width)]


SOURCE = _row_png([
    (255, 255, 255, 255),   # 純白：完全透明
    (225, 225, 225, 255),   # 淺灰：漸變到一半
    (240, 236, 250, 255),   # 最暗色版 236：完全透明
    (0, 0, 0, 255),         # 黑色：保留
    (0, 0, 0, 100),         # 原本半透明：保留原透明度
    (250, 10, 10, 255),     # 紅色：保留
])


def test_remove_white_makes_near_white_transparent_with_soft_edge():
    png, rect = transformed_png(SOURCE, (0, 0, 6, 1), 0, remove_white=True)

    assert _alphas(png) == [0, 128, 0, 255, 100, 255]
    assert rect == (0, 0, 6, 1)


def test_remove_white_disabled_keeps_original_alpha():
    png, _rect = transformed_png(SOURCE, (0, 0, 6, 1), 0)

    assert _alphas(png) == [255, 255, 255, 255, 100, 255]


def test_remove_white_applies_to_rotated_layers():
    png, _rect = transformed_png(SOURCE, (0, 0, 60, 10), 30, remove_white=True)

    with Image.open(io.BytesIO(png)) as image:
        assert image.convert("RGBA").getpixel((0, 0))[3] == 0


def test_flatten_with_remove_white_shows_content_underneath(tmp_path):
    stamp = tmp_path / "白底章.png"
    Image.new("RGBA", (40, 40), (255, 255, 255, 255)).save(stamp)
    with pymupdf.open() as document:
        page = document.new_page(width=200, height=200)
        page.draw_rect((50, 50, 150, 150), color=None, fill=(0, 0, 0))
        pdf = document.tobytes()
    layer = Overlay("白底", 0, str(stamp), (60, 60, 140, 140))

    def center(content):
        with pymupdf.open(stream=content, filetype="pdf") as document:
            return document[0].get_pixmap(alpha=False).pixel(100, 100)

    assert center(flatten_overlays(pdf, (layer,))) == (255, 255, 255)
    assert center(flatten_overlays(pdf, (replace(layer, remove_white=True),))) == (0, 0, 0)
