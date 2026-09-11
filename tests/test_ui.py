import hashlib
from dataclasses import replace
import pytest
from PySide6.QtCore import Qt
from pdf_editor.ui.main_window import MainWindow
from pdf_editor.engine.render import render_page
from pdf_editor.engine.geometry import transform_point, inverse_transform
from pdf_editor.ui.signature_dialog import SignatureDialog
from pdf_editor.ui.canvas import Canvas
from test_text import request_for

def test_reader_preview_apply_undo(qtbot, source_path, font_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_document(source_path)
    qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
    assert window.page_count == 1
    before = window.session.pdf
    window.preview_replacement(request_for(before, font_path))
    qtbot.waitUntil(lambda: window.preview is not None, timeout=30000)
    assert not window.session.dirty
    window.cancel_preview()
    assert window.session.pdf == before
    window.preview_replacement(request_for(before, font_path))
    qtbot.waitUntil(lambda: window.preview is not None, timeout=30000)
    window.apply_preview()
    assert window.session.dirty
    window.session.undo()
    assert window.session.pdf == before
    window.session.saved_fingerprint = window.session.history.current[2]
    window.close()

def test_blank_signature_cannot_accept(qtbot):
    dialog = SignatureDialog()
    qtbot.addWidget(dialog)
    assert not dialog.accept_button.isEnabled()
    assert dialog.png_bytes() == b""

def test_signature_strokes_and_clear(qtbot):
    from PySide6.QtCore import QPoint
    from PIL import Image
    import io
    dialog = SignatureDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mousePress(dialog.pad, Qt.MouseButton.LeftButton, pos=QPoint(20,20))
    qtbot.mouseMove(dialog.pad, QPoint(120,90))
    qtbot.mouseRelease(dialog.pad, Qt.MouseButton.LeftButton, pos=QPoint(120,90))
    assert dialog.accept_button.isEnabled()
    with Image.open(io.BytesIO(dialog.png_bytes())) as image:
        assert image.getbbox() is not None
    dialog.pad.clear()
    assert not dialog.accept_button.isEnabled()

def test_original_font_available_for_selection(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_document(source_path)
    qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
    window.select_run(next(r for r in window.page_data['runs'] if '品質' in r.text))
    assert '原字型' in window.text_panel.font_label.text()
    from pdf_editor.engine.fonts import checked_font, default_font
    assert window.text_panel.font_path != str(default_font())
    assert checked_font(window.text_panel.font_path, '品質').has_glyph(ord('品'))
    window.close()

@pytest.mark.parametrize("rotation", [0,90,180,270])
def test_render_maps_known_corner(pdf_bytes, rotation):
    import pymupdf
    with pymupdf.open(stream=pdf_bytes) as doc:
        doc[0].set_rotation(rotation)
        data = render_page(doc.tobytes(), 0, 1)
    expected = {0:(0,0), 90:(400,0), 180:(500,400), 270:(0,500)}[rotation]
    assert transform_point(data["matrix"],0,0) == pytest.approx(expected)
    assert transform_point(inverse_transform(data["matrix"]),*expected) == pytest.approx((0,0))


def test_high_resolution_render_keeps_display_geometry(pdf_bytes):
    from PIL import Image
    import io

    data = render_page(pdf_bytes, 0, 1.25, pixel_ratio=2.0)
    with Image.open(io.BytesIO(data["png"])) as image:
        assert image.size == (1250, 1000)
    assert data["display_size"] == pytest.approx((625, 500))
    assert data["pixel_ratio"] == 2.0
    assert transform_point(data["matrix"], 500, 400) == pytest.approx((625, 500))


def test_canvas_displays_high_resolution_page_at_logical_size(qtbot, pdf_bytes):
    canvas = Canvas()
    qtbot.addWidget(canvas)
    data = render_page(pdf_bytes, 0, 1.25, pixel_ratio=2.0)

    canvas.display(data)

    page_item = canvas.scene().items()[-1]
    assert page_item.pixmap().devicePixelRatio() == 2.0
    assert canvas.scene().sceneRect().width() == pytest.approx(625)
    assert canvas.scene().sceneRect().height() == pytest.approx(500)


def test_window_requests_high_resolution_page(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_document(source_path)
    qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)

    assert window.page_data["pixel_ratio"] >= 2.0
    window.close()
