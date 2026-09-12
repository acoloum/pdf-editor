import pytest

from pdf_editor.errors import EditorError
from pdf_editor.ocr_assets import validate_ocr_assets


def test_validate_ocr_assets_requires_both_languages(monkeypatch, tmp_path):
    """缺少任一語言模型時，OCR 不得啟動。"""
    monkeypatch.setattr("pdf_editor.ocr_assets.resource_root", lambda: tmp_path)

    with pytest.raises(EditorError) as error:
        validate_ocr_assets()

    assert error.value.code == "OCR_ASSETS"


def test_validate_ocr_assets_rejects_empty_language_model(monkeypatch, tmp_path):
    """空白模型檔會讓引擎在執行時失敗，須提前阻擋。"""
    tessdata = tmp_path / "resources" / "tesseract" / "tessdata"
    tessdata.mkdir(parents=True)
    (tessdata / "chi_tra.traineddata").write_bytes(b"model data")
    (tessdata / "eng.traineddata").touch()
    monkeypatch.setattr("pdf_editor.ocr_assets.resource_root", lambda: tmp_path)

    with pytest.raises(EditorError) as error:
        validate_ocr_assets()

    assert error.value.code == "OCR_ASSETS"


def test_validate_ocr_assets_returns_tessdata_directory(monkeypatch, tmp_path):
    """兩個有效模型存在時，回傳供 OCR 引擎使用的資料夾。"""
    tessdata = tmp_path / "resources" / "tesseract" / "tessdata"
    tessdata.mkdir(parents=True)
    for name in ("chi_tra.traineddata", "eng.traineddata"):
        (tessdata / name).write_bytes(b"model data")
    monkeypatch.setattr("pdf_editor.ocr_assets.resource_root", lambda: tmp_path)

    assert validate_ocr_assets() == tessdata
