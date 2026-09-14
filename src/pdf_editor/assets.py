from pathlib import Path
import io
import hashlib
from PIL import Image
from pdf_editor.errors import EditorError

class AssetStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def import_png(self, source, persistent=True):
        try:
            with Image.open(source) as image:
                if image.format != "PNG" or image.width * image.height > 25000000:
                    raise ValueError()
                content = Path(source).read_bytes()
        except Exception as exc:
            raise EditorError("IMAGE", "請選擇有效 PNG，圖片不可超過 2500 萬像素。") from exc
        return self._store_rgba_png(content)

    def import_png_bytes(self, content: bytes) -> Path:
        try:
            with Image.open(io.BytesIO(content)) as image:
                if image.format != "PNG" or image.width * image.height > 25000000:
                    raise ValueError()
        except Exception as exc:
            raise EditorError("IMAGE", "內嵌圖章不是有效 PNG 或超過大小限制。") from exc
        return self._store_rgba_png(content)

    def _store_rgba_png(self, content: bytes) -> Path:
        try:
            with Image.open(io.BytesIO(content)) as image:
                data = io.BytesIO()
                image.convert("RGBA").save(data, format="PNG")
                normalized = data.getvalue()
        except Exception as exc:
            raise EditorError("IMAGE", "請選擇有效 PNG，圖片不可超過 2500 萬像素。") from exc
        # 暫存與收藏使用不同的 AssetStore 根目錄，由呼叫端決定生命週期。
        path = self.root / (hashlib.sha256(normalized).hexdigest() + ".png")
        if not path.exists():
            path.write_bytes(normalized)
        return path

