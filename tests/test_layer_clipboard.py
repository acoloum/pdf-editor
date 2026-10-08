"""圖層剪貼簿：專用格式編解碼與貼上位置計算。"""

import base64
import json
import math

import pytest
from PIL import Image

from pdf_editor.layer_clipboard import (
    FORMAT_VERSION,
    ClipboardLayer,
    decode_layer,
    encode_layer,
    paste_rect,
)
from pdf_editor.model import Overlay


def _stamp(tmp_path):
    path = tmp_path / "章.png"
    Image.new("RGBA", (40, 20), (200, 30, 30, 255)).save(path)
    return path


def test_encode_then_decode_keeps_layer_settings(tmp_path):
    path = _stamp(tmp_path)
    layer = Overlay("章", 0, str(path), (10, 20, 110, 70), 30, True)

    decoded = decode_layer(encode_layer(layer))

    assert decoded == ClipboardLayer(path.read_bytes(), 100, 50, 30, True)


@pytest.mark.parametrize("data", [
    b"",
    b"not json",
    json.dumps({"version": 999}).encode(),
    json.dumps({"version": FORMAT_VERSION, "width": -1, "height": 5, "angle": 0,
                "remove_white": False, "png": base64.b64encode(b"x").decode()}).encode(),
    json.dumps({"version": FORMAT_VERSION, "width": 5, "height": 5, "angle": 0,
                "remove_white": "yes", "png": base64.b64encode(b"x").decode()}).encode(),
    json.dumps({"version": FORMAT_VERSION, "width": 5, "height": 5, "angle": 0,
                "remove_white": False, "png": "!!!"}).encode(),
])
def test_decode_rejects_invalid_data(data):
    assert decode_layer(data) is None


def test_paste_rect_centers_on_point():
    assert paste_rect((250, 200), 100, 50, (500, 400)) == pytest.approx((200, 175, 300, 225))


def test_paste_rect_pushes_back_inside_page_with_margin():
    assert paste_rect((10, 10), 100, 50, (500, 400)) == pytest.approx((1, 1, 101, 51))
    assert paste_rect((495, 395), 100, 50, (500, 400)) == pytest.approx((399, 349, 499, 399))


def test_paste_rect_shrinks_image_larger_than_page():
    x0, y0, x1, y1 = paste_rect((250, 200), 1000, 200, (500, 400))

    assert (x0, x1) == pytest.approx((1, 499))
    assert (y1 - y0) / (x1 - x0) == pytest.approx(0.2)


def test_paste_rect_keeps_rotated_box_and_plain_rect_inside_page():
    # 旋轉 90 度時外框是 50×100；未旋轉矩形 100×50 也必須留在頁面內（工作層驗證會檢查）。
    x0, y0, x1, y1 = paste_rect((5, 5), 100, 50, (500, 400), 90)

    assert (x0, y0, x1, y1) == pytest.approx((1, 26, 101, 76))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    assert cy - 50 >= 1 and cx - 25 >= 1
