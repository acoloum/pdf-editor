from dataclasses import replace
import hashlib
import pytest
import pymupdf
from pdf_editor.engine import text as text_engine
from pdf_editor.engine.text import extract_runs, replace_text
from pdf_editor import model as editor_model
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

def test_subset_output_is_compact(pdf_bytes, font_path):
    out = replace_text(pdf_bytes, request_for(pdf_bytes, font_path))
    assert len(out) < 500000

def test_second_edit_adds_new_chinese_glyphs(pdf_bytes, font_path):
    first = replace_text(pdf_bytes, request_for(pdf_bytes, font_path))
    run = next(r for r in extract_runs(first, 0) if '檢測' in r.text)
    req = replace(request_for(pdf_bytes, font_path), document_hash=hashlib.sha256(first).hexdigest(),
        run_id=run.id, text='完成覆核 新增字')
    second = replace_text(first, req)
    with pymupdf.open(stream=second) as doc:
        assert '完成覆核' in doc[0].get_text()

def test_replace_text_supports_horizontal_and_vertical_center(pdf_bytes, font_path):
    assert "alignment" in TextReplacement.__dataclass_fields__
    request = replace(request_for(pdf_bytes, font_path),
        text="置中", rect=(100, 300, 400, 380), alignment="center")
    out = replace_text(pdf_bytes, request)
    run = next(r for r in extract_runs(out, 0) if r.text == "置中")
    assert (run.rect[0] + run.rect[2]) / 2 == pytest.approx(250, abs=25)
    assert (run.rect[1] + run.rect[3]) / 2 == pytest.approx(340, abs=25)

def test_replace_text_rejects_unknown_alignment(pdf_bytes, font_path):
    assert "alignment" in TextReplacement.__dataclass_fields__
    request = replace(request_for(pdf_bytes, font_path), alignment="diagonal")
    with pytest.raises(EditorError, match="對齊"):
        replace_text(pdf_bytes, request)

def test_replace_text_supports_horizontal_center(pdf_bytes, font_path):
    request = replace(request_for(pdf_bytes, font_path),
        text="水平", rect=(100, 300, 400, 380), alignment="hcenter")
    out = replace_text(pdf_bytes, request)
    run = next(r for r in extract_runs(out, 0) if r.text == "水平")
    assert (run.rect[0] + run.rect[2]) / 2 == pytest.approx(250, abs=25)

def test_find_table_cell_returns_cell_containing_text(font_path):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.draw_rect((50, 50, 250, 100), width=1)
    page.draw_line((150, 50), (150, 100), width=1)
    page.insert_text((70, 80), "ABC", fontsize=12)
    data = doc.tobytes()
    doc.close()
    run = extract_runs(data, 0)[0]
    assert callable(getattr(text_engine, "find_table_cell", None))
    cell = text_engine.find_table_cell(data, 0, run.rect)
    assert cell == pytest.approx((52, 52, 148, 98))

def fragmented_table_pdf():
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.draw_rect((50, 50, 250, 100), width=1)
    page.draw_line((150, 50), (150, 100), width=1)
    page.insert_htmlbox((70, 62, 145, 92), "<b>ABC</b><i>DEF</i>")
    data = doc.tobytes()
    doc.close()
    return data

def test_extract_runs_combines_font_spans_on_same_line():
    runs = extract_runs(fragmented_table_pdf(), 0)
    assert len(runs) == 1
    assert runs[0].text == "ABCDEF"
    assert runs[0].rect[0] < 80
    assert runs[0].rect[2] > 100

def test_replace_fragmented_table_text_uses_whole_cell(font_path):
    data = fragmented_table_pdf()
    run = extract_runs(data, 0)[0]
    cell = text_engine.find_table_cell(data, 0, run.rect)
    request = TextReplacement(hashlib.sha256(data).hexdigest(), 0, run.id,
        "REPLACED", cell, font_path, 12, (0, 0, 0), "center")
    out = replace_text(data, request)
    with pymupdf.open(stream=out) as doc:
        assert "REPLACED" in doc[0].get_text()
        assert "ABC" not in doc[0].get_text()

def test_insert_text_adds_content_to_empty_table_cell(font_path):
    assert hasattr(editor_model, "TextInsertion")
    assert callable(getattr(text_engine, "insert_text", None))
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.draw_rect((50, 50, 250, 100), width=1)
    data = doc.tobytes()
    doc.close()
    request = editor_model.TextInsertion(hashlib.sha256(data).hexdigest(), 0,
        "重新新增", (52, 52, 248, 98), font_path, 12, (0, 0, 0), "center")
    out = text_engine.insert_text(data, request)
    with pymupdf.open(stream=out) as result:
        assert "重新新增" in result[0].get_text()

def test_insert_text_supports_multiple_chinese_insertions(font_path):
    doc = pymupdf.open()
    doc.new_page(width=300, height=200)
    data = doc.tobytes()
    doc.close()
    first = editor_model.TextInsertion(hashlib.sha256(data).hexdigest(), 0,
        "硬度", (40, 40, 140, 70), font_path, 12, (0, 0, 0), "left")
    once = text_engine.insert_text(data, first)
    second = editor_model.TextInsertion(hashlib.sha256(once).hexdigest(), 0,
        "新增測試", (40, 100, 180, 130), font_path, 12, (0, 0, 0), "left")

    twice = text_engine.insert_text(once, second)

    with pymupdf.open(stream=twice) as result:
        text = result[0].get_text()
        assert "硬度" in text
        assert "新增測試" in text

def test_insert_chinese_after_deleting_existing_text(pdf_bytes, font_path, tmp_path):
    run = next(r for r in extract_runs(pdf_bytes, 0) if "品質" in r.text)
    with pymupdf.open(stream=pdf_bytes) as source:
        xref = next(item[0] for item in source[0].get_fonts(full=True)
            if "Noto" in item[3])
        embedded = source.extract_font(xref)[3]
    embedded_path = tmp_path / "embedded.otf"
    embedded_path.write_bytes(embedded)
    deleted = replace_text(pdf_bytes, TextReplacement(hashlib.sha256(pdf_bytes).hexdigest(),
        0, run.id, "", run.rect, str(embedded_path), run.size, run.color))
    request = editor_model.TextInsertion(hashlib.sha256(deleted).hexdigest(), 0,
        "重新新增", (40, 40, 220, 110), font_path, 16, (0, 0, 0), "center")
    out = text_engine.insert_text(deleted, request)
    with pymupdf.open(stream=out) as result:
        assert "重新新增" in result[0].get_text()

def test_replace_text_centers_in_short_table_row(font_path):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.draw_rect((50, 50, 150, 63), width=0.5)
    page.insert_text((60, 60), "ABC", fontsize=8)
    data = doc.tobytes()
    doc.close()
    run = extract_runs(data, 0)[0]
    request = TextReplacement(hashlib.sha256(data).hexdigest(), 0, run.id,
        "硬度", (51.3, 51.3, 148.7, 61.7), font_path, 8, (0, 0, 0), "center")

    out = replace_text(data, request)

    with pymupdf.open(stream=out) as result:
        replacement = next(r for r in extract_runs(out, 0) if r.text == "硬度")
        assert result[0].rect.contains(replacement.rect)
        assert (replacement.rect[0] + replacement.rect[2]) / 2 == pytest.approx(100, abs=3)
        assert (replacement.rect[1] + replacement.rect[3]) / 2 == pytest.approx(56.5, abs=2)
