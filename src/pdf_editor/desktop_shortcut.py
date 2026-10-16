"""由墨頁 PDF 自己建立或移除桌面捷徑，供安裝程式在被「受控資料夾存取權」封鎖時改用。

安裝程式在暫存資料夾以隨機路徑執行，無法加入允許清單；墨頁 PDF 本身則可在安裝時加入。
為避免被其他程式借用來寫入或刪除受保護資料夾中的任意檔案，這裡不接受路徑參數：
只會處理目前使用者桌面上的「墨頁 PDF.lnk」，且捷徑一律指向執行中的墨頁 PDF。"""
import sys
from pathlib import Path

CREATE_FLAG = "--create-desktop-shortcut"
REMOVE_FLAG = "--remove-desktop-shortcut"
# 與安裝程式的 {userdesktop}\{#AppName}.lnk 相同。
SHORTCUT_NAME = "墨頁 PDF.lnk"


def desktop_shortcut_path():
    from PySide6.QtCore import QStandardPaths
    folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
    if not folder:
        raise OSError("找不到桌面資料夾。")
    return Path(folder) / SHORTCUT_NAME


def create_desktop_shortcut(target, link=None):
    """建立（或更新）指向 target 的桌面捷徑；Qt 在 Windows 上會建立 .lnk 並以程式所在資料夾為工作目錄。"""
    from PySide6.QtCore import QFile
    link = Path(link) if link is not None else desktop_shortcut_path()
    if link.exists():
        link.unlink()
    if not QFile.link(str(target), str(link)) or not link.exists():
        raise OSError(f"無法建立捷徑：{link}")
    return link


def remove_desktop_shortcut(link=None):
    link = Path(link) if link is not None else desktop_shortcut_path()
    link.unlink(missing_ok=True)
    return link


def run_shortcut_command(args):
    """處理命令列旗標，回傳結束代碼：0 成功、1 失敗（例如仍被封鎖）、2 參數錯誤。"""
    if len(args) != 1 or args[0] not in (CREATE_FLAG, REMOVE_FLAG):
        return 2
    from pdf_editor.logs import get_logger
    try:
        if args[0] == CREATE_FLAG:
            link = create_desktop_shortcut(sys.executable)
            get_logger().info("已建立桌面捷徑：%s", link)
        else:
            link = remove_desktop_shortcut()
            get_logger().info("已移除桌面捷徑：%s", link)
    except OSError:
        get_logger().exception("處理桌面捷徑失敗：%s", args[0])
        return 1
    return 0
