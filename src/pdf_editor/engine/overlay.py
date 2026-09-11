import io
import math
import pymupdf
from PIL import Image
from pdf_editor.errors import EditorError

def transformed_image(layer):
    with Image.open(layer.asset_path) as source:
        # 先依文件中的寬高重取樣，再旋轉；顯示與輸出共用此結果。
        x0, y0, x1, y1 = layer.rect
        width, height = x1 - x0, y1 - y0
        if width <= 0 or height <= 0 or not all(math.isfinite(v) for v in (*layer.rect, layer.angle)):
            raise EditorError("GEOMETRY", "圖章位置或大小無效。")
        scale = min(3.0, 3000 / max(width, height))
        image = source.convert("RGBA").resize((max(1, round(width * scale)),
            max(1, round(height * scale))), Image.Resampling.LANCZOS)
        image = image.rotate(-layer.angle, expand=True, resample=Image.Resampling.BICUBIC)
        w, h = image.width / scale, image.height / scale
        cx, cy = (x0+x1)/2, (y0+y1)/2
        rect = (cx-w/2, cy-h/2, cx+w/2, cy+h/2)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), rect

def flatten_overlays(pdf, overlays):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for layer in overlays:
            if not 0 <= layer.page < doc.page_count:
                raise EditorError("PAGE", "圖章所在頁面不存在。")
            data, rect = transformed_image(layer)
            page = doc[layer.page]
            bounds = pymupdf.Rect(0,0,page.cropbox.width,page.cropbox.height)
            if not bounds.contains(rect):
                raise EditorError("GEOMETRY", "旋轉後的圖章超出頁面，請移動或縮小。")
            page.insert_image(rect, stream=data, keep_proportion=False)
        return doc.tobytes(garbage=4, deflate=True)

