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
        assert "已圈選2 段文字、1 個圖章或圖片、1 個註解" in window.statusBar().currentMessage()
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


# ── 表格格線跟著文字一起移動 ──

def _table_pdf():
    with pymupdf.open() as document:
        page = document.new_page(width=500, height=400)
        page.draw_rect((40, 40, 300, 100), color=None, fill=(1, 1, 1))
        for y in (40, 70, 100):
            page.draw_rect((40, y, 300, y + 0.5), color=None, fill=(0, 0, 0))
        page.draw_line((40, 40), (40, 100), color=(0, 0, 0), width=0.8)
        page.draw_line((300, 40), (300, 100), color=(0, 0, 0), width=0.8)
        page.insert_text((50, 62), "HEAD", fontsize=12)
        page.insert_text((50, 92), "CELL", fontsize=12)
        page.insert_text((50, 300), "OUTSIDE", fontsize=12)
        return document.tobytes()


def _black_lines(pdf):
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        return sorted(tuple(round(v, 1) for v in d["rect"]) for d in document[0].get_drawings()
            if d.get("fill") == (0.0, 0.0, 0.0) or d["type"] == "s")


def _open_table_window(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "表格.pdf"
    path.write_bytes(_table_pdf())
    monkeypatch.setattr(main_window.Jobs, "submit", _submit_synchronously)
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1000, 800)
    window.show()
    window.open_document(path)
    qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
    qtbot.waitExposed(window)
    return window


def test_marquee_selects_grid_lines_and_highlights_them(qtbot, tmp_path, monkeypatch):
    window = _open_table_window(qtbot, tmp_path, monkeypatch)
    try:
        canvas = window.canvas
        assert len(window.page_data["drawings"]) == 5
        _drag(qtbot, canvas, _view_point(canvas, 30, 30), _view_point(canvas, 320, 110))

        assert len(canvas.group.drawings) == 5
        assert [run.text for run in canvas.group.runs] == ["HEAD", "CELL"]
        # 兩段文字的虛線框加上五條格線的青色標示。
        assert len(canvas._group_items) == 7
        assert "格線等 5 個圖形" in window.statusBar().currentMessage()
    finally:
        _close(window)


def test_dragging_table_moves_grid_with_text_in_one_undo_step(qtbot, tmp_path, monkeypatch):
    window = _open_table_window(qtbot, tmp_path, monkeypatch)
    try:
        canvas = window.canvas
        before = _black_lines(window.session.pdf)
        _drag(qtbot, canvas, _view_point(canvas, 30, 30), _view_point(canvas, 320, 110))
        # 按在格線上（不是文字上）也能拖曳整批。
        start = _view_point(canvas, 200, 70.2)
        end = _view_point(canvas, 210, 220.2)
        undo_index = window.session.history.index

        qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        _move(canvas, end)
        qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)

        assert window.session.history.index == undo_index + 1
        after = _black_lines(window.session.pdf)
        assert len(after) == len(before)
        for old, new in zip(before, after):
            assert new[1] == pytest.approx(old[1] + 150, abs=1.5)
        runs = {r.text: r.rect for r in window.page_data["runs"]}
        assert runs["CELL"][1] > 220 and runs["OUTSIDE"][1] < 300
        # 移動後仍選取同一批（含格線），可繼續拖曳。
        assert len(canvas.group.drawings) == 5

        window.history_step(False)
        assert _black_lines(window.session.pdf) == before
    finally:
        _close(window)


# ── 表格與頁面原有的圖章一起移動 ──

import io

STAMP = (320, 40, 380, 80)


def _open_table_and_stamp_window(qtbot, tmp_path, monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (20, 60, 220)).save(buffer, format="PNG")
    with pymupdf.open(stream=_table_pdf(), filetype="pdf") as document:
        document[0].insert_image(STAMP, stream=buffer.getvalue())
        pdf = document.tobytes()
    path = tmp_path / "表格與章.pdf"
    path.write_bytes(pdf)
    monkeypatch.setattr(main_window.Jobs, "submit", _submit_synchronously)
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1000, 800)
    window.show()
    window.open_document(path)
    qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
    qtbot.waitExposed(window)
    return window


def _drag_group(qtbot, canvas, start, end):
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    _move(canvas, end)
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)


def test_table_and_page_stamp_move_together(qtbot, tmp_path, monkeypatch):
    window = _open_table_and_stamp_window(qtbot, tmp_path, monkeypatch)
    try:
        canvas = window.canvas
        _drag(qtbot, canvas, _view_point(canvas, 30, 30), _view_point(canvas, 390, 110))
        assert len(canvas.group.images) == 1
        assert "1 個圖章或圖片" in window.statusBar().currentMessage()
        undo_index = window.session.history.index

        # 按在圖章上拖曳。
        _drag_group(qtbot, canvas, _view_point(canvas, 350, 60), _view_point(canvas, 350, 210))

        assert window.session.history.index == undo_index + 1
        (layer,) = window.session.overlays
        assert layer.rect[1] == pytest.approx(STAMP[1] + 150, abs=1.5)
        runs = {r.text: r.rect for r in window.page_data["runs"]}
        assert runs["CELL"][1] > 220
        # 移動後圖章以圖層身分留在整批選取中，可以繼續一起拖曳。
        assert canvas.group.layers == (layer.id,)
        assert canvas.group.images == ()
        assert len(canvas.group.runs) == 2

        window.history_step(False)
        assert window.session.overlays == ()
        assert len(window.page_data["images"]) == 1
    finally:
        _close(window)


def test_stamp_clicked_first_stays_in_marquee_selection(qtbot, tmp_path, monkeypatch):
    window = _open_table_and_stamp_window(qtbot, tmp_path, monkeypatch)
    try:
        canvas = window.canvas
        # 先點圖章（轉成圖層、尚未改動），再拉框把表格和圖章一起圈起來。
        qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton,
            pos=_view_point(canvas, 350, 60))
        qtbot.waitUntil(lambda: len(window.session.overlays) == 1, timeout=10000)
        (layer,) = window.session.overlays

        _drag(qtbot, canvas, _view_point(canvas, 30, 30), _view_point(canvas, 390, 110))

        # 開始圈選時不能把剛轉換的圖章撤銷掉。
        assert window.session.overlays == (layer,)
        assert canvas.group.layers == (layer.id,)
        _drag_group(qtbot, canvas, _view_point(canvas, 200, 70.2), _view_point(canvas, 200, 220.2))
        assert window.session.overlays[0].rect[1] == pytest.approx(STAMP[1] + 150, abs=1.5)
    finally:
        _close(window)


def test_background_click_is_reported_on_release_and_not_for_marquee(qtbot, pdf_bytes):
    canvas, _data = _canvas(qtbot, pdf_bytes)
    blank = QSignalSpy(canvas.background_clicked)
    point = _view_point(canvas, 450, 380)

    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert blank.count() == 0
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert blank.count() == 1

    _drag(qtbot, canvas, _view_point(canvas, 20, 150), _view_point(canvas, 480, 390))
    assert blank.count() == 1
