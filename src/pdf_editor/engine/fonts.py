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

