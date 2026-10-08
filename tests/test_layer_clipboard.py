"""圖層剪貼簿：專用格式編解碼與貼上位置計算。"""

import base64
import json

import pymupdf
import pytest
from PIL import Image

from pdf_editor.layer_clipboard import (
    FORMAT_VERSION,
    ClipboardLayer,
    decode_layer,
    encode_layer,
    paste_rect,
)
from pdf_editor.engine.overlay import flatten_overlays
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
    # 巢狀過深會讓 json 拋出 RecursionError。
    b"[" * 10000,
    # 超大整數轉成浮點數時會拋出 OverflowError。
    ('{"version":1,"width":' + "1" * 400 + ',"height":5,"angle":0,"remove_white":false,"png":"eA=="}').encode(),
    # 布林與字串不算數字。
    json.dumps({"version": True, "width": 5, "height": 5, "angle": 0,
                "remove_white": False, "png": "eA=="}).encode(),
    json.dumps({"version": FORMAT_VERSION, "width": True, "height": 5, "angle": 0,
                "remove_white": False, "png": "eA=="}).encode(),
    json.dumps({"version": FORMAT_VERSION, "width": "5", "height": 5, "angle": 0,
                "remove_white": False, "png": "eA=="}).encode(),
    json.dumps({"version": FORMAT_VERSION, "width": 5, "height": 5, "angle": False,
                "remove_white": False, "png": "eA=="}).encode(),
    # 退化的大小：極端寬扁或超出上限。
    json.dumps({"version": FORMAT_VERSION, "width": 1e308, "height": 1e-300, "angle": 0,
                "remove_white": False, "png": "eA=="}).encode(),
    json.dumps({"version": FORMAT_VERSION, "width": 100001, "height": 5, "angle": 0,
                "remove_white": False, "png": "eA=="}).encode(),
    json.dumps({"version": FORMAT_VERSION, "width": 0.05, "height": 5, "angle": 0,
                "remove_white": False, "png": "eA=="}).encode(),
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


@pytest.mark.parametrize("page_size, size, angle", [
    ((595, 842), (200, 100), 30),
    ((595, 842), (200, 100), 45),
    ((595, 842), (200, 100), 60),
    # 比頁面還大、必須先縮小的大圖；舊的固定 1pt 邊距在 45 度會超出頁面。
    # 大圖旋轉取樣很慢，只保留這個能重現問題的角度。
    ((4000, 4000), (12345, 6789), 45),
])
def test_paste_rect_rotated_layer_can_be_flattened_at_page_corners(tmp_path, page_size, size, angle):
    asset = _stamp(tmp_path)
    doc = pymupdf.open()
    doc.new_page(width=page_size[0], height=page_size[1])
    pdf = doc.tobytes()
    doc.close()
    width, height = page_size
    for center in ((0, 0), (width, 0), (0, height), (width, height)):
        rect = paste_rect(center, size[0], size[1], page_size, angle)
        layer = Overlay("貼上", 0, str(asset), rect, angle)

        assert flatten_overlays(pdf, (layer,))
