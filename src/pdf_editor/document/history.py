from pathlib import Path
import tempfile
import json
import hashlib
import shutil
import os
import msvcrt
from dataclasses import asdict
from pdf_editor.model import Overlay

def fingerprint(pdf, overlays):
    return hashlib.sha256(pdf + json.dumps([asdict(o) for o in overlays],
        sort_keys=True).encode()).hexdigest()

def history_root():
    root = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "LocalPDFEditor" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root

def cleanup_stale():
    root = history_root().resolve()
    for path in root.iterdir():
        if not path.is_dir() or path.is_symlink() or path.resolve().parent != root:
            continue
        marker = path / "session.lock"
        if not marker.is_file():
            continue
        try:
            with marker.open("r+b") as handle:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            shutil.rmtree(path)
        except OSError:
            pass

MAX_ITEMS = 31
MAX_TOTAL_BYTES = 400_000_000


class History:
    def __init__(self, pdf, overlays=()):
        self.root = Path(tempfile.mkdtemp(prefix="session-", dir=history_root()))
        self.lock = (self.root / "session.lock").open("w+b")
        self.lock.write(b"1")
        self.lock.flush()
        self.lock.seek(0)
        msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
        self.items = []
        self.index = -1
        self.serial = 0
        self.push(pdf, overlays)

    def replace_initial(self, pdf, overlays):
        """以已驗證工作層取代剛建立的初始狀態，不建立復原步驟。"""
        assert self.index == 0 and len(self.items) == 1
        path = self.items[0][0]
        overlays = tuple(overlays)
        path.write_bytes(pdf)
        self.items[0] = (path, overlays, fingerprint(pdf, overlays))

    def push(self, pdf, overlays):
        for item in self.items[self.index + 1:]:
            self._remove_file(item[0])
        self.items = self.items[:self.index + 1]
        path = self.root / f"{self.serial}.pdf"
        path.write_bytes(pdf)
        self.serial += 1
        self.items.append((path, tuple(overlays), fingerprint(pdf, overlays)))
        self._trim()
        self.index = len(self.items) - 1

    def can_discard(self, count=1):
        """目前是否位於最新一步，且往前至少還有 count 步可捨棄。"""
        return self.index == len(self.items) - 1 and self.index >= count

    def discard_last(self):
        """撤銷最新一筆且不留重做紀錄，供未改動的暫時轉換使用。"""
        if not self.can_discard():
            raise ValueError("只能捨棄最新的一筆歷程。")
        # 先更新狀態再刪檔，刪檔失敗也不會讓索引失效。
        path = self.items.pop()[0]
        self.index -= 1
        self._remove_file(path)

    @staticmethod
    def _remove_file(path):
        """盡力刪除暫存檔。"""
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Windows 上檔案可能仍被背景工作開啟；殘留檔案不再被引用，
            # 序號不會重複使用，關閉時由 close() 整個資料夾清除。
            pass

    def _trim(self):
        """限制復原步數與暫存總容量，大型文件不會佔滿磁碟。"""
        def total():
            return sum(item[0].stat().st_size for item in self.items if item[0].exists())
        used = total()
        while len(self.items) > 1 and (len(self.items) > MAX_ITEMS or used > MAX_TOTAL_BYTES):
            removed = self.items.pop(0)[0]
            try:
                used -= removed.stat().st_size
            except OSError:
                pass
            self._remove_file(removed)

    @property
    def current(self):
        return self.items[self.index]

    def close(self):
        self.lock.close()
        shutil.rmtree(self.root, ignore_errors=True)

