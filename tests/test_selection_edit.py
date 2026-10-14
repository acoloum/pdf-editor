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
