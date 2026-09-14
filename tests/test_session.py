from dataclasses import replace
import json

import pytest
import pymupdf
from PIL import Image
from pdf_editor.document.session import DocumentSession
from pdf_editor.document.save import save_as
from pdf_editor.errors import EditorError
from pdf_editor.model import Overlay
from pdf_editor.persistent_overlays import MANIFEST_NAME, embed_workspace

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


def test_workspace_with_extra_attachment_falls_back_and_round_trips_bytes(
        source_path, tmp_path):
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 20), (0, 0, 255, 180)).save(stamp)
    workspace = embed_workspace(
        source_path.read_bytes(),
        (Overlay("章", 0, str(stamp), (200, 200, 240, 240)),),
    )
    attachment = b"\x00customer-attachment\xff"
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        document.embfile_add("customer.bin", attachment, filename="customer.bin")
        modified = document.tobytes(garbage=4, deflate=True)
    modified_path = tmp_path / "外加附件.pdf"
    modified_path.write_bytes(modified)

    with DocumentSession.open(modified_path) as session:
        assert session.overlays == ()
        assert "靜態 PDF" in session.open_notice
        output = save_as(session, tmp_path / "附件往返.pdf")

    with pymupdf.open(output) as document:
        assert document.embfile_get("customer.bin") == attachment


@pytest.mark.parametrize("change", ["new_page", "same_page_text"])
def test_externally_changed_workspace_keeps_outer_pdf_as_static(
        source_path, tmp_path, change):
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 20), (0, 0, 255, 180)).save(stamp)
    workspace = embed_workspace(
        source_path.read_bytes(),
        (Overlay("章", 0, str(stamp), (200, 200, 240, 240)),),
    )
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        if change == "new_page":
            document.new_page(width=500, height=400).insert_text((40, 40), "EXTERNAL PAGE")
        else:
            document[0].insert_text((40, 330), "EXTERNAL TEXT")
        changed = document.tobytes(garbage=4, deflate=True)
    changed_path = tmp_path / f"外部變更-{change}.pdf"
    changed_path.write_bytes(changed)

    with DocumentSession.open(changed_path) as session:
        assert session.overlays == ()
        assert "靜態 PDF" in session.open_notice
        with pymupdf.open(stream=session.pdf, filetype="pdf") as document:
            if change == "new_page":
                assert document.page_count == 2
                assert "EXTERNAL PAGE" in document[1].get_text()
            else:
                assert "EXTERNAL TEXT" in document[0].get_text()


def test_workspace_with_pillow_expanded_rotation_falls_back_to_static(
        source_path, tmp_path):
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 10), (0, 0, 255, 180)).save(stamp)
    workspace = embed_workspace(
        source_path.read_bytes(),
        (Overlay("章", 0, str(stamp), (100, 100, 140, 120), 0),),
    )
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        original_manifest = document.embfile_get(MANIFEST_NAME)
        manifest = json.loads(original_manifest.decode("utf-8"))
        manifest["overlays"][0]["rect"] = [458.78, 100, 498.78, 120]
        manifest["overlays"][0]["angle"] = 45
        manifest_xref = next(
            xref for xref in range(1, document.xref_length())
            if document.xref_is_stream(xref)
            and document.xref_stream(xref) == original_manifest
        )
        document.update_stream(
            manifest_xref,
            json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        )
        invalid = document.tobytes(garbage=4, deflate=True)
    path = tmp_path / "旋轉外框損壞.pdf"
    path.write_bytes(invalid)

    with DocumentSession.open(path) as session:
        assert session.overlays == ()
        assert session.open_notice is not None
        assert "靜態 PDF" in session.open_notice


def test_externally_revised_annotation_falls_back_and_survives_resave(tmp_path):
    source = tmp_path / "原始註解.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=220)
        page.add_text_annot((60, 60), "ORIGINAL NOTE")
        source.write_bytes(document.tobytes(garbage=4, deflate=True))
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 20), (0, 40, 255, 180)).save(stamp)
    workspace = embed_workspace(
        source.read_bytes(),
        (Overlay("章", 0, str(stamp), (180, 140, 220, 180)),),
    )
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        page = document[0]
        annotation = next(page.annots())
        annotation.set_info(content="THIRD PARTY REVISED NOTE")
        modified = document.tobytes(garbage=4, deflate=True)
    modified_path = tmp_path / "第三方修改註解.pdf"
    modified_path.write_bytes(modified)

    with DocumentSession.open(modified_path) as session:
        assert session.overlays == ()
        assert session.open_notice is not None
        assert "靜態 PDF" in session.open_notice
        output = save_as(session, tmp_path / "註解往返.pdf")

    with pymupdf.open(output) as document:
        page = document[0]
        annotation = next(page.annots())
        assert annotation.info["content"] == "THIRD PARTY REVISED NOTE"
