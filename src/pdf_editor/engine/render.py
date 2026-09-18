from pathlib import Path
import pymupdf
from pdf_editor.engine.text import extract_runs
from pdf_editor.annotations import list_annotations


def open_document(source):
    """接受 PDF 內容或檔案路徑；傳路徑可免去把整份文件複製到背景程序。"""
    if isinstance(source, (str, Path)):
        return pymupdf.open(str(source))
    return pymupdf.open(stream=source, filetype="pdf")


def read_document(source):
    return Path(source).read_bytes() if isinstance(source, (str, Path)) else source


def render_page(pdf, page, scale, pixel_ratio=1.0):
    # 傳入路徑時在背景程序讀取一次即可，文字與註解分析共用同一份內容。
    content = read_document(pdf)
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        p = doc[page]
        render_scale=scale*pixel_ratio
        pix = p.get_pixmap(matrix=pymupdf.Matrix(render_scale,render_scale), alpha=False)
        return {"png": pix.tobytes("png"), "count":len(doc),
            "matrix":tuple(p.rotation_matrix * pymupdf.Matrix(scale,scale)),
            "pixel_ratio":pixel_ratio,
            "display_size":(pix.width/pixel_ratio,pix.height/pixel_ratio),
            "rotation":p.rotation, "bounds": (0,0,p.cropbox.width,p.cropbox.height),
            "runs":extract_runs(content,page),
            "annotations":list_annotations(content,page), "page":page}

def thumbnail(pdf, page):
    with open_document(pdf) as doc:
        p=doc[page]
        scale=110/max(p.rect.width,p.rect.height)
        return p.get_pixmap(matrix=pymupdf.Matrix(scale,scale)).tobytes("png")
