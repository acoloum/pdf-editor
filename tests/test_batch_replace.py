"""批次取代：整格相同才取代、逐筆勾選、個別失敗不影響其他筆，整批只留一筆復原紀錄。"""
import tempfile

import pymupdf
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

import pdf_editor.ui.main_window as main_window
from pdf_editor.batch_replace import (ReplaceRule, apply_replacements, find_candidates,
    validated_rules)
from pdf_editor.engine.text import extract_runs
from pdf_editor.errors import EditorError
from pdf_editor.ui.batch_replace_actions import RULES_KEY
from pdf_editor.ui.batch_replace_dialog import BatchReplaceDialog
from pdf_editor.ui.main_window import MainWindow

from conftest import FONT
from test_marquee_selection import _submit_synchronously

RULES = (ReplaceRule("韋伯式(HW)", "真直度"), ReplaceRule("韋伯式硬度計", "厚薄規"))
# 表格兩列：檢驗項目欄 (40-160)、檢驗設備欄 (160-300)，列高 30。
ROWS = (40, 70)


def _page(document, extra=()):
    page = document.new_page(width=500, height=400)
    page.insert_font(fontname="noto", fontfile=str(FONT))
    for y in ROWS + (ROWS[-1] + 30,):
        page.draw_line((40, y), (300, y), color=(0, 0, 0), width=0.8)
    for x in (40, 160, 300):
        page.draw_line((x, ROWS[0]), (x, ROWS[-1] + 30), color=(0, 0, 0), width=0.8)
    for row, (item, tool) in enumerate((("長度", "捲尺"), ("韋伯式(HW)", "韋伯式硬度計"))):
        baseline = ROWS[row] + 20
        page.insert_text((60, baseline), item, fontname="noto", fontsize=10)
        page.insert_text((180, baseline), tool, fontname="noto", fontsize=10)
    # 只「包含」規則文字的整行說明，不可被取代。
    page.insert_text((40, 200), "游標卡尺,韋伯式硬度計,厚薄規", fontname="noto", fontsize=10)
    for point, text in extra:
        page.insert_text(point, text, fontname="noto", fontsize=10)
    return page


@pytest.fixture
def report_pdf():
    with pymupdf.open() as document:
        _page(document)
        _page(document)
        document.subset_fonts(fallback=True)
        return document.tobytes()


def _texts(pdf, page):
    return [run.text for run in extract_runs(pdf, page)]


def _cell_center(column, row):
    x0, x1 = ((40, 160), (160, 300))[column]
    return (x0 + x1) / 2, ROWS[row] + 15


def test_finds_only_whole_cell_matches_on_every_page(report_pdf):
    candidates = find_candidates(report_pdf, RULES)

    assert [(c.page, c.run.text, c.new_text) for c in candidates] == [
        (0, "韋伯式(HW)", "真直度"), (0, "韋伯式硬度計", "厚薄規"),
        (1, "韋伯式(HW)", "真直度"), (1, "韋伯式硬度計", "厚薄規")]


def test_matching_ignores_spaces_in_rule_and_page_text(report_pdf):
    candidates = find_candidates(report_pdf, (ReplaceRule(" 韋伯式 硬度計 ", "厚薄規"),))
    assert len(candidates) == 2


@pytest.mark.parametrize("rules,message", [
    ((), "至少輸入一條"),
    ((ReplaceRule("", "真直度"),), "缺少要尋找的文字"),
    ((ReplaceRule("長度", "A"), ReplaceRule("長 度", "B")), "重複"),
    ((ReplaceRule("長度", "長度"),), "相同"),
])
def test_invalid_rules_are_explained(rules, message):
    with pytest.raises(EditorError) as error:
        validated_rules(rules)
    assert message in str(error.value)


def test_replaces_all_matches_and_centers_them_in_their_cells(report_pdf):
    candidates = find_candidates(report_pdf, RULES)

    pdf, applied, failures = apply_replacements(report_pdf, candidates, tempfile.mkdtemp())

    assert applied == 4 and failures == ()
    for page in (0, 1):
        runs = {run.text: run.rect for run in extract_runs(pdf, page)}
        assert "韋伯式(HW)" not in runs and "韋伯式硬度計" not in runs
        assert "游標卡尺,韋伯式硬度計,厚薄規" in runs and "長度" in runs and "捲尺" in runs
        for text, column in (("真直度", 0), ("厚薄規", 1)):
            rect = runs[text]
            cx, cy = _cell_center(column, 1)
            assert abs((rect[0] + rect[2]) / 2 - cx) < 1.5
            assert abs((rect[1] + rect[3]) / 2 - cy) < 2.5


def test_one_failure_does_not_stop_the_others(report_pdf):
    rules = (ReplaceRule("韋伯式(HW)", "真直度"),
        ReplaceRule("韋伯式硬度計", "這段取代文字長到縮小字級後也放不進儲存格裡面"))
    candidates = find_candidates(report_pdf, rules)

    pdf, applied, failures = apply_replacements(report_pdf, candidates, tempfile.mkdtemp())

    assert applied == 2
    assert [(f.candidate.page, f.candidate.run.text) for f in failures] == [
        (0, "韋伯式硬度計"), (1, "韋伯式硬度計")]
    assert all("放不進儲存格" in f.message for f in failures)
    for page in (0, 1):
        texts = _texts(pdf, page)
        assert "真直度" in texts and "韋伯式硬度計" in texts


def test_empty_replacement_deletes_the_text(report_pdf):
    candidates = find_candidates(report_pdf, (ReplaceRule("捲尺", ""),))
    pdf, applied, _failures = apply_replacements(report_pdf, candidates, tempfile.mkdtemp())
    assert applied == 2 and all("捲尺" not in _texts(pdf, page) for page in (0, 1))


def test_dialog_lists_matches_and_lets_user_untick(qtbot, report_pdf):
    dialog = BatchReplaceDialog(report_pdf, [(r.find, r.replace) for r in RULES])
    qtbot.addWidget(dialog)

    dialog.find()

    assert dialog.results.topLevelItemCount() == 4
    assert dialog.apply_button.isEnabled()
    assert "在 2 頁找到 4 筆，已勾選 4 筆" in dialog.summary.text()
    dialog.results.topLevelItem(1).setCheckState(0, Qt.CheckState.Unchecked)
    selected = dialog.selected_candidates()
    assert [(c.page, c.run.text) for c in selected] == [
        (0, "韋伯式(HW)"), (1, "韋伯式(HW)"), (1, "韋伯式硬度計")]
    # 修改規則後，舊結果不再適用。
    dialog.rules.item(0, 1).setText("真直度 2")
    assert dialog.results.topLevelItemCount() == 0 and not dialog.apply_button.isEnabled()


def test_dialog_explains_when_nothing_matches(qtbot, report_pdf):
    dialog = BatchReplaceDialog(report_pdf, [("韋伯式", "真直度")])
    qtbot.addWidget(dialog)
    dialog.find()
    assert dialog.results.topLevelItemCount() == 0
    assert "找不到內容完全相同的文字" in dialog.summary.text()
    assert not dialog.apply_button.isEnabled()


def test_vertical_text_is_listed_but_cannot_be_ticked(qtbot):
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=300)
        page.insert_font(fontname="noto", fontfile=str(FONT))
        page.insert_text((50, 50), "直書", fontname="noto", fontsize=12, rotate=90)
        page.insert_text((150, 50), "直書", fontname="noto", fontsize=12)
        pdf = document.tobytes()
    dialog = BatchReplaceDialog(pdf, [("直書", "橫書")])
    qtbot.addWidget(dialog)

    dialog.find()

    assert dialog.results.topLevelItemCount() == 2
    assert len(dialog.selected_candidates()) == 1
    assert "1 筆無法安全修改" in dialog.summary.text()


def _open_window(qtbot, tmp_path, monkeypatch, pdf):
    path = tmp_path / "材證.pdf"
    path.write_bytes(pdf)
    monkeypatch.setattr(main_window.Jobs, "submit", _submit_synchronously)
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1000, 800)
    window.show()
    window.open_document(path)
    qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
    return window


def test_window_applies_batch_as_one_undo_step_and_remembers_rules(qtbot, tmp_path, monkeypatch,
        report_pdf):
    window = _open_window(qtbot, tmp_path, monkeypatch, report_pdf)
    try:
        def run_dialog(dialog):
            dialog.rules.item(0, 0).setText("韋伯式(HW)")
            dialog.rules.item(0, 1).setText("真直度")
            dialog.add_rule("韋伯式硬度計", "厚薄規")
            dialog.find()
            return 1
        monkeypatch.setattr(BatchReplaceDialog, "exec", run_dialog)
        index = window.session.history.index

        window.show_batch_replace_dialog()

        assert window.session.history.index == index + 1
        for page in (0, 1):
            texts = _texts(window.session.pdf, page)
            assert "真直度" in texts and "厚薄規" in texts and "韋伯式(HW)" not in texts
        assert "已取代 4 筆文字" in window.statusBar().currentMessage()
        assert "韋伯式硬度計" in window.settings.value(RULES_KEY)

        window.history_step(False)
        assert window.session.pdf == report_pdf

        # 下次開啟時沿用上次的規則。
        seen = []
        monkeypatch.setattr(BatchReplaceDialog, "exec",
            lambda dialog: seen.append(dialog.rule_pairs()) or 0)
        window.show_batch_replace_dialog()
        assert seen == [[("韋伯式(HW)", "真直度"), ("韋伯式硬度計", "厚薄規")]]
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_window_reports_items_that_could_not_be_replaced(qtbot, tmp_path, monkeypatch, report_pdf):
    window = _open_window(qtbot, tmp_path, monkeypatch, report_pdf)
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning",
        lambda parent, title, text: warnings.append((title, text)))
    try:
        candidates = find_candidates(window.session.pdf, (ReplaceRule("韋伯式(HW)", "真直度"),
            ReplaceRule("韋伯式硬度計", "這段取代文字長到縮小字級後也放不進儲存格裡面")))

        window.apply_batch_replace(candidates)

        assert "已取代 2 筆文字。2 筆未套用" in window.statusBar().currentMessage()
        (title, text), = warnings
        assert title == "部分文字未取代"
        assert "第 1 頁「韋伯式硬度計」" in text and "第 2 頁「韋伯式硬度計」" in text
        assert "Ctrl+Z" in text
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_batch_replace_is_in_search_menu_with_shortcut(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        action = window.actions["batch_replace"]
        assert action in window.search_menu.actions()
        assert action in QMainWindow.actions(window)
        assert action.shortcut().toString() == "Ctrl+H"
    finally:
        window.close()
