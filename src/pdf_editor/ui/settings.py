"""使用者偏好設定：最近開啟檔案、最後資料夾與視窗狀態。"""
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings

MAX_RECENT_FILES = 10


class AppSettings:
    """以 INI 格式保存設定，方便測試時改寫儲存位置。"""

    def __init__(self):
        self._settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
            "LocalPDFEditor", "LocalPDFEditor")

    def recent_files(self):
        value = self._settings.value("recent/files", [])
        if isinstance(value, str):
            value = [value] if value else []
        return [str(item) for item in (value or []) if str(item)]

    def add_recent_file(self, path):
        path = str(Path(path).resolve())
        files = [item for item in self.recent_files() if Path(item) != Path(path)]
        files.insert(0, path)
        self._settings.setValue("recent/files", files[:MAX_RECENT_FILES])
        self._settings.sync()

    def remove_recent_file(self, path):
        files = [item for item in self.recent_files() if Path(item) != Path(path)]
        self._settings.setValue("recent/files", files)
        self._settings.sync()

    def clear_recent_files(self):
        self._settings.setValue("recent/files", [])
        self._settings.sync()

    def last_directory(self):
        folder = str(self._settings.value("paths/last_directory", "") or "")
        return folder if folder and Path(folder).is_dir() else ""

    def set_last_directory(self, path):
        folder = Path(path)
        if folder.suffix:
            folder = folder.parent
        self._settings.setValue("paths/last_directory", str(folder))

    def bytes_value(self, key):
        value = self._settings.value(key)
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    def value(self, key, default=None):
        return self._settings.value(key, default)

    def set_value(self, key, value):
        self._settings.setValue(key, value)

    def sync(self):
        self._settings.sync()
