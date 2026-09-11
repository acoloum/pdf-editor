import pytest
import pymupdf
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

