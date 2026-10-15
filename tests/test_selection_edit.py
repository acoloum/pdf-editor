import hashlib

import pymupdf
import pytest

from pdf_editor.annotations import add_text_note, list_annotations, mark_text
from pdf_editor.engine.text import extract_runs
from pdf_editor.errors import EditorError
from pdf_editor.model import SelectionEdit
from pdf_editor.selection_edit import edit_selection


def _request(pdf, runs=(), annotations=(), offset=None, page=0):
    return SelectionEdit(hashlib.sha256(pdf).hexdigest(), page,
        tuple(run.id for run in runs), tuple(item.xref for item in annotations), offset)


def _texts(pdf):
    return [run.text for run in extract_runs(pdf, 0)]


def test_delete_removes_selected_runs_and_annotations_together(pdf_bytes, tmp_path):
    runs = [run for run in extract_runs(pdf_bytes, 0) if "品質" in run.text]
    marked = mark_text(pdf_bytes, 0, runs[1].rect, "highlight")
    marked = add_text_note(marked, 0, (300, 330), "備註")
    annotations = list_annotations(marked, 0)
    runs = [run for run in extract_runs(marked, 0) if "品質" in run.text]

    output = edit_selection(marked, _request(marked, runs, annotations), str(tmp_path))

    assert _texts(output) == ["KEEP ME"]
    assert list_annotations(output, 0) == ()


def test_move_shifts_runs_and_annotations_by_same_offset(pdf_bytes, tmp_path):
    runs = [run for run in extract_runs(pdf_bytes, 0) if run.rect[1] > 150]
    marked = mark_text(pdf_bytes, 0, runs[0].rect, "underline")
    marked = add_text_note(marked, 0, (300, 300), "備註")
    before = list_annotations(marked, 0)
    runs = [run for run in extract_runs(marked, 0) if run.rect[1] > 150]

    output = edit_selection(marked, _request(marked, runs, before, (20, 30)), str(tmp_path))

    result = extract_runs(output, 0)
    for run in runs:
        assert any(item.text == run.text
            and item.rect[0] == pytest.approx(run.rect[0] + 20, abs=1.5)
            and item.rect[1] == pytest.approx(run.rect[1] + 30, abs=1.5) for item in result)
    # 輸出時會重新編排物件編號，改以註解類型比對。
    after = {item.kind: item.rect for item in list_annotations(output, 0)}
    for item in before:
        assert after[item.kind][0] == pytest.approx(item.rect[0] + 20, abs=0.5)
        assert after[item.kind][1] == pytest.approx(item.rect[1] + 30, abs=0.5)
    # 未選取的文字維持原位。
    assert any("品質" in item.text and item.rect[1] < 100 for item in result)


def test_move_keeps_annotation_offset_on_rotated_page(tmp_path):
    with pymupdf.open() as doc:
        page = doc.new_page(width=500, height=400)
        page.insert_text((60, 80), "ROTATED", fontsize=16)
        page.set_rotation(90)
        pdf = doc.tobytes()
    run = extract_runs(pdf, 0)[0]
    pdf = mark_text(pdf, 0, run.rect, "highlight")
    before = list_annotations(pdf, 0)
    run = extract_runs(pdf, 0)[0]

    output = edit_selection(pdf, _request(pdf, [run], before, (15, 25)), str(tmp_path))

    after = list_annotations(output, 0)[0]
    assert after.rect[0] == pytest.approx(before[0].rect[0] + 15, abs=0.5)
    assert after.rect[1] == pytest.approx(before[0].rect[1] + 25, abs=0.5)
    moved = extract_runs(output, 0)[0]
    assert moved.rect[0] == pytest.approx(run.rect[0] + 15, abs=1.5)


def test_selected_run_overlapping_unselected_text_is_rejected(tmp_path):
    with pymupdf.open() as doc:
        page = doc.new_page(width=500, height=400)
        page.insert_text((40, 80), "FIRST LINE", fontsize=16)
        page.insert_text((60, 86), "SECOND", fontsize=16)
        pdf = doc.tobytes()
    first = next(run for run in extract_runs(pdf, 0) if run.text == "FIRST LINE")

    with pytest.raises(EditorError) as error:
        edit_selection(pdf, _request(pdf, [first]), str(tmp_path))

    assert error.value.code == "OVERLAP"


def test_move_outside_page_is_rejected(pdf_bytes, tmp_path):
    run = next(run for run in extract_runs(pdf_bytes, 0) if run.text == "KEEP ME")

    with pytest.raises(EditorError) as error:
        edit_selection(pdf_bytes, _request(pdf_bytes, [run], offset=(450, 0)), str(tmp_path))

    assert error.value.code == "GEOMETRY"


def test_moved_note_must_stay_on_page(pdf_bytes, tmp_path):
    pdf = add_text_note(pdf_bytes, 0, (450, 350), "備註")

    with pytest.raises(EditorError) as error:
        edit_selection(pdf, _request(pdf, annotations=list_annotations(pdf, 0),
            offset=(100, 0)), str(tmp_path))

    assert error.value.code == "GEOMETRY"


def test_stale_document_is_rejected(pdf_bytes, tmp_path):
    run = extract_runs(pdf_bytes, 0)[0]
    request = SelectionEdit("0" * 64, 0, (run.id,), (), None)

    with pytest.raises(EditorError) as error:
        edit_selection(pdf_bytes, request, str(tmp_path))

    assert error.value.code == "STALE"


def test_missing_run_is_reported_as_stale(pdf_bytes, tmp_path):
    request = SelectionEdit(hashlib.sha256(pdf_bytes).hexdigest(), 0, ("99:0",), (), None)

    with pytest.raises(EditorError) as error:
        edit_selection(pdf_bytes, request, str(tmp_path))

    assert error.value.code == "STALE"


def test_runs_sharing_a_font_embed_it_once_and_still_render(font_path, tmp_path):
    with pymupdf.open() as doc:
        page = doc.new_page(width=500, height=400)
        page.insert_font(fontname="noto", fontfile=font_path)
        page.insert_text((40, 80), "品質檢驗", fontname="noto", fontsize=16)
        page.insert_text((40, 160), "材料規格", fontname="noto", fontsize=16)
        doc.subset_fonts(fallback=True)
        pdf = doc.tobytes(garbage=4, deflate=True)
    runs = extract_runs(pdf, 0)
    assert len(runs) == 2

    output = edit_selection(pdf, _request(pdf, runs, offset=(0, 20)), str(tmp_path))

    with pymupdf.open(stream=output, filetype="pdf") as doc:
        page = doc[0]
        moved_fonts = {font[4] for font in page.get_fonts() if font[4].startswith("moved")}
        # 兩段文字用同一字型：只子集化、嵌入一次。
        assert len(moved_fonts) == 1
        for run in runs:
            clip = pymupdf.Rect(run.rect) + (0, 20, 0, 20)
            pixmap = page.get_pixmap(clip=clip, dpi=72)
            dark = sum(1 for index in range(0, len(pixmap.samples), pixmap.n)
                if pixmap.samples[index] < 100)
            assert dark > 20


# ── 表格格線等向量圖形 ──

from pdf_editor.selection_edit import selectable_drawings


def _table_pdf(rotation=0):
    """2×1 表格：白色底色、灰色表頭底、細黑格線（填色矩形與描線各一種）與格內文字。"""
    with pymupdf.open() as doc:
        page = doc.new_page(width=500, height=400)
        page.draw_rect((40, 40, 300, 100), color=None, fill=(1, 1, 1))
        page.draw_rect((40, 40, 300, 60), color=None, fill=(0.8, 0.8, 0.8))
        for y in (40, 70, 100):
            page.draw_rect((40, y, 300, y + 0.5), color=None, fill=(0, 0, 0))
        page.draw_line((40, 40), (40, 100), color=(0, 0, 0), width=0.8)
        page.draw_line((300, 40), (300, 100), color=(0, 0, 0), width=0.8)
        page.insert_text((50, 90), "CELL", fontsize=12)
        page.set_rotation(rotation)
        return doc.tobytes()


def _drawings(pdf):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return [(d["type"], tuple(round(v, 1) for v in d["rect"]), d.get("fill"))
            for d in doc[0].get_drawings()]


def _grid(pdf):
    """格線（黑色細線）外框。"""
    return sorted(rect for kind, rect, fill in _drawings(pdf)
        if fill == (0.0, 0.0, 0.0) or kind == "s")


def test_selectable_drawings_skip_white_background():
    pdf = _table_pdf()
    rects = selectable_drawings(pdf, 0)
    # 灰色表頭底、三條橫線與兩條直線；白色底色不列入。
    assert len(rects) == 6
    assert not any(abs(r[3] - 100) < 0.1 and abs(r[1] - 40) < 0.1 and r[2] - r[0] > 200
        for r in rects if r[3] - r[1] > 50)


def test_move_takes_grid_lines_and_text_together(tmp_path):
    pdf = _table_pdf()
    rects = selectable_drawings(pdf, 0)
    runs = extract_runs(pdf, 0)
    request = SelectionEdit(hashlib.sha256(pdf).hexdigest(), 0, tuple(r.id for r in runs), (),
        (10, 150), rects)
    before = _grid(pdf)

    output = edit_selection(pdf, request, str(tmp_path))

    expected = sorted((round(r[0] + 10, 1), round(r[1] + 150, 1), round(r[2] + 10, 1),
        round(r[3] + 150, 1)) for r in before)
    assert _grid(output) == pytest.approx(expected, abs=0.2)
    # 白色底色留在原處；灰色表頭底跟著移動。
    kinds = _drawings(output)
    assert any(fill == (1.0, 1.0, 1.0) and rect[1] == pytest.approx(40, abs=0.2)
        for _kind, rect, fill in kinds)
    assert any(fill and fill[0] == pytest.approx(0.8) and rect[1] == pytest.approx(190, abs=0.2)
        for _kind, rect, fill in kinds)
    assert extract_runs(output, 0)[0].rect[1] == pytest.approx(runs[0].rect[1] + 150, abs=1.5)


def test_delete_removes_selected_grid_lines(tmp_path):
    pdf = _table_pdf()
    rects = selectable_drawings(pdf, 0)
    request = SelectionEdit(hashlib.sha256(pdf).hexdigest(), 0, (), (), None, rects)

    output = edit_selection(pdf, request, str(tmp_path))

    assert _grid(output) == []
    assert [fill for _kind, _rect, fill in _drawings(output)] == [(1.0, 1.0, 1.0)]
    assert [run.text for run in extract_runs(output, 0)] == ["CELL"]


def test_unselected_fill_touched_by_removal_is_put_back(tmp_path):
    pdf = _table_pdf()
    # 只選中間那條橫線：它壓在白色底色上，移除時底色會被連帶移除，必須放回原位。
    middle = next(r for r in selectable_drawings(pdf, 0)
        if abs(r[1] - 70) < 0.1 and r[3] - r[1] < 1)
    request = SelectionEdit(hashlib.sha256(pdf).hexdigest(), 0, (), (), (0, 200), (middle,))

    output = edit_selection(pdf, request, str(tmp_path))

    drawings = _drawings(output)
    assert any(fill == (1.0, 1.0, 1.0) and rect == pytest.approx((40, 40, 300, 100), abs=0.2)
        for _kind, rect, fill in drawings)
    assert len(drawings) == len(_drawings(pdf))
    assert any(rect[1] == pytest.approx(270, abs=0.2) for _kind, rect, _fill in drawings)
    assert not any(rect[1] == pytest.approx(70, abs=0.1) and rect[3] - rect[1] < 1
        for _kind, rect, _fill in drawings)


def test_grid_moves_by_page_offset_on_rotated_page(tmp_path):
    pdf = _table_pdf(rotation=90)
    rects = selectable_drawings(pdf, 0)
    before = _grid(pdf)
    request = SelectionEdit(hashlib.sha256(pdf).hexdigest(), 0, (), (), (20, 30), rects)

    output = edit_selection(pdf, request, str(tmp_path))

    expected = sorted((round(r[0] + 20, 1), round(r[1] + 30, 1), round(r[2] + 20, 1),
        round(r[3] + 30, 1)) for r in before)
    assert _grid(output) == pytest.approx(expected, abs=0.2)


def test_grid_moved_off_page_is_rejected(tmp_path):
    pdf = _table_pdf()
    rects = selectable_drawings(pdf, 0)
    request = SelectionEdit(hashlib.sha256(pdf).hexdigest(), 0, (), (), (300, 0), rects)

    with pytest.raises(EditorError) as error:
        edit_selection(pdf, request, str(tmp_path))

    assert error.value.code == "GEOMETRY"


def test_missing_drawing_is_reported_as_stale(tmp_path):
    pdf = _table_pdf()
    request = SelectionEdit(hashlib.sha256(pdf).hexdigest(), 0, (), (), (0, 10),
        ((1, 2, 3, 4),))

    with pytest.raises(EditorError) as error:
        edit_selection(pdf, request, str(tmp_path))

    assert error.value.code == "STALE"


def test_annotation_appearance_is_not_a_selectable_drawing(tmp_path):
    pdf = mark_text(_table_pdf(), 0, (50, 80, 90, 95), "highlight")
    rects = selectable_drawings(pdf, 0)
    assert len(rects) == 6
    request = SelectionEdit(hashlib.sha256(pdf).hexdigest(), 0, (), (), (0, 150), rects)

    output = edit_selection(pdf, request, str(tmp_path))

    # 註解留在原處，格線全部移走。
    assert list_annotations(output, 0)[0].rect[1] < 100
    assert all(rect[1] > 150 for rect in selectable_drawings(output, 0))
