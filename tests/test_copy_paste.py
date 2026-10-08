"""圖片複製與貼上：剪貼簿格式、貼上規則與 Ctrl+C／Ctrl+V。"""

import io
from dataclasses import replace

import pymupdf
import pytest
from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from pdf_editor.layer_clipboard import MIME_TYPE, decode_layer
from pdf_editor.ui.main_window import MainWindow


@pytest.fixture
def red_stamp(tmp_path):
    path = tmp_path / "紅章.png"
    Image.new("RGBA", (200, 100), (210, 35, 45, 255)).save(path)
    return path


@pytest.fixture
def white_stamp(tmp_path):
    path = tmp_path / "白底章.png"
    Image.new("RGBA", (200, 100), (255, 255, 255, 255)).save(path)
    return path


def _open(qtbot, path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_document(path)
    qtbot.waitUntil(lambda: window.page_data is not None and not window.busy, timeout=30000)
    return window


def _close(window):
    if window.session is not None:
        window.session.saved_fingerprint = window.session.history.current[2]
    window.close()


def _size(rect):
    return rect[2] - rect[0], rect[3] - rect[1]


def test_copy_layer_puts_moye_format_and_png_on_clipboard(qtbot, multi_page_path, red_stamp):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(red_stamp, False)
        layer = window.session.overlays[-1]

        window.copy_selection()

        mime = QApplication.clipboard().mimeData()
        decoded = decode_layer(bytes(mime.data(MIME_TYPE)))
        assert decoded is not None
        assert (decoded.width, decoded.height) == pytest.approx(_size(layer.rect))
        assert mime.hasImage()
        assert bytes(mime.data("image/png")).startswith(b"\x89PNG")
        assert "已複製圖片" in window.statusBar().currentMessage()
    finally:
        _close(window)


def test_copy_with_remove_white_exports_transparent_png(qtbot, multi_page_path, white_stamp):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(white_stamp, False)
        window.set_layer_remove_white(True)
        qtbot.waitUntil(lambda: not window.busy, timeout=30000)

        window.copy_selection()

        png = bytes(QApplication.clipboard().mimeData().data("image/png"))
        with Image.open(io.BytesIO(png)) as image:
            rgba = image.convert("RGBA")
            assert rgba.getpixel((rgba.width // 2, rgba.height // 2))[3] == 0
    finally:
        _close(window)


def test_ctrl_c_copies_text_when_text_was_selected_last(qtbot, source_path, red_stamp):
    window = _open(qtbot, source_path)
    try:
        window.import_layer(red_stamp, False)
        run = next(r for r in window.page_data["runs"] if "KEEP" in r.text)
        window.select_run(run, open_editor=False)

        window.actions["copy_text"].trigger()

        assert QApplication.clipboard().text() == run.text
        assert not QApplication.clipboard().mimeData().hasFormat(MIME_TYPE)
    finally:
        _close(window)


def test_ctrl_c_copies_layer_when_layer_was_selected_last(qtbot, source_path, red_stamp):
    window = _open(qtbot, source_path)
    try:
        run = next(r for r in window.page_data["runs"] if "KEEP" in r.text)
        window.select_run(run, open_editor=False)
        window.import_layer(red_stamp, False)

        window.actions["copy_text"].trigger()

        assert QApplication.clipboard().mimeData().hasFormat(MIME_TYPE)
    finally:
        _close(window)
