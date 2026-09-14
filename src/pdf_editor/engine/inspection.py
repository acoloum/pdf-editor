import pymupdf
from pdf_editor.model import DocumentAccess
from pdf_editor.errors import EditorError
from pdf_editor.persistent_overlays import has_only_workspace_embedded_files

def _access(doc):
    root = doc.pdf_catalog()
    form_type, form_value = doc.xref_get_key(root, "AcroForm")
    special = form_type not in ("null", "none") or any(p.first_widget for p in doc)
    signed = doc.get_sigflags() > 0
    restricted = not (doc.permissions & pymupdf.PDF_PERM_MODIFY)
    attachments = (
        doc.embfile_count() > 0 and not has_only_workspace_embedded_files(doc)
    ) or any(
        a.type[0] == pymupdf.PDF_ANNOT_FILE_ATTACHMENT for p in doc for a in (p.annots() or []))
    reason = ("包含互動表單或簽章，第一版僅供閱讀。" if special or signed else
              "文件不允許修改。" if restricted else
              "包含附件，無法合併或拆分。" if attachments else None)
    return DocumentAccess(not (special or signed or restricted),
        not (special or signed or restricted or attachments), reason)

def unlock_pdf(pdf, password=None):
    try:
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            if doc.needs_pass and (not password or not doc.authenticate(password)):
                raise EditorError("PASSWORD", "請輸入正確的 PDF 密碼。")
            if not doc.page_count:
                raise EditorError("EMPTY", "文件沒有頁面。")
            access = _access(doc)
            data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE) if doc.is_encrypted or password else pdf
            return data, access
    except EditorError:
        raise
    except Exception as exc:
        raise EditorError("INVALID_PDF", "PDF 損毀或格式不受支援。") from exc

def inspect_pdf(pdf, password=None):
    return unlock_pdf(pdf, password)[1]

