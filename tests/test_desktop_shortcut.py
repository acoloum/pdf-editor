"""安裝程式被受控資料夾存取權擋下時，改由墨頁 PDF 自己建立與移除桌面捷徑。"""
import sys

import pytest

import pdf_editor.desktop_shortcut as desktop_shortcut
from pdf_editor.__main__ import main
from pdf_editor.desktop_shortcut import (CREATE_FLAG, REMOVE_FLAG, SHORTCUT_NAME,
    run_shortcut_command)


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    """把桌面換成暫存資料夾，測試不會動到使用者真正的桌面。"""
    folder = tmp_path / "桌面"
    folder.mkdir()
    monkeypatch.setattr(desktop_shortcut, "desktop_shortcut_path", lambda: folder / SHORTCUT_NAME)
    return folder


def test_command_creates_shortcut_to_running_program_and_removes_it(desktop):
    link = desktop / SHORTCUT_NAME

    assert main([CREATE_FLAG]) == 0
    assert link.is_file()
    from PySide6.QtCore import QFileInfo
    assert QFileInfo(str(link)).symLinkTarget().lower() == sys.executable.replace("\\", "/").lower()

    # 重複建立時取代舊捷徑。
    assert main([CREATE_FLAG]) == 0
    assert main([REMOVE_FLAG]) == 0
    assert not link.exists()
    # 已經沒有捷徑時移除也算成功。
    assert main([REMOVE_FLAG]) == 0


@pytest.mark.parametrize("args", [
    [CREATE_FLAG, r"C:\Users\someone\Documents\任意.lnk"],
    [REMOVE_FLAG, r"C:\Users\someone\Documents\重要.docx"],
    ["--create-desktop-shortcut=x"],
])
def test_rejects_extra_arguments_so_no_other_file_can_be_touched(desktop, args):
    assert run_shortcut_command(args) == 2
    assert list(desktop.iterdir()) == []


def test_blocked_write_returns_failure_code(desktop, monkeypatch):
    from PySide6.QtCore import QFile
    monkeypatch.setattr(QFile, "link", staticmethod(lambda target, link: False))
    assert run_shortcut_command([CREATE_FLAG]) == 1
