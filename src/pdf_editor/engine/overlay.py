import io
import math
from pathlib import Path
import pymupdf
from PIL import Image, ImageChops
from pdf_editor.errors import EditorError

# 去除白底：最暗色版 ≥ 235 完全透明，215～235 之間線性漸變，避免留下鋸齒白邊。
_WHITE_FADE_START = 215
_WHITE_CLEAR_FROM = 235
_KNOCKOUT_ALPHA = [
    255 if value <= _WHITE_FADE_START
    else 0 if value >= _WHITE_CLEAR_FROM
    else round(255 * (_WHITE_CLEAR_FROM - value) / (_WHITE_CLEAR_FROM - _WHITE_FADE_START))
    for value in range(256)
]


def _remove_white(image):
    """依每個像素最暗的色版決定透明度，並與原有透明度取較小值。"""
    red, green, blue, alpha = image.split()
    darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
    result = image.copy()
    result.putalpha(ImageChops.darker(alpha, darkest.point(_KNOCKOUT_ALPHA)))
    return result


def transformed_image(layer):
    return transformed_png(Path(layer.asset_path).read_bytes(), layer.rect, layer.angle,
        layer.remove_white)


def transformed_png(content, rect, angle, remove_white=False):
    """依正式輸出規則轉換 PNG，並回傳實際會繪製的頁面矩形。"""
    with Image.open(io.BytesIO(content)) as source:
        # 先依文件中的寬高重取樣，再旋轉；顯示與輸出共用此結果。
        x0, y0, x1, y1 = rect
        width, height = x1 - x0, y1 - y0
        if width <= 0 or height <= 0 or not all(math.isfinite(v) for v in (*rect, angle)):
            raise EditorError("GEOMETRY", "圖章位置或大小無效。")
        image = source.convert("RGBA")
    if remove_white:
        image = _remove_white(image)
    if math.isclose(angle % 360, 0, abs_tol=1e-9):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), rect
    scale = min(3.0, 3000 / max(width, height))
    image = image.resize((max(1, round(width * scale)),
        max(1, round(height * scale))), Image.Resampling.LANCZOS)
    image = image.rotate(-angle, expand=True, resample=Image.Resampling.BICUBIC)
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

