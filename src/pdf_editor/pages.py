import re
import pymupdf
from pdf_editor.engine.inspection import inspect_pdf
from pdf_editor.errors import EditorError
from pdf_editor.document.save import publish_batch

def parse_group(text, page_count):
    result = []
    for part in text.replace("，", ",").split(","):
        match = re.fullmatch(r"\s*(\d+)\s*(?:[-–]\s*(\d+)\s*)?", part)
        if not match:
            raise EditorError("RANGE", "頁碼格式不正確，例如：1,3,5-7。")
        start = int(match[1])
        end = int(match[2] or start)
        if not 1 <= start <= end <= page_count:
            raise EditorError("RANGE", "頁碼超出範圍或順序顛倒。")
        result.extend(range(start - 1, end))
    if len(result) != len(set(result)):
        raise EditorError("RANGE", "同一群組不能重複選取頁面。")
    return tuple(result)

def fixed_groups(page_count, size):
    if size <= 0 or page_count <= 0:
        raise EditorError("RANGE", "頁數必須大於零。")
    return tuple(tuple(range(i, min(i + size, page_count))) for i in range(0, page_count, size))

def merge_pages(sources, order):
    if not order:
        raise EditorError("EMPTY", "請至少選取一頁。")
    for source in sources:
        access = inspect_pdf(source)
        if not access.can_reorganize:
            raise EditorError("READ_ONLY", access.reason)
    docs = []
    try:
        docs = [pymupdf.open(stream=s, filetype="pdf") for s in sources]
        with pymupdf.open() as out:
            for si, pi in order:
                if not 0 <= si < len(docs) or not 0 <= pi < len(docs[si]):
                    raise EditorError("RANGE", "來源頁碼無效。")
                out.insert_pdf(docs[si], from_page=pi, to_page=pi, links=False, widgets=False)
            return out.tobytes(garbage=4, deflate=True)
    finally:
        for doc in docs:
            doc.close()

def split_pages(pdf, groups):
    if not groups or any(not g or len(set(g)) != len(g) for g in groups):
        raise EditorError("RANGE", "頁面群組為空或重複。")
    return tuple(merge_pages((pdf,), tuple((0, p) for p in group)) for group in groups)


def _validate_page(doc, page):
    if not 0 <= page < doc.page_count:
        raise EditorError("RANGE", "頁碼超出文件範圍。")


def _validate_pages(doc,pages):
    selected=tuple(pages)
    if not selected or len(selected)!=len(set(selected)):
        raise EditorError("RANGE","請選取至少一頁，且頁碼不可重複。")
    for page in selected:
        _validate_page(doc,page)
    return tuple(sorted(selected))


def page_order_after_move(page_count,pages,offset):
    if offset not in (-1,1):
        raise EditorError("RANGE","批次頁面只能向上或向下移動一格。")
    selected=tuple(pages)
    if not selected or len(selected)!=len(set(selected)) or any(
            not isinstance(page,int) or not 0<=page<page_count for page in selected):
        raise EditorError("RANGE","選取頁碼無效。")
    selected_ids=set(selected)
    order=list(range(page_count))
    positions=range(1,page_count) if offset<0 else range(page_count-2,-1,-1)
    for position in positions:
        neighbor=position-1 if offset<0 else position+1
        if order[position] in selected_ids and order[neighbor] not in selected_ids:
            order[position],order[neighbor]=order[neighbor],order[position]
    moved=tuple(index for index,page in enumerate(order) if page in selected_ids)
    return tuple(order),moved


def move_page(pdf, page, target):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        _validate_page(doc, page)
        _validate_page(doc, target)
        order=list(range(doc.page_count))
        selected=order.pop(page)
        order.insert(target,selected)
        doc.select(order)
        return doc.tobytes(garbage=4, deflate=True)


def move_pages(pdf,pages,offset):
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        selected=_validate_pages(doc,pages)
        order,_=page_order_after_move(doc.page_count,selected,offset)
        doc.select(order)
        return doc.tobytes(garbage=4,deflate=True)


def rotate_page(pdf, page, degrees):
    if degrees not in (-90, 90):
        raise EditorError("ROTATION", "頁面只能向左或向右旋轉 90 度。")
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        _validate_page(doc, page)
        selected=doc[page]
        selected.set_rotation((selected.rotation + degrees) % 360)
        return doc.tobytes(garbage=4, deflate=True)


def rotate_pages(pdf,pages,degrees):
    if degrees not in (-90,90):
        raise EditorError("ROTATION","頁面只能向左或向右旋轉 90 度。")
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        for page in _validate_pages(doc,pages):
            selected=doc[page]
            selected.set_rotation((selected.rotation+degrees)%360)
        return doc.tobytes(garbage=4,deflate=True)


def delete_page(pdf, page):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        _validate_page(doc, page)
        if doc.page_count == 1:
            raise EditorError("LAST_PAGE", "文件至少必須保留一頁。")
        doc.delete_page(page)
        return doc.tobytes(garbage=4, deflate=True)


def delete_pages(pdf,pages):
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        selected=_validate_pages(doc,pages)
        if len(selected)>=doc.page_count:
            raise EditorError("LAST_PAGE","文件至少必須保留一頁。")
        for page in reversed(selected):
            doc.delete_page(page)
        return doc.tobytes(garbage=4,deflate=True)
