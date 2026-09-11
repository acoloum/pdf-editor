from pathlib import Path
import sys
import pymupdf
from pdf_editor.errors import EditorError

def resource_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))

def default_font():
    return resource_root() / "resources/fonts/NotoSansCJKtc-Regular.otf"

def checked_font(path, text):
    try:
        font = pymupdf.Font(fontfile=str(path))
    except Exception as exc:
        raise EditorError("FONT_INVALID", "無法讀取字型檔。") from exc
    if any(not font.has_glyph(ord(c)) for c in text if not c.isspace()):
        raise EditorError("FONT_MISSING_GLYPH", "字型缺少部分字元，請更換字型。")
    return font

def embedded_font(pdf, page, run, folder):
    import hashlib
    def normalized(name):
        return name.split('+')[-1].replace(' ', '').replace('-', '').lower()
    with pymupdf.open(stream=pdf, filetype='pdf') as doc:
        for item in doc[page].get_fonts(full=True):
            if normalized(item[3]) != normalized(run.font_name):
                continue
            name, extension, kind, content = doc.extract_font(item[0])
            if content and extension in ('otf', 'ttf', 'cff'):
                path = Path(folder) / (hashlib.sha256(content).hexdigest() + '.' + extension)
                path.write_bytes(content)
                try:
                    checked_font(path, run.text)
                    return path
                except EditorError:
                    pass
    return None
