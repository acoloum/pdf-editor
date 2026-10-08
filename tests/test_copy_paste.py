"""圖片複製與貼上：剪貼簿格式、貼上規則與 Ctrl+C／Ctrl+V。"""

import io
from dataclasses import replace

import pymupdf
import pytest
from PIL import Image
from PySide6.QtGui import QColor, QImage
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


def _set_clipboard_image(width, height, color=(30, 90, 200)):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(*color))
    QApplication.clipboard().setImage(image)


def test_paste_keeps_size_angle_and_remove_white(qtbot, multi_page_path, red_stamp):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(red_stamp, False)
        source = replace(window.session.overlays[-1], rect=(20, 20, 80, 50), angle=30,
                         remove_white=True)
        window.move_layer(source)
        window.copy_selection()

        window.paste_layer_at((150, 100))

        pasted = window.session.overlays[-1]
        assert pasted.id != source.id and window.layer_id == pasted.id
        assert _size(pasted.rect) == pytest.approx(_size(source.rect))
        assert (pasted.angle, pasted.remove_white) == (30, True)
        cx, cy = (pasted.rect[0] + pasted.rect[2]) / 2, (pasted.rect[1] + pasted.rect[3]) / 2
        assert (cx, cy) == pytest.approx((150, 100))
    finally:
        _close(window)


def test_paste_on_other_page_and_undo(qtbot, multi_page_path, red_stamp):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(red_stamp, False)
        window.copy_selection()
        window.goto_page(1)
        qtbot.waitUntil(lambda: window.page_data["page"] == 1, timeout=30000)

        window.paste_layer_at((150, 100))
        assert [o.page for o in window.session.overlays] == [0, 1]

        window.history_step(False)
        assert [o.page for o in window.session.overlays] == [0]
    finally:
        _close(window)


def test_paste_into_another_document(qtbot, multi_page_path, source_path, red_stamp):
    first = _open(qtbot, multi_page_path)
    try:
        first.import_layer(red_stamp, False)
        first.copy_selection()
    finally:
        _close(first)
    second = _open(qtbot, source_path)
    try:
        second.paste_layer_at((250, 200))

        pasted = second.session.overlays[-1]
        assert str(second.session.history.root) in pasted.asset_path
    finally:
        _close(second)


def test_paste_external_image_uses_stamp_size_rule(qtbot, multi_page_path):
    window = _open(qtbot, multi_page_path)
    try:
        _set_clipboard_image(400, 200)

        window.paste_layer_at((150, 100))

        pasted = window.session.overlays[-1]
        # 300×200 頁面：預設寬 min(150, 頁寬 40%)=120，高依比例 60。
        assert _size(pasted.rect) == pytest.approx((120, 60))
        assert (pasted.angle, pasted.remove_white) == (0, False)
    finally:
        _close(window)


def test_paste_near_edge_stays_inside_page(qtbot, multi_page_path, red_stamp):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(red_stamp, False)
        window.copy_selection()

        window.paste_layer_at((299, 199))

        x0, y0, x1, y1 = window.session.overlays[-1].rect
        assert 0 <= x0 < x1 <= 300 and 0 <= y0 < y1 <= 200
    finally:
        _close(window)


def test_paste_refused_without_image_or_on_read_only(qtbot, multi_page_path):
    from pdf_editor.model import DocumentAccess

    window = _open(qtbot, multi_page_path)
    try:
        QApplication.clipboard().setText("只有文字")
        window.paste_layer_at((150, 100))
        assert window.session.overlays == ()
        assert "剪貼簿沒有可貼上的圖片" in window.statusBar().currentMessage()

        _set_clipboard_image(40, 40)
        window.session.access = DocumentAccess(False, False, "此文件僅供閱讀")
        window.paste_layer_at((150, 100))
        assert window.session.overlays == ()
    finally:
        _close(window)


def test_ctrl_v_pastes_at_cursor_or_visible_center(qtbot, multi_page_path, red_stamp, monkeypatch):
    import pdf_editor.ui.stamp_actions as stamp_actions

    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(red_stamp, False)
        window.copy_selection()
        assert window.actions["paste_image"].shortcut().toString() == "Ctrl+V"
        # 游標不在頁面上：貼在可見區域中央。
        monkeypatch.setattr(window.canvas, "page_point_at", lambda _pos: None)
        window.actions["paste_image"].trigger()
        center = window.visible_page_center()
        pasted = window.session.overlays[-1]
        assert ((pasted.rect[0] + pasted.rect[2]) / 2) == pytest.approx(
            min(max(center[0], 1 + _size(pasted.rect)[0] / 2), 299 - _size(pasted.rect)[0] / 2))
        # 游標在頁面上：以游標位置為中心。
        monkeypatch.setattr(window.canvas, "page_point_at", lambda _pos: (150, 100))
        window.actions["paste_image"].trigger()
        pasted = window.session.overlays[-1]
        assert ((pasted.rect[0] + pasted.rect[2]) / 2, (pasted.rect[1] + pasted.rect[3]) / 2) == \
            pytest.approx((150, 100))
    finally:
        _close(window)


def test_ctrl_v_in_text_inputs_pastes_text_not_image(qtbot, source_path):
    from PySide6.QtCore import QMimeData, Qt

    window = _open(qtbot, source_path)
    try:
        window.show()
        window.activateWindow()
        qtbot.waitExposed(window)
        image = QImage(40, 40, QImage.Format.Format_RGB32)
        image.fill(QColor(30, 90, 200))
        mime = QMimeData()
        mime.setText("貼上文字")
        mime.setImageData(image)
        QApplication.clipboard().setMimeData(mime)
        # 搜尋框與頁面輸入框有焦點時，Ctrl+V 交給輸入框貼文字，不建立圖層。
        window.search_input.setFocus()
        qtbot.keyClick(window.search_input, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        assert window.search_input.text() == "貼上文字"
        run = next(r for r in window.page_data["runs"] if "KEEP" in r.text)
        window.select_run(run)
        qtbot.waitUntil(lambda: window.canvas.inline_editor is not None
                        and window.canvas.inline_editor.hasFocus(), timeout=5000)
        qtbot.keyClick(window.canvas.inline_editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        assert "貼上文字" in window.canvas.inline_editor.text()
        assert window.session.overlays == ()
        # 對照：焦點在畫布時，Ctrl+V 貼上圖片。
        window.canvas.cancel_inline_editor()
        window.canvas.setFocus()
        qtbot.keyClick(window.canvas, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        assert len(window.session.overlays) == 1
    finally:
        _close(window)


def _menu_actions(menu):
    return {action.text(): action for action in menu.actions()}


def test_context_menu_copies_layer_and_pastes_at_point(qtbot, multi_page_path, red_stamp):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(red_stamp, False)
        layer = window.session.overlays[-1]

        menu = window.build_canvas_menu((150, 100), None, layer)
        actions = _menu_actions(menu)
        actions["複製圖片"].trigger()
        assert QApplication.clipboard().mimeData().hasFormat(MIME_TYPE)

        menu = window.build_canvas_menu((150, 100), None, None)
        actions = _menu_actions(menu)
        assert "複製圖片" not in actions
        assert actions["在此貼上圖片"].isEnabled()
        actions["在此貼上圖片"].trigger()
        pasted = window.session.overlays[-1]
        assert ((pasted.rect[0] + pasted.rect[2]) / 2, (pasted.rect[1] + pasted.rect[3]) / 2) == \
            pytest.approx((150, 100))
    finally:
        _close(window)


def test_context_menu_paste_disabled_without_clipboard_image(qtbot, multi_page_path):
    window = _open(qtbot, multi_page_path)
    try:
        QApplication.clipboard().setText("只有文字")

        actions = _menu_actions(window.build_canvas_menu((150, 100), None, None))

        assert not actions["在此貼上圖片"].isEnabled()
    finally:
        _close(window)


def _single_stamp_pdf(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (0, 60, 255)).save(buffer, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=500, height=400)
        page.insert_image((210, 260, 270, 300), stream=buffer.getvalue())
        path = tmp_path / "單章.pdf"
        path.write_bytes(document.tobytes(garbage=4, deflate=True))
    return path


def test_copy_paste_after_click_conversion_keeps_conversion(qtbot, tmp_path, monkeypatch):
    import pdf_editor.ui.main_window as main_window

    def submit_synchronously(self, function, arguments, success, failure):
        try:
            success(function(*arguments))
        except Exception as exc:
            failure((getattr(exc, "code", "ERROR"), str(exc), ()))

    monkeypatch.setattr(main_window.Jobs, "submit", submit_synchronously)
    window = _open(qtbot, _single_stamp_pdf(tmp_path))
    try:
        window.canvas.image_clicked.emit(window.page_data["images"][0])
        assert len(window.session.overlays) == 1

        window.copy_selection()
        window.paste_layer_at((100, 100))
        window.canvas.background_clicked.emit()

        assert len(window.session.overlays) == 2
    finally:
        _close(window)
