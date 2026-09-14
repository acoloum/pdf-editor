import hashlib
import io
from concurrent.futures import Future
from dataclasses import replace
import pytest
import pymupdf
from PIL import Image
from PySide6.QtCore import Qt, QPoint, QPointF, QItemSelectionModel, QEvent, QCoreApplication
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QAbstractItemView,QDialog,QToolBar
from PySide6.QtTest import QSignalSpy
from pdf_editor.ui.main_window import MainWindow
import pdf_editor.ui.main_window as main_window
from pdf_editor.engine.render import render_page
from pdf_editor.engine.geometry import transform_point, inverse_transform
from pdf_editor.ui.signature_dialog import SignatureDialog
from pdf_editor.ui.canvas import Canvas
from pdf_editor.ui.text_panel import TextPanel
from pdf_editor.annotations import mark_text
from pdf_editor.model import LegacyImageCandidate, Overlay
from pdf_editor.ocr import OcrResult
from pdf_editor.comparison import PageComparison, compare_pages
from test_text import request_for


def _legacy_candidate(page=0, xref=17, rect=(20, 30, 100, 70)):
    stream = io.BytesIO()
    Image.new("RGBA", (80, 40), (30, 70, 210, 255)).save(stream, format="PNG")
    return LegacyImageCandidate(xref, page, rect, stream.getvalue(), 80, 40)


def test_legacy_stamp_dialog_requires_explicit_candidate(qtbot):
    from pdf_editor.ui.legacy_stamp_dialog import LegacyStampDialog

    candidates = (_legacy_candidate(page=0), _legacy_candidate(page=2, xref=23))
    dialog = LegacyStampDialog(candidates)
    qtbot.addWidget(dialog)

    assert not dialog.ok_button.isEnabled()
    assert dialog.selected_candidate is None
    dialog.list.setCurrentRow(1)
    assert dialog.ok_button.isEnabled()
    assert dialog.selected_candidate == candidates[1]

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
    from PySide6.QtGui import QPainter

    canvas = Canvas()
    qtbot.addWidget(canvas)
    data = render_page(pdf_bytes, 0, 1.25, pixel_ratio=2.0)

    canvas.display(data)

    page_item = canvas.scene().items()[-1]
    assert page_item.pixmap().devicePixelRatio() == 2.0
    assert canvas.scene().sceneRect().width() == pytest.approx(625)
    assert canvas.scene().sceneRect().height() == pytest.approx(500)
    assert canvas.renderHints() & QPainter.RenderHint.SmoothPixmapTransform


def test_existing_text_editor_keeps_narrow_cell_center(qtbot, pdf_bytes):
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    canvas.display(render_page(pdf_bytes, 0, 1.0))
    qtbot.waitExposed(canvas)
    cell = (100, 100, 150, 114)

    canvas.begin_inline_text(cell, "真直度", run=object(), size=8)

    expected = canvas.mapFromScene(QPointF(125, 107))
    actual = canvas.inline_editor.geometry().center()
    assert actual.x() == pytest.approx(expected.x(), abs=1)
    assert actual.y() == pytest.approx(expected.y(), abs=1)


def test_existing_text_editor_expands_height_for_readable_input(qtbot, pdf_bytes):
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    canvas.display(render_page(pdf_bytes, 0, 1.0))
    qtbot.waitExposed(canvas)
    cell = (100, 100, 150, 114)

    canvas.begin_inline_text(cell, "真直度", run=object(), size=8,
        alignment="hcenter")

    editor = canvas.inline_editor
    expected = canvas.mapFromScene(QPointF(125, 107))
    assert editor.height() >= editor.sizeHint().height()
    assert editor.geometry().center().y() == pytest.approx(expected.y(), abs=1)


def test_inline_text_editor_shows_horizontal_center_while_typing(qtbot, pdf_bytes):
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.display(render_page(pdf_bytes, 0, 1.0))

    canvas.begin_inline_text((100, 100, 150, 114), "真直度", run=object(), size=8,
        alignment="hcenter")

    assert canvas.inline_editor.alignment() & Qt.AlignmentFlag.AlignHCenter


def test_window_renders_at_native_device_pixel_ratio(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_document(source_path)
    qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)

    assert window.page_data["pixel_ratio"] == pytest.approx(window.canvas.devicePixelRatioF())
    window.close()

def test_canvas_pans_page_with_left_drag_on_background(qtbot, pdf_bytes):
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(300, 250)
    canvas.show()
    canvas.display(render_page(pdf_bytes, 0, 1.5))
    qtbot.waitExposed(canvas)
    before = (canvas.horizontalScrollBar().value(), canvas.verticalScrollBar().value())
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(260, 210))
    qtbot.mouseMove(canvas.viewport(), QPoint(220, 170))
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(220, 170))
    after = (canvas.horizontalScrollBar().value(), canvas.verticalScrollBar().value())
    assert after != before

def test_canvas_dragging_text_emits_moved_run(qtbot, pdf_bytes):
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    data = render_page(pdf_bytes, 0, 1.0)
    canvas.display(data)
    qtbot.waitExposed(canvas)
    run = next(r for r in data["runs"] if "品質" in r.text)
    assert hasattr(canvas, "run_moved")
    spy = QSignalSpy(canvas.run_moved)
    x = (run.rect[0] + run.rect[2]) / 2
    y = (run.rect[1] + run.rect[3]) / 2
    start = canvas.mapFromScene(QPointF(x, y))
    end = start + QPoint(30, 20)
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(canvas.viewport(), end)
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert spy.count() == 1
    assert spy.at(0)[0].rect[0] > run.rect[0]


def test_canvas_layer_corner_drag_resizes_with_original_ratio(qtbot,pdf_bytes,tmp_path):
    from PIL import Image

    image_path=tmp_path/"印章.png"
    Image.new("RGBA",(160,80),(210,35,45,210)).save(image_path)
    layer=Overlay("stamp",0,str(image_path),(100,100,180,140),0)
    canvas=Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700,600)
    canvas.show()
    canvas.display(render_page(pdf_bytes,0,1.0),(layer,),layer.id)
    qtbot.waitExposed(canvas)
    item=next(item for item in canvas.scene().items() if hasattr(item,"layer"))
    assert item.isSelected()
    spy=QSignalSpy(canvas.layer_moved)
    corner=item.mapToScene(item._content_rect().bottomRight())
    start=canvas.mapFromScene(corner-QPointF(2,2))
    end=start+QPoint(40,20)

    qtbot.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start)
    qtbot.mouseMove(canvas.viewport(),end)
    qtbot.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=end)

    assert spy.count()==1
    resized=spy.at(0)[0]
    width=resized.rect[2]-resized.rect[0]
    height=resized.rect[3]-resized.rect[1]
    assert width>80
    assert height>40
    assert width/height==pytest.approx(2.0)


@pytest.mark.parametrize(("corner_method","offset","drag"),(
    ("topLeft",(-3,-3),(-40,-20)),
    ("topRight",(3,-3),(40,-20)),
    ("bottomLeft",(-3,3),(-40,20)),
    ("bottomRight",(3,3),(40,20)),
))
def test_canvas_layer_visible_corner_area_resizes(qtbot,pdf_bytes,tmp_path,
        corner_method,offset,drag):
    from PIL import Image

    image_path=tmp_path/"簽名.png"
    Image.new("RGBA",(160,80),(25,25,25,220)).save(image_path)
    layer=Overlay("signature",0,str(image_path),(100,100,180,140),0)
    canvas=Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700,600)
    canvas.show()
    canvas.display(render_page(pdf_bytes,0,1.0),(layer,),layer.id)
    qtbot.waitExposed(canvas)
    item=next(item for item in canvas.scene().items() if hasattr(item,"layer"))
    spy=QSignalSpy(canvas.layer_moved)
    corner=item.mapToScene(getattr(item._content_rect(),corner_method)())
    start=canvas.mapFromScene(corner+QPointF(*offset))
    end=start+QPoint(*drag)

    qtbot.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start)
    qtbot.mouseMove(canvas.viewport(),end)
    qtbot.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=end)

    assert spy.count()==1
    resized=spy.at(0)[0]
    assert resized.rect[2]-resized.rect[0]>80
    assert (resized.rect[2]-resized.rect[0])/(resized.rect[3]-resized.rect[1]) \
        ==pytest.approx(2.0)

def test_canvas_delete_key_emits_selected_run(qtbot, pdf_bytes):
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    data = render_page(pdf_bytes, 0, 1.0)
    canvas.display(data)
    qtbot.waitExposed(canvas)
    run = next(r for r in data["runs"] if "品質" in r.text)
    assert hasattr(canvas, "run_delete_requested")
    spy = QSignalSpy(canvas.run_delete_requested)
    point = canvas.mapFromScene(QPointF((run.rect[0] + run.rect[2]) / 2,
        (run.rect[1] + run.rect[3]) / 2))
    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
    qtbot.keyClick(canvas, Qt.Key.Key_Delete)
    assert spy.count() == 1
    assert spy.at(0)[0].id == run.id

def test_canvas_text_insertion_mode_emits_page_position(qtbot, pdf_bytes):
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    canvas.display(render_page(pdf_bytes, 0, 1.0))
    qtbot.waitExposed(canvas)
    assert hasattr(canvas, "text_insertion_requested")
    assert hasattr(canvas, "start_text_insertion")
    spy = QSignalSpy(canvas.text_insertion_requested)
    canvas.start_text_insertion()
    point = canvas.mapFromScene(QPointF(300, 330))
    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert spy.count() == 1
    assert spy.at(0)[0] == pytest.approx((300, 330), abs=2)


def test_canvas_crop_frame_corner_drag_emits_page_rectangle(qtbot,pdf_bytes):
    canvas=Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700,600)
    canvas.show()
    canvas.display(render_page(pdf_bytes,0,1.0))
    qtbot.waitExposed(canvas)
    spy=QSignalSpy(canvas.crop_requested)

    canvas.start_crop((0,0,500,400))
    start=canvas.mapFromScene(QPointF(500,400))
    end=canvas.mapFromScene(QPointF(460,360))
    qtbot.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start)
    qtbot.mouseMove(canvas.viewport(),end)
    qtbot.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=end)

    assert spy.count()==1
    assert tuple(spy.at(0)[0])==pytest.approx((0,0,460,360),abs=2)
    assert not canvas._crop_mode


def test_canvas_escape_cancels_crop_without_applying(qtbot,pdf_bytes):
    canvas=Canvas()
    qtbot.addWidget(canvas)
    canvas.display(render_page(pdf_bytes,0,1.0))
    requested=QSignalSpy(canvas.crop_requested)
    cancelled=QSignalSpy(canvas.crop_cancelled)

    canvas.start_crop((0,0,500,400))
    qtbot.keyClick(canvas,Qt.Key.Key_Escape)

    assert requested.count()==0
    assert cancelled.count()==1
    assert not canvas._crop_mode


def test_starting_text_mode_cancels_crop_mode(qtbot,pdf_bytes):
    canvas=Canvas()
    qtbot.addWidget(canvas)
    canvas.display(render_page(pdf_bytes,0,1.0))
    cancelled=QSignalSpy(canvas.crop_cancelled)

    canvas.start_crop((0,0,500,400))
    canvas.start_text_insertion()

    assert cancelled.count()==1
    assert not canvas._crop_mode
    assert canvas._text_insertion

def test_text_panel_has_alignment_choices(qtbot):
    panel = TextPanel()
    qtbot.addWidget(panel)
    assert hasattr(panel, "alignment")
    assert [panel.alignment.itemData(i) for i in range(panel.alignment.count())] == [
        "left", "hcenter", "center"]


def test_text_panel_tracks_user_format_changes(qtbot):
    from types import SimpleNamespace
    panel=TextPanel()
    qtbot.addWidget(panel)
    run=SimpleNamespace(editable=True,reason=None,font_name="Test",text="ABC",size=12,
        color=(0,0,0),rect=(10,10,60,30))

    panel.set_run(run)
    assert not panel.modified
    panel.size.setValue(13)
    assert panel.modified

def test_window_delete_selected_text_is_undoable(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(r for r in window.page_data["runs"] if "品質" in r.text)
        window.select_run(run)
        window.delete_run(run)
        qtbot.waitUntil(lambda: window.session.dirty, timeout=30000)
        assert pymupdf.open(stream=window.session.pdf)[0].get_text().count("品質") == 1
        window.session.undo()
        assert pymupdf.open(stream=window.session.pdf)[0].get_text().count("品質") == 2
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()

def test_window_dragged_text_is_applied_immediately(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(r for r in window.page_data["runs"] if "品質" in r.text)
        window.select_run(run)
        moved = replace(run, rect=(run.rect[0] + 20, run.rect[1] + 15,
            run.rect[2] + 20, run.rect[3] + 15))
        window.move_run(moved)
        qtbot.waitUntil(lambda: not window.busy, timeout=30000)
        assert window.session.dirty
        assert window.preview is None
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()

def test_window_can_add_text_immediately_after_deletion(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(r for r in window.page_data["runs"] if "品質" in r.text)
        window.select_run(run)
        window.delete_run(run)
        qtbot.waitUntil(lambda: not window.busy and window.session.dirty, timeout=30000)
        assert window.insertion_rect is not None
        assert window.text_panel.isEnabled()
        window.text_panel.alignment.setCurrentIndex(2)
        assert window.canvas.inline_editor is not None
        window.canvas.inline_editor.setText("重新新增")
        qtbot.keyClick(window.canvas.inline_editor, Qt.Key.Key_Return)
        qtbot.waitUntil(lambda: not window.busy, timeout=30000)
        assert "重新新增" in pymupdf.open(stream=window.session.pdf)[0].get_text()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()

def test_window_toolbar_add_text_completes_insertion(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.resize(1100, 760)
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)

        window.actions["add_text"].trigger()
        assert window.canvas._text_insertion
        assert window.actions["add_text"].isChecked()
        assert window.canvas.insertion_hint.isVisible()
        x, y = transform_point(window.page_data["matrix"], 300, 330)
        point = window.canvas.mapFromScene(QPointF(x, y))
        qtbot.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
        assert window.insertion_rect is not None
        assert window.text_panel.isEnabled()
        assert not window.actions["add_text"].isChecked()
        assert not window.canvas.insertion_hint.isVisible()

        assert window.canvas.inline_editor is not None
        window.canvas.inline_editor.setText("新增內容")
        qtbot.keyClick(window.canvas.inline_editor, Qt.Key.Key_Return)
        qtbot.waitUntil(lambda: not window.busy, timeout=30000)
        assert "新增內容" in pymupdf.open(stream=window.session.pdf)[0].get_text()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()

def test_window_escape_cancels_visible_text_insertion_mode(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        window.actions["add_text"].trigger()
        assert window.canvas.insertion_hint.isVisible()

        qtbot.keyClick(window.canvas, Qt.Key.Key_Escape)

        assert not window.canvas._text_insertion
        assert not window.canvas.insertion_hint.isVisible()
        assert not window.actions["add_text"].isChecked()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()

def test_window_add_text_explains_active_preview(qtbot, source_path, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    messages = []
    monkeypatch.setattr("pdf_editor.ui.main_window.QMessageBox.information",
        lambda parent, title, message: messages.append((title, message)))
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        window.preview = (window.session.revision, window.session.pdf)

        window.actions["add_text"].trigger()

        assert messages
        assert "套用預覽" in messages[0][1]
        assert "取消預覽" in messages[0][1]
        assert not window.canvas._text_insertion
        assert not window.actions["add_text"].isChecked()
    finally:
        window.preview = None
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()

def test_window_selected_text_opens_editor_on_page(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(item for item in window.page_data["runs"] if "品質" in item.text)

        window.select_run(run)

        assert window.canvas.inline_editor is not None
        assert window.canvas.inline_editor.isVisible()
        assert window.canvas.inline_editor.text() == run.text
        assert window.text_panel.preview_button.isHidden()
        assert window.text_panel.apply_button.isHidden()
        assert window.text_panel.cancel_button.isHidden()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()

def test_window_inline_edit_applies_on_enter(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(item for item in window.page_data["runs"] if "品質" in item.text)
        window.select_run(run)

        window.canvas.inline_editor.setText("即時修改")
        qtbot.keyClick(window.canvas.inline_editor, Qt.Key.Key_Return)

        qtbot.waitUntil(lambda: not window.busy and window.session.dirty, timeout=30000)
        assert window.preview is None
        assert "即時修改" in pymupdf.open(stream=window.session.pdf)[0].get_text()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()

def test_window_inline_edit_applies_when_clicking_away(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(item for item in window.page_data["runs"] if "品質" in item.text)
        window.select_run(run)
        window.canvas.inline_editor.setText("點外套用")

        qtbot.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(10, 10))

        qtbot.waitUntil(lambda: not window.busy and window.session.dirty, timeout=30000)
        assert "點外套用" in pymupdf.open(stream=window.session.pdf)[0].get_text()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()

def test_window_inline_edit_escape_cancels(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(item for item in window.page_data["runs"] if "品質" in item.text)
        window.select_run(run)
        window.canvas.inline_editor.setText("不應寫入")

        qtbot.keyClick(window.canvas.inline_editor, Qt.Key.Key_Escape)

        qtbot.waitUntil(lambda: window.canvas.inline_editor is None, timeout=30000)
        assert not window.session.dirty
        assert "不應寫入" not in pymupdf.open(stream=window.session.pdf)[0].get_text()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()

def test_window_toolbar_add_text_edits_and_applies_on_page(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.resize(1100, 760)
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        window.actions["add_text"].trigger()
        x, y = transform_point(window.page_data["matrix"], 300, 330)
        point = window.canvas.mapFromScene(QPointF(x, y))
        qtbot.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)

        assert window.canvas.inline_editor is not None
        window.canvas.inline_editor.setText("直接新增")
        qtbot.keyClick(window.canvas.inline_editor, Qt.Key.Key_Return)

        qtbot.waitUntil(lambda: not window.busy and window.session.dirty, timeout=30000)
        assert window.preview is None
        assert "直接新增" in pymupdf.open(stream=window.session.pdf)[0].get_text()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def _document_page_texts(pdf):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return [page.get_text().strip() for page in doc]


def _select_thumbnail_pages(window,pages,current=None):
    window.thumbs.clearSelection()
    for page in pages:
        window.thumbs.item(page).setSelected(True)
    if current is not None:
        window.thumbs.setCurrentItem(window.thumbs.item(current),
            QItemSelectionModel.SelectionFlag.NoUpdate)
        window.page=current
    window.thumbnail_selection_changed()


def test_window_page_menu_exposes_management_actions(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.page_menu_button.text() == "頁面操作"
    assert [window.actions[name].text() for name in (
        "page_up", "page_down", "rotate_left", "rotate_right", "duplicate_page",
        "blank_page", "insert_pdf", "extract_pages", "export_png", "direct_crop", "crop_page",
        "delete_page"
    )] == ["選取頁面上移", "選取頁面下移", "選取頁面向左旋轉",
        "選取頁面向右旋轉", "複製選取頁面", "新增空白頁",
        "插入另一份 PDF（全部頁面）…", "抽取選取頁面另存…", "匯出選取頁面為 PNG…",
        "直接拖曳裁切框",
        "精確輸入裁切邊距…", "刪除選取頁面"]
    assert window.actions["page_marks"].text()=="頁碼／浮水印"
    assert window.actions["header_footer"].text()=="頁首頁尾範本"


def test_thumbnail_list_supports_extended_selection(qtbot,multi_page_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)

        assert window.thumbs.selectionMode()==QAbstractItemView.SelectionMode.ExtendedSelection
        _select_thumbnail_pages(window,(0,2),2)
        assert window.selected_page_indices()==(0,2)
        assert window.thumbs.dragEnabled()
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_moves_selected_pages_as_batch(qtbot,multi_page_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        _select_thumbnail_pages(window,(1,2),2)

        window.move_current_page(-1)

        qtbot.waitUntil(lambda:not window.busy and window.session.dirty,timeout=30000)
        assert _document_page_texts(window.session.pdf)==["PAGE 2","PAGE 3","PAGE 1"]
        assert window.selected_page_indices()==(0,1)
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_rotates_selected_pages_as_batch(qtbot,multi_page_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        _select_thumbnail_pages(window,(0,2),2)

        window.rotate_current_page(90)

        qtbot.waitUntil(lambda:not window.busy and window.session.dirty,timeout=30000)
        with pymupdf.open(stream=window.session.pdf,filetype="pdf") as doc:
            assert [page.rotation for page in doc]==[90,0,90]
        assert window.selected_page_indices()==(0,2)
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_deletes_selected_pages_as_batch(qtbot,multi_page_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        _select_thumbnail_pages(window,(0,2),2)

        window.delete_current_page()

        qtbot.waitUntil(lambda:not window.busy and window.page_count==1,timeout=30000)
        assert _document_page_texts(window.session.pdf)==["PAGE 2"]
        assert window.selected_page_indices()==(0,)
        assert not window.actions["delete_page"].isEnabled()
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_moves_selected_thumbnails_as_dragged_group(qtbot,multi_page_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        _select_thumbnail_pages(window,(0,2),2)

        window.move_selected_pages_to((0,2),3)

        qtbot.waitUntil(lambda:not window.busy and window.session.dirty,timeout=30000)
        assert _document_page_texts(window.session.pdf)==["PAGE 2","PAGE 1","PAGE 3"]
        assert window.selected_page_indices()==(1,2)
        assert window.page==2
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_thumbnail_drop_event_emits_selected_group_and_destination(qtbot,multi_page_path):
    class DropEvent:
        def __init__(self,source,position):
            self._source=source
            self._position=position
            self.accepted=False

        def source(self):
            return self._source

        def position(self):
            return self._position

        def setDropAction(self,action):
            self.action=action

        def accept(self):
            self.accepted=True

    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        _select_thumbnail_pages(window,(0,2),2)
        window.thumbs.pages_dropped.disconnect(window.move_selected_pages_to)
        emitted=[]
        window.thumbs.pages_dropped.connect(lambda pages,destination:
            emitted.append((pages,destination)))
        rect=window.thumbs.visualItemRect(window.thumbs.item(1))
        event=DropEvent(window.thumbs,QPointF(rect.center()))

        window.thumbs.dropEvent(event)

        assert event.accepted
        assert event.action==Qt.DropAction.MoveAction
        assert emitted==[((0,2),2)]
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_duplicates_selected_pages_and_can_undo(qtbot,multi_page_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        _select_thumbnail_pages(window,(0,2),2)

        window.duplicate_selected_pages()

        qtbot.waitUntil(lambda:not window.busy and window.page_count==5,timeout=30000)
        assert _document_page_texts(window.session.pdf)==[
            "PAGE 1","PAGE 2","PAGE 3","PAGE 1","PAGE 3"]
        assert window.selected_page_indices()==(3,4)
        assert window.page==4

        window.history_step(False)
        qtbot.waitUntil(lambda:window.page_count==3,timeout=30000)
        assert _document_page_texts(window.session.pdf)==["PAGE 1","PAGE 2","PAGE 3"]
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_adds_blank_page_after_selection_and_can_undo(qtbot,multi_page_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        _select_thumbnail_pages(window,(0,2),2)

        window.add_blank_page()

        qtbot.waitUntil(lambda:not window.busy and window.page_count==4,timeout=30000)
        assert _document_page_texts(window.session.pdf)==["PAGE 1","PAGE 2","PAGE 3",""]
        assert window.selected_page_indices()==(3,)
        assert window.page==3
        window.history_step(False)
        qtbot.waitUntil(lambda:window.page_count==3,timeout=30000)
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_inserts_external_pdf_and_can_undo(qtbot,source_path,multi_page_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)

        window.apply_insert_pages(multi_page_path.read_bytes())

        qtbot.waitUntil(lambda:not window.busy and window.page_count==4,timeout=30000)
        assert _document_page_texts(window.session.pdf)[1:]==["PAGE 1","PAGE 2","PAGE 3"]
        assert window.selected_page_indices()==(1,2,3)
        assert window.page==1
        window.history_step(False)
        qtbot.waitUntil(lambda:window.page_count==1,timeout=30000)
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_extracts_selected_pages_to_new_pdf(qtbot,multi_page_path,tmp_path):
    window=MainWindow()
    qtbot.addWidget(window)
    target=tmp_path/"抽取.pdf"
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        _select_thumbnail_pages(window,(0,2),2)

        window.extract_selected_to(target)

        qtbot.waitUntil(lambda:not window.busy and target.exists(),timeout=30000)
        with pymupdf.open(target) as doc:
            assert [page.get_text().strip() for page in doc]==["PAGE 1","PAGE 3"]
        assert not window.session.dirty
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_searches_document_and_navigates_results(qtbot,multi_page_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)

        window.perform_search("PAGE")

        qtbot.waitUntil(lambda:not window.busy and len(window.search_results)==3,timeout=30000)
        assert window.search_count.text()=="1 / 3"
        assert window.canvas.search_highlight is not None
        window.next_search_result()
        qtbot.waitUntil(lambda:window.page==1 and window.page_data["page"]==1,timeout=30000)
        assert window.search_count.text()=="2 / 3"
        assert window.canvas.search_highlight is not None
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_fit_page_and_width_update_zoom(qtbot,source_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.resize(1200,800)
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)

        window.fit_zoom("page")
        page_scale=window.scale
        window.fit_zoom("width")

        assert page_scale>0
        assert window.scale>=page_scale
        assert window.zoom.currentText()=="適合寬度"
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_zoom_buttons_move_to_neighboring_scale(qtbot,source_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        assert "zoom_in" in window.actions
        assert "zoom_out" in window.actions

        window.scale=1.25
        window.zoom_by(1)
        assert window.scale==pytest.approx(1.5)
        assert window.zoom.currentText()=="150%"

        window.zoom_by(-1)
        assert window.scale==pytest.approx(1.25)
        assert window.zoom.currentText()=="125%"
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_zoom_buttons_disable_at_limits(qtbot,source_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)

        window.scale=0.25
        window.refresh_actions()
        assert not window.actions["zoom_out"].isEnabled()
        assert window.actions["zoom_in"].isEnabled()

        window.scale=5.0
        window.refresh_actions()
        assert window.actions["zoom_out"].isEnabled()
        assert not window.actions["zoom_in"].isEnabled()
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_zoom_buttons_have_keyboard_shortcuts(qtbot):
    window=MainWindow()
    qtbot.addWidget(window)

    assert QKeySequence("Ctrl+-") in window.actions["zoom_out"].shortcuts()
    assert QKeySequence("Ctrl++") in window.actions["zoom_in"].shortcuts()
    assert QKeySequence("Ctrl+=") in window.actions["zoom_in"].shortcuts()


def test_window_zoom_buttons_are_visible_at_default_width(qtbot):
    window=MainWindow()
    qtbot.addWidget(window)
    window.resize(1320,850)
    window.show()
    QCoreApplication.processEvents()

    for name in ("zoom_out","zoom_in"):
        action=window.actions[name]
        toolbar=next(bar for bar in window.findChildren(QToolBar)
            if action in bar.actions())
        assert toolbar.widgetForAction(action).isVisible()


def test_window_zoom_keeps_visible_page_center(qtbot,source_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.resize(1100,700)
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        window.change_zoom("200%")
        qtbot.waitUntil(lambda:window.page_data["display_size"][0]==pytest.approx(1000),
            timeout=30000)
        window.canvas.centerOn(QPointF(700,500))
        before_scene=window.canvas.mapToScene(window.canvas.viewport().rect().center())
        before=transform_point(inverse_transform(window.canvas.matrix),
            before_scene.x(),before_scene.y())

        window.zoom_by(1)

        qtbot.waitUntil(lambda:window.page_data["display_size"][0]==pytest.approx(1250),
            timeout=30000)
        after_scene=window.canvas.mapToScene(window.canvas.viewport().rect().center())
        after=transform_point(inverse_transform(window.canvas.matrix),
            after_scene.x(),after_scene.y())
        assert after==pytest.approx(before,abs=1.5)
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_exports_selected_pages_as_png(qtbot,multi_page_path,tmp_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        _select_thumbnail_pages(window,(0,2),2)

        window.export_selected_png_to(tmp_path,144)

        targets=(tmp_path/"三頁-第001頁.png",tmp_path/"三頁-第003頁.png")
        qtbot.waitUntil(lambda:not window.busy and all(path.exists() for path in targets),
            timeout=30000)
        assert not window.session.dirty
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_crops_selected_pages_and_preserves_selection(qtbot,multi_page_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        _select_thumbnail_pages(window,(0,2),2)

        window.apply_page_crop((10,20,30,40))

        qtbot.waitUntil(lambda:not window.busy and window.session.dirty,timeout=30000)
        with pymupdf.open(stream=window.session.pdf,filetype="pdf") as doc:
            assert (doc[0].cropbox.width,doc[0].cropbox.height)==pytest.approx((260,140))
            assert (doc[1].cropbox.width,doc[1].cropbox.height)==pytest.approx((300,200))
            assert (doc[2].cropbox.width,doc[2].cropbox.height)==pytest.approx((260,140))
        assert window.selected_page_indices()==(0,2)
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_direct_crop_applies_dragged_rectangle_to_captured_pages(qtbot,multi_page_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        _select_thumbnail_pages(window,(0,2),2)

        window.start_direct_crop()
        assert window.canvas._crop_mode
        assert window.crop_pages==(0,2)
        window.apply_direct_crop((10,20,270,160))

        qtbot.waitUntil(lambda:not window.busy and window.session.dirty,timeout=30000)
        with pymupdf.open(stream=window.session.pdf,filetype="pdf") as doc:
            assert (doc[0].cropbox.width,doc[0].cropbox.height)==pytest.approx((260,140))
            assert (doc[1].cropbox.width,doc[1].cropbox.height)==pytest.approx((300,200))
            assert (doc[2].cropbox.width,doc[2].cropbox.height)==pytest.approx((260,140))
        assert window.selected_page_indices()==(0,2)
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_adds_header_footer_to_selected_pages(qtbot,multi_page_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        _select_thumbnail_pages(window,(0,2),2)

        window.apply_header_footer({"text":"內部文件 {page}/{pages}",
            "position":"bottom_right","font_size":9})

        qtbot.waitUntil(lambda:not window.busy and window.session.dirty,timeout=30000)
        with pymupdf.open(stream=window.session.pdf,filetype="pdf") as doc:
            texts=[page.get_text().replace("\xa0"," ") for page in doc]
        assert "內部文件 1/3" in texts[0]
        assert "內部文件" not in texts[1]
        assert "內部文件 3/3" in texts[2]
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_adds_page_numbers_to_selected_pages(qtbot,multi_page_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        _select_thumbnail_pages(window,(0,2),2)

        window.apply_page_decoration("page_number",{
            "start":7,"prefix":"頁碼 ","suffix":"","position":"bottom_center",
            "font_size":10})

        qtbot.waitUntil(lambda:not window.busy and window.session.dirty,timeout=30000)
        with pymupdf.open(stream=window.session.pdf,filetype="pdf") as doc:
            texts=[page.get_text().replace("\xa0"," ") for page in doc]
        assert "頁碼 7" in texts[0]
        assert "頁碼" not in texts[1]
        assert "頁碼 8" in texts[2]
        assert window.selected_page_indices()==(0,2)
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_moves_current_page_and_refreshes_navigation(qtbot, multi_page_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)

        window.move_current_page(1)

        qtbot.waitUntil(lambda: not window.busy and window.session.dirty, timeout=30000)
        assert _document_page_texts(window.session.pdf) == ["PAGE 2", "PAGE 1", "PAGE 3"]
        assert window.page == 1
        assert window.page_count == 3
        assert window.thumbs.count() == 3
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_window_rotates_current_page(qtbot, multi_page_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)

        window.rotate_current_page(90)

        qtbot.waitUntil(lambda: not window.busy and window.session.dirty, timeout=30000)
        with pymupdf.open(stream=window.session.pdf, filetype="pdf") as doc:
            assert doc[0].rotation == 90
        assert window.page == 0
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_window_deletes_page_and_keeps_valid_selection(qtbot, multi_page_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        window.goto_page(2)
        qtbot.waitUntil(lambda: window.page == 2, timeout=30000)

        window.delete_current_page()

        qtbot.waitUntil(lambda: not window.busy and window.page_count == 2, timeout=30000)
        assert _document_page_texts(window.session.pdf) == ["PAGE 1", "PAGE 2"]
        assert window.page == 1
        assert window.thumbs.count() == 2

        window.history_step(False)
        qtbot.waitUntil(lambda: window.page_count == 3, timeout=30000)
        assert _document_page_texts(window.session.pdf) == ["PAGE 1", "PAGE 2", "PAGE 3"]
        assert window.thumbs.count() == 3
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_window_page_actions_follow_current_page(qtbot, multi_page_path, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        assert not window.actions["page_up"].isEnabled()
        assert window.actions["page_down"].isEnabled()
        assert window.actions["delete_page"].isEnabled()

        window.goto_page(2)
        assert window.actions["page_up"].isEnabled()
        assert not window.actions["page_down"].isEnabled()

        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_count == 1, timeout=30000)
        assert not window.actions["delete_page"].isEnabled()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_thumbnail_list_supports_internal_drag_reordering(qtbot, multi_page_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)

        assert window.thumbs.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
        assert window.thumbs.currentRow() == 0
        window.thumbs.pages_dropped.emit((0,),3)
        qtbot.waitUntil(lambda: not window.busy and window.session.dirty, timeout=30000)
        assert _document_page_texts(window.session.pdf) == ["PAGE 2", "PAGE 3", "PAGE 1"]
        assert window.page == 2
        assert window.thumbs.currentRow() == 2
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_unchanged_inline_selection_stays_available_for_markup(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run=next(item for item in window.page_data["runs"] if "品質" in item.text)
        revision=window.session.revision
        window.select_run(run)
        qtbot.waitUntil(lambda:window.canvas.inline_editor.hasFocus(),timeout=30000)

        window.canvas.inline_editor.clearFocus()
        qtbot.wait(50)

        assert not window.busy
        assert window.session.revision==revision
        assert window.run==run
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_markup_menu_exposes_highlight_underline_and_note(qtbot):
    window=MainWindow()
    qtbot.addWidget(window)

    assert window.markup_menu_button.text()=="標記註解"
    assert [window.actions[name].text() for name in (
        "highlight","underline","text_note"
    )]==["螢光標記","加底線","文字註解"]


@pytest.mark.parametrize("kind,expected",[("highlight","Highlight"),("underline","Underline")])
def test_window_applies_native_markup_to_selected_text(qtbot,source_path,kind,expected):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        run=next(item for item in window.page_data["runs"] if "品質" in item.text)
        window.select_run(run)
        window.show()
        qtbot.waitUntil(lambda:window.canvas.inline_editor.hasFocus(),timeout=30000)
        window.canvas.inline_editor.clearFocus()
        qtbot.waitUntil(lambda:not window.busy,timeout=30000)

        window.apply_selected_markup(kind)

        qtbot.waitUntil(lambda:not window.busy and window.session.dirty,timeout=30000)
        with pymupdf.open(stream=window.session.pdf,filetype="pdf") as doc:
            page=doc[0]
            assert [item.type[1] for item in (page.annots() or [])]==[expected]
        window.history_step(False)
        with pymupdf.open(stream=window.session.pdf,filetype="pdf") as doc:
            page=doc[0]
            assert list(page.annots() or [])==[]
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_adds_text_note_at_clicked_position(qtbot,source_path,monkeypatch):
    window=MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr("pdf_editor.ui.main_window.QInputDialog.getMultiLineText",
        lambda *args:("請重新確認尺寸",True))
    try:
        window.resize(1100,760)
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)

        window.actions["text_note"].trigger()
        assert window.canvas._note_insertion
        x,y=transform_point(window.page_data["matrix"],250,300)
        point=window.canvas.mapFromScene(QPointF(x,y))
        qtbot.mouseClick(window.canvas.viewport(),Qt.MouseButton.LeftButton,pos=point)

        qtbot.waitUntil(lambda:not window.busy and window.session.dirty,timeout=30000)
        with pymupdf.open(stream=window.session.pdf,filetype="pdf") as doc:
            page=doc[0]
            notes=list(page.annots() or [])
            assert notes[0].type[1]=="Text"
            assert notes[0].info["content"]=="請重新確認尺寸"
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_markup_menu_exposes_annotation_selection_delete_and_colors(qtbot):
    window=MainWindow()
    qtbot.addWidget(window)

    assert [window.actions[name].text() for name in (
        "select_annotation","highlight_yellow","highlight_green",
        "highlight_pink","highlight_blue","delete_annotation"
    )]==["選取註解","螢光色：黃色","螢光色：綠色","螢光色：粉紅色",
        "螢光色：藍色","刪除選取註解"]


def test_window_selects_and_deletes_annotation_with_delete_key(qtbot,source_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.resize(1100,760)
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        run=next(item for item in window.page_data["runs"] if "品質" in item.text)
        window.select_run(run)
        qtbot.waitUntil(lambda:window.canvas.inline_editor is not None,timeout=30000)
        window.canvas.inline_editor.clearFocus()
        qtbot.wait(50)
        window.apply_selected_markup("highlight")
        qtbot.waitUntil(lambda:not window.busy and window.page_data is not None,timeout=30000)

        window.actions["select_annotation"].trigger()
        x,y=transform_point(window.page_data["matrix"],
            (run.rect[0]+run.rect[2])/2,(run.rect[1]+run.rect[3])/2)
        point=window.canvas.mapFromScene(QPointF(x,y))
        qtbot.mouseClick(window.canvas.viewport(),Qt.MouseButton.LeftButton,pos=point)
        assert window.annotation is not None
        qtbot.keyClick(window.canvas,Qt.Key.Key_Delete)

        qtbot.waitUntil(lambda:not window.busy,timeout=30000)
        with pymupdf.open(stream=window.session.pdf,filetype="pdf") as doc:
            assert list(doc[0].annots() or [])==[]
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_changes_selected_highlight_color_immediately(qtbot,source_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        run=next(item for item in window.page_data["runs"] if "品質" in item.text)
        window.submit_annotation(mark_text,(0,run.rect,"highlight"),"加入標記")
        qtbot.waitUntil(lambda:not window.busy and window.page_data is not None,timeout=30000)
        window.select_annotation(window.page_data["annotations"][0])

        window.actions["highlight_green"].trigger()

        qtbot.waitUntil(lambda:not window.busy,timeout=30000)
        with pymupdf.open(stream=window.session.pdf,filetype="pdf") as doc:
            color=list(doc[0].annots())[0].colors["stroke"]
            assert color==pytest.approx((0.30,0.78,0.48),abs=0.01)
        window.history_step(False)
        with pymupdf.open(stream=window.session.pdf,filetype="pdf") as doc:
            color=list(doc[0].annots())[0].colors["stroke"]
            assert color==pytest.approx((1.0,0.84,0.18),abs=0.01)
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def _submit_synchronously(self, function, arguments, success, failure):
    """讓 UI 測試在不建立背景程序的情況下驗證完成回呼。"""
    try:
        success(function(*arguments))
    except Exception as exc:
        failure((getattr(exc, "code", "ERROR"), str(exc), ()))


def _pdf_with_ocr_marker(pdf):
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        document[0].insert_text((40, 340), "OCR MARKER", fontsize=12)
        return document.tobytes(garbage=4, deflate=True)


def test_window_exposes_ocr_action_for_selected_editable_pages(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)

        assert window.actions["ocr"].text() == "OCR 文字辨識"
        assert window.actions["ocr"].isEnabled()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_window_ocr_requires_an_actual_thumbnail_selection(qtbot, source_path):
    submitted = []

    def capture_submission(function, arguments, success, failure):
        submitted.append(function)

    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        window.jobs.submit = capture_submission
        window.thumbs.clearSelection()
        window.thumbnail_selection_changed()

        assert not window.actions["ocr"].isEnabled()
        window.run_ocr()
        assert submitted == []
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_window_commits_ocr_as_one_undoable_change(qtbot, source_path, tmp_path, monkeypatch):
    calls = []

    def fake_ocr_pages(pdf, pages, tessdata):
        calls.append((pages, tessdata))
        return OcrResult(_pdf_with_ocr_marker(pdf), pages, (), 1)

    monkeypatch.setattr("pdf_editor.ui.main_window.Jobs.submit", _submit_synchronously)
    monkeypatch.setattr("pdf_editor.ui.main_window.ocr_pages", fake_ocr_pages,
        raising=False)
    monkeypatch.setattr("pdf_editor.ui.main_window.validate_ocr_assets", lambda: tmp_path,
        raising=False)
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        before = window.session.pdf

        window.apply_ocr_to_pages((0,))

        assert calls == [((0,), tmp_path)]
        assert window.session.pdf != before
        assert "OCR MARKER" in pymupdf.open(stream=window.session.pdf)[0].get_text()
        assert window.statusBar().currentMessage() == "OCR 完成：辨識 1 頁，跳過 0 頁，共 1 個文字區段。"
        window.history_step(False)
        assert window.session.pdf == before
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_window_keeps_ocr_summary_after_delayed_page_render(
        qtbot, source_path, tmp_path, monkeypatch):
    pending = []

    def defer_submission(function, arguments, success, failure):
        pending.append((function, arguments, success, failure))

    def run_next():
        function, arguments, success, failure = pending.pop(0)
        try:
            success(function(*arguments))
        except Exception as exc:
            failure((getattr(exc, "code", "ERROR"), str(exc), ()))

    def fake_ocr_pages(pdf, pages, tessdata):
        return OcrResult(_pdf_with_ocr_marker(pdf), pages, (), 1)

    monkeypatch.setattr("pdf_editor.ui.main_window.ocr_pages", fake_ocr_pages)
    monkeypatch.setattr("pdf_editor.ui.main_window.validate_ocr_assets", lambda: tmp_path)
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        window.jobs.submit = defer_submission

        window.apply_ocr_to_pages((0,))
        run_next()
        run_next()

        assert window.statusBar().currentMessage() == "OCR 完成：辨識 1 頁，跳過 0 頁，共 1 個文字區段。"
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_window_keeps_history_unchanged_when_ocr_skips_all_pages(
        qtbot, source_path, tmp_path, monkeypatch):
    def fake_ocr_pages(pdf, pages, tessdata):
        return OcrResult(pdf, (), pages, 0)

    monkeypatch.setattr("pdf_editor.ui.main_window.Jobs.submit", _submit_synchronously)
    monkeypatch.setattr("pdf_editor.ui.main_window.ocr_pages", fake_ocr_pages,
        raising=False)
    monkeypatch.setattr("pdf_editor.ui.main_window.validate_ocr_assets", lambda: tmp_path,
        raising=False)
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        before = window.session.pdf
        history_index = window.session.history.index

        window.apply_ocr_to_pages((0,))

        assert window.session.pdf == before
        assert window.session.history.index == history_index
        assert not window.session.can_undo
        assert window.statusBar().currentMessage() == "OCR 完成：全部 1 頁已有文字，未建立變更。"
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def _page_comparison_result(similarity=100.0):
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, format="PNG")
    png = stream.getvalue()
    changed = 0 if similarity == 100.0 else 1
    return PageComparison(png, png, png, similarity, changed, 4, 0, 0)


def _defer_window_jobs(window):
    pending = []

    def submit(function, arguments, success, failure):
        pending.append((function, arguments, success, failure))

    window.jobs.submit = submit
    return pending


def _encrypted_pdf(pdf_bytes):
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        return document.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="user",
            permissions=pymupdf.PDF_PERM_PRINT,
        )


def test_window_exposes_page_comparison_only_for_open_idle_document(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        assert window.actions["compare"].text() == "頁面比較"
        assert not window.actions["compare"].isEnabled()

        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        assert window.actions["compare"].isEnabled()

        window.busy = True
        window.refresh_actions()
        assert not window.actions["compare"].isEnabled()
    finally:
        window.busy = False
        window.close()


def test_comparison_flattens_overlays_without_changing_session_state(
        qtbot, source_path, pdf_bytes, tmp_path, monkeypatch):
    image_path = tmp_path / "比較圖章.png"
    Image.new("RGBA", (80, 40), (220, 20, 20, 255)).save(image_path)
    monkeypatch.setattr("pdf_editor.ui.main_window.Jobs.submit", _submit_synchronously)
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        window.session.set_overlays((
            Overlay("comparison-overlay", 0, str(image_path), (300, 300, 380, 340), 0),
        ))
        revision = window.session.revision
        pdf = window.session.pdf
        history_items = tuple(window.session.history.items)
        history_index = window.session.history.index

        window.open_comparison(pdf_bytes)

        assert window.comparison_dialog is not None
        assert not window.comparison_dialog.summary.text().startswith("相似度 100.0%")
        assert window.session.revision == revision
        assert window.session.pdf == pdf
        assert tuple(window.session.history.items) == history_items
        assert window.session.history.index == history_index
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_comparison_submits_fixed_defaults_and_ignores_older_serial(
        qtbot, source_path, pdf_bytes):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        pending = _defer_window_jobs(window)

        window.open_comparison(pdf_bytes)
        dialog = window.comparison_dialog
        window.request_comparison_page(0)

        assert len(pending) == 2
        assert pending[0][0] is compare_pages
        assert len(pending[0][1]) == 4
        pending[0][2](_page_comparison_result(25.0))
        assert dialog.summary.text() == "正在比較頁面…"

        pending[1][2](_page_comparison_result(75.0))
        assert dialog.summary.text() == "相似度 75.0%｜差異 1 / 4 像素"
    finally:
        window.close()


def test_comparison_ignores_callback_after_session_revision_changes(
        qtbot, source_path, pdf_bytes):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        pending = _defer_window_jobs(window)
        window.open_comparison(pdf_bytes)
        dialog = window.comparison_dialog

        window.session.set_overlays(())
        pending[0][2](_page_comparison_result(50.0))

        assert window.comparison_dialog is None
        assert window.comparison_pdf is None
        assert window.comparison_base_pdf is None
        assert not dialog.isVisible()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_comparison_closes_when_revision_changes_before_next_page_request(
        qtbot, source_path, multi_page_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        pending = _defer_window_jobs(window)
        window.open_comparison(multi_page_path.read_bytes())
        dialog = window.comparison_dialog
        pending[0][2](_page_comparison_result(100.0))
        assert dialog.page_spin.isEnabled()

        window.session.set_overlays(())
        dialog.page_spin.setValue(2)

        assert len(pending) == 1
        assert window.comparison_dialog is None
        assert window.comparison_pdf is None
        assert window.comparison_base_pdf is None
        assert not dialog.isVisible()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_comparison_failure_keeps_concurrent_main_window_job_busy(
        qtbot, source_path, pdf_bytes, monkeypatch):
    warnings = []
    future = Future()
    monkeypatch.setattr("pdf_editor.ui.main_window.QMessageBox.warning",
        lambda parent, title, message: warnings.append((title, message)))
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        qtbot.waitUntil(lambda: not window.jobs.pending, timeout=30000)
        window.jobs.timer.stop()
        monkeypatch.setattr(window.jobs.pool, "submit", lambda *args: future)
        window.open_comparison(pdf_bytes)
        dialog = window.comparison_dialog

        window.busy = True
        window.refresh_actions()
        future.set_result((False, ("COMPARE", "比較工作失敗。", ())))
        window.jobs.poll()

        assert window.busy
        assert not window.actions["ocr"].isEnabled()
        assert dialog.page_spin.isEnabled()
        assert dialog.summary.text() == "比較失敗：比較工作失敗。"
        assert warnings == [("無法比較頁面", "比較工作失敗。")]
    finally:
        window.busy = False
        window.close()


def test_comparison_close_clears_bytes_and_ignores_callback(
        qtbot, source_path, pdf_bytes):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        pending = _defer_window_jobs(window)
        window.open_comparison(pdf_bytes)
        dialog = window.comparison_dialog

        dialog.close()
        qtbot.waitUntil(lambda: window.comparison_dialog is None)
        pending[0][2](_page_comparison_result(50.0))

        assert window.comparison_pdf is None
        assert window.comparison_base_pdf is None
        assert dialog.summary.text() == "正在比較頁面…"
    finally:
        window.close()


def test_comparison_delete_later_clears_references_before_failure_callback(
        qtbot, source_path, pdf_bytes):
    window = MainWindow()
    qtbot.addWidget(window)
    dialog = None
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        pending = _defer_window_jobs(window)
        window.open_comparison(pdf_bytes)
        dialog = window.comparison_dialog

        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        pending[0][3](("COMPARE", "比較工作失敗。", ()))

        assert window.comparison_dialog is None
        assert window.comparison_pdf is None
        assert window.comparison_base_pdf is None
    finally:
        if dialog is not None and window.comparison_dialog is dialog:
            window.clear_comparison(dialog)
        window.close()


def test_switching_document_closes_comparison_and_ignores_callback(
        qtbot, source_path, multi_page_path, pdf_bytes):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        pending = _defer_window_jobs(window)
        window.open_comparison(pdf_bytes)
        dialog = window.comparison_dialog

        window.open_document(multi_page_path)
        pending[0][2](_page_comparison_result(50.0))

        assert window.comparison_dialog is None
        assert window.comparison_pdf is None
        assert dialog.summary.text() == "正在比較頁面…"
    finally:
        window.close()


def test_choose_comparison_pdf_cancels_encrypted_password_prompt(
        qtbot, source_path, pdf_bytes, tmp_path, monkeypatch):
    comparison_path = tmp_path / "加密比較.pdf"
    comparison_path.write_bytes(_encrypted_pdf(pdf_bytes))
    monkeypatch.setattr("pdf_editor.ui.main_window.QFileDialog.getOpenFileName",
        lambda *args: (str(comparison_path), "PDF (*.pdf)"))
    monkeypatch.setattr("pdf_editor.ui.main_window.QInputDialog.getText",
        lambda *args: ("", False))
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)

        window.choose_comparison_pdf()

        assert window.comparison_dialog is None
        assert window.comparison_pdf is None
    finally:
        window.close()


def test_choose_comparison_pdf_reports_wrong_password(
        qtbot, source_path, pdf_bytes, tmp_path, monkeypatch):
    comparison_path = tmp_path / "加密比較.pdf"
    comparison_path.write_bytes(_encrypted_pdf(pdf_bytes))
    errors = []
    monkeypatch.setattr("pdf_editor.ui.main_window.QFileDialog.getOpenFileName",
        lambda *args: (str(comparison_path), "PDF (*.pdf)"))
    monkeypatch.setattr("pdf_editor.ui.main_window.QInputDialog.getText",
        lambda *args: ("wrong", True))
    window = MainWindow()
    qtbot.addWidget(window)
    window.error = errors.append
    try:
        window.open_document(source_path)

        window.choose_comparison_pdf()

        assert errors and errors[0][0] == "PASSWORD"
        assert window.comparison_dialog is None
    finally:
        window.close()


def test_choose_comparison_pdf_reports_file_read_error(
        qtbot, source_path, tmp_path, monkeypatch):
    missing_path = tmp_path / "不存在.pdf"
    errors = []
    monkeypatch.setattr("pdf_editor.ui.main_window.QFileDialog.getOpenFileName",
        lambda *args: (str(missing_path), "PDF (*.pdf)"))
    window = MainWindow()
    qtbot.addWidget(window)
    window.error = errors.append
    try:
        window.open_document(source_path)

        window.choose_comparison_pdf()

        assert errors and errors[0][0] == "OPEN"
        assert window.comparison_dialog is None
    finally:
        window.close()


def _open_window_for_legacy_stamp(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_document(source_path)
    qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
    qtbot.waitUntil(lambda: not window.jobs.pending, timeout=30000)
    return window


def _accept_first_legacy_candidate(dialog):
    dialog.list.setCurrentRow(0)
    return QDialog.DialogCode.Accepted


def test_window_exposes_legacy_stamp_conversion_only_for_open_idle_document(
        qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        assert window.actions["convert_stamp"].text() == "轉換既有圖章"
        assert not window.actions["convert_stamp"].isEnabled()

        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        assert window.actions["convert_stamp"].isEnabled()

        window.busy = True
        window.refresh_actions()
        assert not window.actions["convert_stamp"].isEnabled()
    finally:
        window.busy = False
        window.close()


def test_window_converts_selected_legacy_stamp_into_layer(
        qtbot, source_path, tmp_path, monkeypatch):
    candidate = _legacy_candidate()
    asset_path = tmp_path / "轉換圖章.png"
    asset_path.write_bytes(candidate.png)
    converted_layer = Overlay(
        "converted-stamp", candidate.page, str(asset_path), candidate.rect, 0)
    calls = []

    def fake_convert(pdf, selected, asset_root):
        calls.append((selected, asset_root))
        return pdf, converted_layer

    monkeypatch.setattr(main_window.Jobs, "submit", _submit_synchronously)
    monkeypatch.setattr(main_window, "find_convertible_images", lambda pdf: (candidate,))
    monkeypatch.setattr(main_window, "convert_legacy_image", fake_convert)
    monkeypatch.setattr(main_window.LegacyStampDialog, "exec",
        _accept_first_legacy_candidate)
    window = _open_window_for_legacy_stamp(qtbot, source_path)
    try:
        window.convert_legacy_stamp()

        assert window.session.overlays == (converted_layer,)
        assert window.layer_id == converted_layer.id
        assert window.session.dirty
        assert calls == [(candidate, window.asset_root)]
        assert not window.busy
        assert window.statusBar().currentMessage() == "已轉換為可編輯圖章。"
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_window_reports_when_no_legacy_stamp_candidate_exists(
        qtbot, source_path, monkeypatch):
    monkeypatch.setattr(main_window.Jobs, "submit", _submit_synchronously)
    monkeypatch.setattr(main_window, "find_convertible_images", lambda pdf: ())
    window = _open_window_for_legacy_stamp(qtbot, source_path)
    try:
        before = window.session.history.index

        window.convert_legacy_stamp()

        assert window.session.history.index == before
        assert window.session.overlays == ()
        assert not window.busy
        assert window.actions["convert_stamp"].isEnabled()
        assert "沒有可安全轉換" in window.statusBar().currentMessage()
    finally:
        window.close()


def test_window_cancelled_legacy_stamp_conversion_keeps_document_unchanged(
        qtbot, source_path, monkeypatch):
    candidate = _legacy_candidate()
    monkeypatch.setattr(main_window.Jobs, "submit", _submit_synchronously)
    monkeypatch.setattr(main_window, "find_convertible_images", lambda pdf: (candidate,))
    monkeypatch.setattr(main_window.LegacyStampDialog, "exec",
        lambda dialog: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(main_window, "convert_legacy_image",
        lambda *args: pytest.fail("取消後不應執行轉換"))
    window = _open_window_for_legacy_stamp(qtbot, source_path)
    try:
        before = window.session.history.index

        window.convert_legacy_stamp()

        assert window.session.history.index == before
        assert window.session.overlays == ()
        assert not window.busy
        assert window.actions["convert_stamp"].isEnabled()
    finally:
        window.close()


def test_window_ignores_stale_legacy_stamp_scan_result(
        qtbot, source_path, monkeypatch):
    candidate = _legacy_candidate()
    opened_dialogs = []
    monkeypatch.setattr(main_window.LegacyStampDialog, "__init__",
        lambda self, candidates, parent=None: opened_dialogs.append(tuple(candidates)))
    window = _open_window_for_legacy_stamp(qtbot, source_path)
    try:
        pending = _defer_window_jobs(window)
        window.convert_legacy_stamp()
        window.token += 1

        pending.pop(0)[2]((candidate,))

        assert opened_dialogs == []
        assert window.session.overlays == ()
        assert not window.busy
    finally:
        window.close()


def test_window_ignores_legacy_stamp_conversion_after_revision_changes(
        qtbot, source_path, tmp_path, monkeypatch):
    candidate = _legacy_candidate()
    asset_path = tmp_path / "過期圖章.png"
    asset_path.write_bytes(candidate.png)
    converted_layer = Overlay(
        "stale-stamp", candidate.page, str(asset_path), candidate.rect, 0)
    monkeypatch.setattr(main_window.LegacyStampDialog, "exec",
        _accept_first_legacy_candidate)
    window = _open_window_for_legacy_stamp(qtbot, source_path)
    try:
        pending = _defer_window_jobs(window)
        window.convert_legacy_stamp()
        pending.pop(0)[2]((candidate,))
        assert len(pending) == 1

        window.session.set_overlays(())
        pending.pop(0)[2]((window.session.pdf, converted_layer))

        assert converted_layer not in window.session.overlays
        assert window.layer_id is None
        assert not window.busy
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_window_legacy_stamp_scan_failure_restores_controls(
        qtbot, source_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning",
        lambda parent, title, message: warnings.append((title, message)))
    window = _open_window_for_legacy_stamp(qtbot, source_path)
    try:
        pending = _defer_window_jobs(window)
        window.convert_legacy_stamp()

        pending.pop(0)[3](("STAMP_CONVERSION", "無法掃描圖章。", ()))

        assert not window.busy
        assert window.actions["convert_stamp"].isEnabled()
        assert warnings == [("無法完成操作", "無法掃描圖章。")]
    finally:
        window.close()
