import io
import sys
import types

import pymupdf
import pytest
from PIL import Image

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
