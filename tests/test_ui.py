import hashlib
import io
from pathlib import Path
from concurrent.futures import Future
from dataclasses import replace
import pytest
import pymupdf
from PIL import Image
from PySide6.QtCore import Qt, QPoint, QPointF, QItemSelectionModel, QEvent, QCoreApplication
from PySide6.QtGui import QKeySequence, QMouseEvent
from PySide6.QtWidgets import QAbstractItemView,QDialog,QToolBar
from PySide6.QtTest import QSignalSpy
from pdf_editor.ui.main_window import MainWindow
import pdf_editor.ui.main_window as main_window
import pdf_editor.ui.stamp_actions as stamp_actions
from pdf_editor.document.session import DocumentSession
from pdf_editor.engine.render import render_page
from pdf_editor.engine.geometry import transform_point, inverse_transform
from pdf_editor.ui.signature_dialog import SignatureDialog
from pdf_editor.ui.canvas import Canvas
from pdf_editor.ui.text_panel import TextPanel
from pdf_editor.engine.fonts import default_font
from pdf_editor.errors import EditorError
from pdf_editor.annotations import mark_text
from pdf_editor.model import DocumentAccess, LegacyImageCandidate, Overlay
from pdf_editor.ocr import OcrResult
from pdf_editor.comparison import PageComparison, compare_pages


def _legacy_candidate(page=0, xref=17, rect=(20, 30, 100, 70)):
    stream = io.BytesIO()
    Image.new("RGBA", (80, 40), (30, 70, 210, 255)).save(stream, format="PNG")
    return LegacyImageCandidate(xref, page, rect, stream.getvalue(), 80, 40)


NOTO = Path(__file__).parents[1] / "resources/fonts/NotoSansCJKtc-Regular.otf"


def _stamp_page_pdf():
    """一頁含可編輯文字與唯一圖章（不與文字重疊）的 PDF。"""
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (0, 60, 255)).save(buffer, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=500, height=400)
        page.insert_font(fontname="noto", fontfile=str(NOTO))
        page.insert_text((40, 80), "品質檢驗 ABC 123", fontname="noto", fontsize=16)
        page.insert_image((210, 260, 270, 300), stream=buffer.getvalue())
        document.subset_fonts(fallback=True)
        return document.tobytes(garbage=4, deflate=True)


def _view_point(canvas, x, y):
    return canvas.mapFromScene(QPointF(*transform_point(canvas.matrix, x, y)))


def _hover(canvas, point, buttons=Qt.MouseButton.NoButton):
    """送出滑鼠移動事件（qtbot.mouseMove 在此環境不會送達檢視區）；buttons 為按住的按鍵。"""
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(point),
        QPointF(canvas.viewport().mapToGlobal(point)), Qt.MouseButton.NoButton,
        buttons, Qt.KeyboardModifier.NoModifier)
    QCoreApplication.sendEvent(canvas.viewport(), event)


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

    page_item = next(item for item in canvas.scene().items() if hasattr(item, "pixmap"))
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


def test_canvas_hover_and_click_on_editable_image(qtbot):
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    data = render_page(_stamp_page_pdf(), 0, 1.0)
    canvas.display(data)
    qtbot.waitExposed(canvas)
    spy = QSignalSpy(canvas.image_clicked)
    center = _view_point(canvas, 240, 280)

    _hover(canvas, center)
    assert canvas.image_hover is not None
    assert canvas.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor

    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=center)
    assert spy.count() == 1
    assert spy.at(0)[0] == data["images"][0]

    _hover(canvas, _view_point(canvas, 450, 380))
    assert canvas.image_hover is None
    assert canvas.viewport().cursor().shape() != Qt.CursorShape.PointingHandCursor


def test_canvas_hover_keeps_pointing_cursor_when_moving_from_layer_to_image(qtbot, tmp_path):
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (100, 80), (210, 35, 45, 255)).save(stamp)
    layer = Overlay("stamp", 0, str(stamp), (140, 260, 200, 300), 0)
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    canvas.display(render_page(_stamp_page_pdf(), 0, 1.0), (layer,), None)
    qtbot.waitExposed(canvas)

    _hover(canvas, _view_point(canvas, 170, 280))
    assert canvas.image_hover is None
    _hover(canvas, _view_point(canvas, 240, 280))

    assert canvas.image_hover is not None
    assert canvas.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_canvas_dragging_layer_across_image_shows_no_image_hover(qtbot, tmp_path):
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (100, 80), (210, 35, 45, 255)).save(stamp)
    layer = Overlay("stamp", 0, str(stamp), (140, 260, 200, 300), 0)
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    canvas.display(render_page(_stamp_page_pdf(), 0, 1.0), (layer,), None)
    qtbot.waitExposed(canvas)

    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=_view_point(canvas, 170, 280))
    # 按住按鍵一次拖到圖片上方：圖層尚未跟上游標，也不能出現圖片提示或手指游標。
    _hover(canvas, _view_point(canvas, 240, 280), Qt.MouseButton.LeftButton)

    assert canvas.image_hover is None
    assert canvas.viewport().cursor().shape() != Qt.CursorShape.PointingHandCursor
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=_view_point(canvas, 240, 280))


def test_canvas_right_click_does_not_report_image_or_background(qtbot):
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    canvas.display(render_page(_stamp_page_pdf(), 0, 1.0))
    qtbot.waitExposed(canvas)
    images = QSignalSpy(canvas.image_clicked)
    blank = QSignalSpy(canvas.background_clicked)

    for point in (_view_point(canvas, 240, 280), _view_point(canvas, 450, 380)):
        qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.RightButton, pos=point)
        qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=point)

    assert (images.count(), blank.count()) == (0, 0)


def test_canvas_prefers_text_over_image_and_reports_background_click(qtbot, pdf_bytes):
    # 共用測試 PDF 的圖片實際範圍約 (101,40,329,120)（等比例置中），其上疊有文字。
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    data = render_page(pdf_bytes, 0, 1.0)
    canvas.display(data)
    qtbot.waitExposed(canvas)
    assert data["images"]
    images = QSignalSpy(canvas.image_clicked)
    runs = QSignalSpy(canvas.run_selected)
    blank = QSignalSpy(canvas.background_clicked)
    run = next(r for r in data["runs"] if "品質" in r.text and r.rect[1] < 120)

    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton,
        pos=_view_point(canvas, (run.rect[0] + run.rect[2]) / 2, (run.rect[1] + run.rect[3]) / 2))
    assert (runs.count(), images.count()) == (1, 0)

    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=_view_point(canvas, 300, 110))
    assert images.count() == 1

    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=_view_point(canvas, 450, 380))
    assert blank.count() == 1


def test_canvas_layer_uses_device_pixels_on_high_dpi(qtbot,pdf_bytes,tmp_path):
    image_path=tmp_path/"印章.png"
    Image.new("RGBA",(800,400),(210,35,45,210)).save(image_path)
    layer=Overlay("stamp",0,str(image_path),(100,100,180,140),0)
    canvas=Canvas()
    qtbot.addWidget(canvas)

    canvas.display(render_page(pdf_bytes,0,1.25,pixel_ratio=2.0),(layer,),layer.id)

    item=next(item for item in canvas.scene().items() if hasattr(item,"layer"))
    # 圖章須以實體像素繪製，邏輯大小仍與頁面座標一致。
    assert item.pixmap().devicePixelRatio()==2.0
    assert item.pixmap().width()==200
    assert item._content_rect().width()==pytest.approx(100,abs=1)
    assert item.source_pixmap.width()==800


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

    for name in ("zoom_out","zoom_in","previous_page","next_page"):
        # 縮放與翻頁按鈕移到狀態列，預設寬度下必須完整可見。
        button=window.status_buttons[name]
        assert button.defaultAction() is window.actions[name]
        assert button.isVisible()


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

        qtbot.waitUntil(lambda:window.page_data["display_size"][0]==pytest.approx(1250,abs=1),
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


def test_markup_menu_keeps_every_action_clickable_for_editable_document(
        qtbot,source_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        names=("highlight","underline","text_note","select_annotation",
            "highlight_yellow","highlight_green","highlight_pink",
            "highlight_blue","delete_annotation")

        assert [name for name in names if not window.actions[name].isEnabled()]==[]
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


@pytest.mark.parametrize("action,label",[
    ("highlight","螢光標記"),
    ("underline","底線"),
])
def test_markup_action_explains_missing_text_selection(
        qtbot,source_path,action,label):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)

        assert window.actions[action].isEnabled()
        window.actions[action].trigger()

        assert window.statusBar().currentMessage()==(
            f"請先點選要加入{label}的文字；選取後請再次選擇{label}。")
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


@pytest.mark.parametrize("active_action",[
    "add_text","text_note","select_annotation","direct_crop",
])
def test_markup_action_cancels_conflicting_canvas_mode(
        qtbot,source_path,active_action):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        window.actions[active_action].trigger()

        window.actions["highlight"].trigger()

        assert not window.canvas._text_insertion
        assert not window.canvas._note_insertion
        assert not window.canvas._annotation_selection
        assert not window.canvas._crop_mode
        assert not window.actions["add_text"].isChecked()
        assert not window.actions["text_note"].isChecked()
        assert not window.actions["select_annotation"].isChecked()
        assert not window.actions["direct_crop"].isChecked()
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_markup_action_after_note_mode_selects_text_instead_of_adding_note(
        qtbot,source_path,monkeypatch):
    window=MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr("pdf_editor.ui.main_window.QInputDialog.getMultiLineText",
        lambda *args:("",False))
    try:
        window.resize(1100,760)
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        note_requests=QSignalSpy(window.canvas.note_insertion_requested)
        run=next(item for item in window.page_data["runs"] if "品質" in item.text)
        window.actions["text_note"].trigger()

        window.actions["highlight"].trigger()
        x,y=transform_point(window.page_data["matrix"],
            (run.rect[0]+run.rect[2])/2,(run.rect[1]+run.rect[3])/2)
        point=window.canvas.mapFromScene(QPointF(x,y))
        qtbot.mouseClick(window.canvas.viewport(),Qt.MouseButton.LeftButton,pos=point)

        assert note_requests.count()==0
        assert window.run==run
    finally:
        window.canvas.cancel_inline_editor()
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


@pytest.mark.parametrize("action,message",[
    ("highlight_green","請先點選要變更顏色的螢光標記；選取後請再次選擇顏色。"),
    ("delete_annotation","請點選要刪除的註解；選取後按 Delete。"),
])
def test_annotation_management_action_starts_selection_when_none_is_selected(
        qtbot,source_path,action,message):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)

        assert window.actions[action].isEnabled()
        window.actions[action].trigger()

        assert window.actions["select_annotation"].isChecked()
        assert window.canvas._annotation_selection
        assert window.statusBar().currentMessage()==message
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


def test_window_applies_markup_to_selected_noneditable_text(qtbot,source_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        run=next(item for item in window.page_data["runs"] if "品質" in item.text)
        noneditable=replace(run,editable=False)
        window.select_run(noneditable)

        assert window.actions["highlight"].isEnabled()
        assert window.canvas.inline_editor is None
        assert not window.text_panel.isEnabled()
        assert window.statusBar().currentMessage()==(
            "此文字無法安全修改，但仍可加入螢光標記或底線。")
        window.actions["highlight"].trigger()

        qtbot.waitUntil(lambda:not window.busy and window.session.dirty,timeout=30000)
        with pymupdf.open(stream=window.session.pdf,filetype="pdf") as doc:
            assert [item.type[1] for item in (doc[0].annots() or [])]==["Highlight"]
    finally:
        window.session.saved_fingerprint=window.session.history.current[2]
        window.close()


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


def test_highlight_color_action_explains_non_highlight_selection(qtbot,source_path):
    window=MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
        run=next(item for item in window.page_data["runs"] if "品質" in item.text)
        window.submit_annotation(mark_text,(0,run.rect,"underline"),"加入底線")
        qtbot.waitUntil(lambda:not window.busy and window.page_data is not None,timeout=30000)
        window.select_annotation(window.page_data["annotations"][0])

        assert window.actions["highlight_green"].isEnabled()
        window.actions["highlight_green"].trigger()

        assert window.statusBar().currentMessage()=="只有螢光標記可以變更顏色。"
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
    monkeypatch.setattr("pdf_editor.ui.page_actions.ocr_pages", fake_ocr_pages,
        raising=False)
    monkeypatch.setattr("pdf_editor.ui.page_actions.validate_ocr_assets", lambda: tmp_path,
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

    monkeypatch.setattr("pdf_editor.ui.page_actions.ocr_pages", fake_ocr_pages)
    monkeypatch.setattr("pdf_editor.ui.page_actions.validate_ocr_assets", lambda: tmp_path)
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
    monkeypatch.setattr("pdf_editor.ui.page_actions.ocr_pages", fake_ocr_pages,
        raising=False)
    monkeypatch.setattr("pdf_editor.ui.page_actions.validate_ocr_assets", lambda: tmp_path,
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
        assert window.actions["convert_stamp"].text() == "編輯既有圖片…"
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


def _open_stamp_page_window(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "材質證明.pdf"
    path.write_bytes(_stamp_page_pdf())
    monkeypatch.setattr(main_window.Jobs, "submit", _submit_synchronously)
    return _open_window_for_legacy_stamp(qtbot, path)


def _click_first_image(window):
    image = window.page_data["images"][0]
    window.canvas.image_clicked.emit(image)
    return image


def _close_without_prompt(window):
    # 尚未改動的圖片轉換在關閉時會自動還原；先還原再標記已儲存，關閉時才不會詢問是否儲存。
    window.discard_pending_conversion()
    window.session.saved_fingerprint = window.session.history.current[2]
    window.close()


def test_clicking_image_converts_it_into_selected_layer(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        image = _click_first_image(window)

        assert len(window.session.overlays) == 1
        layer = window.session.overlays[0]
        assert (layer.page, layer.rect) == (image.page, image.rect)
        assert window.layer_id == layer.id
        assert window.panels.currentWidget() is window.overlay_panel
        assert not window.busy
    finally:
        _close_without_prompt(window)


def test_clicking_image_moves_selection_from_previous_layer(qtbot, tmp_path, monkeypatch):
    from pdf_editor.ui.canvas import LayerItem

    stamp = tmp_path / "舊章.png"
    Image.new("RGBA", (40, 40), (210, 35, 45, 255)).save(stamp)
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        old = Overlay("old-stamp", 0, str(stamp), (300, 100, 340, 140), 0)
        window.session.set_overlays((old,))
        window.select_layer(old.id)
        window.request_render()

        _click_first_image(window)

        new_id = window.session.overlays[-1].id
        selected = {item.layer.id for item in window.canvas.scene().items()
            if isinstance(item, LayerItem) and item.isSelected()}
        assert selected == {new_id}
    finally:
        _close_without_prompt(window)


def test_blank_click_reverts_unchanged_image_conversion(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)

        window.canvas.background_clicked.emit()

        assert window.session.overlays == ()
        assert not window.session.dirty
        assert not window.session.can_redo
        assert window.layer_id is None
    finally:
        _close_without_prompt(window)


def test_moved_image_layer_is_kept_after_blank_click(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)
        layer = window.session.overlays[0]
        window.move_layer(replace(layer, rect=(100, 100, 160, 140)))

        window.canvas.background_clicked.emit()

        assert window.session.overlays[0].rect == (100, 100, 160, 140)
        assert window.session.dirty
    finally:
        _close_without_prompt(window)


def test_selecting_same_layer_keeps_pending_conversion(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)
        layer = window.session.overlays[0]

        # 開始拖曳轉換後的圖層時會再次選取它，不能因此撤銷。
        window.canvas.layer_selected.emit(layer.id)

        assert window.session.overlays == (layer,)
    finally:
        _close_without_prompt(window)


def test_undo_after_unchanged_image_conversion_only_reverts_it(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)

        window.history_step(False)

        assert window.session.overlays == ()
        assert not window.session.dirty
        assert not window.session.can_undo
        assert not window.session.can_redo
    finally:
        _close_without_prompt(window)


def test_selecting_text_reverts_conversion_and_reselects_text(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)
        run = next(r for r in window.page_data["runs"] if "品質" in r.text)

        window.select_run(run)

        assert window.session.overlays == ()
        assert window.run is not None and "品質" in window.run.text
    finally:
        _close_without_prompt(window)


def test_save_as_reverts_unchanged_image_conversion_first(qtbot, tmp_path, monkeypatch):
    target = tmp_path / "另存.pdf"
    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName",
        lambda *args, **kwargs: (str(target), "PDF (*.pdf)"))
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)

        window.save()

        assert window.session.overlays == ()
        assert target.exists()
        assert not window.session.dirty
    finally:
        _close_without_prompt(window)


def test_failed_image_click_reports_reason_in_status_bar(qtbot, tmp_path, monkeypatch):
    def reject(*_args):
        raise EditorError("STAMP_CONVERSION", "這張圖片在文件中重複使用，無法單獨編輯。")

    monkeypatch.setattr(stamp_actions, "convert_image_at", reject)
    monkeypatch.setattr(main_window.QMessageBox, "warning",
        lambda *args: pytest.fail("點圖片失敗不應跳出對話框"))
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)

        assert window.session.overlays == ()
        assert "重複使用" in window.statusBar().currentMessage()
        assert not window.busy
    finally:
        _close_without_prompt(window)


def test_render_reuses_cached_editable_images_when_zooming(qtbot, tmp_path, monkeypatch):
    calls = []
    real_render = main_window.render_page

    def spy_render(*args):
        calls.append(args)
        return real_render(*args)

    monkeypatch.setattr(main_window, "render_page", spy_render)
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        images = window.page_data["images"]
        assert images
        calls.clear()

        window.zoom_by(1)

        assert calls and calls[-1][4] is False
        assert window.page_data["images"] == images
    finally:
        _close_without_prompt(window)


def test_unchanged_conversion_is_kept_when_history_was_trimmed(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)
        monkeypatch.setattr(window.session, "can_discard", lambda count=1: False)

        window.canvas.background_clicked.emit()

        assert len(window.session.overlays) == 1
        assert window.pending_conversion is None
    finally:
        _close_without_prompt(window)


class _DeferredJobs:
    """把視窗的背景工作排隊，flush() 時才依序執行，用來重現非同步的時序問題。"""

    def __init__(self, window):
        self.queue = []
        window.jobs.submit = self.submit

    def submit(self, function, arguments, success, failure):
        self.queue.append((function, arguments, success, failure))

    def flush(self):
        while self.queue:
            function, arguments, success, failure = self.queue.pop(0)
            try:
                result = function(*arguments)
            except Exception as exc:
                failure((getattr(exc, "code", "ERROR"), str(exc), ()))
                continue
            success(result)


def _two_stamp_pdf():
    """第 1 頁有文字、兩張可編輯圖章與螢光標記，並留有未被引用的物件；第 2 頁只有文字。"""
    with pymupdf.open() as document:
        page = document.new_page(width=500, height=400)
        page.insert_font(fontname="noto", fontfile=str(NOTO))
        page.insert_text((40, 80), "品質檢驗 ABC 123", fontname="noto", fontsize=16)
        for rect, color in (((210, 260, 270, 300), (0, 60, 255)), ((320, 260, 380, 300), (255, 60, 0))):
            buffer = io.BytesIO()
            Image.new("RGB", (60, 40), color).save(buffer, format="PNG")
            page.insert_image(rect, stream=buffer.getvalue())
        page.insert_text((40, 160), "MARKED TEXT", fontsize=16)
        second = document.new_page(width=500, height=400)
        second.insert_text((40, 80), "PAGE TWO", fontsize=16)
        document.subset_fonts(fallback=True)
        compact = document.tobytes(garbage=4, deflate=True)
    with pymupdf.open(stream=compact, filetype="pdf") as document:
        # 增量儲存的實際文件常留有未被引用的物件，且位於註解之前。
        orphan = document.get_new_xref()
        document.update_object(orphan, "<</Orphan true>>")
        document[0].add_highlight_annot(pymupdf.Rect(38, 144, 160, 164))
        document[0].add_text_annot((440, 40), "註解")
        return document.tobytes(deflate=True)


def _open_deferred_window(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "兩張圖章.pdf"
    path.write_bytes(_two_stamp_pdf())
    monkeypatch.setattr(main_window.Jobs, "submit", _submit_synchronously)
    window = _open_window_for_legacy_stamp(qtbot, path)
    window.resize(1000, 800)
    window.show()
    qtbot.waitExposed(window)
    return window, _DeferredJobs(window)


def _click_image_at(window, jobs, rect):
    image = next(item for item in window.page_data["images"] if item.rect == rect)
    window.canvas.image_clicked.emit(image)
    jobs.flush()
    return image


def _close_deferred(window, jobs):
    jobs.flush()
    _close_without_prompt(window)


def _page_text(pdf, page=0):
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        return document[page].get_text()


FIRST_STAMP = (210, 260, 270, 300)
SECOND_STAMP = (320, 260, 380, 300)


def test_fast_text_drag_after_pending_conversion_keeps_text(qtbot, tmp_path, monkeypatch):
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_image_at(window, jobs, FIRST_STAMP)
        run = next(r for r in window.page_data["runs"] if "品質" in r.text)
        start = _view_point(window.canvas, (run.rect[0] + run.rect[2]) / 2,
            (run.rect[1] + run.rect[3]) / 2)

        # 在重新渲染前就按住文字拖曳並放開。
        qtbot.mousePress(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        qtbot.mouseRelease(window.canvas.viewport(), Qt.MouseButton.LeftButton,
            pos=start + QPoint(40, 30))
        jobs.flush()

        assert window.session.overlays == ()
        assert "品質檢驗" in _page_text(window.session.pdf)
        assert not window.session.dirty
    finally:
        _close_deferred(window, jobs)


def test_move_run_ignores_run_that_is_not_selected(qtbot, tmp_path, monkeypatch):
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        run = next(r for r in window.page_data["runs"] if "品質" in r.text)
        window.run = None

        window.move_run(replace(run, rect=(80, 120, 260, 140)))
        jobs.flush()

        assert not window.session.dirty
        assert "品質檢驗" in _page_text(window.session.pdf)
    finally:
        _close_deferred(window, jobs)


def test_selecting_annotation_after_revert_keeps_same_annotation(qtbot, tmp_path, monkeypatch):
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        original = next(item for item in window.page_data["annotations"] if item.kind == "Highlight")
        _click_image_at(window, jobs, FIRST_STAMP)
        stale = next(item for item in window.page_data["annotations"] if item.kind == "Highlight")

        window.select_annotation(stale)
        jobs.flush()

        assert window.session.overlays == ()
        assert window.annotation is not None
        assert (window.annotation.xref, window.annotation.kind) == (original.xref, "Highlight")
    finally:
        _close_deferred(window, jobs)


def test_dragging_other_layer_while_conversion_pending_applies_move(qtbot, tmp_path, monkeypatch):
    stamp = tmp_path / "其他章.png"
    Image.new("RGBA", (40, 40), (210, 35, 45, 255)).save(stamp)
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        other = Overlay("other-stamp", 0, str(stamp), (300, 100, 340, 140), 0)
        window.session.set_overlays((other,))
        window.request_render()
        jobs.flush()
        before = window.session.history.index
        _click_image_at(window, jobs, FIRST_STAMP)
        canvas = window.canvas
        start = _view_point(canvas, 320, 120)
        end = _view_point(canvas, 370, 170)

        qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        _hover(canvas, end, Qt.MouseButton.LeftButton)
        # 背景工作在拖曳途中完成，不能因此重建畫面而讓拖曳失效。
        jobs.flush()
        qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
        jobs.flush()

        assert [layer.id for layer in window.session.overlays] == ["other-stamp"]
        moved = window.session.overlays[0]
        assert moved.rect[0] > other.rect[0] + 10 and moved.rect[1] > other.rect[1] + 10
        assert window.session.history.index == before + 1
        assert window.session.history.items[before][1] == (other,)
        assert FIRST_STAMP in [item.rect for item in window.page_data["images"]]
    finally:
        _close_deferred(window, jobs)


def test_stale_render_failure_is_only_logged(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(main_window.QMessageBox, "warning",
        lambda *args: pytest.fail("過時的渲染失敗不應跳出對話框"))
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        window.request_render()
        window.request_render()
        stale_failure = jobs.queue[0][3]
        window.busy = True

        stale_failure(("RENDER", "暫存檔已被刪除", ()))

        assert window.busy
    finally:
        window.busy = False
        _close_deferred(window, jobs)


def test_failure_applying_clicked_image_is_reported(qtbot, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args: warnings.append(args))
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        def broken_apply(*_args):
            raise RuntimeError("寫入暫存檔失敗")

        monkeypatch.setattr(window.session, "apply_state", broken_apply)

        _click_image_at(window, jobs, FIRST_STAMP)

        assert warnings
        assert not window.busy
        assert window.pending_conversion is None
    finally:
        monkeypatch.delattr(window.session, "apply_state")
        _close_deferred(window, jobs)


def test_text_click_revert_reopens_text_editor(qtbot, tmp_path, monkeypatch):
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_image_at(window, jobs, FIRST_STAMP)
        run = next(r for r in window.page_data["runs"] if "品質" in r.text)

        window.select_run(run)
        jobs.flush()

        assert window.session.overlays == ()
        assert window.run is not None and "品質" in window.run.text
        assert window.canvas.inline_editor is not None
        assert not window.text_panel.info.text().startswith("已套用")
    finally:
        _close_deferred(window, jobs)


def test_reopen_reselect_includes_non_editable_text(qtbot, tmp_path, monkeypatch):
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        run = replace(next(r for r in window.page_data["runs"] if "品質" in r.text), editable=False)
        window._reselect_after_render = (0, tuple(run.rect), run.text, True)

        window.reselect_text_after_render({"page": 0, "runs": (run,)})

        assert window.run == run
        assert "無法安全修改" in window.text_panel.info.text()
    finally:
        _close_deferred(window, jobs)


def test_stale_pending_conversion_does_not_clear_other_reselect(qtbot, tmp_path, monkeypatch):
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_image_at(window, jobs, FIRST_STAMP)
        layer = window.session.overlays[0]
        window.move_layer(replace(layer, rect=(100, 100, 160, 140)))
        jobs.flush()
        run = next(r for r in window.page_data["runs"] if "品質" in r.text)
        other = (0, (1, 2, 3, 4), "其他流程", False)
        window._reselect_after_render = other

        window.select_run(run)

        assert window._reselect_after_render == other
        assert window.session.overlays[0].rect == (100, 100, 160, 140)
    finally:
        window._reselect_after_render = None
        _close_deferred(window, jobs)


def test_two_clicked_images_are_both_reverted_by_blank_click(qtbot, tmp_path, monkeypatch):
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        before = window.session.history.index
        _click_image_at(window, jobs, FIRST_STAMP)
        _click_image_at(window, jobs, SECOND_STAMP)
        assert len(window.session.overlays) == 2

        window.canvas.background_clicked.emit()
        jobs.flush()

        assert window.session.overlays == ()
        assert window.session.history.index == before
        assert not window.session.can_redo
        assert not window.session.dirty
        assert {item.rect for item in window.page_data["images"]} == {FIRST_STAMP, SECOND_STAMP}
    finally:
        _close_deferred(window, jobs)


def test_page_change_reverts_and_renders_only_new_page(qtbot, tmp_path, monkeypatch):
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_image_at(window, jobs, FIRST_STAMP)

        window.goto_page(1)

        assert window.session.overlays == ()
        assert not window.session.dirty
        renders = [item for item in jobs.queue if item[0] is main_window.render_page]
        assert [item[1][1] for item in renders] == [1]
        jobs.flush()
        assert window.page_data["page"] == 1
    finally:
        _close_deferred(window, jobs)


def test_closing_with_unchanged_conversion_does_not_prompt(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(main_window.QMessageBox, "question",
        lambda *args, **kwargs: pytest.fail("未改動的圖片轉換不應詢問是否儲存"))
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    _click_image_at(window, jobs, FIRST_STAMP)
    assert window.session.dirty

    window.close()

    assert window.closed


def _layer_drag_window(qtbot, tmp_path, monkeypatch):
    """開好視窗並放一個其他圖層；回傳視窗、延後工作與該圖層。"""
    stamp = tmp_path / "其他章.png"
    Image.new("RGBA", (40, 40), (210, 35, 45, 255)).save(stamp)
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    other = Overlay("other-stamp", 0, str(stamp), (300, 100, 340, 140), 0)
    window.session.set_overlays((other,))
    window.request_render()
    jobs.flush()
    return window, jobs, other


def _click_image_leaving_render_queued(window, jobs):
    """只執行點圖片的轉換工作，轉換後的重繪留在佇列中（尚未完成）。"""
    image = next(item for item in window.page_data["images"] if item.rect == FIRST_STAMP)
    window.canvas.image_clicked.emit(image)
    function, arguments, success, failure = jobs.queue.pop(0)
    success(function(*arguments))
    assert [item[0] for item in jobs.queue] == [main_window.render_page]


def _assert_other_layer_moved(window, other):
    assert [layer.id for layer in window.session.overlays] == ["other-stamp"]
    moved = window.session.overlays[0]
    assert moved.rect[0] > other.rect[0] + 10 and moved.rect[1] > other.rect[1] + 10
    assert FIRST_STAMP in [item.rect for item in window.page_data["images"]]


def test_in_flight_render_failing_on_deleted_file_is_ignored_during_drag(
        qtbot, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args: warnings.append(args))
    window, jobs, other = _layer_drag_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_image_leaving_render_queued(window, jobs)
        canvas = window.canvas
        start, end = _view_point(canvas, 320, 120), _view_point(canvas, 370, 170)

        qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        _hover(canvas, end, Qt.MouseButton.LeftButton)
        # 轉換後的重繪此時才執行：暫存檔已被自動還原刪除，失敗結果必須當成過時。
        jobs.flush()
        qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
        jobs.flush()

        _assert_other_layer_moved(window, other)
        assert warnings == []
        assert not window.busy
    finally:
        _close_deferred(window, jobs)


def test_in_flight_render_completing_during_drag_does_not_rebuild_scene(
        qtbot, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args: warnings.append(args))
    window, jobs, other = _layer_drag_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_image_leaving_render_queued(window, jobs)
        function, arguments, success, _failure = jobs.queue.pop(0)
        stale_result = function(*arguments)
        canvas = window.canvas
        start, end = _view_point(canvas, 320, 120), _view_point(canvas, 370, 170)

        qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        _hover(canvas, end, Qt.MouseButton.LeftButton)
        # 轉換後的重繪在拖曳途中完成，不能重建（已過時的）畫面而讓拖曳失效。
        success(stale_result)
        qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
        jobs.flush()

        _assert_other_layer_moved(window, other)
        assert warnings == []
    finally:
        _close_deferred(window, jobs)


def test_image_click_does_not_leave_canvas_marked_as_pressed(qtbot, tmp_path, monkeypatch):
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        point = _view_point(window.canvas, 240, 280)

        qtbot.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)

        assert window.busy
        assert not window.canvas.pointer_pressed
        jobs.flush()
        assert len(window.session.overlays) == 1
    finally:
        _close_deferred(window, jobs)


def test_deferred_render_runs_when_canvas_is_disabled_mid_press(qtbot, tmp_path, monkeypatch):
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        window._render_after_release = True
        window.canvas.pointer_pressed = True

        # 按住期間畫布被停用時放開事件不會送達；改在停用時補做延後的重繪。
        window.canvas.setEnabled(False)

        assert not window.canvas.pointer_pressed
        assert not window._render_after_release
        assert [item[0] for item in jobs.queue] == [main_window.render_page]
    finally:
        window.canvas.setEnabled(True)
        _close_deferred(window, jobs)


def test_text_press_keeps_drag_when_conversion_cannot_be_reverted(qtbot, tmp_path, monkeypatch):
    window, jobs = _open_deferred_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_image_at(window, jobs, FIRST_STAMP)
        monkeypatch.setattr(window.session, "can_discard", lambda count=1: False)
        run = next(r for r in window.page_data["runs"] if "品質" in r.text)
        point = _view_point(window.canvas, (run.rect[0] + run.rect[2]) / 2,
            (run.rect[1] + run.rect[3]) / 2)

        qtbot.mousePress(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
        try:
            # 無法還原時照常選取文字，畫布上的文字拖曳不能被取消。
            assert window.canvas.selected_run is not None
            assert len(window.session.overlays) == 1
        finally:
            qtbot.mouseRelease(window.canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
    finally:
        monkeypatch.delattr(window.session, "can_discard")
        _close_deferred(window, jobs)


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
    monkeypatch.setattr(stamp_actions, "find_convertible_images", lambda pdf: (candidate,))
    monkeypatch.setattr(stamp_actions, "convert_legacy_image", fake_convert)
    monkeypatch.setattr(stamp_actions.LegacyStampDialog, "exec",
        _accept_first_legacy_candidate)
    window = _open_window_for_legacy_stamp(qtbot, source_path)
    try:
        window.convert_legacy_stamp()

        assert window.session.overlays == (converted_layer,)
        assert window.layer_id == converted_layer.id
        assert window.session.dirty
        assert calls == [(candidate, window.asset_root)]
        assert not window.busy
        assert window.statusBar().currentMessage() == "已轉為可編輯圖片，可拖曳移動或縮放。"
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_window_save_reopens_stamp_as_editable_overlay(
        qtbot, source_path, tmp_path, monkeypatch):
    target = tmp_path / "介面另存.pdf"
    stamp = tmp_path / "介面圖章.png"
    Image.new("RGBA", (20, 20), (0, 40, 255, 200)).save(stamp)
    window = _open_window_for_legacy_stamp(qtbot, source_path)
    try:
        layer = Overlay("ui-stamp", 0, str(stamp), (200, 180, 240, 220), 0)
        window.session.set_overlays((layer,))
        monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName",
            lambda *args: (str(target), "PDF (*.pdf)"))

        window.save()

        qtbot.waitUntil(
            lambda: not window.busy and not window.jobs.pending and target.exists(),
            timeout=30000,
        )
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()
        with DocumentSession.open(target) as reopened:
            assert len(reopened.overlays) == 1
            assert reopened.overlays[0].id == "ui-stamp"
            assert reopened.overlays[0].rect == (200, 180, 240, 220)
    finally:
        if window.session is not None:
            window.session.saved_fingerprint = window.session.history.current[2]
            window.close()


def test_window_reports_when_no_legacy_stamp_candidate_exists(
        qtbot, source_path, monkeypatch):
    monkeypatch.setattr(main_window.Jobs, "submit", _submit_synchronously)
    monkeypatch.setattr(stamp_actions, "find_convertible_images", lambda pdf: ())
    window = _open_window_for_legacy_stamp(qtbot, source_path)
    try:
        before = window.session.history.index

        window.convert_legacy_stamp()

        assert window.session.history.index == before
        assert window.session.overlays == ()
        assert not window.busy
        assert window.actions["convert_stamp"].isEnabled()
        assert "沒有可單獨編輯的圖片" in window.statusBar().currentMessage()
    finally:
        window.close()


def test_window_cancelled_legacy_stamp_conversion_keeps_document_unchanged(
        qtbot, source_path, monkeypatch):
    candidate = _legacy_candidate()
    monkeypatch.setattr(main_window.Jobs, "submit", _submit_synchronously)
    monkeypatch.setattr(stamp_actions, "find_convertible_images", lambda pdf: (candidate,))
    monkeypatch.setattr(stamp_actions.LegacyStampDialog, "exec",
        lambda dialog: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(stamp_actions, "convert_legacy_image",
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
    monkeypatch.setattr(stamp_actions.LegacyStampDialog, "__init__",
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
    monkeypatch.setattr(stamp_actions.LegacyStampDialog, "exec",
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


def test_text_panel_font_combo_has_default_noto_first(qtbot):
    panel = TextPanel()
    qtbot.addWidget(panel)
    assert panel.font_combo.itemData(0) == str(default_font())
    assert panel.font_path == str(default_font())


def test_text_panel_bold_checkbox_emits_format(qtbot):
    panel = TextPanel()
    qtbot.addWidget(panel)
    spy = QSignalSpy(panel.format_requested)
    panel.bold.setChecked(True)
    assert spy.count() == 1


def test_text_panel_shows_glyph_warning_for_missing_chars(qtbot, monkeypatch):
    import types
    panel = TextPanel()
    qtbot.addWidget(panel)
    panel.text.setPlainText("缺字測試")
    monkeypatch.setattr("pdf_editor.ui.text_panel.pymupdf.Font",
        lambda **_: types.SimpleNamespace(has_glyph=lambda code: False))
    panel._refresh_glyph_warning()
    assert not panel.glyph_warning.isHidden()
    assert "缺少" in panel.glyph_warning.text()


def test_text_panel_hides_glyph_warning_when_complete(qtbot, monkeypatch):
    import types
    panel = TextPanel()
    qtbot.addWidget(panel)
    panel.text.setPlainText("完整文字")
    monkeypatch.setattr("pdf_editor.ui.text_panel.pymupdf.Font",
        lambda **_: types.SimpleNamespace(has_glyph=lambda code: True))
    panel._refresh_glyph_warning()
    assert panel.glyph_warning.isHidden()


def test_text_panel_shows_load_failure_warning(qtbot, monkeypatch):
    panel = TextPanel()
    qtbot.addWidget(panel)
    panel.text.setPlainText("測試")
    monkeypatch.setattr("pdf_editor.ui.text_panel.pymupdf.Font",
        lambda **_: (_ for _ in ()).throw(RuntimeError("無法開啟")))
    panel._refresh_glyph_warning()
    assert "無法讀取字型檔" in panel.glyph_warning.text()


def test_window_inline_edit_applies_bold_when_checked(qtbot, source_path, monkeypatch):
    monkeypatch.setattr("pdf_editor.engine.text.resolve_bold", lambda path: None)
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(item for item in window.page_data["runs"] if "品質" in item.text)
        window.select_run(run)
        window.canvas.inline_editor.setText("粗體測試")
        window.text_panel.bold.setChecked(True)
        qtbot.waitUntil(lambda: not window.busy and window.session.dirty, timeout=30000)
        with pymupdf.open(stream=window.session.pdf) as doc:
            assert "粗體測試" in doc[0].get_text()
            # 有同族粗體字型檔時使用真粗體，否則以描邊模擬粗體。
            fonts=" ".join(font[3] for font in doc[0].get_fonts()).lower()
            assert b"2 Tr" in doc[0].read_contents() or "bold" in fonts
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_signature_png_is_high_resolution_and_cropped(qtbot):
    dialog = SignatureDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mousePress(dialog.pad, Qt.MouseButton.LeftButton, pos=QPoint(100,60))
    qtbot.mouseMove(dialog.pad, QPoint(200,110))
    qtbot.mouseRelease(dialog.pad, Qt.MouseButton.LeftButton, pos=QPoint(200,110))
    with Image.open(io.BytesIO(dialog.png_bytes())) as image:
        # 筆跡範圍約 100×50 邏輯像素，應以超取樣倍率輸出且裁掉空白。
        factor = dialog.pad.SUPERSAMPLE
        assert image.width >= 100 * factor
        assert image.width < 600 * factor
        assert image.height < 220 * factor


def test_read_only_document_lists_no_clickable_images(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        window.resize(1000, 800)
        window.show()
        qtbot.waitExposed(window)
        window.session.access = DocumentAccess(False, False, "此文件僅供閱讀")
        window.request_render()
        clicked = QSignalSpy(window.canvas.image_clicked)

        assert window.page_data["images"] == ()
        _hover(window.canvas, _view_point(window.canvas, 240, 280))
        assert window.canvas.image_hover is None
        qtbot.mouseClick(window.canvas.viewport(), Qt.MouseButton.LeftButton,
            pos=_view_point(window.canvas, 240, 280))
        assert clicked.count() == 0

        # 唯讀判斷不能污染快取：回到可編輯後仍要有可點選的圖片。
        window.session.access = DocumentAccess(True, True, None)
        window.request_render()
        assert window.page_data["images"]
    finally:
        _close_without_prompt(window)


def test_list_entry_discards_unchanged_click_conversion(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)
        assert len(window.session.overlays) == 1
        monkeypatch.setattr(stamp_actions, "find_convertible_images", lambda pdf: ())

        window.convert_legacy_stamp()

        assert window.session.overlays == ()
        assert not window.session.dirty
        assert window.pending_conversion is None
    finally:
        _close_without_prompt(window)


def test_clicking_image_is_refused_while_comparison_is_open(
        qtbot, tmp_path, monkeypatch, pdf_bytes):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        image = window.page_data["images"][0]
        window.open_comparison(pdf_bytes)
        dialog = window.comparison_dialog
        revision = window.session.revision

        window.canvas.image_clicked.emit(image)

        assert window.comparison_dialog is dialog
        assert window.session.revision == revision
        assert window.session.overlays == ()
        assert not window.busy
        assert window.statusBar().currentMessage() == "頁面比較開啟中，無法編輯圖片。"
    finally:
        window.close_comparison()
        _close_without_prompt(window)
