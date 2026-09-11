import math

import pymupdf

from pdf_editor.errors import EditorError
from pdf_editor.model import AnnotationInfo


def _page(doc,page):
    if not 0<=page<doc.page_count:
        raise EditorError("RANGE","頁碼超出文件範圍。")
    return doc[page]


def _annotation(page,xref):
    if not isinstance(xref,int) or xref<=0:
        raise EditorError("ANNOTATION","註解識別碼無效。")
    for item in page.annots() or ():
        if item.xref==xref:
            return item
    raise EditorError("ANNOTATION","找不到選取的註解，請重新選取。")


def _checked_color(color):
    if len(color)!=3 or not all(math.isfinite(value) and 0<=value<=1 for value in color):
        raise EditorError("COLOR","註解顏色無效。")
    return tuple(float(value) for value in color)


def list_annotations(pdf,page):
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        selected=_page(doc,page)
        result=[]
        for item in selected.annots() or ():
            stroke=item.colors.get("stroke")
            color=tuple(float(value) for value in stroke) if stroke else None
            result.append(AnnotationInfo(item.xref,item.type[1],tuple(item.rect),color,
                item.info.get("content","") or ""))
        return tuple(result)


def delete_annotation(pdf,page,xref):
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        selected=_page(doc,page)
        item=_annotation(selected,xref)
        selected.delete_annot(item)
        return doc.tobytes(garbage=4,deflate=True)


def set_highlight_color(pdf,page,xref,color):
    checked=_checked_color(color)
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        selected=_page(doc,page)
        item=_annotation(selected,xref)
        if item.type[0]!=pymupdf.PDF_ANNOT_HIGHLIGHT:
            raise EditorError("ANNOTATION_TYPE","只有螢光標記可以變更顏色。")
        item.set_colors(stroke=checked)
        item.update()
        return doc.tobytes(garbage=4,deflate=True)


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
