import math

import pymupdf

from pdf_editor.errors import EditorError


def _page(doc,page):
    if not 0<=page<doc.page_count:
        raise EditorError("RANGE","頁碼超出文件範圍。")
    return doc[page]


def mark_text(pdf,page,rect,kind):
    if kind not in ("highlight","underline"):
        raise EditorError("MARKUP","文字標記類型無效。")
    if not all(math.isfinite(value) for value in rect):
        raise EditorError("GEOMETRY","文字標記位置無效。")
    box=pymupdf.Rect(rect)
    if box.is_empty:
        raise EditorError("GEOMETRY","文字標記範圍不可為空。")
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        selected=_page(doc,page)
        if not selected.rect.contains(box):
            raise EditorError("GEOMETRY","文字標記必須位於頁面內。")
        if kind=="highlight":
            annotation=selected.add_highlight_annot(box)
            annotation.set_colors(stroke=(1.0,0.84,0.18))
        else:
            annotation=selected.add_underline_annot(box)
            annotation.set_colors(stroke=(0.1,0.42,0.82))
        annotation.set_info(title="墨頁 PDF")
        annotation.update()
        return doc.tobytes(garbage=4,deflate=True)


def add_text_note(pdf,page,point,text):
    if not text.strip():
        raise EditorError("NOTE_EMPTY","請輸入文字註解內容。")
    if len(text)>10000:
        raise EditorError("NOTE_LENGTH","文字註解過長，請縮短內容。")
    if len(point)!=2 or not all(math.isfinite(value) for value in point):
        raise EditorError("GEOMETRY","文字註解位置無效。")
    location=pymupdf.Point(*point)
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        selected=_page(doc,page)
        if not selected.rect.contains(location):
            raise EditorError("GEOMETRY","文字註解必須位於頁面內。")
        annotation=selected.add_text_annot(location,text,icon="Note")
        annotation.set_info(title="墨頁 PDF",content=text)
        annotation.update()
        return doc.tobytes(garbage=4,deflate=True)
