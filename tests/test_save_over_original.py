"""覆蓋原檔儲存：備份、覆寫保護與介面流程。"""
import os
import time
from pathlib import Path

import pymupdf
import pytest

from pdf_editor.document.save import (backup_original, prune_backups, save_as, write_pdf)
from pdf_editor.document.session import DocumentSession
from pdf_editor.errors import EditorError
from pdf_editor.ui.background_jobs import overwrite_document
from pdf_editor.ui.main_window import MainWindow
import pdf_editor.ui.main_window as main_window


def _edited(pdf, text="OVERWRITTEN"):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        doc[0].insert_text((40, 150), text, fontsize=12)
        return doc.tobytes()


def test_backup_original_copies_file_with_timestamp(tmp_path, source_path):
    folder = tmp_path / "backups"
    first = backup_original(source_path, folder)
    second = backup_original(source_path, folder)
    assert first.read_bytes() == source_path.read_bytes()
    # 同一秒內連續備份不會互相覆蓋。
    assert first != second
    assert second.exists()
    assert source_path.stem in first.name
    assert backup_original(tmp_path / "不存在.pdf", folder) is None


def test_prune_backups_removes_old_and_oversized(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    old = folder / "20200101-000000_舊檔.pdf"
    old.write_bytes(b"x" * 100)
    stale = time.time() - 90 * 86400
    os.utime(old, (stale, stale))
    recent = [folder / f"2026010{index}-000000_新檔.pdf" for index in range(1, 4)]
    for item in recent:
        item.write_bytes(b"y" * 1000)
    removed = prune_backups(folder, limit=2500, keep_days=60)
    assert not old.exists()
    assert removed >= 1
    assert sum(item.stat().st_size for item in folder.glob("*.pdf")) <= 2500


def test_write_pdf_still_protects_the_source_by_default(source_path, tmp_path):
    with DocumentSession.open(source_path) as session:
        with pytest.raises(EditorError) as error:
            save_as(session, source_path, overwrite=True)
        assert error.value.code == "SOURCE"
        # 明確允許時才可覆寫來源。
        result = save_as(session, source_path, overwrite=True, allow_source=True)
        assert result == source_path


def test_overwrite_document_keeps_a_backup(tmp_path, source_path):
    original = source_path.read_bytes()
    folder = tmp_path / "backups"
    path, backup = overwrite_document(_edited(original), (), str(source_path), str(folder))
    assert path == str(source_path)
    assert source_path.read_bytes() != original
    assert Path(backup).read_bytes() == original
    with pymupdf.open(source_path) as doc:
        assert "OVERWRITTEN" in doc[0].get_text()


def test_window_save_overwrites_original_and_clears_dirty(qtbot, source_path, monkeypatch):
    original = source_path.read_bytes()
    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(main_window.QMessageBox, "question",
        lambda *args, **kwargs: main_window.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(MainWindow, "confirm_overwrite", lambda self, source: True)
    try:
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(item for item in window.page_data["runs"] if "KEEP" in item.text)
        window.delete_run(run)
        qtbot.waitUntil(lambda: not window.busy and window.session.dirty, timeout=30000)

        window.actions["save"].trigger()

        qtbot.waitUntil(lambda: not window.busy and not window.session.dirty, timeout=30000)
        assert source_path.read_bytes() != original
        assert "已儲存" in window.statusBar().currentMessage()
        assert "原檔備份" in window.statusBar().currentMessage()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_save_menu_offers_save_as_and_backup_folder(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        labels = [action.text() for action in window.save_menu.actions()]
        assert window.actions["save_as"] in window.save_menu.actions()
        assert "開啟原檔備份資料夾" in labels
        assert window.actions["save"].text() == "儲存"
        assert window.actions["save"].menu() is window.save_menu
    finally:
        window.close()


def test_overwrite_confirmation_can_be_remembered(qtbot, source_path, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    shown = []

    class FakeBox:
        def __init__(self, parent=None):
            shown.append(parent)
            self._check = None

        def setIcon(self, *_):
            pass

        setWindowTitle = setText = setInformativeText = setStandardButtons = setDefaultButton = setIcon

        def setCheckBox(self, box):
            self._check = box
            box.setChecked(True)

        def exec(self):
            return main_window.QMessageBox.StandardButton.Save

    monkeypatch.setattr(main_window, "QMessageBox", type("Box", (), {
        "Icon": main_window.QMessageBox.Icon,
        "StandardButton": main_window.QMessageBox.StandardButton,
        "__new__": lambda cls, parent=None: FakeBox(parent)}))
    try:
        assert window.confirm_overwrite(source_path)
        assert window.settings.value("save/skip_overwrite_confirm", "") == "yes"
        # 記住選擇後不再顯示確認視窗。
        assert window.confirm_overwrite(source_path)
        assert len(shown) == 1
    finally:
        window.close()
