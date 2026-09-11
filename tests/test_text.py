from dataclasses import replace
import hashlib
import pytest
import pymupdf
from pdf_editor.engine.text import extract_runs, replace_text
from pdf_editor.model import TextReplacement
from pdf_editor.errors import EditorError

def request_for(data, font_path):
    run = next(r for r in extract_runs(data, 0) if "品質" in r.text)
    return TextReplacement(hashlib.sha256(data).hexdigest(), 0, run.id,
        "檢測合格 XYZ 789", (40, 55, 390, 105), font_path, 16, (0, 0, 0))

def test_replace_removes_only_target_and_preserves_background(pdf_bytes, font_path):
    request = request_for(pdf_bytes, font_path)
    out = replace_text(pdf_bytes, request)
    with pymupdf.open(stream=out) as new, pymupdf.open(stream=pdf_bytes) as old:
        assert "檢測合格" in new[0].get_text(clip=(30, 40, 400, 120))
        assert "品質" not in new[0].get_text(clip=(30, 40, 400, 120))
        assert "品質檢驗" in new[0].get_text(clip=(30, 140, 400, 200))
        for clip in [(0, 130, 500, 400), (25, 35, 410, 50), (25, 110, 410, 126)]:
            assert new[0].get_pixmap(clip=clip).samples == old[0].get_pixmap(clip=clip).samples

@pytest.mark.parametrize("kind", ["overflow", "stale", "glyph", "overlap"])
def test_reject_unsafe_replacement(pdf_bytes, font_path, kind):
    req = request_for(pdf_bytes, font_path)
    if kind == "overflow":
        req = replace(req, text="文字" * 500)
    elif kind == "stale":
        req = replace(req, document_hash="invalid")
    elif kind == "glyph":
        req = replace(req, text=chr(0x10FFFF))
    else:
        req = replace(req, rect=(30, 40, 400, 280))
    with pytest.raises(EditorError):
        replace_text(pdf_bytes, req)

@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotated_page_replacement(pdf_bytes, font_path, rotation):
    with pymupdf.open(stream=pdf_bytes) as doc:
        doc[0].set_rotation(rotation)
        data = doc.tobytes()
    out = replace_text(data, request_for(data, font_path))
    with pymupdf.open(stream=out) as doc:
        assert doc[0].rotation == rotation
        assert "檢測合格" in doc[0].get_text()

def test_vertical_text_is_read_only(font_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((100, 250), "VERTICAL", rotate=90)
    runs = extract_runs(doc.tobytes(), 0)
    assert runs and not runs[0].editable

