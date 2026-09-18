from pathlib import Path
from pdf_editor.document.history import History
from pdf_editor.engine.inspection import unlock_pdf
from pdf_editor.errors import EditorError
from pdf_editor.persistent_overlays import load_workspace


def _load_workspace_or_none(pdf, asset_root):
    """工作層無效時保留可見 PDF，並回傳可供介面顯示的提示。"""
    try:
        return load_workspace(pdf, asset_root), None
    except EditorError as exc:
        if exc.code != "WORKSPACE":
            raise
        return None, str(exc)

class DocumentSession:
    @classmethod
    def open(cls, path: Path, password=None):
        obj = cls()
        obj.source = Path(path).resolve()
        data, obj.access = unlock_pdf(obj.source.read_bytes(), password)
        obj.history = History(data)
        workspace, obj.open_notice = _load_workspace_or_none(
            data, obj.history.root / "assets"
        )
        if workspace:
            obj.history.replace_initial(workspace.base_pdf, workspace.overlays)
        obj.saved_fingerprint = obj.history.current[2]
        obj.revision = 0
        obj.password_used = bool(password)
        return obj

    @property
    def pdf(self):
        """目前版本的 PDF 內容；同一版本重複取用時不再重讀檔案。"""
        path, _overlays, fingerprint = self.history.current
        cached = getattr(self, "_pdf_cache", None)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        data = path.read_bytes()
        self._pdf_cache = (fingerprint, data)
        return data

    @property
    def pdf_path(self):
        """目前版本的暫存檔路徑，供背景工作直接讀取，不必複製內容。"""
        return self.history.current[0]

    @property
    def document_key(self):
        """目前版本的識別碼，供快取索引使用。"""
        return self.history.current[2]

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
        self.apply_state(pdf, self.overlays)

    def apply_state(self, pdf, overlays):
        self._check_edit()
        self.history.push(pdf, tuple(overlays))
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
