from pathlib import Path
import io
import pytest
import pymupdf
from PIL import Image

FONT = Path(__file__).parents[1] / "resources/fonts/NotoSansCJKtc-Regular.otf"

@pytest.fixture
def font_path():
    return str(FONT)

@pytest.fixture(scope="session")
def pdf_bytes():
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=400)
    buf = io.BytesIO()
    Image.new("RGB", (200, 70), (190, 225, 240)).save(buf, format="PNG")
    page.insert_image((30, 40, 400, 120), stream=buf.getvalue())
    page.draw_rect((25, 35, 410, 125), color=(0.1, 0.2, 0.6), width=2)
    page.insert_font(fontname="noto", fontfile=str(FONT))
    page.insert_text((40, 80), "品質檢驗 ABC 123", fontname="noto", fontsize=16)
    page.insert_text((40, 180), "品質檢驗 ABC 123", fontname="noto", fontsize=16)
    page.insert_text((40, 260), "KEEP ME", fontsize=18)
    doc.subset_fonts(fallback=True)
    data = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return data

@pytest.fixture
def source_path(tmp_path, pdf_bytes):
    path = tmp_path / "原始.pdf"
    path.write_bytes(pdf_bytes)
    return path


@pytest.fixture
def multi_page_path(tmp_path):
    doc = pymupdf.open()
    for index in range(3):
        page = doc.new_page(width=300, height=200)
        page.insert_text((40, 80), f"PAGE {index + 1}", fontsize=18)
    path = tmp_path / "三頁.pdf"
    path.write_bytes(doc.tobytes())
    doc.close()
    return path
