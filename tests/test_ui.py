import hashlib
from dataclasses import replace
import pytest
import pymupdf
from PySide6.QtCore import Qt, QPoint, QPointF, QModelIndex
from PySide6.QtWidgets import QAbstractItemView
from PySide6.QtTest import QSignalSpy
from pdf_editor.ui.main_window import MainWindow
from pdf_editor.engine.render import render_page
from pdf_editor.engine.geometry import transform_point, inverse_transform
from pdf_editor.ui.signature_dialog import SignatureDialog
from pdf_editor.ui.canvas import Canvas
from pdf_editor.ui.text_panel import TextPanel
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


def test_window_page_menu_exposes_management_actions(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.page_menu_button.text() == "頁面操作"
    assert [window.actions[name].text() for name in (
        "page_up", "page_down", "rotate_left", "rotate_right", "delete_page"
    )] == ["上移一頁", "下移一頁", "逆時針旋轉", "順時針旋轉", "刪除此頁"]


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
        moved = window.thumbs.model().moveRow(QModelIndex(), 0, QModelIndex(), 3)

        assert moved
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
