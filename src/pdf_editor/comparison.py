"""PDF 頁面的像素差異比較。"""

from dataclasses import dataclass
import io

import pymupdf
from PIL import Image, ImageChops, ImageOps

from pdf_editor.engine.inspection import unlock_pdf
from pdf_editor.errors import EditorError


@dataclass(frozen=True)
class PageComparison:
    """頁面比較產生的預覽影像及統計結果。"""

    base_png: bytes
    comparison_png: bytes
    difference_png: bytes
    similarity: float
    changed_pixels: int
    total_pixels: int
    base_page: int
    comparison_page: int


def _validate_option_ranges(dpi, threshold, base_page, comparison_page):
    if (isinstance(dpi, bool) or not isinstance(dpi, (int, float))
            or not 72 <= dpi <= 300):
        raise EditorError("COMPARE_RANGE", "比較解析度必須介於 72 與 300 DPI。")
    if (isinstance(threshold, bool) or not isinstance(threshold, int)
            or not 0 <= threshold <= 255):
        raise EditorError("COMPARE_RANGE", "差異門檻必須介於 0 與 255。")
    if (isinstance(base_page, bool) or not isinstance(base_page, int)
            or isinstance(comparison_page, bool) or not isinstance(comparison_page, int)
            or base_page < 0 or comparison_page < 0):
        raise EditorError("COMPARE_RANGE", "比較頁碼超出文件範圍。")


def _render(pdf, page, dpi):
    """以指定解析度將一頁 PDF 渲染成 RGB 圖像。"""
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        if page >= document.page_count:
            raise EditorError("COMPARE_RANGE", "比較頁碼超出文件範圍。")
        pixmap = document[page].get_pixmap(
            matrix=pymupdf.Matrix(dpi / 72, dpi / 72), alpha=False)
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def _align_to_base(base, comparison):
    """將比較圖等比例縮放並置中到基準圖尺寸的白色畫布。"""
    contained = ImageOps.contain(comparison, base.size)
    canvas = Image.new("RGB", base.size, "white")
    offset = ((base.width - contained.width) // 2, (base.height - contained.height) // 2)
    canvas.paste(contained, offset)
    return canvas


def _png(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def compare_pages(base_pdf: bytes, base_page: int, comparison_pdf: bytes,
                  comparison_page: int, dpi: int = 144,
                  threshold: int = 24) -> PageComparison:
    """比較兩份 PDF 的指定頁面，回傳預覽影像與像素相似度。"""
    _validate_option_ranges(dpi, threshold, base_page, comparison_page)
    base_pdf, _ = unlock_pdf(base_pdf)
    comparison_pdf, _ = unlock_pdf(comparison_pdf)
    base = _render(base_pdf, base_page, dpi).convert("RGB")
    comparison = _align_to_base(base, _render(comparison_pdf, comparison_page, dpi).convert("RGB"))

    difference = ImageChops.difference(base, comparison)
    maximum = ImageChops.lighter(
        ImageChops.lighter(difference.getchannel("R"), difference.getchannel("G")),
        difference.getchannel("B"))
    mask = maximum.point(lambda value: 255 if value > threshold else 0)
    changed_pixels = mask.histogram()[255]
    total_pixels = base.width * base.height
    highlighted = Image.blend(base, Image.new("RGB", base.size, "red"), 0.45)
    marked = Image.composite(highlighted, base, mask)

    return PageComparison(
        base_png=_png(base),
        comparison_png=_png(comparison),
        difference_png=_png(marked),
        similarity=round((total_pixels - changed_pixels) * 100 / total_pixels, 1),
        changed_pixels=changed_pixels,
        total_pixels=total_pixels,
        base_page=base_page,
        comparison_page=comparison_page,
    )
