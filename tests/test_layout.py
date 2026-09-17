"""工具列分組、浮動搜尋列、狀態列與文件資訊面板測試。"""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QToolButton

from pdf_editor.ui.info_panel import describe_document, paper_name
from pdf_editor.ui.main_window import MainWindow


def _close(window):
    if window.session is not None:
        window.session.saved_fingerprint = window.session.history.current[2]
    window.close()


def test_toolbar_groups_stamp_tools_into_menu(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        button = window.stamp_menu_button
        assert button.defaultAction() is window.actions["stamp"]
        assert button.popupMode() == QToolButton.ToolButtonPopupMode.MenuButtonPopup
        menu_actions = button.menu().actions()
        for name in ("stamp", "collection", "convert_stamp"):
            assert window.actions[name] in menu_actions
            # 收進選單的功能不直接出現在工具列上。
        assert window.actions["collection"] not in window.toolbar.actions()
        assert window.actions["convert_stamp"] not in window.toolbar.actions()
        for name in ("open", "save", "add_text", "signature", "ocr", "compare"):
            assert window.actions[name].toolTip()
    finally:
        _close(window)


def test_search_bar_is_hidden_until_ctrl_f(qtbot, multi_page_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        assert not window.search_bar.isVisible()
        window.actions["search"].trigger()
        assert window.search_bar.isVisible()
        window.perform_search("PAGE")
        qtbot.waitUntil(lambda: not window.busy and len(window.search_results) == 3, timeout=30000)
        assert window.search_bar.count.text() == "1 / 3"
        qtbot.keyClick(window.search_input, Qt.Key.Key_Escape)
        assert not window.search_bar.isVisible()
        assert window.canvas.search_highlight is None
    finally:
        _close(window)


def test_info_panel_shows_document_details_until_text_selected(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        assert window.panels.currentWidget() is window.info_panel
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        assert window.panels.currentWidget() is window.info_panel
        assert window.info_panel.values["檔名"].text() == source_path.name
        assert window.info_panel.values["頁數"].text() == "1 頁"
        run = next(r for r in window.page_data["runs"] if "品質" in r.text)
        window.select_run(run)
        assert window.panels.currentWidget() is window.text_panel
        window.canvas.cancel_inline_editor()
        window.run = None
        window.refresh_actions()
        assert window.panels.currentWidget() is window.info_panel
    finally:
        _close(window)


def test_status_bar_shows_page_size_pointer_and_dirty_state(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.resize(1320, 850)
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        assert window.page_total.text() == "/ 1"
        assert window.page_size_label.text() == "176 × 141 mm"
        assert window.dirty_label.text() == ""
        center = window.canvas.mapFromScene(QPointF(10, 10))
        point = window.canvas.page_point_at(center)
        window.show_pointer_position(point)
        assert window.pointer_label.text().startswith("X ")
        window.show_pointer_position(None)
        assert window.pointer_label.text() == ""
        run = next(r for r in window.page_data["runs"] if "KEEP" in r.text)
        window.delete_run(run)
        qtbot.waitUntil(lambda: not window.busy and window.session.dirty, timeout=30000)
        window.refresh_actions()
        assert "未儲存" in window.dirty_label.text()
    finally:
        _close(window)


def test_describe_document_reports_paper_size(pdf_bytes):
    info = describe_document(pdf_bytes, 0)
    assert info["頁數"] == "1 頁"
    assert "mm" in info["頁面尺寸"]
    assert paper_name(595, 842) == "A4 直式"
    assert paper_name(842, 595) == "A4 橫式"
    assert paper_name(500, 400) == ""


def test_toolbar_labels_fit_default_window_width(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.resize(1320, 850)
        window.show()
        qtbot.waitExposed(window)
        buttons = [button for button in window.toolbar.findChildren(QToolButton)
            if button.objectName() != "qt_toolbar_ext_button"]
        extension = [button for button in window.toolbar.findChildren(QToolButton)
            if button.objectName() == "qt_toolbar_ext_button" and button.isVisible()]
        # 預設寬度下不得出現「»」溢出按鈕，文字也不能被省略。
        assert not extension
        assert all(button.width() >= button.sizeHint().width() for button in buttons if button.isVisible())
    finally:
        _close(window)
