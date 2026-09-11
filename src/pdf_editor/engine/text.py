import hashlib
import math
import pymupdf
from pdf_editor.model import TextRun, TextReplacement
from pdf_editor.errors import EditorError
from pdf_editor.engine.fonts import checked_font

def extract_runs(pdf: bytes, page: int) -> list[TextRun]:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        result = []
        for bi, block in enumerate(doc[page].get_text("dict")["blocks"]):
            for li, line in enumerate(block.get("lines", [])):
                for si, span in enumerate(line["spans"]):
                    text = span["text"]
                    if not text.strip():
                        continue
                    editable = line["dir"] == (1.0, 0.0) and "\ufffd" not in text
                    color = span["color"]
                    result.append(TextRun(f"{bi}:{li}:{si}", text, tuple(span["bbox"]),
                        span["font"], span["size"], editable,
                        None if editable else "此區段方向或文字編碼不支援修改。",
                        (((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255)))
        return result

def replace_text(pdf: bytes, request: TextReplacement) -> bytes:
    if hashlib.sha256(pdf).hexdigest() != request.document_hash:
        raise EditorError("STALE", "文件已變更，請重新選取文字。")
    if not math.isfinite(request.size) or not 1 <= request.size <= 300:
        raise EditorError("SIZE", "字級必須介於 1 與 300。")
    if len(request.text) > 10000:
        raise EditorError("TEXT_OVERFLOW", "文字過長，請縮短內容。")
    if not all(math.isfinite(v) for v in request.rect + request.color):
        raise EditorError("GEOMETRY", "位置或色彩數值無效。")
    runs = extract_runs(pdf, request.page)
    run = next((r for r in runs if r.id == request.run_id), None)
    if not run or not run.editable:
        raise EditorError("UNSUPPORTED_TEXT", "此文字無法安全修改。")
    rect = pymupdf.Rect(request.rect)
    old_rect = pymupdf.Rect(run.rect)
    if rect.is_empty:
        raise EditorError("GEOMETRY", "文字框不可為空。")
    for other in runs:
        if other.id != run.id and (old_rect.intersects(other.rect) or rect.intersects(other.rect)):
            raise EditorError("OVERLAP", "文字框與其他文字重疊，請縮小範圍。")
    checked_font(request.font_path, request.text)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[request.page]
        if page.first_annot:
            if any(a.type[0] == pymupdf.PDF_ANNOT_REDACT for a in page.annots()):
                raise EditorError("REDACTIONS", "此頁有尚未套用的遮蔽標記，無法修改。")
        bounds = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
        if not bounds.contains(rect):
            raise EditorError("GEOMETRY", "文字框必須位於頁面內。")
        page.insert_font(fontname="replacement", fontfile=request.font_path)
        shape = page.new_shape()
        remaining = shape.insert_textbox(rect, request.text, fontname="replacement",
            fontsize=request.size, color=request.color)
        if remaining < 0:
            raise EditorError("TEXT_OVERFLOW", "文字超出範圍，請擴大文字框或減小字級。")
        page.add_redact_annot(old_rect, fill=False, cross_out=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        # 移除後重新建立形狀，避免使用已失效的頁面資源。
        page.insert_font(fontname="replacement", fontfile=request.font_path)
        shape = page.new_shape()
        shape.insert_textbox(rect, request.text, fontname="replacement",
            fontsize=request.size, color=request.color)
        shape.commit()
        doc.subset_fonts()
        return doc.tobytes(garbage=4, deflate=True)

