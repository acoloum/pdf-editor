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
                data = io.BytesIO()
                image.convert("RGBA").save(data, format="PNG")
                content = data.getvalue()
        except Exception as exc:
            raise EditorError("IMAGE", "請選擇有效 PNG，圖片不可超過 2500 萬像素。") from exc
        # 暫存與收藏使用不同的 AssetStore 根目錄，由呼叫端決定生命週期。
        path = self.root / (hashlib.sha256(content).hexdigest() + ".png")
        if not path.exists():
            path.write_bytes(content)
        return path

