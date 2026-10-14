"""以滑鼠左鍵拖曳圈選多個物件，整批刪除或移動。"""
import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent, QCoreApplication
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QSignalSpy

import pdf_editor.ui.main_window as main_window
from pdf_editor.engine.geometry import transform_point
from pdf_editor.engine.render import render_page
from pdf_editor.model import Overlay
from pdf_editor.ui.canvas import Canvas, LayerItem
from pdf_editor.ui.main_window import MainWindow


def _view_point(canvas, x, y):
    return canvas.mapFromScene(QPointF(*transform_point(canvas.matrix, x, y)))


def _move(canvas, point, buttons=Qt.MouseButton.LeftButton):
    """送出滑鼠移動事件（qtbot.mouseMove 在此環境不會送達檢視區）；buttons 為按住的按鍵。"""
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(point),
        QPointF(canvas.viewport().mapToGlobal(point)), Qt.MouseButton.NoButton,
        buttons, Qt.KeyboardModifier.NoModifier)
    QCoreApplication.sendEvent(canvas.viewport(), event)


def _drag(qtbot, canvas, start, end):
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    _move(canvas, start + (end - start) / 2)
    _move(canvas, end)
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)


def _stamp(tmp_path):
    path = tmp_path / "章.png"
    Image.new("RGBA", (100, 80), (210, 35, 45, 255)).save(path)
    return path


def _canvas(qtbot, pdf, layers=(), scale=1.0, size=(700, 600)):
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(*size)
    canvas.show()
    data = render_page(pdf, 0, scale)
    canvas.display(data, layers, None)
    qtbot.waitExposed(canvas)
    return canvas, data


def test_marquee_selects_only_objects_fully_inside(qtbot, pdf_bytes, tmp_path):
    layer = Overlay("stamp", 0, str(_stamp(tmp_path)), (300, 280, 360, 320), 0)
    canvas, _data = _canvas(qtbot, pdf_bytes, (layer,))
    spy = QSignalSpy(canvas.objects_selected)

    _drag(qtbot, canvas, _view_point(canvas, 20, 150), _view_point(canvas, 480, 390))

    assert spy.count() == 1
    selection = spy.at(0)[0]
    assert selection.layers == ("stamp",)
    texts = sorted(run.text for run in selection.runs)
    assert texts[0] == "KEEP ME" and "品質" in texts[1] and len(texts) == 2
    # 上方只被框到一部分的文字不算。
    assert all(run.rect[1] > 150 for run in selection.runs)
    assert canvas.group == selection
    assert canvas._marquee_item is None
    assert next(item for item in canvas.scene().items() if isinstance(item, LayerItem)).isSelected()


def test_left_drag_on_background_no_longer_scrolls_page(qtbot, pdf_bytes):
    canvas, _data = _canvas(qtbot, pdf_bytes, scale=1.5, size=(300, 250))
    before = (canvas.horizontalScrollBar().value(), canvas.verticalScrollBar().value())

    _drag(qtbot, canvas, QPoint(260, 210), QPoint(220, 170))

    assert (canvas.horizontalScrollBar().value(), canvas.verticalScrollBar().value()) == before


def test_dragging_group_member_moves_whole_selection(qtbot, pdf_bytes, tmp_path):
    layer = Overlay("stamp", 0, str(_stamp(tmp_path)), (300, 280, 360, 320), 0)
    canvas, data = _canvas(qtbot, pdf_bytes, (layer,))
    _drag(qtbot, canvas, _view_point(canvas, 20, 150), _view_point(canvas, 480, 390))
    moved = QSignalSpy(canvas.selection_moved)
    run = next(r for r in data["runs"] if r.text == "KEEP ME")
    start = _view_point(canvas, (run.rect[0] + run.rect[2]) / 2, (run.rect[1] + run.rect[3]) / 2)
    item = next(item for item in canvas.scene().items() if isinstance(item, LayerItem))
    origin = item.pos()

    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    _move(canvas, start + QPoint(30, 20))
    assert item.pos() == origin + QPointF(30, 20)
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start + QPoint(30, 20))

    assert moved.count() == 1
    selection, offset = moved.at(0)
    assert selection.layers == ("stamp",)
    assert offset == pytest.approx((30, 20))
    # 重新渲染前先記下新位置，之後才能找回同一批物件。
    assert canvas.group.runs[0].rect[0] == pytest.approx(selection.runs[0].rect[0] + 30)


def test_delete_and_escape_act_on_group(qtbot, pdf_bytes):
    canvas, _data = _canvas(qtbot, pdf_bytes)
    _drag(qtbot, canvas, _view_point(canvas, 20, 150), _view_point(canvas, 480, 390))
    deleted = QSignalSpy(canvas.selection_delete_requested)

    qtbot.keyClick(canvas, Qt.Key.Key_Delete)
    assert deleted.count() == 1
    assert deleted.at(0)[0] == canvas.group

    qtbot.keyClick(canvas, Qt.Key.Key_Escape)
    assert canvas.group is None
    assert canvas._group_items == []


def test_clicking_outside_group_clears_it(qtbot, pdf_bytes):
    canvas, _data = _canvas(qtbot, pdf_bytes)
    _drag(qtbot, canvas, _view_point(canvas, 20, 150), _view_point(canvas, 480, 390))

    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=_view_point(canvas, 450, 30))

    assert canvas.group is None


def test_drag_starting_on_image_selects_instead_of_editing_image(qtbot, pdf_bytes):
    canvas, data = _canvas(qtbot, pdf_bytes)
    assert data["images"]
    images = QSignalSpy(canvas.image_clicked)
    selected = QSignalSpy(canvas.objects_selected)

    # 從圖片上開始往左下拖曳，圈住下方兩段文字。
    _drag(qtbot, canvas, _view_point(canvas, 300, 110), _view_point(canvas, 20, 390))

    assert images.count() == 0
    assert selected.count() == 1


def test_single_layer_marquee_behaves_like_click(qtbot, pdf_bytes, tmp_path):
    layer = Overlay("stamp", 0, str(_stamp(tmp_path)), (300, 280, 360, 320), 0)
    canvas, _data = _canvas(qtbot, pdf_bytes, (layer,))
    layers = QSignalSpy(canvas.layer_selected)
    groups = QSignalSpy(canvas.objects_selected)

    _drag(qtbot, canvas, _view_point(canvas, 290, 270), _view_point(canvas, 370, 330))

    assert (layers.count(), groups.count()) == (1, 0)
    assert canvas.group is None


def test_group_survives_rerender(qtbot, pdf_bytes, tmp_path):
    layer = Overlay("stamp", 0, str(_stamp(tmp_path)), (300, 280, 360, 320), 0)
    canvas, data = _canvas(qtbot, pdf_bytes, (layer,))
    _drag(qtbot, canvas, _view_point(canvas, 20, 150), _view_point(canvas, 480, 390))
    selection = canvas.group

    canvas.display(data, (layer,), None)

    assert canvas.group is not None
    assert canvas.group.layers == selection.layers
    assert [run.text for run in canvas.group.runs] == [run.text for run in selection.runs]
    assert len(canvas._group_items) == len(selection.runs)


# ── 主視窗：整批刪除與移動只留下一筆復原紀錄 ──

def _submit_synchronously(self, function, arguments, success, failure):
    try:
        success(function(*arguments))
    except Exception as exc:
        failure((getattr(exc, "code", "ERROR"), str(exc), ()))


def _marked_pdf():
    with pymupdf.open() as document:
        page = document.new_page(width=500, height=400)
        page.insert_text((40, 80), "STAY HERE", fontsize=16)
        page.insert_text((40, 160), "MARKED TEXT", fontsize=16)
        page.insert_text((40, 200), "SECOND LINE", fontsize=16)
        page.add_highlight_annot(pymupdf.Rect(38, 144, 160, 164))
        page.add_text_annot((440, 40), "註解")
        return document.tobytes(deflate=True)


def _open_window(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "圈選.pdf"
    path.write_bytes(_marked_pdf())
    monkeypatch.setattr(main_window.Jobs, "submit", _submit_synchronously)
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1000, 800)
    window.show()
    window.open_document(path)
    qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
    qtbot.waitExposed(window)
    layer = Overlay("stamp", 0, str(_stamp(tmp_path)), (300, 150, 360, 190), 0)
    window.session.set_overlays((layer,))
    window.request_render()
    return window


def _close(window):
    window.session.saved_fingerprint = window.session.history.current[2]
    window.close()


def _page_state(pdf):
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        page = document[0]
        annotations = sorted(item.type[1] for item in page.annots())
        return page.get_text(), annotations


def test_window_deletes_marquee_selection_in_one_undo_step(qtbot, tmp_path, monkeypatch):
    window = _open_window(qtbot, tmp_path, monkeypatch)
    try:
        canvas = window.canvas
        _drag(qtbot, canvas, _view_point(canvas, 20, 130), _view_point(canvas, 400, 215))
        assert canvas.group is not None and canvas.group.count == 4
        assert "已圈選 4 個物件" in window.statusBar().currentMessage()
        undo_index = window.session.history.index

        qtbot.keyClick(canvas, Qt.Key.Key_Delete)

        text, annotations = _page_state(window.session.pdf)
        assert "MARKED TEXT" not in text and "SECOND LINE" not in text
        assert "STAY HERE" in text
        assert annotations == ["Text"]
        assert window.session.overlays == ()
        assert canvas.group is None
        assert window.session.history.index == undo_index + 1

        window.history_step(False)
        text, annotations = _page_state(window.session.pdf)
        assert "MARKED TEXT" in text and annotations == ["Highlight", "Text"]
        assert len(window.session.overlays) == 1
    finally:
        _close(window)


def test_window_moves_marquee_selection_in_one_undo_step(qtbot, tmp_path, monkeypatch):
    window = _open_window(qtbot, tmp_path, monkeypatch)
    try:
        canvas = window.canvas
        _drag(qtbot, canvas, _view_point(canvas, 20, 130), _view_point(canvas, 400, 215))
        run = next(r for r in window.page_data["runs"] if r.text == "SECOND LINE")
        start = _view_point(canvas, (run.rect[0] + run.rect[2]) / 2, (run.rect[1] + run.rect[3]) / 2)
        end = _view_point(canvas, (run.rect[0] + run.rect[2]) / 2 + 20,
            (run.rect[1] + run.rect[3]) / 2 + 100)
        undo_index = window.session.history.index

        qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        _move(canvas, end)
        qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)

        assert window.session.history.index == undo_index + 1
        runs = {r.text: r.rect for r in window.page_data["runs"]}
        assert runs["SECOND LINE"][1] == pytest.approx(run.rect[1] + 100, abs=2)
        assert runs["MARKED TEXT"][1] > 230
        assert runs["STAY HERE"][1] < 100
        assert window.session.overlays[0].rect[1] == pytest.approx(250, abs=2)
        with pymupdf.open(stream=window.session.pdf, filetype="pdf") as document:
            page = document[0]
            highlight = next(item for item in page.annots() if item.type[1] == "Highlight")
            assert highlight.rect.y0 > 230
        # 移動後仍維持整批選取，可以繼續拖曳或刪除。
        assert canvas.group is not None and canvas.group.count == 4
    finally:
        _close(window)


def test_window_rejects_move_into_unselected_text_and_keeps_layout(qtbot, tmp_path, monkeypatch):
    window = _open_window(qtbot, tmp_path, monkeypatch)
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args: None)
    try:
        canvas = window.canvas
        _drag(qtbot, canvas, _view_point(canvas, 20, 175), _view_point(canvas, 200, 215))
        assert [r.text for r in canvas.group.runs] == ["SECOND LINE"]
        run = next(r for r in window.page_data["runs"] if r.text == "SECOND LINE")
        before = window.session.pdf
        start = _view_point(canvas, run.rect[0] + 5, (run.rect[1] + run.rect[3]) / 2)
        # 往上拖到「MARKED TEXT」上方，與未選取的文字重疊。
        end = _view_point(canvas, run.rect[0] + 5, (run.rect[1] + run.rect[3]) / 2 - 40)
        canvas_group = canvas.group

        qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        _move(canvas, end)
        qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)

        assert window.session.pdf == before
        assert "重疊" in window.statusBar().currentMessage()
        # 失敗後在原位置保留選取。
        assert canvas.group is not None
        assert canvas.group.runs[0].rect == pytest.approx(canvas_group.runs[0].rect)
    finally:
        _close(window)


def test_disabling_canvas_mid_drag_abandons_marquee(qtbot, pdf_bytes):
    canvas, data = _canvas(qtbot, pdf_bytes)
    start = _view_point(canvas, 20, 150)
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    _move(canvas, _view_point(canvas, 480, 390))
    assert canvas._marquee_item is not None

    # 背景工作開始時畫布被停用，放開事件不會送達。
    canvas.setEnabled(False)
    canvas.setEnabled(True)

    assert canvas._marquee_start is None
    assert canvas._marquee_item is None
    run = next(r for r in data["runs"] if r.text == "KEEP ME")
    moved = QSignalSpy(canvas.run_moved)
    point = _view_point(canvas, (run.rect[0] + run.rect[2]) / 2, (run.rect[1] + run.rect[3]) / 2)
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
    _move(canvas, point + QPoint(20, 10))
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=point + QPoint(20, 10))
    assert moved.count() == 1
