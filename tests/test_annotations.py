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


def test_list_annotations_reports_native_annotation(pdf_bytes):
    marked=_module().mark_text(pdf_bytes,0,(40,60,210,90),"highlight")

    items=_module().list_annotations(marked,0)

    assert len(items)==1
    assert items[0].xref>0
    assert items[0].kind=="Highlight"
    assert items[0].color==pytest.approx((1.0,0.84,0.18),abs=0.01)


def test_delete_annotation_removes_selected_xref(pdf_bytes):
    marked=_module().mark_text(pdf_bytes,0,(40,60,210,90),"highlight")
    xref=_module().list_annotations(marked,0)[0].xref

    output=_module().delete_annotation(marked,0,xref)

    assert _module().list_annotations(output,0)==()


def test_change_highlight_color_updates_native_annotation(pdf_bytes):
    marked=_module().mark_text(pdf_bytes,0,(40,60,210,90),"highlight")
    xref=_module().list_annotations(marked,0)[0].xref

    output=_module().set_highlight_color(marked,0,xref,(0.30,0.78,0.48))

    assert _module().list_annotations(output,0)[0].color==pytest.approx(
        (0.30,0.78,0.48),abs=0.01)


def test_change_color_rejects_non_highlight_annotation(pdf_bytes):
    noted=_module().add_text_note(pdf_bytes,0,(250,300),"請重新確認")
    xref=_module().list_annotations(noted,0)[0].xref

    with pytest.raises(EditorError) as error:
        _module().set_highlight_color(noted,0,xref,(1.0,0.0,0.0))

    assert error.value.code=="ANNOTATION_TYPE"
