"""讀取 PDF 書籤（目錄）與頁面文字，供側欄與複製功能使用。"""
from dataclasses import dataclass, field

import pymupdf


@dataclass
class OutlineEntry:
    title: str
    page: int
    children: list = field(default_factory=list)


def read_outline(pdf):
    """將 PDF 目錄轉為樹狀結構；頁碼為 0 起算，無效頁碼為 -1。"""
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        toc = doc.get_toc(simple=True)
        page_count = doc.page_count
    roots = []
    stack = []
    for level, title, page in toc:
        entry = OutlineEntry(str(title).strip() or "（未命名）",
            page - 1 if 1 <= page <= page_count else -1)
        # 層級跳號時（例如 1 直接到 3）掛在最近的上層項目。
        while stack and stack[-1][0] >= level:
            stack.pop()
        (stack[-1][1].children if stack else roots).append(entry)
        stack.append((level, entry))
    return roots


def page_text(pdf, page):
    """依閱讀順序取出單頁文字。"""
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        if not 0 <= page < doc.page_count:
            return ""
        return doc[page].get_text("text", sort=True).strip()
