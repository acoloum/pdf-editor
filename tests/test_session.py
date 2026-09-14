from dataclasses import replace

import pytest
from PIL import Image
from pdf_editor.document.session import DocumentSession
from pdf_editor.document.save import save_as
from pdf_editor.errors import EditorError
from pdf_editor.model import Overlay

def test_save_undo_dirty(source_path, tmp_path, pdf_bytes):
    with DocumentSession.open(source_path) as session:
        import pymupdf
        with pymupdf.open(stream=pdf_bytes) as doc:
            doc[0].insert_text((50, 320), "CHANGED")
            changed = doc.tobytes()
        session.apply_pdf(changed)
        save_as(session, tmp_path / "saved.pdf")
        assert not session.dirty
        session.undo()
        assert session.dirty and session.pdf == pdf_bytes
        session.redo()
        assert not session.dirty
        session.undo()
        session.apply_pdf(changed)
        assert not session.can_redo

def test_original_and_existing_output_protected(source_path, tmp_path):
    before = source_path.read_bytes()
    with DocumentSession.open(source_path) as session:
        with pytest.raises(EditorError):
            save_as(session, source_path, overwrite=True)
        target = tmp_path / "other.pdf"
        target.write_bytes(b"existing")
        with pytest.raises(EditorError):
            save_as(session, target)
        assert target.read_bytes() == b"existing"
    assert source_path.read_bytes() == before

def test_failed_save_preserves_dirty(source_path, tmp_path):
    with DocumentSession.open(source_path) as session:
        with pytest.raises(EditorError):
            save_as(session, tmp_path / "missing" / "out.pdf")
        assert session.pdf == source_path.read_bytes()

def test_history_limit(source_path):
    import pymupdf
    with DocumentSession.open(source_path) as session:
        for i in range(33):
            with pymupdf.open(stream=session.pdf) as doc:
                doc.set_metadata({"title": str(i)})
                session.apply_pdf(doc.tobytes())
        count = 0
        while session.can_undo:
            session.undo()
            count += 1
        assert count == 30


def test_apply_state_can_flatten_overlays_and_undo(source_path):
    with DocumentSession.open(source_path) as session:
        layer = Overlay("stamp", 0, "stamp.png", (10, 10, 30, 30), 0)
        session.set_overlays((layer,))
        before_pdf = session.pdf

        session.apply_state(before_pdf, ())

        assert session.overlays == ()
        session.undo()
        assert session.overlays == (layer,)


def test_saved_stamp_reopens_as_editable_overlay(source_path, tmp_path):
    """遺失工作層還原時，重開檔案後無法再移動已存的圖章。"""
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 20), (0, 0, 255, 180)).save(stamp)
    target = tmp_path / "可重編.pdf"

    with DocumentSession.open(source_path) as session:
        session.set_overlays((Overlay("章", 0, str(stamp), (200, 200, 240, 240)),))
        save_as(session, target)

    with DocumentSession.open(target) as reopened:
        original = reopened.overlays[0]
        reopened.set_overlays((replace(original, rect=(260, 200, 300, 240)),))
        assert reopened.overlays[0].rect == (260, 200, 300, 240)
