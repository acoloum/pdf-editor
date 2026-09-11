import pymupdf
from pdf_editor.engine.text import extract_runs

def render_page(pdf, page, scale):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        p = doc[page]
        pix = p.get_pixmap(matrix=pymupdf.Matrix(scale,scale), alpha=False)
        return {"png": pix.tobytes("png"), "count":len(doc),
            "matrix":tuple(p.rotation_matrix * pymupdf.Matrix(scale,scale)),
            "rotation":p.rotation, "bounds": (0,0,p.cropbox.width,p.cropbox.height),
            "runs":extract_runs(pdf,page), "page":page}

def thumbnail(pdf, page):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        p=doc[page]
        scale=110/max(p.rect.width,p.rect.height)
        return p.get_pixmap(matrix=pymupdf.Matrix(scale,scale)).tobytes("png")

