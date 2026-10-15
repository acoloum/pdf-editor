"""存檔被 Windows「受控制資料夾存取」封鎖或目標檔被占用時，顯示明確原因並記錄實際例外。"""
import sys

import pymupdf
import pytest

import pdf_editor.document.save as save
import pdf_editor.folder_access as folder_access
from pdf_editor.document.save import publish_batch, write_pdf
from pdf_editor.errors import EditorError
from pdf_editor.folder_access import (FolderBlockedError, blocked_program,
    controlled_folder_access_enabled)
from pdf_editor.logs import log_path
from pdf_editor.page_images import export_pages_as_png
from pdf_editor.ui.background_jobs import overwrite_document
from pdf_editor.workers import _invoke


def _blocked(monkeypatch, enabled=True):
    """模擬受控制資料夾存取：建立檔案時收到 FileNotFoundError（實機觀察到的行為）。"""
    def refuse(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")
    monkeypatch.setattr(folder_access.tempfile, "mkstemp", refuse)
    monkeypatch.setattr(folder_access, "controlled_folder_access_enabled", lambda: enabled)


def _files(folder):
    return sorted(path.name for path in folder.iterdir())


def test_blocked_overwrite_explains_how_to_allow_the_program(monkeypatch, source_path):
    original = source_path.read_bytes()
    _blocked(monkeypatch)

    with pytest.raises(FolderBlockedError) as error:
        overwrite_document(original, (), str(source_path))

    message = str(error.value)
    assert error.value.code == "FOLDER_BLOCKED"
    assert "受控制資料夾存取" in message and "原檔未被修改" in message
    assert str(source_path.parent) in message and blocked_program() in message
    assert "最近封鎖的應用程式" in message and "另存新檔" in message
    assert source_path.read_bytes() == original
    assert _files(source_path.parent) == [source_path.name]


def test_without_controlled_folder_access_keeps_the_general_save_error(monkeypatch, source_path,
        tmp_path):
    _blocked(monkeypatch, enabled=False)

    with pytest.raises(EditorError) as error:
        write_pdf(source_path.read_bytes(), tmp_path / "新.pdf")

    assert error.value.code == "SAVE_FAILED"
    assert isinstance(error.value.__cause__, FileNotFoundError)


def test_missing_folder_is_not_reported_as_blocked(monkeypatch, source_path, tmp_path):
    monkeypatch.setattr(folder_access, "controlled_folder_access_enabled", lambda: True)

    with pytest.raises(EditorError) as error:
        write_pdf(source_path.read_bytes(), tmp_path / "不存在" / "新.pdf")

    assert error.value.code == "SAVE_FAILED"


def test_target_in_use_keeps_original_and_says_so(monkeypatch, source_path):
    original = source_path.read_bytes()
    def locked(source, target):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(save.os, "replace", locked)

    with pytest.raises(EditorError) as error:
        overwrite_document(original, (), str(source_path))

    assert error.value.code == "FILE_IN_USE"
    assert source_path.name in str(error.value) and "其他程式" in str(error.value)
    assert source_path.read_bytes() == original
    # 暫存檔已清除。
    assert _files(source_path.parent) == [source_path.name]


def test_blocked_batch_export_writes_nothing(monkeypatch, pdf_bytes, tmp_path):
    _blocked(monkeypatch)

    with pytest.raises(FolderBlockedError):
        publish_batch((pdf_bytes, pdf_bytes), (tmp_path / "1.pdf", tmp_path / "2.pdf"))

    assert _files(tmp_path) == []


def test_blocked_png_export_is_reported(monkeypatch, pdf_bytes, tmp_path):
    _blocked(monkeypatch)

    with pytest.raises(FolderBlockedError):
        export_pages_as_png(pdf_bytes, (0,), tmp_path, "頁面")

    assert _files(tmp_path) == []


def test_background_job_logs_the_actual_exception(monkeypatch, source_path):
    _blocked(monkeypatch)

    ok, (code, message, completed) = _invoke(overwrite_document,
        (source_path.read_bytes(), (), str(source_path)))

    assert not ok and code == "FOLDER_BLOCKED" and completed == ()
    log = log_path().read_text(encoding="utf-8")
    assert "實際例外：FileNotFoundError" in log
    # 保留堆疊，才看得出是在哪一步失敗。
    assert "Traceback" in log and "mkstemp" not in message


@pytest.mark.skipif(sys.platform != "win32", reason="只有 Windows 有受控制資料夾存取")
@pytest.mark.parametrize("policy,user,expected", [
    (None, 1, True),      # 使用者在 Windows 安全性中開啟
    (None, 2, False),     # 稽核模式只記錄、不封鎖
    (None, None, False),  # 沒有設定
    (0, 1, False),        # 群組原則關閉，優先於使用者設定
    (1, 0, True),
])
def test_reads_block_mode_with_group_policy_first(monkeypatch, policy, user, expected):
    import winreg
    values = {"Policies": policy, "Microsoft": user}
    opened = []

    class Key:
        def __init__(self, path):
            self.path = path
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def open_key(root, path):
        assert root == winreg.HKEY_LOCAL_MACHINE
        opened.append(path)
        return Key(path)

    def query(handle, name):
        assert name == "EnableControlledFolderAccess"
        value = values["Policies" if "Policies" in handle.path else "Microsoft"]
        if value is None:
            raise FileNotFoundError(2, "找不到值")
        return value, winreg.REG_DWORD

    monkeypatch.setattr(winreg, "OpenKey", open_key)
    monkeypatch.setattr(winreg, "QueryValueEx", query)

    assert controlled_folder_access_enabled() is expected
    assert "Policies" in opened[0]


def test_installed_build_names_its_own_exe(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Programs\LocalPDFEditor\LocalPDFEditor.exe")
    monkeypatch.setattr(sys, "_base_executable", r"C:\Python\python.exe", raising=False)
    assert blocked_program() == r"C:\Programs\LocalPDFEditor\LocalPDFEditor.exe"
