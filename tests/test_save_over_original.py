"""覆蓋原檔儲存：覆寫保護與介面流程。"""

import pymupdf
import pytest

from pdf_editor.document.save import save_as
from pdf_editor.document.session import DocumentSession
from pdf_editor.errors import EditorError
from pdf_editor.ui.background_jobs import overwrite_document
from pdf_editor.ui.main_window import MainWindow
import pdf_editor.ui.main_window as main_window


def _edited(pdf, text="OVERWRITTEN"):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        doc[0].insert_text((40, 150), text, fontsize=12)
        return doc.tobytes()


def test_write_pdf_still_protects_the_source_by_default(source_path, tmp_path):
    with DocumentSession.open(source_path) as session:
        with pytest.raises(EditorError) as error:
            save_as(session, source_path, overwrite=True)
        assert error.value.code == "SOURCE"
        # 明確允許時才可覆寫來源。
        result = save_as(session, source_path, overwrite=True, allow_source=True)
        assert result == source_path


def test_overwrite_document_replaces_the_original(source_path):
    original = source_path.read_bytes()
    path = overwrite_document(_edited(original), (), str(source_path))
    assert path == str(source_path)
    assert source_path.read_bytes() != original
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
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_save_menu_offers_save_as(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        assert window.save_menu.actions() == [window.actions["save_as"]]
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
