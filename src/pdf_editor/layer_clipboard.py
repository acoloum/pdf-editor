"""圖層剪貼簿：墨頁專用格式的編碼、解碼，以及貼上位置的計算（不依賴 Qt）。"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import math
from pathlib import Path

from pdf_editor.model import Overlay, Rect

MIME_TYPE = "application/x-moye-pdf-layer"
FORMAT_VERSION = 1
# 貼上時與頁面邊緣至少保留的距離（點）；旋轉圖片另依取樣誤差加大，見 _page_margin。
_PAGE_MARGIN = 1.0
# 旋轉輸出（engine.overlay.transformed_png）的取樣上限與每點最大像素數。
_SAMPLE_MAX_PIXELS = 3000
_SAMPLE_MAX_SCALE = 3.0
# 取樣後旋轉的外框會因像素取整多出的像素數，邊界要留足這個餘裕。
_SAMPLE_SLACK_PIXELS = 2
# 專用格式可接受的圖片邊長範圍（點）；過小或過大都視為毀損資料。
_MIN_SIZE = 0.1
_MAX_SIZE = 100000


@dataclass(frozen=True)
class ClipboardLayer:
    """剪貼簿中的圖片；width／height 為 None 表示外部圖片，大小由貼上端決定。"""
    png: bytes
    width: float | None = None
    height: float | None = None
    angle: float = 0
    remove_white: bool = False


def encode_layer(layer: Overlay) -> bytes:
    """把圖層的原始圖檔與大小、角度、去除白底設定編成墨頁專用格式。

    圖檔遺失或無法讀取時會拋出 OSError，由呼叫端處理。
    """
    x0, y0, x1, y1 = layer.rect
    payload = {
        "version": FORMAT_VERSION,
        "width": x1 - x0,
        "height": y1 - y0,
        "angle": layer.angle,
        "remove_white": layer.remove_white,
        "png": base64.b64encode(Path(layer.asset_path).read_bytes()).decode("ascii"),
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def decode_layer(data: bytes) -> ClipboardLayer | None:
    """解析墨頁專用格式；版本不符或內容毀損時回傳 None，由呼叫端改用一般圖片。"""
    try:
        payload = json.loads(bytes(data).decode("utf-8"))
        if (not isinstance(payload, dict) or not _is_integer(payload.get("version"))
                or payload["version"] != FORMAT_VERSION):
            return None
        values = (payload["width"], payload["height"], payload["angle"])
        remove_white = payload["remove_white"]
        if not all(_is_number(value) for value in values) or not isinstance(remove_white, bool):
            return None
        width, height, angle = (float(value) for value in values)
        if (not math.isfinite(angle)
                or not all(_MIN_SIZE <= value <= _MAX_SIZE for value in (width, height))):
            return None
        png = base64.b64decode(payload["png"], validate=True)
        if not png:
            return None
        return ClipboardLayer(png, width, height, angle, remove_white)
    except Exception:
        # 任何程式都能寫入這個剪貼簿格式；不論是巢狀過深（RecursionError）、
        # 超大數字（OverflowError）或其他意外內容，都只能視為格式無效，不可讓例外外洩。
        return None


def _is_integer(value) -> bool:
    """JSON 整數；bool 雖是 int 的子類別，但不算數字。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    """JSON 數字（整數或浮點數），排除 bool 與字串。"""
    return _is_integer(value) or isinstance(value, float)


def _page_margin(width: float, height: float, angle: float) -> float:
    """貼上時與頁面邊緣的距離；旋轉圖片依輸出的取樣倍率多留幾個像素的餘裕。

    以縮小前的大小計算：圖片愈大取樣倍率愈低、像素取整的誤差愈大，所以這個值只會偏保守。
    """
    if math.isclose(angle % 360, 0, abs_tol=1e-9):
        return _PAGE_MARGIN
    scale = min(_SAMPLE_MAX_SCALE, _SAMPLE_MAX_PIXELS / max(width, height))
    return max(_PAGE_MARGIN, _SAMPLE_SLACK_PIXELS / scale)


def paste_rect(center, width: float, height: float, page_size, angle: float = 0) -> Rect:
    """以 center 為中心放置圖片；未旋轉矩形與旋轉後外框都必須留在頁面內，放不下時等比例縮小。"""
    page_width, page_height = page_size
    radians = math.radians(angle)
    cos, sin = abs(math.cos(radians)), abs(math.sin(radians))
    box_width = max(width, width * cos + height * sin)
    box_height = max(height, width * sin + height * cos)
    margin = _page_margin(width, height, angle)
    usable_width = page_width - 2 * margin
    usable_height = page_height - 2 * margin
    scale = min(1.0, usable_width / box_width, usable_height / box_height)
    width, height = width * scale, height * scale
    box_width, box_height = box_width * scale, box_height * scale
    cx = min(max(center[0], margin + box_width / 2), page_width - margin - box_width / 2)
    cy = min(max(center[1], margin + box_height / 2), page_height - margin - box_height / 2)
    return (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)
