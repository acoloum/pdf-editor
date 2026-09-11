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

