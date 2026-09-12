import io

import pymupdf
import pytest
from PIL import Image

from pdf_editor.errors import EditorError
from pdf_editor.comparison import compare_pages


def make_pdf(width=200, height=120, color=(255, 255, 255), block=None):
    """建立可預期像素內容的單頁 PDF。"""
    document = pymupdf.open()
    page = document.new_page(width=width, height=height)
    page.draw_rect(page.rect, color=None, fill=tuple(channel / 255 for channel in color), overlay=False)
    if block is not None:
        rectangle, block_color = block
        page.draw_rect(rectangle, color=None,
            fill=tuple(channel / 255 for channel in block_color))
    data = document.tobytes()
    document.close()
    return data


def zero_user_unit_pdf():
    """建立會由 PyMuPDF 渲染為零尺寸 pixmap 的頁面。"""
    document = pymupdf.open()
    page = document.new_page(width=200, height=120)
    document.xref_set_key(page.xref, "UserUnit", "0")
    data = document.tobytes()
    document.close()
    return data


@pytest.fixture
def simple_pdf():
    return make_pdf()


@pytest.fixture
def changed_pdf():
    return make_pdf(block=(pymupdf.Rect(60, 40, 140, 90), (0, 0, 0)))


def test_identical_pages_have_one_hundred_percent_similarity(simple_pdf):
    result = compare_pages(simple_pdf, 0, simple_pdf, 0)

    assert result.similarity == 100.0
    assert result.changed_pixels == 0
    assert result.total_pixels > 0


def test_changed_block_is_marked_red(simple_pdf, changed_pdf):
    result = compare_pages(simple_pdf, 0, changed_pdf, 0)
    image = Image.open(io.BytesIO(result.difference_png)).convert("RGB")

    assert 0.0 < result.similarity < 100.0
    assert any(red > green + 30 and red > blue + 30
        for red, green, blue in image.get_flattened_data())


def test_channel_difference_equal_to_threshold_is_ignored(monkeypatch, simple_pdf):
    base = Image.new("RGB", (4, 3), (100, 100, 100))
    comparison = Image.new("RGB", (4, 3), (124, 100, 100))
    rendered = iter((base, comparison))
    monkeypatch.setattr("pdf_editor.comparison._render", lambda *_: next(rendered))

    result = compare_pages(simple_pdf, 0, simple_pdf, 0, threshold=24)

    assert result.changed_pixels == 0
    assert result.similarity == 100.0


def test_channel_difference_above_threshold_is_counted(monkeypatch, simple_pdf):
    base = Image.new("RGB", (4, 3), (100, 100, 100))
    comparison = Image.new("RGB", (4, 3), (125, 100, 100))
    rendered = iter((base, comparison))
    monkeypatch.setattr("pdf_editor.comparison._render", lambda *_: next(rendered))

    result = compare_pages(simple_pdf, 0, simple_pdf, 0, threshold=24)

    assert result.changed_pixels == 12
    assert result.similarity == 0.0


def test_different_size_page_is_scaled_and_centered_on_base_canvas():
    base_pdf = make_pdf(width=200, height=100)
    comparison_pdf = make_pdf(width=100, height=200, color=(0, 0, 0))

    result = compare_pages(base_pdf, 0, comparison_pdf, 0)
    base = Image.open(io.BytesIO(result.base_png)).convert("RGB")
    comparison = Image.open(io.BytesIO(result.comparison_png)).convert("RGB")

    assert comparison.size == base.size
    assert comparison.getpixel((0, comparison.height // 2)) == (255, 255, 255)
    assert comparison.getpixel((comparison.width // 2, comparison.height // 2)) == (0, 0, 0)


@pytest.mark.parametrize(("width", "height", "pixel"), (
    (1, 1000, (199, 120)),
    (1000, 1, (200, 119)),
))
def test_extremely_thin_comparison_page_keeps_at_least_one_pixel(width, height, pixel, simple_pdf):
    comparison_pdf = make_pdf(width=width, height=height, color=(0, 0, 0))

    result = compare_pages(simple_pdf, 0, comparison_pdf, 0)
    comparison = Image.open(io.BytesIO(result.comparison_png)).convert("RGB")

    assert comparison.size == (400, 240)
    assert comparison.getpixel(pixel) == (0, 0, 0)


@pytest.mark.parametrize("zero_is_base", (True, False))
def test_zero_size_rendered_page_raises_invalid_pdf_at_each_input_position(simple_pdf, zero_is_base):
    zero_size_pdf = zero_user_unit_pdf()
    base_pdf, comparison_pdf = ((zero_size_pdf, simple_pdf) if zero_is_base
                                else (simple_pdf, zero_size_pdf))

    with pytest.raises(EditorError) as error:
        compare_pages(base_pdf, 0, comparison_pdf, 0)

    assert error.value.code == "INVALID_PDF"


@pytest.mark.parametrize("kwargs", (
    {"dpi": 71},
    {"dpi": 301},
    {"threshold": -1},
    {"threshold": 256},
    {"base_page": -1},
    {"comparison_page": -1},
    {"base_page": 1},
    {"comparison_page": 1},
))
def test_invalid_comparison_ranges_raise_compare_range(simple_pdf, kwargs):
    base_page = kwargs.pop("base_page", 0)
    comparison_page = kwargs.pop("comparison_page", 0)

    with pytest.raises(EditorError) as error:
        compare_pages(simple_pdf, base_page, simple_pdf, comparison_page, **kwargs)

    assert error.value.code == "COMPARE_RANGE"


def test_empty_pdf_uses_existing_empty_error(simple_pdf):
    stream = io.BytesIO()
    stream.write(b"%PDF-1.7\n")
    offsets = [0]
    for number, body in ((1, b"<< /Type /Catalog /Pages 2 0 R >>"),
                         (2, b"<< /Type /Pages /Kids [] /Count 0 >>")):
        offsets.append(stream.tell())
        stream.write(f"{number} 0 obj\n".encode())
        stream.write(body)
        stream.write(b"\nendobj\n")
    xref = stream.tell()
    stream.write(b"xref\n0 3\n0000000000 65535 f \n")
    stream.writelines(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    stream.write(f"trailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    empty_pdf = stream.getvalue()

    with pytest.raises(EditorError) as error:
        compare_pages(empty_pdf, 0, simple_pdf, 0)

    assert error.value.code == "EMPTY"


def test_encrypted_pdf_uses_existing_password_error(simple_pdf):
    with pymupdf.open(stream=simple_pdf) as document:
        encrypted_pdf = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner", user_pw="user")

    with pytest.raises(EditorError) as error:
        compare_pages(encrypted_pdf, 0, simple_pdf, 0)

    assert error.value.code == "PASSWORD"


def test_invalid_pdf_uses_existing_invalid_pdf_error(simple_pdf):
    with pytest.raises(EditorError) as error:
        compare_pages(b"not a PDF", 0, simple_pdf, 0)

    assert error.value.code == "INVALID_PDF"
