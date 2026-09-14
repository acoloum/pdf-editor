import pymupdf
import pytest
from PIL import Image

from pdf_editor.engine.inspection import inspect_pdf, unlock_pdf
from pdf_editor.errors import EditorError
from pdf_editor.model import Overlay
from pdf_editor.persistent_overlays import embed_workspace

def test_form_is_read_only(pdf_bytes):
    with pymupdf.open(stream=pdf_bytes) as doc:
        widget = pymupdf.Widget()
        widget.field_name = "name"
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.rect = pymupdf.Rect(20, 300, 200, 340)
        doc[0].add_widget(widget)
        access = inspect_pdf(doc.tobytes(), None)
    assert not access.can_edit and not access.can_reorganize

def test_attachment_blocks_reorganization(pdf_bytes):
    with pymupdf.open(stream=pdf_bytes) as doc:
        doc.embfile_add("note.txt", b"note")
        access = inspect_pdf(doc.tobytes(), None)
    assert not access.can_reorganize


def test_internal_stamp_workspace_does_not_block_reorganization(pdf_bytes, tmp_path):
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 10), (0, 40, 255, 255)).save(stamp)
    workspace = embed_workspace(
        pdf_bytes,
        (Overlay("stamp", 0, str(stamp), (100, 100, 120, 110)),),
    )

    access = inspect_pdf(workspace)

    assert access.can_reorganize


def test_unreferenced_internal_named_attachment_blocks_reorganization(
        pdf_bytes, tmp_path):
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 10), (0, 40, 255, 255)).save(stamp)
    workspace = embed_workspace(
        pdf_bytes,
        (Overlay("stamp", 0, str(stamp), (100, 100, 120, 110)),),
    )
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        document.embfile_add("moye-pdf/assets/manual.pdf", b"manual")
        contaminated = document.tobytes(garbage=4, deflate=True)

    access = inspect_pdf(contaminated)

    assert not access.can_reorganize

def test_password_and_restricted_permissions(pdf_bytes):
    with pymupdf.open(stream=pdf_bytes) as doc:
        encrypted = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner", user_pw="user", permissions=pymupdf.PDF_PERM_PRINT)
    with pytest.raises(EditorError):
        unlock_pdf(encrypted, "wrong")
    data, access = unlock_pdf(encrypted, "user")
    assert not access.can_edit and not access.can_reorganize
    with pymupdf.open(stream=data) as doc:
        assert doc.page_count == 1
