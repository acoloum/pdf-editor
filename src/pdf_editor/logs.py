"""錯誤紀錄檔：主程序與背景工作程序共用，方便使用者回報問題。"""
import logging
import os
from pathlib import Path

LOG_NAME = "pdf-editor.log"
MAX_LOG_BYTES = 2_000_000

_logger = None


def log_directory():
    override = os.environ.get("PDF_EDITOR_LOG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "LocalPDFEditor" / "logs"


def log_path():
    return log_directory() / LOG_NAME


def get_logger():
    """取得共用記錄器；紀錄檔無法寫入時靜默略過，不影響主要功能。"""
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("pdf_editor")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        folder = log_directory()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / LOG_NAME
        # 啟動時超過上限就改名保留一份舊紀錄，避免檔案無限增長。
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            path.replace(folder / "pdf-editor.old.log")
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] pid=%(process)d %(message)s"))
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())
    _logger = logger
    return logger
