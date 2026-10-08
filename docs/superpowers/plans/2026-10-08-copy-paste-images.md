# 圖片複製與貼上 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 選取圖層按 `Ctrl+C` 複製（同時放墨頁專用格式與 PNG 到 Windows 剪貼簿），按 `Ctrl+V` 以滑鼠位置為中心貼成新圖層；也能貼上其他程式複製的圖片。

**Architecture:** 新增不依賴 Qt 的 `layer_clipboard.py` 負責專用格式編解碼與貼上矩形計算；`stamp_actions.py` 負責與剪貼簿、工作階段互動；`main_window.py` 掛上 `Ctrl+C`（依最後選取對象分派）與 `Ctrl+V` 動作及右鍵選單；`canvas.py` 提供游標下的圖層查詢。貼上沿用既有 `Overlay`、`AssetStore`、`set_overlays` 歷程與縮圖更新。

**Tech Stack:** Python 3.12、PySide6（`QClipboard`、`QMimeData`）、PyMuPDF、Pillow、pytest／pytest-qt。

**規格：** `docs/superpowers/specs/2026-10-08-copy-paste-images-design.md`

**共同約定：**
- 工作目錄 `C:\墨頁PDF\pdf-editor`，分支 `feature/copy-paste-images`。
- 測試一律用 `work/build-venv312/Scripts/python.exe -m pytest ...`。
- 註解、docstring、使用者訊息、commit 訊息一律繁體中文；commit 訊息結尾加上 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。
- `ui/*.py` 用緊湊寫法（逗號後不空格）；其他模組用 PEP 8。工作目錄是 CRLF（autocrlf=true），diff 只能有實際改動（Git Bash 的 `sed -i` 會把 CRLF 改成 LF，避免使用）。
- 測試中不得留下開著的強制對話框（需要時 monkeypatch `QMessageBox`）。

---

## 檔案結構

| 檔案 | 動作 | 責任 |
|---|---|---|
| `src/pdf_editor/layer_clipboard.py` | 新增 | `MIME_TYPE`、`ClipboardLayer`、`encode_layer`、`decode_layer`、`paste_rect` |
| `src/pdf_editor/ui/canvas.py` | 修改 | `layer_at(view_pos)` |
| `src/pdf_editor/ui/stamp_actions.py` | 修改 | `selected_layer`、`copy_layer`、`copy_selection`、`clipboard_layer`、`can_paste_image`、`paste_layer_at`、`paste_image` |
| `src/pdf_editor/ui/text_actions.py` | 修改 | 選文字／註解時記錄最後選取對象 |
| `src/pdf_editor/ui/main_window.py` | 修改 | `last_selection` 狀態、`Ctrl+C` 分派、`paste_image` 動作、右鍵選單 |
| `tests/test_layer_clipboard.py` | 新增 | 純函式測試 |
| `tests/test_copy_paste.py` | 新增 | 介面流程測試 |
| `tests/test_ui.py` | 修改 | `layer_at` 測試 |
| `docs/使用說明.md` | 修改 | 新增說明段落 |

---

### Task 1：剪貼簿格式與貼上矩形

**Files:**
- Create: `src/pdf_editor/layer_clipboard.py`
- Create: `tests/test_layer_clipboard.py`

- [ ] **Step 1：寫失敗測試**

建立 `tests/test_layer_clipboard.py`：

```python
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
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_layer_clipboard.py -q`
Expected: 收集失敗，`ModuleNotFoundError: No module named 'pdf_editor.layer_clipboard'`

- [ ] **Step 3：實作**

建立 `src/pdf_editor/layer_clipboard.py`：

```python
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
```

- [ ] **Step 4：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_layer_clipboard.py -q`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add src/pdf_editor/layer_clipboard.py tests/test_layer_clipboard.py
git commit -m "功能：新增圖層剪貼簿格式與貼上位置計算" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2：畫布查詢游標下的圖層

**Files:**
- Modify: `src/pdf_editor/ui/canvas.py`（在 `_annotation_at` 之後）
- Test: `tests/test_ui.py`（在 `test_canvas_layer_uses_device_pixels_on_high_dpi` 之後）

- [ ] **Step 1：寫失敗測試**

```python
def test_canvas_layer_at_returns_layer_under_view_point(qtbot, pdf_bytes, tmp_path):
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (40, 40), (210, 35, 45, 255)).save(stamp)
    layer = Overlay("layer-at", 0, str(stamp), (300, 200, 340, 240), 0)
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    canvas.display(render_page(pdf_bytes, 0, 1.0), (layer,))
    qtbot.waitExposed(canvas)

    assert canvas.layer_at(_view_point(canvas, 320, 220)) == layer
    assert canvas.layer_at(_view_point(canvas, 450, 380)) is None
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_ui.py -q -k layer_at`
Expected: FAIL，`AttributeError: 'Canvas' object has no attribute 'layer_at'`

- [ ] **Step 3：實作**

`src/pdf_editor/ui/canvas.py` 在 `_annotation_at` 之後加入：

```python
    def layer_at(self,view_pos):
        """視窗座標下最上層的圖層；沒有時回傳 None。"""
        for item in self.items(view_pos):
            if isinstance(item,LayerItem):
                return item.layer
        return None
```

- [ ] **Step 4：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_ui.py -q -k "layer_at or canvas_layer"`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add src/pdf_editor/ui/canvas.py tests/test_ui.py
git commit -m "功能：畫布可查詢游標下的圖層" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3：複製圖層與 Ctrl+C 分派

**Files:**
- Modify: `src/pdf_editor/ui/stamp_actions.py`
- Modify: `src/pdf_editor/ui/text_actions.py`（`select_run`、`select_annotation`）
- Modify: `src/pdf_editor/ui/main_window.py`（`__init__` 狀態、`copy_text` 動作、`open_document`）
- Create: `tests/test_copy_paste.py`

- [ ] **Step 1：寫失敗測試**

建立 `tests/test_copy_paste.py`：

```python
"""圖片複製與貼上：剪貼簿格式、貼上規則與 Ctrl+C／Ctrl+V。"""

import io
from dataclasses import replace

import pymupdf
import pytest
from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from pdf_editor.layer_clipboard import MIME_TYPE, decode_layer
from pdf_editor.ui.main_window import MainWindow


@pytest.fixture
def red_stamp(tmp_path):
    path = tmp_path / "紅章.png"
    Image.new("RGBA", (200, 100), (210, 35, 45, 255)).save(path)
    return path


@pytest.fixture
def white_stamp(tmp_path):
    path = tmp_path / "白底章.png"
    Image.new("RGBA", (200, 100), (255, 255, 255, 255)).save(path)
    return path


def _open(qtbot, path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_document(path)
    qtbot.waitUntil(lambda: window.page_data is not None and not window.busy, timeout=30000)
    return window


def _close(window):
    if window.session is not None:
        window.session.saved_fingerprint = window.session.history.current[2]
    window.close()


def _size(rect):
    return rect[2] - rect[0], rect[3] - rect[1]


def test_copy_layer_puts_moye_format_and_png_on_clipboard(qtbot, multi_page_path, red_stamp):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(red_stamp, False)
        layer = window.session.overlays[-1]

        window.copy_selection()

        mime = QApplication.clipboard().mimeData()
        decoded = decode_layer(bytes(mime.data(MIME_TYPE)))
        assert decoded is not None
        assert (decoded.width, decoded.height) == pytest.approx(_size(layer.rect))
        assert mime.hasImage()
        assert bytes(mime.data("image/png")).startswith(b"\x89PNG")
        assert "已複製圖片" in window.statusBar().currentMessage()
    finally:
        _close(window)


def test_copy_with_remove_white_exports_transparent_png(qtbot, multi_page_path, white_stamp):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(white_stamp, False)
        window.set_layer_remove_white(True)
        qtbot.waitUntil(lambda: not window.busy, timeout=30000)

        window.copy_selection()

        png = bytes(QApplication.clipboard().mimeData().data("image/png"))
        with Image.open(io.BytesIO(png)) as image:
            rgba = image.convert("RGBA")
            assert rgba.getpixel((rgba.width // 2, rgba.height // 2))[3] == 0
    finally:
        _close(window)


def test_ctrl_c_copies_text_when_text_was_selected_last(qtbot, source_path, red_stamp):
    window = _open(qtbot, source_path)
    try:
        window.import_layer(red_stamp, False)
        run = next(r for r in window.page_data["runs"] if "KEEP" in r.text)
        window.select_run(run, open_editor=False)

        window.actions["copy_text"].trigger()

        assert QApplication.clipboard().text() == run.text
        assert not QApplication.clipboard().mimeData().hasFormat(MIME_TYPE)
    finally:
        _close(window)


def test_ctrl_c_copies_layer_when_layer_was_selected_last(qtbot, source_path, red_stamp):
    window = _open(qtbot, source_path)
    try:
        run = next(r for r in window.page_data["runs"] if "KEEP" in r.text)
        window.select_run(run, open_editor=False)
        window.import_layer(red_stamp, False)

        window.actions["copy_text"].trigger()

        assert QApplication.clipboard().mimeData().hasFormat(MIME_TYPE)
    finally:
        _close(window)
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_copy_paste.py -q`
Expected: FAIL，`AttributeError: 'MainWindow' object has no attribute 'copy_selection'`

- [ ] **Step 3：`stamp_actions.py` 加入複製**

import 區加入：

```python
from PySide6.QtCore import QMimeData
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pdf_editor.engine.overlay import flatten_overlays, transformed_image
from pdf_editor.layer_clipboard import MIME_TYPE, encode_layer
```

（`flatten_overlays` 原本已 import，改成同一行；`QFileDialog` 的 import 保留並與 `QApplication` 合併為 `from PySide6.QtWidgets import QApplication,QFileDialog`。）

在 `select_layer` 之前加入：

```python
    def selected_layer(self):
        """目前選取的圖層；沒有時回傳 None。"""
        if not self.session:
            return None
        return next((o for o in self.session.overlays if o.id==self.layer_id),None)

    def copy_selection(self):
        """Ctrl+C：最後選取的是圖層就複製圖片，否則照舊複製文字。"""
        layer=self.selected_layer() if self.last_selection=="layer" else None
        if layer is not None:
            self.copy_layer(layer)
            return
        self.copy_selected_text()

    def copy_layer(self,layer):
        """同時放入墨頁專用格式與畫面外觀的 PNG，墨頁與其他程式都能貼上。"""
        try:
            data=encode_layer(layer)
            png,_rect=transformed_image(layer)
        except Exception:
            self.statusBar().showMessage("無法複製圖片，圖檔可能已被移除。")
            return
        mime=QMimeData()
        mime.setData(MIME_TYPE,data)
        mime.setData("image/png",png)
        mime.setImageData(QImage.fromData(png,"PNG"))
        QApplication.clipboard().setMimeData(mime)
        self.statusBar().showMessage("已複製圖片；按 Ctrl+V 貼在滑鼠位置，也可以貼到其他程式。")
```

`select_layer` 的第一行之前加入 `self.last_selection="layer"`。

- [ ] **Step 4：`text_actions.py` 記錄文字與註解選取**

`select_run` 在 `if not self.session or not self.session.access.can_edit: return` 之後加入：

```python
        self.last_selection="text"
```

`select_annotation` 第一行之前加入：

```python
        self.last_selection="annotation"
```

- [ ] **Step 5：`main_window.py` 狀態與動作**

`__init__` 在 `self.pending_conversion=None` 之後加入：

```python
        # 最後選取的對象（"layer"／"text"／"annotation"），決定 Ctrl+C 複製圖片還是文字。
        self.last_selection=None
```

動作表中 `copy_text` 那一列改為：

```python
            ("copy_text","複製",self.copy_selection,("Ctrl+C",),"複製選取的文字或圖片（Ctrl+C）"),
```

`open_document` 中 `self.pending_conversion=None` 之後加入 `self.last_selection=None`。

- [ ] **Step 6：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_copy_paste.py tests/test_document_tools.py -q`
Expected: 全部 PASS。若既有測試斷言 `copy_text` 動作的文字「複製文字」，改為新的「複製」（只改期望值）。

- [ ] **Step 7：Commit**

```bash
git add src/pdf_editor/ui/stamp_actions.py src/pdf_editor/ui/text_actions.py src/pdf_editor/ui/main_window.py tests/test_copy_paste.py
git commit -m "功能：Ctrl+C 可複製選取的圖層到剪貼簿，並依最後選取對象決定複製圖片或文字" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4：貼上圖片與 Ctrl+V

**Files:**
- Modify: `src/pdf_editor/ui/stamp_actions.py`
- Modify: `src/pdf_editor/ui/main_window.py`（動作表、`refresh_actions`）
- Test: `tests/test_copy_paste.py`

- [ ] **Step 1：寫失敗測試**

`tests/test_copy_paste.py` 最後加入：

```python
def _set_clipboard_image(width, height, color=(30, 90, 200)):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(*color))
    QApplication.clipboard().setImage(image)


def test_paste_keeps_size_angle_and_remove_white(qtbot, multi_page_path, red_stamp):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(red_stamp, False)
        source = replace(window.session.overlays[-1], rect=(20, 20, 80, 50), angle=30,
                         remove_white=True)
        window.move_layer(source)
        window.copy_selection()

        window.paste_layer_at((150, 100))

        pasted = window.session.overlays[-1]
        assert pasted.id != source.id and window.layer_id == pasted.id
        assert _size(pasted.rect) == pytest.approx(_size(source.rect))
        assert (pasted.angle, pasted.remove_white) == (30, True)
        cx, cy = (pasted.rect[0] + pasted.rect[2]) / 2, (pasted.rect[1] + pasted.rect[3]) / 2
        assert (cx, cy) == pytest.approx((150, 100))
    finally:
        _close(window)


def test_paste_on_other_page_and_undo(qtbot, multi_page_path, red_stamp):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(red_stamp, False)
        window.copy_selection()
        window.goto_page(1)
        qtbot.waitUntil(lambda: window.page_data["page"] == 1, timeout=30000)

        window.paste_layer_at((150, 100))
        assert [o.page for o in window.session.overlays] == [0, 1]

        window.history_step(False)
        assert [o.page for o in window.session.overlays] == [0]
    finally:
        _close(window)


def test_paste_into_another_document(qtbot, multi_page_path, source_path, red_stamp):
    first = _open(qtbot, multi_page_path)
    try:
        first.import_layer(red_stamp, False)
        first.copy_selection()
    finally:
        _close(first)
    second = _open(qtbot, source_path)
    try:
        second.paste_layer_at((250, 200))

        pasted = second.session.overlays[-1]
        assert str(second.session.history.root) in pasted.asset_path
    finally:
        _close(second)


def test_paste_external_image_uses_stamp_size_rule(qtbot, multi_page_path):
    window = _open(qtbot, multi_page_path)
    try:
        _set_clipboard_image(400, 200)

        window.paste_layer_at((150, 100))

        pasted = window.session.overlays[-1]
        # 300×200 頁面：預設寬 min(150, 頁寬 40%)=120，高依比例 60。
        assert _size(pasted.rect) == pytest.approx((120, 60))
        assert (pasted.angle, pasted.remove_white) == (0, False)
    finally:
        _close(window)


def test_paste_near_edge_stays_inside_page(qtbot, multi_page_path, red_stamp):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(red_stamp, False)
        window.copy_selection()

        window.paste_layer_at((299, 199))

        x0, y0, x1, y1 = window.session.overlays[-1].rect
        assert 0 <= x0 < x1 <= 300 and 0 <= y0 < y1 <= 200
    finally:
        _close(window)


def test_paste_refused_without_image_or_on_read_only(qtbot, multi_page_path):
    from pdf_editor.model import DocumentAccess

    window = _open(qtbot, multi_page_path)
    try:
        QApplication.clipboard().setText("只有文字")
        window.paste_layer_at((150, 100))
        assert window.session.overlays == ()
        assert "剪貼簿沒有可貼上的圖片" in window.statusBar().currentMessage()

        _set_clipboard_image(40, 40)
        window.session.access = DocumentAccess(False, False, "此文件僅供閱讀")
        window.paste_layer_at((150, 100))
        assert window.session.overlays == ()
    finally:
        _close(window)


def test_ctrl_v_pastes_at_cursor_or_visible_center(qtbot, multi_page_path, red_stamp, monkeypatch):
    import pdf_editor.ui.stamp_actions as stamp_actions

    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(red_stamp, False)
        window.copy_selection()
        assert window.actions["paste_image"].shortcut().toString() == "Ctrl+V"
        # 游標不在頁面上：貼在可見區域中央。
        monkeypatch.setattr(window.canvas, "page_point_at", lambda _pos: None)
        window.actions["paste_image"].trigger()
        center = window.visible_page_center()
        pasted = window.session.overlays[-1]
        assert ((pasted.rect[0] + pasted.rect[2]) / 2) == pytest.approx(
            min(max(center[0], 1 + _size(pasted.rect)[0] / 2), 299 - _size(pasted.rect)[0] / 2))
        # 游標在頁面上：以游標位置為中心。
        monkeypatch.setattr(window.canvas, "page_point_at", lambda _pos: (150, 100))
        window.actions["paste_image"].trigger()
        pasted = window.session.overlays[-1]
        assert ((pasted.rect[0] + pasted.rect[2]) / 2, (pasted.rect[1] + pasted.rect[3]) / 2) == \
            pytest.approx((150, 100))
    finally:
        _close(window)
```

檔案開頭 import 加入 `from PySide6.QtGui import QColor, QImage`（取代原本只 import `QImage` 的那行）。

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_copy_paste.py -q`
Expected: 新測試 FAIL，`AttributeError: 'MainWindow' object has no attribute 'paste_layer_at'`

- [ ] **Step 3：`stamp_actions.py` 實作貼上**

import 區調整為（與 Task 3 合併）：

```python
from PySide6.QtCore import QBuffer,QIODevice,QMimeData
from PySide6.QtGui import QCursor,QImage,QPixmap
from pdf_editor.layer_clipboard import MIME_TYPE,ClipboardLayer,decode_layer,encode_layer,paste_rect
```

在 `copy_layer` 之後加入：

```python
    def clipboard_layer(self):
        """讀取剪貼簿：優先墨頁專用格式，其次一般圖片；沒有圖片時回傳 None。"""
        mime=QApplication.clipboard().mimeData()
        if mime is None:
            return None
        if mime.hasFormat(MIME_TYPE):
            item=decode_layer(bytes(mime.data(MIME_TYPE)))
            if item is not None:
                return item
        if not mime.hasImage():
            return None
        data=mime.imageData()
        image=data.toImage() if isinstance(data,QPixmap) else QImage(data)
        if image.isNull():
            return None
        buffer=QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer,"PNG")
        return ClipboardLayer(bytes(buffer.data()))

    def can_paste_image(self):
        mime=QApplication.clipboard().mimeData()
        return mime is not None and (mime.hasFormat(MIME_TYPE) or mime.hasImage())

    def paste_image(self):
        """Ctrl+V：以滑鼠位置為中心貼上；游標不在頁面上時貼在可見區域中央。"""
        if not self.session:
            return
        point=self.canvas.page_point_at(self.canvas.viewport().mapFromGlobal(QCursor.pos()))
        self.paste_layer_at(point if point is not None else self.visible_page_center())

    def paste_layer_at(self,point):
        """在目前頁面以 point 為中心貼上剪貼簿圖片，建立可復原的新圖層。"""
        if not self.session or point is None or self.page_data is None:
            return
        if self.busy or not self.session.access.can_edit:
            self.statusBar().showMessage("目前無法貼上圖片。")
            return
        if self.comparison_dialog is not None:
            self.statusBar().showMessage("頁面比較開啟中，無法貼上圖片。")
            return
        item=self.clipboard_layer()
        if item is None:
            self.statusBar().showMessage("剪貼簿沒有可貼上的圖片。")
            return
        try:
            asset=AssetStore(self.session.history.root/"assets").import_png_bytes(item.png)
            if item.width is None:
                # 外部圖片沿用「蓋章」的大小規則（上次調整的寬度，或預設寬度），比例依原圖。
                from PIL import Image
                with Image.open(asset) as image:
                    ratio=image.height/image.width
                x0,y0,x1,y1=self.new_stamp_rect(asset,ratio)
                width,height=x1-x0,y1-y0
            else:
                width,height=item.width,item.height
            rect=paste_rect(point,width,height,self.page_data["bounds"][2:],item.angle)
            layer=Overlay(uuid.uuid4().hex,self.page,str(asset),rect,item.angle,item.remove_white)
            # 先驗證能正常輸出，失敗時不寫入歷程。
            flatten_overlays(self.session.pdf,(layer,))
        except Exception as exc:
            self.statusBar().showMessage("無法貼上圖片："+str(exc))
            return
        self.session.set_overlays(self.session.overlays+(layer,))
        self.select_layer(layer.id)
        self.refresh_actions()
        self.request_render("已貼上圖片，可拖曳移動或縮放。")
        self.queue_thumbnails((layer.page,))
```

（`AssetStore`、`Overlay`、`uuid` 已在檔案中 import。`set_overlays` 先於 `select_layer`，因此有待確認的點圖轉換時，版本已改變，`select_layer` 不會撤銷它。）

- [ ] **Step 4：`main_window.py` 加入動作**

動作表中 `copy_page_text` 那一列之後加入：

```python
            ("paste_image","貼上圖片",self.paste_image,("Ctrl+V",),"在滑鼠位置貼上剪貼簿中的圖片（Ctrl+V）"),
```

`refresh_actions` 中 `self.actions["copy_text"].setEnabled(active)` 之後加入：

```python
        self.actions["paste_image"].setEnabled(edit)
```

- [ ] **Step 5：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_copy_paste.py -q`
Expected: 全部 PASS

- [ ] **Step 6：Commit**

```bash
git add src/pdf_editor/ui/stamp_actions.py src/pdf_editor/ui/main_window.py tests/test_copy_paste.py
git commit -m "功能：Ctrl+V 以滑鼠位置貼上複製的圖層或其他程式的圖片" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5：右鍵選單與點選轉換的互動

**Files:**
- Modify: `src/pdf_editor/ui/main_window.py`（`build_canvas_menu`、`show_canvas_menu`）
- Test: `tests/test_copy_paste.py`

- [ ] **Step 1：寫失敗測試**

`tests/test_copy_paste.py` 最後加入：

```python
def _menu_actions(menu):
    return {action.text(): action for action in menu.actions()}


def test_context_menu_copies_layer_and_pastes_at_point(qtbot, multi_page_path, red_stamp):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(red_stamp, False)
        layer = window.session.overlays[-1]

        menu = window.build_canvas_menu((150, 100), None, layer)
        actions = _menu_actions(menu)
        actions["複製圖片"].trigger()
        assert QApplication.clipboard().mimeData().hasFormat(MIME_TYPE)

        menu = window.build_canvas_menu((150, 100), None, None)
        actions = _menu_actions(menu)
        assert "複製圖片" not in actions
        assert actions["在此貼上圖片"].isEnabled()
        actions["在此貼上圖片"].trigger()
        pasted = window.session.overlays[-1]
        assert ((pasted.rect[0] + pasted.rect[2]) / 2, (pasted.rect[1] + pasted.rect[3]) / 2) == \
            pytest.approx((150, 100))
    finally:
        _close(window)


def test_context_menu_paste_disabled_without_clipboard_image(qtbot, multi_page_path):
    window = _open(qtbot, multi_page_path)
    try:
        QApplication.clipboard().setText("只有文字")

        actions = _menu_actions(window.build_canvas_menu((150, 100), None, None))

        assert not actions["在此貼上圖片"].isEnabled()
    finally:
        _close(window)


def _single_stamp_pdf(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (0, 60, 255)).save(buffer, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=500, height=400)
        page.insert_image((210, 260, 270, 300), stream=buffer.getvalue())
        path = tmp_path / "單章.pdf"
        path.write_bytes(document.tobytes(garbage=4, deflate=True))
    return path


def test_copy_paste_after_click_conversion_keeps_conversion(qtbot, tmp_path, monkeypatch):
    import pdf_editor.ui.main_window as main_window

    def submit_synchronously(self, function, arguments, success, failure):
        try:
            success(function(*arguments))
        except Exception as exc:
            failure((getattr(exc, "code", "ERROR"), str(exc), ()))

    monkeypatch.setattr(main_window.Jobs, "submit", submit_synchronously)
    window = _open(qtbot, _single_stamp_pdf(tmp_path))
    try:
        window.canvas.image_clicked.emit(window.page_data["images"][0])
        assert len(window.session.overlays) == 1

        window.copy_selection()
        window.paste_layer_at((100, 100))
        window.canvas.background_clicked.emit()

        assert len(window.session.overlays) == 2
    finally:
        _close(window)
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_copy_paste.py -q -k "context_menu or click_conversion"`
Expected: context menu 測試 FAIL（`TypeError: build_canvas_menu() takes 3 positional arguments but 4 were given`）。轉換互動測試可能已通過（Task 4 已確保順序），屬於回歸保護。

- [ ] **Step 3：實作右鍵選單**

`build_canvas_menu` 改為接受 `layer=None`：

```python
    def build_canvas_menu(self,point,run,layer=None):
        menu=QMenu(self)
        if layer is not None:
            copy_image=menu.addAction("複製圖片")
            copy_image.triggered.connect(lambda _checked=False,item=layer:self.copy_layer(item))
            menu.addSeparator()
        if run is not None and run.text.strip():
```

（其後原有內容不變。）在 `if point is not None and edit:` 區塊內、`note` 之後加入：

```python
            paste=menu.addAction("在此貼上圖片")
            paste.setEnabled(self.can_paste_image())
            paste.triggered.connect(lambda _checked=False,position=point:self.paste_layer_at(position))
```

`show_canvas_menu` 改為：

```python
    def show_canvas_menu(self,global_position,point,run):
        if not self.session:
            return
        layer=self.canvas.layer_at(self.canvas.viewport().mapFromGlobal(global_position))
        self.build_canvas_menu(point,run,layer).exec(global_position)
```

- [ ] **Step 4：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_copy_paste.py tests/test_document_tools.py -q`
Expected: 全部 PASS（`test_canvas_context_menu_offers_copy_and_insert` 仍通過）

- [ ] **Step 5：Commit**

```bash
git add src/pdf_editor/ui/main_window.py tests/test_copy_paste.py
git commit -m "功能：右鍵選單可複製圖片並在點選位置貼上" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6：使用說明與全套測試

**Files:**
- Modify: `docs/使用說明.md`

- [ ] **Step 1：新增說明段落**

在「**去除白底**」段落之後加入（保持檔案原本的行尾）：

```markdown
**複製與貼上圖片**：選取頁面上的圖章、簽名或已轉成圖層的圖片後按 `Ctrl+C`（或在圖片上按右鍵選「複製圖片」），再按 `Ctrl+V` 就會以滑鼠所在位置為中心貼上一份可獨立移動、縮放的副本；滑鼠不在頁面上時貼在畫面中央，也可以在頁面按右鍵選「在此貼上圖片」。貼上的圖片保留原本的大小、角度與去除白底設定，可貼到同一頁、其他頁或另一份文件，並可用 `Ctrl+Z` 復原。`Ctrl+C` 依最後點選的對象決定複製圖片或文字。複製的圖片也會放到 Windows 剪貼簿，可以直接貼進 Word、LINE 或小畫家；反過來，在其他程式複製的圖片也能在墨頁按 `Ctrl+V` 貼成圖層，大小沿用「蓋章」上次調整的寬度。PDF 中原有的圖片要先點一下轉成圖層才能複製。
```

- [ ] **Step 2：跑全套測試**

Run: `work/build-venv312/Scripts/python.exe -m pytest -q`
Expected: 全部 PASS

- [ ] **Step 3：Commit**

```bash
git add docs/使用說明.md
git commit -m "文件：說明圖片複製與貼上" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7：昇版 0.22.0、建置與驗收（由主控者執行）

- [ ] 昇版：`pyproject.toml`、`src/pdf_editor/__init__.py`、`packaging/installer.iss`、`docs/使用說明.md` 第 1 行改為 0.22.0。
- [ ] 建置：`./scripts/build.ps1 -InnoCompiler 'C:/Tools/Inno/ISCC.exe' -PythonExecutable 'work/build-venv312/Scripts/python.exe'`。
- [ ] 靜默安裝到 `work\v0220-install`，確認註冊表版本，跑 `--smoke-test` 與 `--ocr-comparison-smoke-test`，核對安裝檔打包的程式碼含 `layer_clipboard`。
- [ ] 請使用者用 `work\v0220-install\LocalPDFEditor.exe` 完整路徑開啟（不要用舊捷徑），實機確認：Ctrl+C／Ctrl+V 複製貼上、跨頁貼上、貼到 Word 或小畫家、從其他程式貼進來、右鍵選單。
- [ ] 在 `docs/驗收紀錄.md` 最上方新增 0.22.0 驗收紀錄，提交「發布：昇版至 0.22.0 …」commit，最後合併回 `develop`。
