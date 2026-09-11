import pytest
import pymupdf

from pdf_editor.engine.text import extract_runs
from pdf_editor.errors import EditorError


def _module():
    import importlib
    return importlib.import_module("pdf_editor.annotations")


def _annotation(pdf):
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        page=doc[0]
        items=list(page.annots() or [])
        assert len(items)==1
        item=items[0]
        return item.type[1],item.info.get("content","")


@pytest.mark.parametrize("kind,expected",[("highlight","Highlight"),("underline","Underline")])
def test_mark_text_creates_native_pdf_annotation(pdf_bytes,kind,expected):
    run=next(item for item in extract_runs(pdf_bytes,0) if "品質" in item.text)

    output=_module().mark_text(pdf_bytes,0,run.rect,kind)

    assert _annotation(output)[0]==expected


def test_add_text_note_preserves_content(pdf_bytes):
    output=_module().add_text_note(pdf_bytes,0,(250,300),"請重新確認尺寸")

    kind,content=_annotation(output)
    assert kind=="Text"
    assert content=="請重新確認尺寸"


def test_annotation_rejects_invalid_kind(pdf_bytes):
    with pytest.raises(EditorError) as error:
        _module().mark_text(pdf_bytes,0,(10,10,20,20),"invalid")

    assert error.value.code=="MARKUP"


def test_text_note_rejects_empty_content(pdf_bytes):
    with pytest.raises(EditorError) as error:
        _module().add_text_note(pdf_bytes,0,(250,300),"  ")

    assert error.value.code=="NOTE_EMPTY"
