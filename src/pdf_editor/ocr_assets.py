from pathlib import Path

from pdf_editor.engine.fonts import resource_root
from pdf_editor.errors import EditorError


_MODEL_NAMES = ("chi_tra.traineddata", "eng.traineddata")


def ocr_tessdata_path() -> Path:
    """回傳封裝內 Tesseract 語言模型資料夾。"""
    return resource_root() / "resources" / "tesseract" / "tessdata"


def validate_ocr_assets() -> Path:
    """確認離線 OCR 所需的繁中與英文模型皆已封裝。"""
    tessdata = ocr_tessdata_path()
    if not all((tessdata / name).is_file() and (tessdata / name).stat().st_size > 0
               for name in _MODEL_NAMES):
        raise EditorError("OCR_ASSETS", "找不到 OCR 語言模型，請重新安裝墨頁 PDF。")
    return tessdata
