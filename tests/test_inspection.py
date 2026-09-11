import pymupdf
import pytest
from pdf_editor.engine.inspection import inspect_pdf, unlock_pdf
from pdf_editor.errors import EditorError

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
