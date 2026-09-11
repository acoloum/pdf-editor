import pymupdf

from pdf_editor.errors import EditorError
from pdf_editor.model import SearchMatch


def find_text(pdf,query):
    text=str(query).strip()
    if not text:
        raise EditorError("SEARCH","請輸入要搜尋的文字。")
    matches=[]
    with pymupdf.open(stream=pdf,filetype="pdf") as doc:
        for page_index,page in enumerate(doc):
            for rect in page.search_for(text):
                matches.append(SearchMatch(page_index,
                    (rect.x0,rect.y0,rect.x1,rect.y1)))
                if len(matches)>=10000:
                    raise EditorError("SEARCH","搜尋結果超過 10,000 筆，請輸入更完整的文字。")
    return tuple(matches)
