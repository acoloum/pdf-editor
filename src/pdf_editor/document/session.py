from pathlib import Path
from pdf_editor.document.history import History
from pdf_editor.engine.inspection import unlock_pdf
from pdf_editor.errors import EditorError

class DocumentSession:
    @classmethod
    def open(cls, path: Path, password=None):
        obj = cls()
        obj.source = Path(path).resolve()
        data, obj.access = unlock_pdf(obj.source.read_bytes(), password)
        obj.history = History(data)
        obj.saved_fingerprint = obj.history.current[2]
        obj.revision = 0
        obj.password_used = bool(password)
        return obj

    @property
    def pdf(self):
        return self.history.current[0].read_bytes()

    @property
    def overlays(self):
        return self.history.current[1]

    @property
    def dirty(self):
        return self.history.current[2] != self.saved_fingerprint

    @property
    def can_undo(self):
        return self.history.index > 0

    @property
    def can_redo(self):
        return self.history.index < len(self.history.items) - 1

    def _check_edit(self):
        if not self.access.can_edit:
            raise EditorError("READ_ONLY", self.access.reason or "文件僅供閱讀。")

    def apply_pdf(self, pdf):
        self._check_edit()
        self.history.push(pdf, self.overlays)
        self.revision += 1

    def set_overlays(self, items):
        self._check_edit()
        self.history.push(self.pdf, tuple(items))
        self.revision += 1

    def undo(self):
        if self.can_undo:
            self.history.index -= 1
            self.revision += 1

    def redo(self):
        if self.can_redo:
            self.history.index += 1
            self.revision += 1

    def close(self):
        self.history.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

