import pytest
import pymupdf
from pdf_editor import pages
from pdf_editor.pages import parse_group, fixed_groups, merge_pages, split_pages
from pdf_editor.document.save import publish_batch
from pdf_editor.errors import EditorError, BatchPublishError

def test_parser():
    assert parse_group("1,3,5-7", 10) == (0, 2, 4, 5, 6)
    assert fixed_groups(5, 2) == ((0, 1), (2, 3), (4,))

@pytest.mark.parametrize("text", ["", "0", "3-1", "11", "1,1", "1,,2"])
def test_invalid(text):
    with pytest.raises(EditorError):
        parse_group(text, 10)

def test_merge_split_order(pdf_bytes):
    with pymupdf.open(stream=pdf_bytes) as doc:
        doc.new_page(width=250, height=300).insert_text((30, 30), "SECOND")
        data = doc.tobytes()
    out = merge_pages((data,), ((0, 1), (0, 0)))
    parts = split_pages(out, ((0,), (1,)))
    with pymupdf.open(stream=parts[0]) as first, pymupdf.open(stream=parts[1]) as second:
        assert first[0].rect.width == 250
        assert "SECOND" in first[0].get_text()
        assert "品質" in second[0].get_text()

def test_batch_failure_reports_completed(tmp_path, pdf_bytes, monkeypatch):
    import os
    real = os.rename
    targets = (tmp_path / "1.pdf", tmp_path / "2.pdf")
    def fail_second(a, b):
        if b == targets[1]:
            raise PermissionError()
        real(a, b)
    monkeypatch.setattr(os, "rename", fail_second)
    with pytest.raises(BatchPublishError) as err:
        publish_batch((pdf_bytes, pdf_bytes), targets)
    assert err.value.completed == (targets[0],)
    assert targets[0].exists() and not targets[1].exists()
    assert not list(tmp_path.glob(".pdf-editor-*"))


def _three_page_pdf():
    doc = pymupdf.open()
    for index in range(3):
        page = doc.new_page(width=300, height=200)
        page.insert_text((40, 80), f"PAGE {index + 1}", fontsize=18)
    data = doc.tobytes()
    doc.close()
    return data


def _page_texts(pdf):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return [page.get_text().strip() for page in doc]


def test_move_page_changes_order():
    moved = pages.move_page(_three_page_pdf(), 0, 1)

    assert _page_texts(moved) == ["PAGE 2", "PAGE 1", "PAGE 3"]


def test_rotate_page_changes_rotation_without_losing_text():
    rotated = pages.rotate_page(_three_page_pdf(), 1, 90)

    with pymupdf.open(stream=rotated, filetype="pdf") as doc:
        assert doc[1].rotation == 90
        assert doc[1].get_text().strip() == "PAGE 2"


def test_delete_page_removes_only_selected_page():
    deleted = pages.delete_page(_three_page_pdf(), 1)

    assert _page_texts(deleted) == ["PAGE 1", "PAGE 3"]


def test_delete_page_rejects_last_remaining_page(pdf_bytes):
    with pytest.raises(EditorError) as error:
        pages.delete_page(pdf_bytes, 0)

    assert error.value.code == "LAST_PAGE"


def test_move_pages_moves_contiguous_selection_and_preserves_order():
    moved = pages.move_pages(_three_page_pdf(), (1, 2), -1)

    assert _page_texts(moved) == ["PAGE 2", "PAGE 3", "PAGE 1"]


def test_move_pages_moves_non_contiguous_selection_down():
    moved = pages.move_pages(_three_page_pdf(), (0, 2), 1)

    assert _page_texts(moved) == ["PAGE 2", "PAGE 1", "PAGE 3"]


def test_rotate_pages_rotates_every_selected_page():
    rotated = pages.rotate_pages(_three_page_pdf(), (0, 2), 90)

    with pymupdf.open(stream=rotated, filetype="pdf") as doc:
        assert [page.rotation for page in doc] == [90, 0, 90]


def test_delete_pages_removes_every_selected_page():
    deleted = pages.delete_pages(_three_page_pdf(), (0, 2))

    assert _page_texts(deleted) == ["PAGE 2"]


def test_delete_pages_rejects_removing_every_page():
    with pytest.raises(EditorError) as error:
        pages.delete_pages(_three_page_pdf(), (0, 1, 2))

    assert error.value.code == "LAST_PAGE"


def test_page_order_after_drop_moves_non_contiguous_pages_as_group():
    order,moved=pages.page_order_after_drop(3,(0,2),3)

    assert order==(1,0,2)
    assert moved==(1,2)


def test_move_pages_to_moves_contiguous_pages_to_start():
    moved=pages.move_pages_to(_three_page_pdf(),(1,2),0)

    assert _page_texts(moved)==["PAGE 2","PAGE 3","PAGE 1"]


def test_page_order_after_drop_is_unchanged_inside_selected_group():
    order,moved=pages.page_order_after_drop(3,(1,2),2)

    assert order==(0,1,2)
    assert moved==(1,2)


def test_duplicate_pages_inserts_copies_after_last_selected_page():
    duplicated=pages.duplicate_pages(_three_page_pdf(),(0,2))

    assert _page_texts(duplicated)==[
        "PAGE 1","PAGE 2","PAGE 3","PAGE 1","PAGE 3"]


def test_duplicate_pages_rejects_invalid_selection():
    with pytest.raises(EditorError) as error:
        pages.duplicate_pages(_three_page_pdf(),(0,3))

    assert error.value.code=="RANGE"
