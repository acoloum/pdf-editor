import io
import shutil
import sys
import types

import pymupdf
import pytest
from PIL import Image

import pdf_editor.ocr as ocr
from pdf_editor.engine import fonts
from pdf_editor.errors import EditorError
from pdf_editor.ocr import OcrWord, ocr_pages, parse_tsv, recognize_image


def make_tsv(*words):
    rows = ["left\ttop\twidth\theight\tconf\ttext"]
    rows.extend(
        f"{left}\t{top}\t{width}\t{height}\t{confidence}\t{text}"
        for text, confidence, (left, top, width, height) in words
    )
    return "\n".join(rows) + "\n"


def scan_pdf(page_count=1):
    doc = pymupdf.open()
    image = io.BytesIO()
    Image.new("RGB", (500, 400), (190, 225, 240)).save(image, format="PNG")
    for _ in range(page_count):
        page = doc.new_page(width=500, height=400)
        page.insert_image(page.rect, stream=image.getvalue())
    result = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return result


def render_png(pdf):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc[0].get_pixmap(alpha=False).tobytes("png")


def find_ocr_span(pdf, text):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[0]
        span = next(span for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", []) for span in line["spans"] if span["text"] == text)
        return pymupdf.Rect(span["bbox"]), page.rotation, page.derotation_matrix


def expected_ocr_rect(pixel_rect, derotation_matrix, dpi=300):
    left, top, width, height = pixel_rect
    scale = dpi / 72
    return pymupdf.Rect(left / scale, top / scale,
        (left + width) / scale, (top + height) / scale) * derotation_matrix


def assert_rect_within(expected, actual, tolerance=0.01):
    assert expected.x0 - tolerance <= actual.x0
    assert expected.y0 - tolerance <= actual.y0
    assert actual.x1 <= expected.x1 + tolerance
    assert actual.y1 <= expected.y1 + tolerance


@pytest.fixture
def scanned_pdf():
    return scan_pdf()


@pytest.fixture
def two_scan_pdf():
    return scan_pdf(2)


def test_parse_tsv_returns_word_above_minimum_confidence():
    tsv = "left\ttop\twidth\theight\tconf\ttext\n40\t60\t120\t80\t92.4\t材料\n"

    assert parse_tsv(tsv) == (OcrWord("材料", 92.4, (40, 60, 120, 80)),)


def test_parse_tsv_drops_word_below_minimum_confidence():
    tsv = "left\ttop\twidth\theight\tconf\ttext\n40\t60\t120\t80\t34.9\t材料\n"

    assert parse_tsv(tsv) == ()


def test_parse_tsv_rejects_missing_required_columns():
    tsv = "left\ttop\twidth\theight\tconf\n40\t60\t120\t80\t92.4\n"

    with pytest.raises(EditorError) as error:
        parse_tsv(tsv)

    assert error.value.code == "OCR_OUTPUT"


def test_parse_tsv_rejects_row_with_missing_text_value():
    tsv = "left\ttop\twidth\theight\tconf\ttext\n40\t60\t120\t80\t92.4\n"

    with pytest.raises(EditorError) as error:
        parse_tsv(tsv)

    assert error.value.code == "OCR_OUTPUT"


def test_ocr_pages_adds_searchable_invisible_text_without_changing_page_pixels(scanned_pdf, tmp_path):
    def recognizer(image, tessdata, language):
        return make_tsv(("材料測試報告", 90, (100, 120, 420, 170)))

    before = render_png(scanned_pdf)
    result = ocr_pages(scanned_pdf, (0,), tmp_path, recognizer=recognizer)

    with pymupdf.open(stream=result.pdf, filetype="pdf") as doc:
        assert "材料測試報告" in doc[0].get_text()
    assert render_png(result.pdf) == before
    assert result.processed_pages == (0,)
    assert result.word_count == 1


def test_ocr_pages_skips_page_that_already_has_text(pdf_bytes, tmp_path):
    result = ocr_pages(pdf_bytes, (0,), tmp_path, recognizer=lambda *args: pytest.fail())

    assert result.processed_pages == ()
    assert result.skipped_pages == (0,)
    assert result.pdf == pdf_bytes


def test_ocr_pages_does_not_return_partial_pdf_when_later_page_has_no_result(two_scan_pdf, tmp_path):
    calls = iter((make_tsv(("第一頁", 90, (10, 10, 100, 40))), make_tsv()))

    with pytest.raises(EditorError) as error:
        ocr_pages(two_scan_pdf, (0, 1), tmp_path, recognizer=lambda *args: next(calls))

    assert error.value.code == "OCR_EMPTY"


def test_ocr_pages_keeps_text_horizontal_on_a_rotated_page(scanned_pdf, tmp_path):
    with pymupdf.open(stream=scanned_pdf, filetype="pdf") as source:
        source[0].set_rotation(90)
        rotated_pdf = source.tobytes()

    result = ocr_pages(rotated_pdf, (0,), tmp_path,
        recognizer=lambda *args: make_tsv(("旋轉頁面", 90, (60, 80, 160, 40))))

    with pymupdf.open(stream=result.pdf, filetype="pdf") as doc:
        line = next(line for block in doc[0].get_text("dict")["blocks"]
            for line in block.get("lines", []) if "旋轉頁面" in line["spans"][0]["text"])
    assert line["dir"] == pytest.approx((0.0, -1.0))


def test_ocr_pages_keeps_bottom_edge_text_within_its_tsv_box(scanned_pdf, tmp_path):
    pixel_rect = (80, 1530, 300, 120)
    result = ocr_pages(scanned_pdf, (0,), tmp_path,
        recognizer=lambda *args: make_tsv(("底緣文字", 90, pixel_rect)))

    span_rect, _, derotation = find_ocr_span(result.pdf, "底緣文字")
    expected = expected_ocr_rect(pixel_rect, derotation)

    assert_rect_within(expected, span_rect)


def test_ocr_pages_keeps_rotated_bottom_edge_text_within_its_tsv_box(scanned_pdf, tmp_path):
    with pymupdf.open(stream=scanned_pdf, filetype="pdf") as source:
        source[0].set_rotation(90)
        rotated_pdf = source.tobytes()
    pixel_rect = (80, 1950, 500, 100)
    result = ocr_pages(rotated_pdf, (0,), tmp_path,
        recognizer=lambda *args: make_tsv(("旋轉底緣", 90, pixel_rect)))

    span_rect, rotation, derotation = find_ocr_span(result.pdf, "旋轉底緣")
    expected = expected_ocr_rect(pixel_rect, derotation)

    assert rotation == 90
    assert_rect_within(expected, span_rect)


def test_ocr_pages_writes_text_with_the_frozen_resource_font(scanned_pdf, tmp_path, monkeypatch):
    """凍結版資源位於 _internal 時，仍可寫入不可見文字。"""
    frozen_root = tmp_path / "_internal"
    frozen_font = frozen_root / "resources" / "fonts" / "NotoSansCJKtc-Regular.otf"
    frozen_font.parent.mkdir(parents=True)
    shutil.copy2(fonts.default_font(), frozen_font)
    monkeypatch.setattr(fonts, "resource_root", lambda: frozen_root)
    monkeypatch.setattr(ocr, "OCR_FONT",
        frozen_root.parent / "resources" / "fonts" / "NotoSansCJKtc-Regular.otf", raising=False)

    result = ocr.ocr_pages(scanned_pdf, (0,), tmp_path,
        recognizer=lambda *args: make_tsv(("凍結資源文字", 90, (80, 100, 280, 60))))

    with pymupdf.open(stream=result.pdf, filetype="pdf") as doc:
        assert "凍結資源文字" in doc[0].get_text()


def test_recognize_image_returns_tesseract_tsv(monkeypatch, tmp_path):
    class Api:
        def __init__(self, path, lang):
            self.path = path
            self.language = lang

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def SetImage(self, image):
            self.image = image

        def Recognize(self):
            return None

        def GetTSVText(self, page_number):
            return make_tsv(("辨識文字", 90, (10, 20, 30, 40)))

    monkeypatch.setitem(sys.modules, "tesserocr", types.SimpleNamespace(PyTessBaseAPI=Api))
    image = Image.new("RGB", (50, 50), "white")

    assert recognize_image(image, tmp_path) == make_tsv(("辨識文字", 90, (10, 20, 30, 40)))
