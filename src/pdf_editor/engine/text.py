import hashlib
import math
import pymupdf
from pdf_editor.model import TextRun, TextReplacement, TextInsertion
from pdf_editor.errors import EditorError
from pdf_editor.engine.fonts import checked_font

ALIGNMENTS = {"left": pymupdf.TEXT_ALIGN_LEFT, "hcenter": pymupdf.TEXT_ALIGN_CENTER,
    "center": pymupdf.TEXT_ALIGN_CENTER}

def find_table_cell(pdf: bytes, page: int, rect) -> tuple[float, float, float, float] | None:
    """找出包含文字中心點的表格儲存格，找不到時回傳 None。"""
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        box = pymupdf.Rect(rect)
        point = pymupdf.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
        try:
            tables = doc[page].find_tables().tables
        except (AttributeError, RuntimeError):
            return None
        candidates = []
        for table in tables:
            for cell in table.cells:
                if cell is None:
                    continue
                candidate = pymupdf.Rect(cell)
                if candidate.contains(point):
                    candidates.append(candidate)
        if not candidates:
            return None
        cell = min(candidates, key=lambda item: item.width * item.height)
        inset = min(2.0, cell.width / 10, cell.height / 10)
        return (cell.x0 + inset, cell.y0 + inset, cell.x1 - inset, cell.y1 - inset)

def _vertical_centered_rect(rect, text, size):
    box = pymupdf.Rect(rect)
    lines = max(1, text.count("\n") + 1)
    content_height = min(box.height, lines * size * 2.0)
    top = box.y0 + max(0, (box.height - content_height) / 2)
    return pymupdf.Rect(box.x0, top, box.x1, top + content_height)

def _new_text_shape(page, rect, text, font_path, font, size, color, alignment, fontname):
    """建立文字形狀；單行文字以基準線寫入，避免窄表格列被誤判為溢位。"""
    box = pymupdf.Rect(rect)
    page.insert_font(fontname=fontname, fontfile=font_path)
    shape = page.new_shape()
    if "\n" not in text:
        width = font.text_length(text, fontsize=size)
        if width > box.width + 0.01 or size > box.height + 0.01:
            raise EditorError("TEXT_OVERFLOW", "文字超出範圍，請擴大文字框或減小字級。")
        x = box.x0 if alignment == "left" else box.x0 + (box.width - width) / 2
        if alignment == "center":
            baseline = box.y0 + (box.height - (font.ascender - font.descender) * size) / 2 \
                + font.ascender * size
        else:
            baseline = box.y0 + font.ascender * size
        shape.insert_text((x, baseline), text, fontname=fontname, fontsize=size, color=color)
        return shape
    placement = _vertical_centered_rect(box, text, size) if alignment == "center" else box
    remaining = shape.insert_textbox(placement, text, fontname=fontname,
        fontsize=size, color=color, align=ALIGNMENTS[alignment])
    if remaining < 0:
        raise EditorError("TEXT_OVERFLOW", "文字超出範圍，請擴大文字框或減小字級。")
    return shape

def insert_text(pdf: bytes, request: TextInsertion) -> bytes:
    if hashlib.sha256(pdf).hexdigest() != request.document_hash:
        raise EditorError("STALE", "文件已變更，請重新指定文字位置。")
    if not request.text.strip():
        raise EditorError("TEXT_EMPTY", "請輸入要新增的文字。")
    if not math.isfinite(request.size) or not 1 <= request.size <= 300:
        raise EditorError("SIZE", "字級必須介於 1 與 300。")
    if len(request.text) > 10000:
        raise EditorError("TEXT_OVERFLOW", "文字過長，請縮短內容。")
    if request.alignment not in ALIGNMENTS:
        raise EditorError("ALIGNMENT", "文字對齊方式無效。")
    if not all(math.isfinite(v) for v in request.rect + request.color):
        raise EditorError("GEOMETRY", "位置或色彩數值無效。")
    rect = pymupdf.Rect(request.rect)
    if rect.is_empty:
        raise EditorError("GEOMETRY", "文字框不可為空。")
    for run in extract_runs(pdf, request.page):
        if rect.intersects(run.rect):
            raise EditorError("OVERLAP", "文字框與其他文字重疊，請縮小範圍。")
    font = checked_font(request.font_path, request.text)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[request.page]
        bounds = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
        if not bounds.contains(rect):
            raise EditorError("GEOMETRY", "文字框必須位於頁面內。")
        shape = _new_text_shape(page, rect, request.text, request.font_path, font,
            request.size, request.color, request.alignment, "insertion")
        shape.commit()
        doc.subset_fonts(fallback=True)
        return doc.tobytes(garbage=4, deflate=True)

def extract_runs(pdf: bytes, page: int) -> list[TextRun]:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        result = []
        for bi, block in enumerate(doc[page].get_text("dict")["blocks"]):
            for li, line in enumerate(block.get("lines", [])):
                spans = line["spans"]
                text = "".join(span["text"] for span in spans)
                if not text.strip():
                    continue
                rect = pymupdf.Rect(spans[0]["bbox"])
                for span in spans[1:]:
                    rect |= pymupdf.Rect(span["bbox"])
                editable = line["dir"] == (1.0, 0.0) and "\ufffd" not in text
                color = spans[0]["color"]
                result.append(TextRun(f"{bi}:{li}", text, tuple(rect),
                    spans[0]["font"], max(span["size"] for span in spans), editable,
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
    if request.alignment not in ALIGNMENTS:
        raise EditorError("ALIGNMENT", "文字對齊方式無效。")
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
    font = checked_font(request.font_path, request.text) if request.text else None
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[request.page]
        if page.first_annot:
            if any(a.type[0] == pymupdf.PDF_ANNOT_REDACT for a in page.annots()):
                raise EditorError("REDACTIONS", "此頁有尚未套用的遮蔽標記，無法修改。")
        bounds = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
        if not bounds.contains(rect):
            raise EditorError("GEOMETRY", "文字框必須位於頁面內。")
        if request.text:
            _new_text_shape(page, rect, request.text, request.font_path, font,
                request.size, request.color, request.alignment, "replacement")
        page.add_redact_annot(old_rect, fill=False, cross_out=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        if not request.text:
            return doc.tobytes(garbage=4, deflate=True)
        # 移除後重新建立形狀，避免使用已失效的頁面資源。
        shape = _new_text_shape(page, rect, request.text, request.font_path, font,
            request.size, request.color, request.alignment, "replacement")
        shape.commit()
        doc.subset_fonts(fallback=True)
        return doc.tobytes(garbage=4, deflate=True)
