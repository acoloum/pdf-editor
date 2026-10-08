from pathlib import Path
import pymupdf
from pdf_editor.engine.overlay import draw_overlay
from pdf_editor.engine.text import extract_runs
from pdf_editor.annotations import list_annotations
from pdf_editor.legacy_overlay_conversion import editable_images_on_page


def open_document(source):
    """接受 PDF 內容或檔案路徑；傳路徑可免去把整份文件複製到背景程序。"""
    if isinstance(source, (str, Path)):
        return pymupdf.open(str(source))
    return pymupdf.open(stream=source, filetype="pdf")


def read_document(source):
    return Path(source).read_bytes() if isinstance(source, (str, Path)) else source


def render_page(pdf, page, scale, pixel_ratio=1.0, include_images=True):
    # 傳入路徑時在背景程序讀取一次即可，文字與註解分析共用同一份內容。
    content = read_document(pdf)
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        p = doc[page]
        render_scale=scale*pixel_ratio
        pix = p.get_pixmap(matrix=pymupdf.Matrix(render_scale,render_scale), alpha=False)
        data = {"png": pix.tobytes("png"), "count":len(doc),
            "matrix":tuple(p.rotation_matrix * pymupdf.Matrix(scale,scale)),
            "pixel_ratio":pixel_ratio,
            "display_size":(pix.width/pixel_ratio,pix.height/pixel_ratio),
            "rotation":p.rotation, "bounds": (0,0,p.cropbox.width,p.cropbox.height),
            "runs":extract_runs(content,page),
            "annotations":list_annotations(content,page), "page":page}
        # 縮放等只需重畫的情況可略過，避免重複分析圖片。
        if include_images:
            data["images"]=_editable_images(doc,page)
        return data

def _editable_images(document, page):
    """可直接點選編輯的圖片；分析失敗時不影響頁面顯示。"""
    try:
        return editable_images_on_page(document, page)
    except Exception:
        return ()

def thumbnail(pdf, page, overlays=()):
    """產生頁面縮圖；overlays 中屬於此頁的圖層會依輸出規則一併畫上（只改記憶體中的副本）。"""
    with open_document(pdf) as doc:
        p=doc[page]
        for layer in overlays:
            if layer.page==page:
                draw_overlay(p,layer)
        scale=110/max(p.rect.width,p.rect.height)
        return p.get_pixmap(matrix=pymupdf.Matrix(scale,scale)).tobytes("png")
