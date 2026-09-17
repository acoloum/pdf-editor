"""開檔方式、頁面瀏覽、視窗狀態與快捷鍵的整合測試。"""
import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent, QKeySequence, QWheelEvent
from PySide6.QtWidgets import QToolBar, QToolButton

from pdf_editor.ui.main_window import MainWindow
from pdf_editor.ui.settings import AppSettings


def _close(window):
    if window.session is not None:
        window.session.saved_fingerprint = window.session.history.current[2]
    window.close()


def _wheel(canvas, delta, modifiers=Qt.KeyboardModifier.NoModifier):
    position = QPointF(canvas.viewport().rect().center())
    event = QWheelEvent(position, canvas.viewport().mapToGlobal(position), QPoint(),
        QPoint(0, delta), Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
    canvas.wheelEvent(event)


def _pdf_mime(*paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def test_dropping_pdf_on_canvas_opens_document(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        mime = _pdf_mime(source_path)
        event = QDropEvent(QPointF(20, 20), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        window.canvas.dropEvent(event)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        assert window.session.source == source_path
    finally:
        _close(window)


def test_non_pdf_drop_is_ignored(tmp_path):
    text = tmp_path / "說明.txt"
    text.write_text("x", encoding="utf-8")
    from pdf_editor.ui.canvas import Canvas
    assert Canvas.dropped_pdf_paths(_pdf_mime(text)) == []


def test_opening_document_updates_recent_files_and_menu(qtbot, source_path, multi_page_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        window.open_document(multi_page_path)
        recent = AppSettings().recent_files()
        assert recent[:2] == [str(multi_page_path.resolve()), str(source_path.resolve())]
        window.rebuild_recent_menu()
        labels = [action.text() for action in window.recent_menu.actions()]
        assert any(multi_page_path.name in label for label in labels)
        assert "清除清單" in labels
        button = next(bar for bar in window.findChildren(QToolBar)
            if window.actions["open"] in bar.actions()).widgetForAction(window.actions["open"])
        assert button.popupMode() == QToolButton.ToolButtonPopupMode.MenuButtonPopup
    finally:
        _close(window)


def test_missing_recent_file_is_removed(qtbot, tmp_path, monkeypatch):
    import pdf_editor.ui.main_window as main_window
    missing = tmp_path / "不存在.pdf"
    AppSettings().add_recent_file(missing)
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args: None)
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        assert not window.open_path(missing)
        assert AppSettings().recent_files() == []
    finally:
        _close(window)


def test_ctrl_wheel_zooms_and_edge_wheel_turns_page(qtbot, multi_page_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.resize(1200, 900)
        window.show()
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        before = window.scale
        _wheel(window.canvas, 120, Qt.KeyboardModifier.ControlModifier)
        assert window.scale > before

        # 頁面完整顯示時沒有捲軸，向下滾一格就翻到下一頁。
        window.fit_zoom("page")
        qtbot.waitUntil(lambda: window.page_data is not None and not window.busy, timeout=30000)
        _wheel(window.canvas, -120)
        qtbot.waitUntil(lambda: window.page == 1, timeout=30000)
        _wheel(window.canvas, 120)
        qtbot.waitUntil(lambda: window.page == 0, timeout=30000)
    finally:
        _close(window)


def test_page_actions_have_navigation_shortcuts(qtbot, multi_page_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        assert QKeySequence("PgDown") in window.actions["next_page"].shortcuts()
        window.actions["next_page"].trigger()
        assert window.page == 1
        window.actions["last_page"].trigger()
        assert window.page == 2
        window.actions["previous_page"].trigger()
        assert window.page == 1
        window.actions["first_page"].trigger()
        assert window.page == 0
    finally:
        _close(window)


def test_save_accepts_ctrl_s(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        shortcuts = window.actions["save"].shortcuts()
        assert QKeySequence("Ctrl+S") in shortcuts
        assert QKeySequence("Ctrl+Shift+S") in shortcuts
        # 快捷鍵需掛在主視窗本身，工具列隱藏或收合時仍可使用。
        from PySide6.QtWidgets import QWidget
        assert window.actions["save"] in QWidget.actions(window)
    finally:
        _close(window)


def test_window_state_is_restored(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1111, 777)
    window.zoom.setCurrentText("150%")
    window.close()

    restored = MainWindow()
    qtbot.addWidget(restored)
    try:
        assert restored.width() == 1111
        assert restored.height() == 777
        assert restored.zoom.currentText() == "150%"
        assert restored.scale == pytest.approx(1.5)
    finally:
        _close(restored)
