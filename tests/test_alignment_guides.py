"""拖曳文字、圖片與整批選取時的對齊輔助線與吸附。"""
import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent, QCoreApplication
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QSignalSpy

from pdf_editor.engine.geometry import inverse_transform, transform_point, transformed_rect
from pdf_editor.engine.render import render_page
from pdf_editor.model import Overlay
from pdf_editor.ui.alignment_guides import alignment_targets, snap
from pdf_editor.ui.canvas import Canvas, LayerItem

ALT = Qt.KeyboardModifier.AltModifier
NONE = Qt.KeyboardModifier.NoModifier


def test_snaps_center_to_page_center_and_reports_page_guide():
    targets = alignment_targets((500, 400), [])
    (dx, dy), guides = snap((200, 50, 296, 70), targets)

    # 中心 248 離頁面中線 250 只差 2 像素：吸附過去。
    assert (dx, dy) == (2, 0)
    assert [(g.axis, g.value, g.page) for g in guides] == [("x", 250, True)]
    assert (guides[0].start, guides[0].end) == (0, 400)


def test_snaps_to_nearest_object_edge_and_spans_both_objects():
    targets = alignment_targets((500, 400), [(40, 100, 160, 120)])
    (dx, dy), guides = snap((43, 300, 93, 330), targets)

    assert (dx, dy) == (-3, 0)
    (guide,) = guides
    assert (guide.axis, guide.value, guide.page) == ("x", 40, False)
    # 線段從另一個物件延伸到移動中的物件。
    assert (guide.start, guide.end) == (100, 330)


def test_far_from_every_target_does_not_snap():
    targets = alignment_targets((500, 400), [(40, 100, 160, 120)])
    assert snap((70, 210, 110, 230), targets) == ((0, 0), ())


@pytest.fixture
def simple_pdf():
    """只有一行文字的頁面，避免其他物件的對齊位置干擾測試。"""
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=400)
    page.insert_text((40, 300), "KEEP ME", fontsize=18)
    data = doc.tobytes()
    doc.close()
    return data


def _move(canvas, point, modifiers=NONE):
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(point),
        QPointF(canvas.viewport().mapToGlobal(point)), Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, modifiers)
    QCoreApplication.sendEvent(canvas.viewport(), event)


def _canvas(qtbot, pdf, layers=()):
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    data = render_page(pdf, 0, 1.0)
    canvas.display(data, layers, None)
    qtbot.waitExposed(canvas)
    return canvas, data


def _scene_rect(canvas, rect):
    return transformed_rect(canvas.matrix, rect)


def _view(canvas, x, y):
    return canvas.mapFromScene(QPointF(x, y))


def _keep_me(data):
    return next(r for r in data["runs"] if r.text == "KEEP ME")


def _drag_run_to_page_center(qtbot, canvas, run, modifiers=NONE):
    """把文字拖到中心離頁面中線 3 像素處，回傳拖曳中的輔助線與按下位置。"""
    r = _scene_rect(canvas, run.rect)
    start = _view(canvas, (r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
    page_center = canvas.sceneRect().width() / 2
    end = start + QPoint(round(page_center - (r[0] + r[2]) / 2) + 3, 37)
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    _move(canvas, start + (end - start) / 2, modifiers)
    _move(canvas, end, modifiers)
    guides = canvas.guides()
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, modifiers, end)
    return guides, end - start


def test_dragging_text_snaps_to_page_center_and_shows_guide(qtbot, simple_pdf):
    canvas, data = _canvas(qtbot, simple_pdf)
    run = _keep_me(data)
    moved = QSignalSpy(canvas.run_moved)

    guides, _offset = _drag_run_to_page_center(qtbot, canvas, run)

    page = canvas.sceneRect()
    assert any(line.x1() == line.x2() == pytest.approx(page.width() / 2)
        and line.y1() == 0 and line.y2() == pytest.approx(page.height()) for line in guides)
    assert moved.count() == 1
    rect = moved.at(0)[0].rect
    center = transform_point(inverse_transform(canvas.matrix), page.width() / 2, 0)[0]
    assert (rect[0] + rect[2]) / 2 == pytest.approx(center, abs=0.01)
    # 放開後輔助線消失。
    assert canvas.guides() == []


def test_holding_alt_moves_text_freely_without_guides(qtbot, simple_pdf):
    canvas, data = _canvas(qtbot, simple_pdf)
    run = _keep_me(data)
    moved = QSignalSpy(canvas.run_moved)

    guides, offset = _drag_run_to_page_center(qtbot, canvas, run, ALT)

    assert guides == []
    rect = moved.at(0)[0].rect
    assert rect[0] - run.rect[0] == pytest.approx(offset.x() / canvas.matrix[0], abs=0.01)


def test_small_drag_below_threshold_does_not_snap(qtbot, simple_pdf):
    canvas, data = _canvas(qtbot, simple_pdf)
    run = _keep_me(data)
    moved = QSignalSpy(canvas.run_moved)
    r = _scene_rect(canvas, run.rect)
    start = _view(canvas, (r[0] + r[2]) / 2, (r[1] + r[3]) / 2)

    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    _move(canvas, start + QPoint(1, 0))
    assert canvas.guides() == []
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start + QPoint(1, 0))

    assert moved.at(0)[0].rect[0] - run.rect[0] == pytest.approx(1 / canvas.matrix[0], abs=0.01)


def test_dragging_layer_snaps_left_edge_to_text(qtbot, simple_pdf, tmp_path):
    path = tmp_path / "章.png"
    # 圖片比例與圖層外框相同，畫面上的位置才會等於圖層外框。
    Image.new("RGBA", (120, 80), (210, 35, 45, 255)).save(path)
    layer = Overlay("stamp", 0, str(path), (300, 280, 360, 320), 0)
    canvas, data = _canvas(qtbot, simple_pdf, (layer,))
    item = next(item for item in canvas.scene().items() if isinstance(item, LayerItem))
    text_left = _scene_rect(canvas, _keep_me(data).rect)[0]
    moved = QSignalSpy(canvas.layer_moved)
    content = item.scene_image_rect()
    start = _view(canvas, content.center().x(), content.center().y())
    # 圖章左緣拖到文字左緣右邊約 3 像素。
    end = start + QPoint(round(text_left - content.left()) + 3, -47)

    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    _move(canvas, start + (end - start) / 2)
    _move(canvas, end)
    assert item.scene_image_rect().left() == pytest.approx(text_left)
    assert any(line.x1() == line.x2() == pytest.approx(text_left) for line in canvas.guides())
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)

    assert moved.count() == 1
    assert moved.at(0)[0].rect[0] == pytest.approx(_keep_me(data).rect[0], abs=0.01)
    assert canvas.guides() == []


def test_dragging_group_snaps_its_bounds_to_page_center(qtbot, simple_pdf):
    canvas, data = _canvas(qtbot, simple_pdf)
    run = _keep_me(data)
    r = _scene_rect(canvas, run.rect)
    # 圈選整行文字成為整批選取。
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=_view(canvas, r[0] - 10, r[1] - 10))
    _move(canvas, _view(canvas, r[2] + 10, r[3] + 10))
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=_view(canvas, r[2] + 10, r[3] + 10))
    assert canvas.group is not None and canvas.group.runs
    moved = QSignalSpy(canvas.selection_moved)
    start = _view(canvas, (r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
    page_center = canvas.sceneRect().width() / 2
    end = start + QPoint(round(page_center - (r[0] + r[2]) / 2) - 4, -37)

    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    _move(canvas, end)
    assert any(line.x1() == line.x2() == pytest.approx(page_center) for line in canvas.guides())
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)

    _selection, (dx, _dy) = moved.at(0)
    assert (run.rect[0] + run.rect[2]) / 2 + dx == pytest.approx(
        transform_point(inverse_transform(canvas.matrix), page_center, 0)[0], abs=0.01)
    assert canvas.guides() == []


def _open_window(qtbot, tmp_path, monkeypatch):
    import pdf_editor.ui.main_window as main_window
    from test_marquee_selection import _submit_synchronously
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=400)
    page.insert_text((40, 80), "FIRST LINE", fontsize=16)
    page.insert_text((40, 200), "MOVE ME", fontsize=16)
    path = tmp_path / "對齊.pdf"
    path.write_bytes(doc.tobytes())
    doc.close()
    monkeypatch.setattr(main_window.Jobs, "submit", _submit_synchronously)
    window = main_window.MainWindow()
    qtbot.addWidget(window)
    window.resize(1000, 800)
    window.show()
    window.open_document(path)
    qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
    qtbot.waitExposed(window)
    return window


def test_inline_editor_does_not_cover_guides_while_dragging_text(qtbot, tmp_path, monkeypatch):
    window = _open_window(qtbot, tmp_path, monkeypatch)
    try:
        canvas = window.canvas
        run = next(r for r in window.page_data["runs"] if r.text == "MOVE ME")
        r = _scene_rect(canvas, run.rect)
        start = _view(canvas, (r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
        qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        # 按下文字會在原位開啟輸入框；往下拖時靠左對齊的輔助線會穿過原位。
        assert canvas.inline_editor is not None
        _move(canvas, start + QPoint(2, 60))
        assert any(line.x1() == line.x2() == pytest.approx(r[0]) for line in canvas.guides())
        # 輸入框只是變透明（仍保有焦點），不能隱藏，否則失去焦點會提前套用文字。
        editor = canvas.inline_editor
        assert editor.isVisible() and editor.graphicsEffect().opacity() == 0
        # 拖回原位放開（沒有移動）：輸入框重新出現。
        _move(canvas, start)
        qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        assert canvas.inline_editor is editor and editor.graphicsEffect() is None
        assert canvas.guides() == []
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()
