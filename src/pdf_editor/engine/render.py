import pymupdf
from pdf_editor.engine.text import extract_runs

def render_page(pdf, page, scale, pixel_ratio=1.0):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        p = doc[page]
        render_scale=scale*pixel_ratio
        pix = p.get_pixmap(matrix=pymupdf.Matrix(render_scale,render_scale), alpha=False)
        return {"png": pix.tobytes("png"), "count":len(doc),
            "matrix":tuple(p.rotation_matrix * pymupdf.Matrix(scale,scale)),
            "pixel_ratio":pixel_ratio,
            "display_size":(pix.width/pixel_ratio,pix.height/pixel_ratio),
            "rotation":p.rotation, "bounds": (0,0,p.cropbox.width,p.cropbox.height),
            "runs":extract_runs(pdf,page), "page":page}

def thumbnail(pdf, page):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        p=doc[page]
        scale=110/max(p.rect.width,p.rect.height)
        return p.get_pixmap(matrix=pymupdf.Matrix(scale,scale)).tobytes("png")
