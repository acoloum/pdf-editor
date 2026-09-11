import pytest
from pdf_editor.document.session import DocumentSession
from pdf_editor.document.save import save_as
from pdf_editor.errors import EditorError

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

