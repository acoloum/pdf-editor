"""圖層剪貼簿：墨頁專用格式的編碼、解碼，以及貼上位置的計算（不依賴 Qt）。"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import math
from pathlib import Path

from pdf_editor.model import Overlay, Rect

MIME_TYPE = "application/x-moye-pdf-layer"
FORMAT_VERSION = 1
# 貼上時與頁面邊緣保留的距離，避免旋轉後的取樣誤差讓圖片超出頁面。
_PAGE_MARGIN = 1.0


@dataclass(frozen=True)
class ClipboardLayer:
    """剪貼簿中的圖片；width／height 為 None 表示外部圖片，大小由貼上端決定。"""
    png: bytes
    width: float | None = None
    height: float | None = None
    angle: float = 0
    remove_white: bool = False


def encode_layer(layer: Overlay) -> bytes:
    """把圖層的原始圖檔與大小、角度、去除白底設定編成墨頁專用格式。"""
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
        if not isinstance(payload, dict) or payload.get("version") != FORMAT_VERSION:
            return None
        width = float(payload["width"])
        height = float(payload["height"])
        angle = float(payload["angle"])
        remove_white = payload["remove_white"]
        if (not all(math.isfinite(value) for value in (width, height, angle))
                or width <= 0 or height <= 0 or not isinstance(remove_white, bool)):
            return None
        png = base64.b64decode(payload["png"], validate=True)
        if not png:
            return None
        return ClipboardLayer(png, width, height, angle, remove_white)
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeDecodeError):
        return None


def paste_rect(center, width: float, height: float, page_size, angle: float = 0) -> Rect:
    """以 center 為中心放置圖片；未旋轉矩形與旋轉後外框都必須留在頁面內，放不下時等比例縮小。"""
    page_width, page_height = page_size
    radians = math.radians(angle)
    cos, sin = abs(math.cos(radians)), abs(math.sin(radians))
    box_width = max(width, width * cos + height * sin)
    box_height = max(height, width * sin + height * cos)
    usable_width = page_width - 2 * _PAGE_MARGIN
    usable_height = page_height - 2 * _PAGE_MARGIN
    scale = min(1.0, usable_width / box_width, usable_height / box_height)
    width, height = width * scale, height * scale
    box_width, box_height = box_width * scale, box_height * scale
    cx = min(max(center[0], _PAGE_MARGIN + box_width / 2), page_width - _PAGE_MARGIN - box_width / 2)
    cy = min(max(center[1], _PAGE_MARGIN + box_height / 2), page_height - _PAGE_MARGIN - box_height / 2)
    return (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)
