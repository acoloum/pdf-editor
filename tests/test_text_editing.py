"""文字編輯體驗：自動調整文字框、失敗保留輸入、延遲套用與自動重新選取。"""
import hashlib

import pymupdf
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy

from pdf_editor.engine.fonts import default_font
from pdf_editor.engine.text import extract_runs, fit_text_box, insert_text, overlaps, replace_text
from pdf_editor.errors import EditorError
from pdf_editor.model import TextInsertion, TextReplacement
from pdf_editor.ui.main_window import MainWindow
from pdf_editor.ui.text_panel import TextPanel


def _close(window):
    if window.session is not None:
        window.session.saved_fingerprint = window.session.history.current[2]
    window.close()


def _replacement(pdf, run, text, fit, rect=None, alignment="left"):
    return TextReplacement(hashlib.sha256(pdf).hexdigest(), 0, run.id, text, rect or run.rect,
        str(default_font()), run.size, run.color, alignment, False, fit)


@pytest.fixture
def layout_pdf():
    """左右各有一段文字，下方另有一行，用來檢查加寬與加高的邊界。"""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 60), "LEFT", fontsize=12)
    page.insert_text((300, 60), "RIGHT", fontsize=12)
    page.insert_text((40, 90), "BELOW", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def test_overlaps_ignores_touching_edges():
    assert not overlaps((0, 0, 10, 10), (10, 0, 20, 10))
    assert not overlaps((0, 0, 10, 10), (0, 10.3, 10, 20))
    assert overlaps((0, 0, 10, 10), (5, 5, 20, 20))


def test_expand_grows_right_until_neighbour(layout_pdf):
    run = next(r for r in extract_runs(layout_pdf, 0) if r.text == "LEFT")
    result = replace_text(layout_pdf, _replacement(layout_pdf, run, "LEFT AND MORE", "expand"))
    with pymupdf.open(stream=result) as doc:
        assert "LEFT AND MORE" in doc[0].get_text()
    with pytest.raises(EditorError) as error:
        replace_text(layout_pdf, _replacement(layout_pdf, run, "LEFT " * 20, "expand"))
    assert error.value.code == "TEXT_OVERFLOW"
    assert "右側只剩" in str(error.value)


def test_none_fit_keeps_previous_overflow_behaviour(layout_pdf):
    run = next(r for r in extract_runs(layout_pdf, 0) if r.text == "LEFT")
    with pytest.raises(EditorError) as error:
        replace_text(layout_pdf, _replacement(layout_pdf, run, "LEFT AND MORE", "none"))
    assert error.value.code == "TEXT_OVERFLOW"


def test_shrink_reduces_size_within_limit():
    font = pymupdf.Font("helv")
    width = font.text_length("12345", fontsize=10)
    rect, size = fit_text_box((0, 0, width * 0.8, 12), "12345", font, 10, "shrink", "center",
        [], pymupdf.Rect(0, 0, 400, 300))
    assert 7 <= size < 10
    with pytest.raises(EditorError) as error:
        fit_text_box((0, 0, width * 0.5, 12), "12345", font, 10, "shrink", "center",
            [], pymupdf.Rect(0, 0, 400, 300))
    assert "儲存格" in str(error.value)


def test_centered_expand_grows_both_sides():
    font = pymupdf.Font("helv")
    rect, size = fit_text_box((180, 10, 220, 22), "A much longer title", font, 10, "expand",
        "hcenter", [], pymupdf.Rect(0, 0, 400, 300))
    assert size == 10
    assert (rect[0] + rect[2]) / 2 == pytest.approx(200)
    assert rect[2] - rect[0] > 40


def test_multiline_expand_stops_at_text_below():
    font = pymupdf.Font("helv")
    with pytest.raises(EditorError) as error:
        fit_text_box((40, 50, 120, 62), "one\ntwo\nthree", font, 12, "expand", "left",
            [(40, 70, 90, 82)], pymupdf.Rect(0, 0, 400, 300))
    assert "行數太多" in str(error.value)


def test_insertion_expand_avoids_existing_text(layout_pdf):
    request = TextInsertion(hashlib.sha256(layout_pdf).hexdigest(), 0, "NEW TEXT HERE",
        (40, 150, 60, 164), str(default_font()), 12, (0, 0, 0), "left", False, "expand")
    result = insert_text(layout_pdf, request)
    with pymupdf.open(stream=result) as doc:
        assert "NEW TEXT HERE" in doc[0].get_text()


def test_bold_runs_are_detected(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 60), "Plain", fontname="helv", fontsize=12)
    page.insert_text((40, 90), "Heavy", fontname="hebo", fontsize=12)
    data = doc.tobytes()
    doc.close()
    runs = {run.text: run for run in extract_runs(data, 0)}
    assert not runs["Plain"].bold
    assert runs["Heavy"].bold


def test_numeric_panel_changes_are_debounced(qtbot):
    from types import SimpleNamespace
    panel = TextPanel()
    qtbot.addWidget(panel)
    panel.set_run(SimpleNamespace(editable=True, reason=None, font_name="Test", text="ABC",
        size=12, color=(0, 0, 0), rect=(10, 10, 60, 30), bold=True))
    assert panel.bold.isChecked()
    assert panel.rect() == pytest.approx((10, 10, 60, 30))
    spy = QSignalSpy(panel.format_requested)
    panel.size.setValue(13)
    panel.size.setValue(14)
    panel.box[2].setValue(80)
    assert spy.count() == 0
    assert panel.modified
    qtbot.waitUntil(lambda: spy.count() == 1, timeout=2000)
    qtbot.wait(panel.NUMERIC_DELAY_MS + 100)
    assert spy.count() == 1
    assert panel.size.keyboardTracking() is False


def test_failed_edit_keeps_typed_text_and_shows_reason(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(item for item in window.page_data["runs"] if "KEEP" in item.text)
        window.select_run(run)
        long_text = "很長的內容" * 40
        window.canvas.inline_editor.setText(long_text)
        qtbot.keyClick(window.canvas.inline_editor, Qt.Key.Key_Return)
        qtbot.waitUntil(lambda: not window.busy and window.canvas.inline_editor is not None,
            timeout=30000)
        assert window.canvas.inline_editor.text() == long_text
        assert window.canvas.inline_error.isVisible()
        assert "太長" in window.canvas.inline_error.text()
        assert window.canvas.inline_editor.property("error")
        assert not window.session.dirty
        # 使用者修正內容後，紅框提示立即消失。
        qtbot.keyClick(window.canvas.inline_editor, Qt.Key.Key_Backspace)
        assert not window.canvas.inline_error.isVisible()
    finally:
        _close(window)


def test_overlap_failure_highlights_conflicting_text(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        runs = window.page_data["runs"]
        target = next(item for item in runs if "KEEP" in item.text)
        other = next(item for item in runs if "品質" in item.text)
        request = TextInsertion("hash", 0, "X", other.rect, str(default_font()), 12, (0, 0, 0))
        window.reopen_text_editor((None, "X", other.rect), request,
            "文字框與其他文字重疊，請移動或縮小文字框。", "OVERLAP")
        assert window.canvas.conflict_items
        assert window.canvas.inline_editor.text() == "X"
        assert target.rect != other.rect
    finally:
        _close(window)


def test_successful_edit_reselects_text_for_further_formatting(qtbot, source_path):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(item for item in window.page_data["runs"] if "KEEP" in item.text)
        window.select_run(run)
        window.canvas.inline_editor.setText("KEEP IT LONGER")
        qtbot.keyClick(window.canvas.inline_editor, Qt.Key.Key_Return)
        qtbot.waitUntil(lambda: not window.busy and window.run is not None
            and window.run.text == "KEEP IT LONGER", timeout=30000)
        assert window.canvas.inline_editor is None
        assert window.canvas.selected_run == window.run
        assert window.panels.currentWidget() is window.text_panel
        assert window.text_panel.isEnabled()
        # 選取狀態下按 Enter 重新開啟輸入框。
        window.canvas.setFocus()
        qtbot.keyClick(window.canvas, Qt.Key.Key_Return)
        assert window.canvas.inline_editor is not None
    finally:
        _close(window)
