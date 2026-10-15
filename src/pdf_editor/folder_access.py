"""Windows 安全性的「受控制資料夾存取」（勒索軟體防護）：偵測存檔被封鎖，改以明確的處理步驟提示。

被封鎖時建立檔案會失敗，Python 收到的是 FileNotFoundError 或 PermissionError，
與一般的路徑或權限問題無法區分；因此在資料夾確實存在、且系統啟用封鎖模式時才判定為被封鎖。"""
import sys
import tempfile
from pathlib import Path

from pdf_editor.errors import EditorError

# 群組原則的設定優先於使用者在「Windows 安全性」中的設定。
_SETTING_KEYS = (
    r"SOFTWARE\Policies\Microsoft\Windows Defender\Windows Defender Exploit Guard\Controlled Folder Access",
    r"SOFTWARE\Microsoft\Windows Defender\Windows Defender Exploit Guard\Controlled Folder Access",
)
# 1 為封鎖模式；0 關閉、2 稽核模式，3 與 4 只保護磁碟區段，都不會擋下寫入資料夾。
_BLOCK_MODE = 1


def blocked_program():
    """被封鎖的程式路徑：安裝版是 LocalPDFEditor.exe；以虛擬環境執行原始碼時，
    venv 的 python.exe 只是轉接器，Windows 記錄與要允許的是底層的 Python。"""
    if getattr(sys, "frozen", False):
        return sys.executable
    return getattr(sys, "_base_executable", None) or sys.executable


class FolderBlockedError(EditorError):
    def __init__(self, folder, program=None):
        program = program or blocked_program()
        super().__init__("FOLDER_BLOCKED", (
            "Windows 的「受控制資料夾存取」（勒索軟體防護）不允許墨頁 PDF 寫入這個資料夾，原檔未被修改：\n"
            f"{folder}\n\n"
            "請擇一處理：\n"
            "1. 允許墨頁 PDF 寫入（需要系統管理員權限）：開啟「Windows 安全性」→「病毒與威脅防護」→"
            "「勒索軟體防護」下的「管理勒索軟體防護」→「允許應用程式通過受控制資料夾存取」→"
            "「新增允許的應用程式」→「最近封鎖的應用程式」，允許下列程式後再存一次：\n"
            f"{program}\n"
            "2. 改用「另存新檔」，存到不受保護的資料夾（桌面、文件、圖片等資料夾預設受保護）。"))
        self.folder = str(folder)


def controlled_folder_access_enabled():
    """系統是否以封鎖模式啟用受控制資料夾存取；一般使用者即可讀取這項設定。"""
    if sys.platform != "win32":
        return False
    import winreg
    for key in _SETTING_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
                value, _kind = winreg.QueryValueEx(handle, "EnableControlledFolderAccess")
        except OSError:
            continue
        return value == _BLOCK_MODE
    return False


def create_temp(folder, prefix=".pdf-editor-", suffix=".tmp"):
    """在目標資料夾建立暫存檔，回傳 (fd, 路徑)；被受控制資料夾存取封鎖時丟出 FolderBlockedError。"""
    folder = Path(folder)
    try:
        fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=folder)
    except OSError as exc:
        if folder.is_dir() and controlled_folder_access_enabled():
            raise FolderBlockedError(folder) from exc
        raise
    return fd, Path(name)
