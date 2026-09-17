"""書籤、複製文字、列印與右鍵選單測試。"""
import pymupdf
import pytest
from PySide6.QtWidgets import QApplication

from pdf_editor.outline import page_text, read_outline
from pdf_editor.ui.main_window import MainWindow
from pdf_editor.ui.printing import print_pages


def _close(window):
    if window.session is not None:
        window.session.saved_fingerprint = window.session.history.current[2]
    window.close()


@pytest.fixture
def outlined_path(tmp_path):
    doc = pymupdf.open()
    for index in range(4):
        doc.new_page(width=300, height=200).insert_text((40, 80), f"CHAPTER {index + 1}", fontsize=18)
    doc.set_toc([[1, "第一章", 1], [2, "第一節", 2], [1, "第二章", 3], [2, "第二節", 4]])
    path = tmp_path / "書籤.pdf"
    path.write_bytes(doc.tobytes())
    doc.close()
    return path


def test_read_outline_builds_tree(outlined_path):
    roots = read_outline(outlined_path.read_bytes())
    assert [entry.title for entry in roots] == ["第一章", "第二章"]
    assert roots[0].children[0].title == "第一節"
    assert roots[0].children[0].page == 1
    assert roots[1].children[0].title == "第二節"
    assert roots[1].children[0].page == 3


def test_page_text_returns_reading_order(multi_page_path):
    assert page_text(multi_page_path.read_bytes(), 1) == "PAGE 2"
    assert page_text(multi_page_path.read_bytes(), 9) == ""


def test_outline_tab_navigates_pages(qtbot, outlined_path, multi_page_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(outlined_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        assert window.outline_tree.topLevelItemCount() == 2
        chapter = window.outline_tree.topLevelItem(1)
        window.open_outline_item(chapter)
        assert window.page == 2
        window.open_document(multi_page_path)
        assert window.outline_tree.topLevelItemCount() == 0
        assert not window.outline_empty.isHidden()
        assert window.outline_tree.isHidden()
    finally:
        _close(window)


def test_copy_page_and_selected_text(qtbot, multi_page_path, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        window.actions["copy_page_text"].trigger()
        assert QApplication.clipboard().text() == "PAGE 1"
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(r for r in window.page_data["runs"] if "KEEP" in r.text)
        window.canvas.selected_run = run
        window.copy_selected_text()
        assert QApplication.clipboard().text() == run.text
    finally:
        _close(window)


def test_canvas_context_menu_offers_copy_and_insert(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(r for r in window.page_data["runs"] if "KEEP" in r.text)
        menu = window.build_canvas_menu((50, 50), run)
        labels = [action.text() for action in menu.actions()]
        assert any(label.startswith("複製「KEEP") for label in labels)
        assert "複製本頁全部文字" in labels
        assert "在此新增文字" in labels
        assert "頁面操作" in labels
        assert window.actions["print"] in menu.actions()
    finally:
        _close(window)


def test_print_pages_to_pdf_with_overlays(qtbot, tmp_path, multi_page_path):
    from PySide6.QtPrintSupport import QPrinter
    target = tmp_path / "列印輸出.pdf"
    printer = QPrinter(QPrinter.PrinterMode.ScreenResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(target))
    seen = []
    printed = print_pages(printer, multi_page_path.read_bytes(), [0, 2],
        lambda index, total: seen.append((index, total)))
    assert printed == 2
    assert seen == [(0, 2), (1, 2)]
    with pymupdf.open(target) as doc:
        assert doc.page_count == 2


def test_print_can_be_cancelled(tmp_path, multi_page_path):
    from PySide6.QtPrintSupport import QPrinter
    printer = QPrinter(QPrinter.PrinterMode.ScreenResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "取消.pdf"))
    printed = print_pages(printer, multi_page_path.read_bytes(), [0, 1, 2],
        lambda index, total: index < 1)
    assert printed == 1


def test_window_print_range_uses_selected_pages(qtbot, multi_page_path):
    from PySide6.QtPrintSupport import QPrinter
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        printer = QPrinter()
        printer.setPrintRange(QPrinter.PrintRange.PageRange)
        printer.setFromTo(2, 3)
        assert window.print_page_indices(printer) == [1, 2]
        printer.setPrintRange(QPrinter.PrintRange.AllPages)
        assert window.print_page_indices(printer) == [0, 1, 2]
    finally:
        _close(window)


def test_print_preview_uses_lower_resolution(qtbot, multi_page_path):
    from PySide6.QtGui import QPainter, QPicture
    from PySide6.QtPrintSupport import QPrinter
    from pdf_editor.ui.printing import is_preview
    picture = QPicture()
    painter = QPainter(picture)
    try:
        # 預覽元件以圖片引擎記錄內容，需辨識為預覽以降低解析度。
        assert is_preview(painter)
    finally:
        painter.end()


def test_print_action_opens_preview_first(qtbot, multi_page_path, monkeypatch):
    from PySide6.QtPrintSupport import QPrintPreviewDialog
    window = MainWindow()
    qtbot.addWidget(window)
    opened = []

    def fake_exec(dialog):
        opened.append(dialog.windowTitle())
        return QPrintPreviewDialog.DialogCode.Rejected

    monkeypatch.setattr(QPrintPreviewDialog, "exec", fake_exec)
    try:
        window.open_document(multi_page_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        window.actions["print"].trigger()
        assert opened == [f"預覽列印 — {multi_page_path.name}"]
        assert window.statusBar().currentMessage() == "已關閉預覽列印。"
    finally:
        _close(window)


def test_qt_standard_dialogs_are_translated(qtbot):
    from PySide6.QtCore import QCoreApplication
    from pdf_editor.__main__ import _install_qt_translations
    app = QApplication.instance()
    translator = _install_qt_translations(app)
    try:
        assert translator is not None
        assert QCoreApplication.translate("QPrintPreviewDialog", "Print") == "列印"
    finally:
        app.removeTranslator(translator)
