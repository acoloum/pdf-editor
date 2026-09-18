"""背景工作使用的文件處理函式；在工作程序中執行，不依賴介面。"""
from pathlib import Path

from pdf_editor.document.save import publish_batch, write_pdf
from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.errors import EditorError
from pdf_editor.page_decorations import (add_header_footer, add_image_watermark,
    add_page_numbers, add_text_watermark, crop_pages)
from pdf_editor.page_images import export_pages_as_png
from pdf_editor.pages import (delete_pages, duplicate_pages, extract_pages, insert_blank_page,
    insert_pages, merge_pages, move_pages, move_pages_to, rotate_pages, split_pages)
from pdf_editor.persistent_overlays import embed_workspace


def export_document(pdf,layers,target,source,overwrite):
    data=embed_workspace(pdf,layers) if layers else pdf
    return str(write_pdf(data,Path(target),overwrite,(Path(source),)))

def overwrite_document(pdf, layers, target):
    """覆蓋原檔：先寫入暫存檔並驗證內容，再原子取代原檔。"""
    data = embed_workspace(pdf, layers) if layers else pdf
    path = write_pdf(data, Path(target), overwrite=True, sources=(Path(target),), allow_source=True)
    return str(path)






def export_merge(sources,order,target,paths):
    return str(write_pdf(merge_pages(sources,order),Path(target),False,tuple(Path(p) for p in paths)))

def export_split(pdf,layers,groups,folder,source):
    data=flatten_overlays(pdf,layers) if layers else pdf
    documents=split_pages(data,groups)
    targets=tuple(Path(folder)/f"拆分_{i+1:03}.pdf" for i in range(len(groups)))
    return tuple(str(p) for p in publish_batch(documents,targets,(Path(source),)))

def export_extract(pdf,layers,pages,target,source):
    data=flatten_overlays(pdf,layers) if layers else pdf
    extracted=extract_pages(data,pages)
    return str(write_pdf(extracted,Path(target),False,(Path(source),)))

def export_page_images(pdf,layers,pages,folder,stem,dpi):
    data=flatten_overlays(pdf,layers) if layers else pdf
    return tuple(str(path) for path in export_pages_as_png(
        data,pages,Path(folder),stem,dpi))

def edit_page_document(pdf,layers,operation,pages,target=None):
    data=flatten_overlays(pdf,layers) if layers else pdf
    if operation=="move":
        return move_pages(data,pages,target)
    if operation=="move_to":
        return move_pages_to(data,pages,target)
    if operation=="rotate":
        return rotate_pages(data,pages,target)
    if operation=="delete":
        return delete_pages(data,pages)
    if operation=="duplicate":
        return duplicate_pages(data,pages)
    if operation=="blank":
        return insert_blank_page(data,pages[0])
    if operation=="insert":
        return insert_pages(data,target,pages[0])
    if operation=="crop":
        return crop_pages(data,pages,target)
    if operation=="page_number":
        return add_page_numbers(data,pages,target["start"],target["prefix"],
            target["suffix"],target["position"],target["font_size"],target["font_path"])
    if operation=="text_watermark":
        return add_text_watermark(data,pages,target["text"],target["font_size"],
            target["opacity"],target["angle"],target["font_path"])
    if operation=="image_watermark":
        return add_image_watermark(data,pages,target["image_path"],
            target["width_percent"],target["opacity"],target["angle"])
    if operation=="header_footer":
        return add_header_footer(data,pages,target["text"],target["position"],
            target["font_size"],target["font_path"])
    raise EditorError("PAGE_OPERATION","頁面操作無效。")
