# 編輯既有圖片 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓使用者直接點 PDF 裡原有的圖片（例如材質證明左下角的圖章）就能移動、縮放，並可勾選「去除白底」；同時修正既有轉換流程對正常圖章的誤判。

**Architecture:** 沿用 0.20.x 的「既有影像抽離為 `Overlay` 工作層」機制（`legacy_overlay_conversion.py`）。`render_page` 在渲染時一併算出本頁可點選的圖片；畫布點到圖片時發出訊號，主視窗在背景只針對該圖跑完整安全檢查並抽離。轉換後若使用者沒有任何改動就離開，透過新的 `DocumentSession.discard_last()` 撤銷且不留重做紀錄。去除白底是 `Overlay` 的新欄位，在 `transformed_png` 統一處理，顯示與輸出共用。

**Tech Stack:** Python 3.12、PySide6、PyMuPDF、Pillow、pytest／pytest-qt。

**規格：** `docs/superpowers/specs/2026-10-08-edit-existing-images-design.md`

**共同約定：**
- 工作目錄：`C:\墨頁PDF\pdf-editor`，分支 `develop`。
- 測試一律用 `work/build-venv312/Scripts/python.exe -m pytest ...`（`.venv` 是 3.14 且缺 tesserocr）。
- 程式碼註解、docstring、使用者訊息、commit 訊息一律繁體中文。commit 訊息結尾加上：
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```
- 程式風格：`src/pdf_editor/ui/*.py` 用緊湊寫法（逗號後不空格、`a=b`），`legacy_overlay_conversion.py`、`persistent_overlays.py` 用 PEP 8 寫法；新增程式碼跟隨所在檔案的風格。

---

## 檔案結構

| 檔案 | 動作 | 責任 |
|---|---|---|
| `src/pdf_editor/model.py` | 修改 | `Overlay.remove_white`、新增 `EditableImage` |
| `src/pdf_editor/legacy_overlay_conversion.py` | 修改 | 放寬外觀比對、`editable_images_on_page`、`convert_image_at`、抽出共用的 `_extract_candidate` |
| `src/pdf_editor/engine/render.py` | 修改 | 頁面資料加入 `images` |
| `src/pdf_editor/engine/overlay.py` | 修改 | 去除白底影像處理 |
| `src/pdf_editor/persistent_overlays.py` | 修改 | 工作層選填欄位 `remove_white` |
| `src/pdf_editor/document/history.py` | 修改 | `History.discard_last()` |
| `src/pdf_editor/document/session.py` | 修改 | `DocumentSession.discard_last()` |
| `src/pdf_editor/ui/overlay_panel.py` | 修改 | 「去除白底」勾選框 |
| `src/pdf_editor/ui/canvas.py` | 修改 | 可點選圖片、游標回饋、`image_clicked`／`background_clicked` 訊號 |
| `src/pdf_editor/ui/stamp_actions.py` | 修改 | 點圖片轉換、待確認轉換與自動還原、去除白底切換、多頁蓋章保留設定 |
| `src/pdf_editor/ui/text_actions.py` | 修改 | 選文字／註解前先自動還原 |
| `src/pdf_editor/ui/main_window.py` | 修改 | 訊號連接、狀態初始化、換頁／存檔／關閉／復原時自動還原、選單改名 |
| `src/pdf_editor/ui/legacy_stamp_dialog.py` | 修改 | 對話框標題改名 |
| `tests/test_legacy_overlay_conversion.py` | 修改 | 外觀比對、快速清單、點選轉換 |
| `tests/test_session.py` | 修改 | `discard_last` |
| `tests/test_remove_white.py` | 新增 | 去除白底影像處理 |
| `tests/test_persistent_overlays.py` | 修改 | 工作層 `remove_white` |
| `tests/test_stamps.py` | 修改 | 面板勾選、多頁蓋章保留設定 |
| `tests/test_ui.py` | 修改 | 畫布點選、主視窗流程、改名 |
| `docs/使用說明.md`、`docs/驗收紀錄.md`、`pyproject.toml`、`src/pdf_editor/__init__.py`、`packaging/installer.iss` | 修改 | 說明文件與昇版 0.21.0 |

---

### Task 1：外觀比對容許反鋸齒誤差

**Files:**
- Modify: `src/pdf_editor/legacy_overlay_conversion.py`（`import` 區與 `_rendered_region_matches`，約第 1–12 行、第 158–173 行）
- Test: `tests/test_legacy_overlay_conversion.py`

- [ ] **Step 1：寫失敗測試**

在 `tests/test_legacy_overlay_conversion.py` 的 import 改為：

```python
from pdf_editor.legacy_overlay_conversion import (
    _samples_match,
    convert_legacy_image,
    find_convertible_images,
)
```

檔案最後加入：

```python
def _white_samples(width, height):
    return bytes([255]) * (width * height * 3)


def test_samples_match_accepts_sparse_antialias_noise():
    # 實際材質證明：刪除再放回圖章後，表格線附近有 45 個取樣值差 3～4。
    first = _white_samples(1000, 1000)
    second = bytearray(first)
    for index in range(0, 45 * 997, 997):
        second[index] = 251

    assert _samples_match(1000, 1000, 3, first, bytes(second))


def test_samples_match_rejects_any_large_difference():
    first = _white_samples(1000, 1000)
    second = bytearray(first)
    second[12345] = 225

    assert not _samples_match(1000, 1000, 3, first, bytes(second))


def test_samples_match_rejects_widespread_small_differences():
    first = _white_samples(1000, 1000)
    second = bytearray(first)
    for index in range(0, 2000 * 997, 997):
        second[index] = 251

    assert not _samples_match(1000, 1000, 3, first, bytes(second))


def test_samples_match_rejects_mismatched_or_empty_samples():
    assert not _samples_match(0, 0, 3, b"", b"")
    assert not _samples_match(2, 2, 3, _white_samples(2, 2), _white_samples(2, 1))
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_legacy_overlay_conversion.py -q`
Expected: 收集失敗，`ImportError: cannot import name '_samples_match'`

- [ ] **Step 3：實作**

`src/pdf_editor/legacy_overlay_conversion.py` 的 import 改為：

```python
import io
import uuid

import pymupdf
from PIL import Image, ImageChops
```

在 `find_convertible_images` 之前加入常數：

```python
# 外觀比對容許的誤差：一般取樣值差距須 ≤ 2；刪除再放回影像時，
# 附近線條的反鋸齒可能出現零星差異，因此允許極少量取樣值稍大。
_SAMPLE_TOLERANCE = 2
_MAX_SAMPLE_DIFFERENCE = 24
_MAX_OUTLIER_RATIO = 0.0005
```

把 `_rendered_region_matches` 整段換成：

```python
def _rendered_region_matches(
        original_pdf: bytes, replacement_pdf: bytes,
        candidate: LegacyImageCandidate) -> bool:
    samples = []
    for pdf in (original_pdf, replacement_pdf):
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            pixmap = document[candidate.page].get_pixmap(
                matrix=pymupdf.Matrix(2, 2), alpha=False
            )
            samples.append((pixmap.width, pixmap.height, pixmap.n, pixmap.samples))
    if samples[0][:3] != samples[1][:3]:
        return False
    return _samples_match(*samples[0][:3], samples[0][3], samples[1][3])


def _samples_match(width: int, height: int, n: int, first: bytes, second: bytes) -> bool:
    """比對兩張渲染圖；容許零星反鋸齒差異，但拒絕任何明顯變化。"""
    mode = {1: "L", 3: "RGB"}.get(n)
    if mode is None or width <= 0 or height <= 0 or not first:
        return False
    if len(first) != width * height * n or len(second) != len(first):
        return False
    difference = ImageChops.difference(
        Image.frombytes(mode, (width, height), bytes(first)),
        Image.frombytes(mode, (width, height), bytes(second)),
    )
    histogram = difference.histogram()
    outliers = 0
    for band in range(n):
        counts = histogram[band * 256:(band + 1) * 256]
        if any(counts[_MAX_SAMPLE_DIFFERENCE + 1:]):
            return False
        outliers += sum(counts[_SAMPLE_TOLERANCE + 1:])
    return outliers <= len(first) * _MAX_OUTLIER_RATIO
```

- [ ] **Step 4：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_legacy_overlay_conversion.py -q`
Expected: 全部 PASS（含既有的「圖片被向量遮住仍被拒絕」測試）

- [ ] **Step 5：用實際材質證明確認不再誤判**

Run（檔案只在本機，不放進程式庫）：

```bash
work/build-venv312/Scripts/python.exe -c "import sys; sys.path.insert(0,'src'); from pdf_editor.legacy_overlay_conversion import find_convertible_images; c=find_convertible_images(open('../115.01.02-2014-F.pdf','rb').read()); print([(i.page,i.rect,i.width,i.height) for i in c])"
```

Expected: 印出一筆，`(0, (119.11..., 448.63..., 202.52..., 521.22...), 387, 336)`

- [ ] **Step 6：Commit**

```bash
git add src/pdf_editor/legacy_overlay_conversion.py tests/test_legacy_overlay_conversion.py
git commit -m "修正：既有圖章轉換的外觀比對容許反鋸齒誤差，避免正常圖章被誤判" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2：頁面上可點選圖片的快速清單

**Files:**
- Modify: `src/pdf_editor/model.py`（`LegacyImageCandidate` 之後）
- Modify: `src/pdf_editor/legacy_overlay_conversion.py`
- Modify: `src/pdf_editor/engine/render.py`
- Test: `tests/test_legacy_overlay_conversion.py`

- [ ] **Step 1：寫失敗測試**

`tests/test_legacy_overlay_conversion.py` 的 import 改為：

```python
from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.engine.render import render_page
from pdf_editor.errors import EditorError
from pdf_editor.legacy_overlay_conversion import (
    _samples_match,
    convert_legacy_image,
    editable_images_on_page,
    find_convertible_images,
)
from pdf_editor.model import EditableImage
```

檔案最後加入：

```python
def _editable_images(pdf, page=0):
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        return editable_images_on_page(document, page)


def test_editable_images_on_page_lists_single_use_stamp_only():
    images = _editable_images(_pdf_with_unique_stamp_and_reused_logo())

    assert [(item.page, item.rect) for item in images] == [(0, (210, 260, 270, 300))]
    assert all(isinstance(item, EditableImage) for item in images)


@pytest.mark.parametrize("pdf_factory", [
    _single_scanned_page_pdf,
    _pdf_with_rotated_asymmetric_stamp,
    _pdf_with_image_drawn_twice_at_same_rect,
    _pdf_with_reused_image,
])
def test_editable_images_on_page_skips_unsafe_images(pdf_factory):
    assert _editable_images(pdf_factory()) == ()


def test_render_page_includes_editable_images():
    images = render_page(_pdf_with_unique_stamp(), 0, 1.0)["images"]

    assert [(item.page, item.rect) for item in images] == [(0, (210, 260, 270, 300))]
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_legacy_overlay_conversion.py -q`
Expected: 收集失敗，`ImportError: cannot import name 'editable_images_on_page'`

- [ ] **Step 3：新增資料模型**

`src/pdf_editor/model.py` 在 `LegacyImageCandidate` 之後加入：

```python
@dataclass(frozen=True)
class EditableImage:
    """頁面上可直接點選編輯的既有圖片；實際轉換前仍會再完整檢查。"""
    xref: int
    page: int
    rect: Rect
```

- [ ] **Step 4：實作快速清單**

`src/pdf_editor/legacy_overlay_conversion.py`：

import 改為 `from pdf_editor.model import EditableImage, LegacyImageCandidate, Overlay`。

在 `convert_legacy_image` 之後加入：

```python
def editable_images_on_page(document, page_number: int) -> tuple[EditableImage, ...]:
    """快速列出本頁可直接點選編輯的圖片；不做渲染比對，轉換時會再完整把關。"""
    page = document[page_number]
    xrefs = {image[0] for image in page.get_images(full=True) if image[0] > 0}
    if not xrefs:
        return ()
    # 其他頁面的資源引用同一張圖，就當作重複使用，不提供直接點選。
    elsewhere = {
        image[0]
        for number in range(document.page_count) if number != page_number
        for image in document.get_page_images(number, full=True)
    }
    found = []
    for xref in sorted(xrefs - elsewhere):
        rects = page.get_image_rects(xref)
        if len(rects) != 1:
            continue
        rect = _rect_tuple(rects[0])
        if _is_not_page_scan(page, rect) and _has_supported_placement(page, xref, rect):
            found.append(EditableImage(xref, page_number, rect))
    return tuple(found)
```

- [ ] **Step 5：`render_page` 加入 `images`**

`src/pdf_editor/engine/render.py`：

import 區加入：

```python
from pdf_editor.legacy_overlay_conversion import editable_images_on_page
```

`render_page` 回傳的字典最後一行改為：

```python
            "annotations":list_annotations(content,page), "page":page,
            "images":_editable_images(doc,page)}
```

在 `render_page` 之後加入：

```python
def _editable_images(document, page):
    """可直接點選編輯的圖片；分析失敗時不影響頁面顯示。"""
    try:
        return editable_images_on_page(document, page)
    except Exception:
        return ()
```

- [ ] **Step 6：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_legacy_overlay_conversion.py tests/test_layout.py -q`
Expected: 全部 PASS

- [ ] **Step 7：Commit**

```bash
git add src/pdf_editor/model.py src/pdf_editor/legacy_overlay_conversion.py src/pdf_editor/engine/render.py tests/test_legacy_overlay_conversion.py
git commit -m "功能：渲染頁面時列出可直接點選編輯的既有圖片" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3：只轉換點到的那張圖片

**Files:**
- Modify: `src/pdf_editor/legacy_overlay_conversion.py`（`convert_legacy_image`，約第 41–69 行）
- Test: `tests/test_legacy_overlay_conversion.py`

- [ ] **Step 1：寫失敗測試**

import 加入 `convert_image_at`（依字母序放在 `_samples_match` 之後）。檔案開頭加入 `from dataclasses import replace`。檔案最後加入：

```python
def test_convert_image_at_extracts_clicked_stamp(tmp_path):
    source = _pdf_with_unique_stamp()
    image = _editable_images(source)[0]

    base_pdf, layer = convert_image_at(source, image, tmp_path / "assets")

    assert _blue_stamp_pixels(base_pdf) == 0
    assert (layer.page, layer.rect) == (0, image.rect)
    assert _blue_stamp_pixels(flatten_overlays(base_pdf, (layer,))) > 0


def test_convert_image_at_rejects_reused_image_without_changes(tmp_path):
    source = _pdf_with_reused_image()
    with pymupdf.open(stream=source, filetype="pdf") as document:
        xref = document[0].get_images()[0][0]

    with pytest.raises(EditorError, match="重複使用") as error:
        convert_image_at(source, EditableImage(xref, 0, (210, 260, 270, 300)),
                         tmp_path / "assets")

    assert error.value.code == "STAMP_CONVERSION"
    assert not (tmp_path / "assets").exists()


def test_convert_image_at_rejects_stale_position(tmp_path):
    source = _pdf_with_unique_stamp()
    stale = replace(_editable_images(source)[0], rect=(0, 0, 10, 10))

    with pytest.raises(EditorError, match="位置已變更"):
        convert_image_at(source, stale, tmp_path / "assets")


def test_convert_image_at_rejects_image_covered_by_vector(tmp_path):
    source = _pdf_with_vector_covering_stamp()
    # 快速清單樂觀列出，實際轉換時的外觀比對負責擋下。
    image = _editable_images(source)[0]

    with pytest.raises(EditorError, match="無法安全編輯"):
        convert_image_at(source, image, tmp_path / "assets")

    assert not (tmp_path / "assets").exists()
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_legacy_overlay_conversion.py -q`
Expected: 收集失敗，`ImportError: cannot import name 'convert_image_at'`

- [ ] **Step 3：抽出共用流程並實作 `convert_image_at`**

把 `convert_legacy_image` 整段換成以下三個函式：

```python
_CANDIDATE_CHANGED = "圖章候選已變更，請重新掃描後再選取。"
_IMAGE_CHANGED = "圖片位置已變更，請再點一次。"


def convert_legacy_image(pdf: bytes, candidate: LegacyImageCandidate, asset_root) -> tuple[bytes, Overlay]:
    """原子地抽離候選影像，成功時才建立資產與工作層。"""
    try:
        current = {item.xref: item for item in find_convertible_images(pdf)}
        if current.get(candidate.xref) != candidate:
            raise EditorError("STAMP_CONVERSION", _CANDIDATE_CHANGED)
        return _extract_candidate(pdf, candidate, asset_root, _CANDIDATE_CHANGED)
    except EditorError as exc:
        if exc.code == "STAMP_CONVERSION":
            raise
        raise EditorError("STAMP_CONVERSION", "既有圖章轉換失敗，文件未被變更。") from exc
    except Exception as exc:
        raise EditorError("STAMP_CONVERSION", "既有圖章轉換失敗，文件未被變更。") from exc


def convert_image_at(pdf: bytes, image: EditableImage, asset_root) -> tuple[bytes, Overlay]:
    """直接點選圖片時，只檢查並抽離這一張；任何條件不符都不改動文件。"""
    try:
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            uses = _image_uses(document).get(image.xref, [])
            if len(uses) > 1:
                raise EditorError("STAMP_CONVERSION", "這張圖片在文件中重複使用，無法單獨編輯。")
            # 頁面資料可能已過時；位置不符時不猜測，避免改到別張圖。
            if uses != [(image.page, image.rect)]:
                raise EditorError("STAMP_CONVERSION", _IMAGE_CHANGED)
            page = document[image.page]
            if not _is_not_page_scan(page, image.rect):
                raise EditorError("STAMP_CONVERSION", "整頁掃描的圖片無法單獨編輯。")
            if not _has_supported_placement(page, image.xref, image.rect):
                raise EditorError("STAMP_CONVERSION", "旋轉或傾斜擺放的圖片無法單獨編輯。")
            candidate = _candidate_from_use(document, image.xref, image.page, image.rect)
        return _extract_candidate(pdf, candidate, asset_root, _IMAGE_CHANGED)
    except EditorError as exc:
        if exc.code == "STAMP_CONVERSION":
            raise
        raise EditorError("STAMP_CONVERSION", "圖片轉換失敗，文件未被變更。") from exc
    except Exception as exc:
        raise EditorError("STAMP_CONVERSION", "圖片轉換失敗，文件未被變更。") from exc


def _extract_candidate(pdf: bytes, candidate: LegacyImageCandidate, asset_root,
                       changed_message: str) -> tuple[bytes, Overlay]:
    """從副本移除影像並驗證外觀；全部通過後才建立資產。"""
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        # 在實際刪除前重新確認此 xref 仍僅有一個使用位置。
        uses = _image_uses(document)
        if uses.get(candidate.xref) != [(candidate.page, candidate.rect)]:
            raise EditorError("STAMP_CONVERSION", changed_message)
        document[candidate.page].delete_image(candidate.xref)
        base_pdf = document.tobytes(garbage=4, deflate=True)

    _verify_pdf(base_pdf, candidate)
    if not _replacement_from_base_preserves_appearance(pdf, base_pdf, candidate):
        raise EditorError(
            "STAMP_CONVERSION",
            "這張圖片受其他內容或繪圖狀態影響，無法安全編輯。",
        )
    asset = AssetStore(asset_root).import_png_bytes(candidate.png)
    return base_pdf, Overlay(uuid.uuid4().hex, candidate.page, str(asset), candidate.rect)
```

- [ ] **Step 4：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_legacy_overlay_conversion.py -q`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add src/pdf_editor/legacy_overlay_conversion.py tests/test_legacy_overlay_conversion.py
git commit -m "功能：新增只轉換點選圖片的流程，並與清單轉換共用抽離檢查" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4：捨棄最後一筆歷程

**Files:**
- Modify: `src/pdf_editor/document/history.py`（`push` 之後）
- Modify: `src/pdf_editor/document/session.py`（`redo` 之後）
- Test: `tests/test_session.py`

- [ ] **Step 1：寫失敗測試**

`tests/test_session.py` 最後加入（若檔案尚未 import `pytest` 與 `DocumentSession`，在開頭補上 `import pytest` 與 `from pdf_editor.document.session import DocumentSession`）：

```python
def test_discard_last_removes_latest_step_without_redo(source_path):
    with DocumentSession.open(source_path) as session:
        original = session.pdf
        session.apply_pdf(original + b"\n%discard")
        discarded_path = session.pdf_path
        revision = session.revision

        session.discard_last()

        assert session.pdf == original
        assert not session.can_redo
        assert not session.dirty
        assert session.revision == revision + 1
        assert not discarded_path.exists()


def test_discard_last_refuses_when_not_at_latest_step(source_path):
    with DocumentSession.open(source_path) as session:
        with pytest.raises(ValueError):
            session.discard_last()
        session.apply_pdf(session.pdf + b"\n%step")
        session.undo()
        with pytest.raises(ValueError):
            session.discard_last()
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_session.py -q`
Expected: 2 FAIL，`AttributeError: 'DocumentSession' object has no attribute 'discard_last'`

- [ ] **Step 3：實作**

`src/pdf_editor/document/history.py` 在 `push` 之後加入：

```python
    def discard_last(self):
        """撤銷最新一筆且不留重做紀錄，供未改動的暫時轉換使用。"""
        if self.index <= 0 or self.index != len(self.items) - 1:
            raise ValueError("只能捨棄最新的一筆歷程。")
        path = self.items.pop()[0]
        path.unlink(missing_ok=True)
        self.index -= 1
```

`src/pdf_editor/document/session.py` 在 `redo` 之後加入：

```python
    def discard_last(self):
        self.history.discard_last()
        self.revision += 1
```

- [ ] **Step 4：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_session.py -q`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add src/pdf_editor/document/history.py src/pdf_editor/document/session.py tests/test_session.py
git commit -m "功能：歷程可捨棄最新一筆且不留重做紀錄" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5：圖層去除白底

**Files:**
- Modify: `src/pdf_editor/model.py`（`Overlay`）
- Modify: `src/pdf_editor/engine/overlay.py`（`transformed_image`、`transformed_png`）
- Create: `tests/test_remove_white.py`

- [ ] **Step 1：寫失敗測試**

建立 `tests/test_remove_white.py`：

```python
"""圖層去除白底：顯示與輸出共用同一套處理。"""

from dataclasses import replace
import io

import pymupdf
from PIL import Image

from pdf_editor.engine.overlay import flatten_overlays, transformed_png
from pdf_editor.model import Overlay


def _row_png(pixels):
    image = Image.new("RGBA", (len(pixels), 1))
    for x, pixel in enumerate(pixels):
        image.putpixel((x, 0), pixel)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _alphas(png):
    with Image.open(io.BytesIO(png)) as image:
        rgba = image.convert("RGBA")
        return [rgba.getpixel((x, 0))[3] for x in range(rgba.width)]


SOURCE = _row_png([
    (255, 255, 255, 255),   # 純白：完全透明
    (225, 225, 225, 255),   # 淺灰：漸變到一半
    (240, 236, 250, 255),   # 最暗色版 236：完全透明
    (0, 0, 0, 255),         # 黑色：保留
    (0, 0, 0, 100),         # 原本半透明：保留原透明度
    (250, 10, 10, 255),     # 紅色：保留
])


def test_remove_white_makes_near_white_transparent_with_soft_edge():
    png, rect = transformed_png(SOURCE, (0, 0, 6, 1), 0, remove_white=True)

    assert _alphas(png) == [0, 128, 0, 255, 100, 255]
    assert rect == (0, 0, 6, 1)


def test_remove_white_disabled_keeps_original_alpha():
    png, _rect = transformed_png(SOURCE, (0, 0, 6, 1), 0)

    assert _alphas(png) == [255, 255, 255, 255, 100, 255]


def test_remove_white_applies_to_rotated_layers():
    png, _rect = transformed_png(SOURCE, (0, 0, 60, 10), 30, remove_white=True)

    with Image.open(io.BytesIO(png)) as image:
        assert image.convert("RGBA").getpixel((0, 0))[3] == 0


def test_flatten_with_remove_white_shows_content_underneath(tmp_path):
    stamp = tmp_path / "白底章.png"
    Image.new("RGBA", (40, 40), (255, 255, 255, 255)).save(stamp)
    with pymupdf.open() as document:
        page = document.new_page(width=200, height=200)
        page.draw_rect((50, 50, 150, 150), color=None, fill=(0, 0, 0))
        pdf = document.tobytes()
    layer = Overlay("白底", 0, str(stamp), (60, 60, 140, 140))

    def center(content):
        with pymupdf.open(stream=content, filetype="pdf") as document:
            return document[0].get_pixmap(alpha=False).pixel(100, 100)

    assert center(flatten_overlays(pdf, (layer,))) == (255, 255, 255)
    assert center(flatten_overlays(pdf, (replace(layer, remove_white=True),))) == (0, 0, 0)
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_remove_white.py -q`
Expected: FAIL，`TypeError: transformed_png() got an unexpected keyword argument 'remove_white'`

- [ ] **Step 3：`Overlay` 新增欄位**

`src/pdf_editor/model.py` 的 `Overlay` 改為：

```python
@dataclass(frozen=True)
class Overlay:
    id: str
    page: int
    asset_path: str
    rect: Rect
    angle: float = 0
    # 把接近白色的像素變透明；資產檔不變，取消即可還原。
    remove_white: bool = False
```

- [ ] **Step 4：實作影像處理**

`src/pdf_editor/engine/overlay.py`：

import 改為 `from PIL import Image, ImageChops`。

在 `transformed_image` 之前加入：

```python
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
```

`transformed_image` 與 `transformed_png` 整段換成：

```python
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
```

- [ ] **Step 5：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_remove_white.py tests/test_persistent_overlays.py tests/test_stamps.py -q`
Expected: 全部 PASS

- [ ] **Step 6：Commit**

```bash
git add src/pdf_editor/model.py src/pdf_editor/engine/overlay.py tests/test_remove_white.py
git commit -m "功能：圖層可去除白底，顯示與輸出共用同一套處理" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6：工作層保存去除白底設定

**Files:**
- Modify: `src/pdf_editor/persistent_overlays.py`（`_validated_overlay_data`、`_prepare_assets`、`_restore_overlay`）
- Test: `tests/test_persistent_overlays.py`

- [ ] **Step 1：寫失敗測試**

`tests/test_persistent_overlays.py` 最後加入：

```python
def _white_stamp(tmp_path):
    stamp = tmp_path / "白底章.png"
    Image.new("RGBA", (20, 10), (255, 255, 255, 255)).save(stamp)
    return stamp


def _manifest_of(workspace):
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        return json.loads(document.embfile_get(MANIFEST_NAME).decode("utf-8"))


def test_workspace_round_trips_remove_white(tmp_path, pdf_bytes):
    layer = Overlay("章-1", 0, str(_white_stamp(tmp_path)), (100, 110, 140, 130), 0, True)
    workspace = embed_workspace(pdf_bytes, (layer,))

    assert _manifest_of(workspace)["overlays"][0]["remove_white"] is True
    restored = load_workspace(workspace, tmp_path / "assets")
    assert restored.overlays[0].remove_white is True


def test_workspace_omits_remove_white_when_disabled(tmp_path, pdf_bytes):
    layer = Overlay("章-1", 0, str(_white_stamp(tmp_path)), (100, 110, 140, 130))
    workspace = embed_workspace(pdf_bytes, (layer,))

    # 沒勾選時不寫入，舊版程式仍可讀取。
    assert "remove_white" not in _manifest_of(workspace)["overlays"][0]
    assert load_workspace(workspace, tmp_path / "assets").overlays[0].remove_white is False


def test_workspace_rejects_non_boolean_remove_white(tmp_path, pdf_bytes):
    layer = Overlay("章-1", 0, str(_white_stamp(tmp_path)), (100, 110, 140, 130))
    workspace = embed_workspace(pdf_bytes, (layer,))
    manifest = _manifest_of(workspace)
    manifest["overlays"][0]["remove_white"] = "yes"
    tampered = _embed_with_replaced_manifest(
        workspace, json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    )

    with pytest.raises(EditorError, match="工作層無法驗證"):
        load_workspace(tampered, tmp_path / "assets")
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_persistent_overlays.py -q`
Expected: `test_workspace_round_trips_remove_white` FAIL（`KeyError: 'remove_white'`）

- [ ] **Step 3：實作**

`src/pdf_editor/persistent_overlays.py`：

在 `_PDF_REFERENCE` 之後加入：

```python
_OVERLAY_KEYS = {"id", "page", "rect", "angle", "asset_name", "asset_sha256"}
# 選填欄位只在啟用時寫入，未使用的檔案與舊版程式完全相容。
_OPTIONAL_OVERLAY_KEYS = {"remove_white"}
```

`_validated_overlay_data` 開頭的檢查改為：

```python
        if not isinstance(item, dict) or not (
            _OVERLAY_KEYS <= set(item) <= _OVERLAY_KEYS | _OPTIONAL_OVERLAY_KEYS
        ):
            raise ValueError()
        identifier = item["id"]
        page = item["page"]
        rect = item["rect"]
        angle = item["angle"]
        asset_name = item["asset_name"]
        asset_sha256 = item["asset_sha256"]
        remove_white = item.get("remove_white", False)
```

同一函式的大條件式最後（`or asset_name not in names` 之後）加一行：

```python
            or not isinstance(remove_white, bool)
```

回傳改為：

```python
        return identifier, page, tuple(rect), angle, asset_name, content, remove_white
```

`_prepare_assets` 的 `manifest_overlays.append(...)` 改為：

```python
        entry = {
            "id": overlay.id,
            "page": overlay.page,
            "rect": list(overlay.rect),
            "angle": overlay.angle,
            "asset_name": name,
            "asset_sha256": digest,
        }
        if overlay.remove_white:
            entry["remove_white"] = True
        manifest_overlays.append(entry)
```

`_restore_overlay` 的解包與回傳改為：

```python
        identifier, page, rect, angle, _asset_name, content, remove_white = (
            _validated_overlay_data(item, names, document, page_bounds)
        )
        path = asset_store.import_png_bytes(content)
        if path not in existing_assets:
            created_assets.add(path)
        return Overlay(identifier, page, str(path), rect, angle, remove_white)
```

- [ ] **Step 4：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_persistent_overlays.py tests/test_save_over_original.py -q`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add src/pdf_editor/persistent_overlays.py tests/test_persistent_overlays.py
git commit -m "功能：另存時保存圖層的去除白底設定，重開後仍可調整" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7：右側面板「去除白底」勾選框

**Files:**
- Modify: `src/pdf_editor/ui/overlay_panel.py`
- Modify: `src/pdf_editor/ui/stamp_actions.py`（新增 `set_layer_remove_white`；`stamp_to_pages` 第 191 行）
- Modify: `src/pdf_editor/ui/main_window.py`（第 419–421 行附近連接訊號）
- Test: `tests/test_stamps.py`

- [ ] **Step 1：寫失敗測試**

`tests/test_stamps.py` 最後加入：

```python
@pytest.fixture
def white_stamp_png(tmp_path):
    path = tmp_path / "白底章.png"
    Image.new("RGBA", (200, 100), (255, 255, 255, 255)).save(path)
    return path


def test_overlay_panel_toggles_remove_white_with_undo(qtbot, multi_page_path, white_stamp_png):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(white_stamp_png, False)
        layer_id = window.layer_id
        assert not window.overlay_panel.remove_white.isChecked()

        window.overlay_panel.remove_white.setChecked(True)
        qtbot.waitUntil(lambda: not window.busy, timeout=30000)

        assert next(o for o in window.session.overlays if o.id == layer_id).remove_white
        window.history_step(False)
        assert not next(o for o in window.session.overlays if o.id == layer_id).remove_white
    finally:
        _close(window)


def test_stamp_to_pages_keeps_remove_white(qtbot, multi_page_path, white_stamp_png, monkeypatch):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(white_stamp_png, False)
        window.overlay_panel.remove_white.setChecked(True)
        qtbot.waitUntil(lambda: not window.busy, timeout=30000)
        monkeypatch.setattr(StampPagesDialog, "exec", lambda self: self.all_pages.setChecked(True) or 1)

        window.stamp_to_pages()

        assert len(window.session.overlays) == 3
        assert all(item.remove_white for item in window.session.overlays)
    finally:
        _close(window)
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_stamps.py -q`
Expected: 2 FAIL，`AttributeError: 'OverlayPanel' object has no attribute 'remove_white'`

- [ ] **Step 3：面板加入勾選框**

`src/pdf_editor/ui/overlay_panel.py`：

import 改為：

```python
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QLabel,QDoubleSpinBox,
    QPushButton,QCheckBox)
```

類別訊號加入 `remove_white_toggled=Signal(bool)`。在 `layout.addWidget(apply)` 之後加入：

```python
        self.remove_white=QCheckBox("去除白底（讓底下的內容透出）")
        self.remove_white.setToolTip("把接近白色的部分變透明；原圖不會被修改，取消勾選即可還原")
        self.remove_white.toggled.connect(self.remove_white_toggled)
        layout.addWidget(self.remove_white)
```

`set_layer` 最後加入：

```python
        # 切換選取時同步勾選狀態，不觸發修改。
        self.remove_white.blockSignals(True)
        self.remove_white.setChecked(layer.remove_white)
        self.remove_white.blockSignals(False)
```

- [ ] **Step 4：處理切換並讓多頁蓋章保留設定**

`src/pdf_editor/ui/stamp_actions.py` 在 `update_layer` 之後加入：

```python
    def set_layer_remove_white(self,checked):
        """切換選取圖層的去除白底；與移動、縮放相同，會留下可復原的紀錄。"""
        layer=next((o for o in self.session.overlays if o.id==self.layer_id),None) if self.session else None
        if layer is None or layer.remove_white==checked:
            return
        self.move_layer(replace(layer,remove_white=checked))
```

`stamp_to_pages` 中的

```python
                added.append(Overlay(uuid.uuid4().hex,page,layer.asset_path,layer.rect,layer.angle))
```

改為：

```python
                added.append(replace(layer,id=uuid.uuid4().hex,page=page))
```

`src/pdf_editor/ui/main_window.py` 在 `self.overlay_panel.delete_requested.connect(self.delete_layer)` 之後加入：

```python
        self.overlay_panel.remove_white_toggled.connect(self.set_layer_remove_white)
```

- [ ] **Step 5：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_stamps.py -q`
Expected: 全部 PASS

- [ ] **Step 6：Commit**

```bash
git add src/pdf_editor/ui/overlay_panel.py src/pdf_editor/ui/stamp_actions.py src/pdf_editor/ui/main_window.py tests/test_stamps.py
git commit -m "功能：圖章面板新增「去除白底」勾選，多頁蓋章時一併沿用" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8：畫布點選圖片與游標提示

**Files:**
- Modify: `src/pdf_editor/ui/canvas.py`（訊號宣告約第 277–297 行、`__init__`、`display`、`mousePressEvent` 約第 875–880 行、`leaveEvent`、`mouseMoveEvent`）
- Test: `tests/test_ui.py`

- [ ] **Step 1：寫失敗測試**

`tests/test_ui.py` 開頭 import 加入 `from pathlib import Path` 與 `from pdf_editor.errors import EditorError`。在 `_legacy_candidate` 之後加入共用輔助函式：

```python
NOTO = Path(__file__).parents[1] / "resources/fonts/NotoSansCJKtc-Regular.otf"


def _stamp_page_pdf():
    """一頁含可編輯文字與唯一圖章（不與文字重疊）的 PDF。"""
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (0, 60, 255)).save(buffer, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=500, height=400)
        page.insert_font(fontname="noto", fontfile=str(NOTO))
        page.insert_text((40, 80), "品質檢驗 ABC 123", fontname="noto", fontsize=16)
        page.insert_image((210, 260, 270, 300), stream=buffer.getvalue())
        document.subset_fonts(fallback=True)
        return document.tobytes(garbage=4, deflate=True)


def _view_point(canvas, x, y):
    return canvas.mapFromScene(QPointF(*transform_point(canvas.matrix, x, y)))
```

在 `test_canvas_dragging_text_emits_moved_run` 之後加入：

```python
def test_canvas_hover_and_click_on_editable_image(qtbot):
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    data = render_page(_stamp_page_pdf(), 0, 1.0)
    canvas.display(data)
    qtbot.waitExposed(canvas)
    spy = QSignalSpy(canvas.image_clicked)
    center = _view_point(canvas, 240, 280)

    qtbot.mouseMove(canvas.viewport(), center)
    assert canvas.image_hover is not None
    assert canvas.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor

    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=center)
    assert spy.count() == 1
    assert spy.at(0)[0] == data["images"][0]

    qtbot.mouseMove(canvas.viewport(), _view_point(canvas, 450, 380))
    assert canvas.image_hover is None


def test_canvas_prefers_text_over_image_and_reports_background_click(qtbot, pdf_bytes):
    # 共用測試 PDF 的圖片 (30,40,400,120) 上疊有文字。
    canvas = Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(700, 600)
    canvas.show()
    data = render_page(pdf_bytes, 0, 1.0)
    canvas.display(data)
    qtbot.waitExposed(canvas)
    assert data["images"]
    images = QSignalSpy(canvas.image_clicked)
    runs = QSignalSpy(canvas.run_selected)
    blank = QSignalSpy(canvas.background_clicked)
    run = next(r for r in data["runs"] if "品質" in r.text and r.rect[1] < 120)

    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton,
        pos=_view_point(canvas, (run.rect[0] + run.rect[2]) / 2, (run.rect[1] + run.rect[3]) / 2))
    assert (runs.count(), images.count()) == (1, 0)

    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=_view_point(canvas, 350, 110))
    assert images.count() == 1

    qtbot.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=_view_point(canvas, 450, 380))
    assert blank.count() == 1
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_ui.py -q -k "editable_image or prefers_text_over_image"`
Expected: 2 FAIL，`AttributeError: 'Canvas' object has no attribute 'image_clicked'`

- [ ] **Step 3：實作**

`src/pdf_editor/ui/canvas.py`：

訊號宣告最後（`context_menu_requested` 之後）加入：

```python
    image_clicked=Signal(object)
    background_clicked=Signal()
```

`__init__` 中 `self.annotations=[]` 之後加入：

```python
        self.editable_images=()
        self.image_hover=None
        self._hover_image=None
```

`display` 中 `self.annotations=data.get("annotations",())` 之後加入（`scene().clear()` 已移除舊外框，只需重設參照）：

```python
        self.editable_images=data.get("images",())
        self.image_hover=None
        self._hover_image=None
        self.viewport().unsetCursor()
```

在 `_annotation_at` 之後加入：

```python
    def _image_at(self,scene_pos):
        """游標下可直接點選編輯的圖片（以 PDF 座標判斷）。"""
        x,y=transform_point(inverse_transform(self.matrix),scene_pos.x(),scene_pos.y())
        for image in reversed(self.editable_images):
            x0,y0,x1,y1=image.rect
            if x0<=x<=x1 and y0<=y<=y1:
                return image
        return None

    def _image_hover_target(self,scene_pos):
        """只有在沒有圖層、文字或進行中模式時，才提示圖片可點選。"""
        if (self._crop_mode or self._text_insertion or self._note_insertion
                or self._annotation_selection or self._drag_run
                or self.dragMode()!=QGraphicsView.DragMode.NoDrag):
            return None
        if isinstance(self.scene().itemAt(scene_pos,QTransform()),LayerItem):
            return None
        if self._run_at(scene_pos):
            return None
        return self._image_at(scene_pos)

    def _update_image_hover(self,scene_pos):
        image=self._image_hover_target(scene_pos)
        if image==self._hover_image:
            return
        self.clear_image_hover()
        if image is None:
            return
        r=transformed_rect(self.matrix,image.rect)
        pen=QPen(QColor(COLORS["accent"]),2,Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self.image_hover=self.scene().addRect(r[0],r[1],r[2]-r[0],r[3]-r[1],pen)
        self.image_hover.setZValue(4)
        self.image_hover.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._hover_image=image
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)

    def clear_image_hover(self):
        if self.image_hover is not None:
            self.scene().removeItem(self.image_hover)
        self.image_hover=None
        self._hover_image=None
        self.viewport().unsetCursor()
```

`mousePressEvent` 末段的

```python
            self.clear_text_selection()
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        super().mousePressEvent(event)
```

改為：

```python
            self.clear_text_selection()
            image=self._image_at(scene_pos)
            if image and event.button()==Qt.MouseButton.LeftButton:
                # 文字優先；沒點到文字才轉交圖片編輯。
                self.clear_image_hover()
                self.image_clicked.emit(image)
                event.accept()
                return
            self.background_clicked.emit()
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        super().mousePressEvent(event)
```

`leaveEvent` 改為：

```python
    def leaveEvent(self,event):
        self.pointer_moved.emit(None)
        if self.image_hover is not None:
            self.clear_image_hover()
        super().leaveEvent(event)
```

`mouseMoveEvent` 最後一行 `super().mouseMoveEvent(event)` 之前加入：

```python
        self._update_image_hover(self.mapToScene(event.position().toPoint()))
```

- [ ] **Step 4：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_ui.py -q -k "canvas"`
Expected: 全部 PASS（含既有的平移、拖曳文字、圖層縮放測試）

- [ ] **Step 5：Commit**

```bash
git add src/pdf_editor/ui/canvas.py tests/test_ui.py
git commit -m "功能：滑鼠移到可編輯圖片會提示，點選時通知主視窗" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9：主視窗點圖片轉換與自動還原

**Files:**
- Modify: `src/pdf_editor/ui/stamp_actions.py`
- Modify: `src/pdf_editor/ui/text_actions.py`（`select_run` 第 17 行、`select_annotation` 第 227 行）
- Modify: `src/pdf_editor/ui/main_window.py`（`__init__` 狀態與訊號、`confirm_leave`、`goto_page`、`history_step`、`save_over_original`、`save`）
- Test: `tests/test_ui.py`

- [ ] **Step 1：寫失敗測試**

`tests/test_ui.py` 在 `test_window_converts_selected_legacy_stamp_into_layer` 之前加入：

```python
def _open_stamp_page_window(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "材質證明.pdf"
    path.write_bytes(_stamp_page_pdf())
    monkeypatch.setattr(main_window.Jobs, "submit", _submit_synchronously)
    return _open_window_for_legacy_stamp(qtbot, path)


def _click_first_image(window):
    image = window.page_data["images"][0]
    window.canvas.image_clicked.emit(image)
    return image


def _close_without_prompt(window):
    window.session.saved_fingerprint = window.session.history.current[2]
    window.close()


def test_clicking_image_converts_it_into_selected_layer(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        image = _click_first_image(window)

        assert len(window.session.overlays) == 1
        layer = window.session.overlays[0]
        assert (layer.page, layer.rect) == (image.page, image.rect)
        assert window.layer_id == layer.id
        assert window.panels.currentWidget() is window.overlay_panel
        assert not window.busy
    finally:
        _close_without_prompt(window)


def test_blank_click_reverts_unchanged_image_conversion(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)

        window.canvas.background_clicked.emit()

        assert window.session.overlays == ()
        assert not window.session.dirty
        assert not window.session.can_redo
        assert window.layer_id is None
    finally:
        _close_without_prompt(window)


def test_moved_image_layer_is_kept_after_blank_click(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)
        layer = window.session.overlays[0]
        window.move_layer(replace(layer, rect=(100, 100, 160, 140)))

        window.canvas.background_clicked.emit()

        assert window.session.overlays[0].rect == (100, 100, 160, 140)
        assert window.session.dirty
    finally:
        _close_without_prompt(window)


def test_selecting_same_layer_keeps_pending_conversion(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)
        layer = window.session.overlays[0]

        # 開始拖曳轉換後的圖層時會再次選取它，不能因此撤銷。
        window.canvas.layer_selected.emit(layer.id)

        assert window.session.overlays == (layer,)
    finally:
        _close_without_prompt(window)


def test_undo_after_unchanged_image_conversion_only_reverts_it(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)

        window.history_step(False)

        assert window.session.overlays == ()
        assert not window.session.dirty
        assert not window.session.can_undo
        assert not window.session.can_redo
    finally:
        _close_without_prompt(window)


def test_selecting_text_reverts_conversion_and_reselects_text(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)
        run = next(r for r in window.page_data["runs"] if "品質" in r.text)

        window.select_run(run)

        assert window.session.overlays == ()
        assert window.run is not None and "品質" in window.run.text
    finally:
        _close_without_prompt(window)


def test_save_as_reverts_unchanged_image_conversion_first(qtbot, tmp_path, monkeypatch):
    target = tmp_path / "另存.pdf"
    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName",
        lambda *args, **kwargs: (str(target), "PDF (*.pdf)"))
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)

        window.save()

        assert window.session.overlays == ()
        assert target.exists()
        assert not window.session.dirty
    finally:
        _close_without_prompt(window)


def test_failed_image_click_reports_reason_in_status_bar(qtbot, tmp_path, monkeypatch):
    def reject(*_args):
        raise EditorError("STAMP_CONVERSION", "這張圖片在文件中重複使用，無法單獨編輯。")

    monkeypatch.setattr(stamp_actions, "convert_image_at", reject)
    monkeypatch.setattr(main_window.QMessageBox, "warning",
        lambda *args: pytest.fail("點圖片失敗不應跳出對話框"))
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)

        assert window.session.overlays == ()
        assert "重複使用" in window.statusBar().currentMessage()
        assert not window.busy
    finally:
        _close_without_prompt(window)
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_ui.py -q -k "image_conversion or clicking_image or image_layer or same_layer or reselects_text or image_click"`
Expected: 全部 FAIL（點圖片沒有任何反應，`window.session.overlays == ()`；或 `AttributeError: ... 'convert_image_at'`）

- [ ] **Step 3：`stamp_actions.py` 實作點選轉換與自動還原**

import 改為：

```python
from pdf_editor.legacy_overlay_conversion import (
    convert_image_at,
    convert_legacy_image,
    find_convertible_images,
)
```

模組 docstring 改為 `"""圖章與簽名：匯入、收藏、點選或轉換既有圖片、多頁連續蓋章與圖層調整。"""`

在 `convert_legacy_stamp` 之前加入：

```python
    def edit_image_at(self,image):
        """直接點選頁面上的圖片：在背景轉成可拖曳、縮放的圖層。"""
        if not self.session or self.busy or not self.session.access.can_edit:
            return
        token=self.token
        revision=self.session.revision
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage("正在準備編輯圖片…")

        def done(result):
            self._apply_clicked_image(result,token,revision)

        def failed(error):
            self._finish_clicked_image_failure(error,token,revision)

        self.jobs.submit(convert_image_at,(self.session.pdf,image,self.asset_root),done,failed)

    def _apply_clicked_image(self,result,token,revision):
        self.busy=False
        if not self._legacy_stamp_context_is_current(token,revision):
            self.refresh_actions()
            return
        base_pdf,layer=result
        pending=self.pending_conversion
        pending_ids=pending[1] if pending and pending[0]==revision else ()
        self.session.apply_state(base_pdf,self.session.overlays+(layer,))
        # 記下轉換後的版本；之後若沒有任何改動就離開，會自動撤銷轉換。
        # 必須先記錄再選取，選取同一圖層時才不會被當成離開。
        self.pending_conversion=(self.session.revision,pending_ids+(layer.id,))
        self.clear_search_results()
        self.select_layer(layer.id)
        self.refresh_actions()
        self.request_render("可拖曳圖片移動，或拖曳四角縮放；右側可勾選「去除白底」。")

    def _finish_clicked_image_failure(self,error,token,revision):
        self.busy=False
        self.refresh_actions()
        if self._legacy_stamp_context_is_current(token,revision):
            # 點圖片失敗只在狀態列說明，不跳出對話框打斷操作。
            self.statusBar().showMessage(error[1])

    def discard_pending_conversion(self):
        """點圖片轉換後若沒有任何改動，撤銷轉換且不留重做紀錄；回傳是否有撤銷。"""
        if self.busy:
            return False
        pending,self.pending_conversion=self.pending_conversion,None
        if pending is None or self.session is None:
            return False
        revision,layer_ids=pending
        if self.session.revision!=revision:
            return False
        # 大型文件的轉換前狀態可能已被歷程上限裁掉；無法全部撤銷時保留轉換。
        if not self.session.can_discard(len(layer_ids)):
            return False
        for _ in layer_ids:
            self.session.discard_last()
        if self.layer_id in layer_ids:
            self.layer_id=None
            self.panels.setCurrentWidget(self.text_panel)
        self.clear_search_results()
        self.refresh_actions()
        self.request_render()
        return True
```

`select_layer` 開頭（`self.canvas.cancel_inline_editor()` 之前）加入：

```python
        pending=self.pending_conversion
        if pending and id not in pending[1]:
            self.discard_pending_conversion()
```

- [ ] **Step 4：`text_actions.py` 選文字與註解前先還原**

`select_run` 在 `if not self.session or not self.session.access.can_edit: return` 之後加入：

```python
        if self.pending_conversion is not None:
            # 撤銷轉換會重新渲染頁面；先登記，渲染完成後選回這段文字。
            self._reselect_after_render=(self.page,tuple(run.rect),run.text)
            if self.discard_pending_conversion():
                return
            self._reselect_after_render=None
```

`select_annotation` 第一行之前加入：

```python
        self.discard_pending_conversion()
```

- [ ] **Step 5：`main_window.py` 狀態、訊號與離開時機**

`__init__` 在 `self.layer_id=None` 之後加入：

```python
        # 點圖片轉換後尚未改動的狀態：(轉換後版本, 轉換出的圖層 ID)。
        self.pending_conversion=None
```

在 `self.canvas.layer_moved.connect(self.move_layer)` 之後加入：

```python
        self.canvas.image_clicked.connect(self.edit_image_at)
        self.canvas.background_clicked.connect(self.discard_pending_conversion)
```

`confirm_leave` 改為：

```python
    def confirm_leave(self):
        if self.busy:
            return False
        self.discard_pending_conversion()
        if self.session and self.session.dirty:
```

（其餘不變。）

`goto_page` 在第一行的 guard 之後加入：

```python
        self.discard_pending_conversion()
```

`history_step` 改為：

```python
    def history_step(self,redo):
        if not self.session or self.busy:
            return
        # 尚未改動的圖片轉換：這次復原只撤銷轉換本身。
        if self.discard_pending_conversion():
            return
        self.cancel_editing_modes(crop=False)
```

（其餘不變。）

`save_over_original` 與 `save` 兩個函式，都在 `if not self.session or self.busy: return` 之後加入：

```python
        self.discard_pending_conversion()
```

另外，`open_document` 成功換成新文件時要清掉舊狀態：在 `open_document` 中 `self.token+=1`（第 768 行）那一行之後加入：

```python
        self.pending_conversion=None
```

- [ ] **Step 5b：快取可點選圖片清單，縮放與重繪時不重算**

`render_page` 已有 `include_images=True` 參數（False 時結果沒有 `"images"`）。可點選圖片只跟文件版本與頁碼有關，與縮放無關。

`__init__` 在 `self.pending_conversion=None` 之後加入：

```python
        # 可點選圖片清單：{(文件版本, 頁碼): images}，只保留目前文件版本的項目。
        self._image_cache={}
```

`request_render` 中 `layers=self.session.overlays` 之後加入：

```python
        image_key=(self.session.document_key,self.page)
        cached_images=self._image_cache.get(image_key)
```

`done` 裡 `self.page_data=result` 之前加入：

```python
            if "images" in result:
                self._image_cache={key:value for key,value in self._image_cache.items()
                    if key[0]==image_key[0]}
                self._image_cache[image_key]=result["images"]
            else:
                result["images"]=cached_images
```

最後的 `self.jobs.submit(render_page,(data,self.page,self.scale,pixel_ratio),done,self.error)` 改為：

```python
        self.jobs.submit(render_page,(data,self.page,self.scale,pixel_ratio,cached_images is None),
            done,self.error)
```

若既有測試擷取 `render_page` 的參數並比對整個 tuple，依新參數調整該測試的期望值（不要改動測試要驗證的行為）。

在 Step 1 的測試之後再加入兩個測試：

```python
def test_render_reuses_cached_editable_images_when_zooming(qtbot, tmp_path, monkeypatch):
    calls = []
    real_render = main_window.render_page

    def spy_render(*args):
        calls.append(args)
        return real_render(*args)

    monkeypatch.setattr(main_window, "render_page", spy_render)
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        images = window.page_data["images"]
        assert images
        calls.clear()

        window.zoom_by(1)

        assert calls and calls[-1][4] is False
        assert window.page_data["images"] == images
    finally:
        _close_without_prompt(window)


def test_unchanged_conversion_is_kept_when_history_was_trimmed(qtbot, tmp_path, monkeypatch):
    window = _open_stamp_page_window(qtbot, tmp_path, monkeypatch)
    try:
        _click_first_image(window)
        monkeypatch.setattr(window.session, "can_discard", lambda count=1: False)

        window.canvas.background_clicked.emit()

        assert len(window.session.overlays) == 1
        assert window.pending_conversion is None
    finally:
        _close_without_prompt(window)
```

- [ ] **Step 6：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_ui.py tests/test_stamps.py -q`
Expected: 全部 PASS

- [ ] **Step 7：Commit**

```bash
git add src/pdf_editor/ui/stamp_actions.py src/pdf_editor/ui/text_actions.py src/pdf_editor/ui/main_window.py tests/test_ui.py
git commit -m "功能：直接點選頁面圖片即可移動縮放，未改動就離開時自動還原" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10：選單改名與使用說明

**Files:**
- Modify: `src/pdf_editor/ui/main_window.py`（第 182 行、第 314–316 行 tips）
- Modify: `src/pdf_editor/ui/legacy_stamp_dialog.py`（第 22 行）
- Modify: `tests/test_ui.py`（第 2010 行附近）
- Modify: `docs/使用說明.md`

- [ ] **Step 1：更新測試期望**

`tests/test_ui.py` 中

```python
        assert window.actions["convert_stamp"].text() == "轉換既有圖章"
```

改為：

```python
        assert window.actions["convert_stamp"].text() == "編輯既有圖片…"
```

- [ ] **Step 2：執行測試確認失敗**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_ui.py -q -k "exposes_legacy_stamp_conversion"`
Expected: FAIL，`AssertionError: assert '轉換既有圖章' == '編輯既有圖片…'`

- [ ] **Step 3：改名**

`src/pdf_editor/ui/main_window.py`：

```python
            ("convert_stamp","編輯既有圖片…",self.convert_legacy_stamp,None),
```

tips 中兩項改為：

```python
            "stamp":"匯入 PNG 圖章；點右側箭頭可使用常用圖章或編輯既有圖片",
            "convert_stamp":"列出文件中可單獨編輯的圖片，選取後即可移動、縮放（也可直接在頁面上點圖片）",
```

`src/pdf_editor/ui/legacy_stamp_dialog.py`：

```python
        self.setWindowTitle("編輯既有圖片")
```

- [ ] **Step 4：更新使用說明**

`docs/使用說明.md`：

第 19 行的 `「蓋章」右側箭頭內含「常用圖章」與「轉換既有圖章」。` 改為 `「蓋章」右側箭頭內含「常用圖章」與「編輯既有圖片…」。`

第 107 行整段換成：

```markdown
**編輯 PDF 中原有的圖片**：滑鼠移到頁面上原有的圖片（例如材質證明上的公司章）時，游標會變成手指並出現虛線外框，直接點一下，約半秒後出現四角控制點，就可以拖曳移動或拖曳四角縮放，也能用右側欄位精確調整。如果點了之後沒有做任何調整就點別處、換頁、存檔或關閉，程式會自動還原，文件不會被標成已修改。點到的位置同時有文字時會優先選取文字；這時可改用「蓋章」右側箭頭中的「編輯既有圖片…」，從縮圖清單選取後按「轉換為可編輯圖章」。

只有在整份文件中使用一次、只有一個放置位置、沒有旋轉或傾斜，且面積小於頁面 90% 的單一圖片可以編輯。為避免誤改文件，整頁掃描、跨頁或重複使用的 Logo／影像、被其他內容覆蓋的圖片，以及由線條或文字組成的向量章都不會轉換；無法編輯時，狀態列會說明原因，文件不會被變更。轉換與調整都可用 `Ctrl+Z` 復原、`Ctrl+Y` 重做。

**去除白底**：有些圖章沒有透明背景，移到表格或文字上會用白底蓋住底下的內容。選取圖章後勾選右側的「去除白底」，接近白色的部分就會變透明；原圖不會被修改，取消勾選即可還原。這個設定會隨另存的檔案保存。
```

- [ ] **Step 5：執行測試確認通過**

Run: `work/build-venv312/Scripts/python.exe -m pytest tests/test_ui.py -q -k "legacy"`
Expected: 全部 PASS

- [ ] **Step 6：Commit**

```bash
git add src/pdf_editor/ui/main_window.py src/pdf_editor/ui/legacy_stamp_dialog.py tests/test_ui.py docs/使用說明.md
git commit -m "調整：「轉換既有圖章」改名為「編輯既有圖片」並更新使用說明" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11：全套測試與實際材質證明驗收

**Files:**
- Create（暫存，不提交）：scratchpad 目錄下的 `acceptance_edit_image.py`

- [ ] **Step 1：跑全套測試**

Run: `work/build-venv312/Scripts/python.exe -m pytest -q`
Expected: 全部 PASS。若有失敗，先用 superpowers:systematic-debugging 找出原因再修。

- [ ] **Step 2：撰寫實機驗收腳本**

在 scratchpad 目錄建立 `acceptance_edit_image.py`（以參數傳入材質證明路徑，不放進程式庫）：

```python
"""以實際材質證明驗收：點選轉換、移動縮放、去除白底、另存重開。"""
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path.cwd() / "src"))
from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.legacy_overlay_conversion import (
    _samples_match, convert_image_at, editable_images_on_page, find_convertible_images)
from pdf_editor.persistent_overlays import embed_workspace, load_workspace

source = Path(sys.argv[1]).read_bytes()
work = Path(tempfile.mkdtemp(prefix="驗收-"))

with pymupdf.open(stream=source, filetype="pdf") as document:
    images = editable_images_on_page(document, 0)
print("可點選圖片：", [(i.xref, i.rect) for i in images])
assert len(images) == 1
assert len(find_convertible_images(source)) == 1, "清單入口也應列出"

base, layer = convert_image_at(source, images[0], work / "assets")
x0, y0, x1, y1 = layer.rect
moved = replace(layer, rect=(x0 + 200, y0, x0 + 200 + (x1 - x0) * 1.3, y0 + (y1 - y0) * 1.3),
                remove_white=True)
saved = embed_workspace(base, (moved,))
(work / "已編輯.pdf").write_bytes(saved)

restored = load_workspace(saved, work / "restored")
assert restored is not None and restored.overlays[0].remove_white
assert restored.overlays[0].rect == moved.rect


def render(pdf, clip):
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        pixmap = document[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False, clip=clip)
        return pixmap.width, pixmap.height, pixmap.n, pixmap.samples


# 表格區（圖章上方）不得有任何可見變化。
table = pymupdf.Rect(30, 120, 570, 430)
before, after = render(source, table), render(flatten_overlays(base, (moved,)), table)
assert _samples_match(*before[:3], before[3], after[3]), "表格區有變化"
# 舊位置不殘留圖章。
old = pymupdf.Rect(layer.rect)
blank = render(flatten_overlays(base, ()), old)
assert set(blank[3]) <= set(range(240, 256)), "舊位置仍有圖章"
print("驗收通過，輸出：", work / "已編輯.pdf")
```

- [ ] **Step 3：執行驗收腳本**

Run: `work/build-venv312/Scripts/python.exe <scratchpad>/acceptance_edit_image.py ../115.01.02-2014-F.pdf`
Expected: 印出「可點選圖片：[(72, (119.11…, 448.63…, 202.52…, 521.22…))]」與「驗收通過」。

- [ ] **Step 4：目視檢查輸出**

把輸出 PDF 渲染成 PNG，用 Read 工具檢視：圖章右移、放大，白底透明，表格與頁尾文字完整、沒有重影。

```bash
work/build-venv312/Scripts/python.exe -c "import pymupdf,sys; d=pymupdf.open(sys.argv[1]); d[0].get_pixmap(dpi=100).save(sys.argv[2])" "<上一步印出的已編輯.pdf>" "<scratchpad>/已編輯.png"
```

---

### Task 12：昇版 0.21.0、建置與驗收紀錄

**Files:**
- Modify: `pyproject.toml:7`、`src/pdf_editor/__init__.py:2`、`packaging/installer.iss:5`、`docs/使用說明.md:1`、`docs/驗收紀錄.md`

- [ ] **Step 1：昇版**

- `pyproject.toml`：`version = "0.21.0"`
- `src/pdf_editor/__init__.py`：`__version__ = "0.21.0"`
- `packaging/installer.iss`：`AppVersion=0.21.0`
- `docs/使用說明.md` 第 1 行：`# 墨頁 PDF 0.21.0`

- [ ] **Step 2：建置安裝程式**

Run（PowerShell）：

```powershell
./scripts/build.ps1 -InnoCompiler 'C:/Tools/Inno/ISCC.exe' -PythonExecutable 'work/build-venv312/Scripts/python.exe'
```

Expected: 建置成功，`dist/installer/` 產生 0.21.0 安裝程式。

- [ ] **Step 3：安裝到含中文的路徑並跑煙霧測試**

依記憶中的規則，靜默安裝到 `work\v0210-install`（含中文的上層路徑 `C:\墨頁PDF`），執行安裝版的 `--ocr-comparison-smoke-test`，確認結束代碼為 0；並以安裝版開啟材質證明，請使用者實際點圖章拖曳、縮放、勾選去除白底並另存，確認重開後仍可編輯。

- [ ] **Step 4：撰寫驗收紀錄**

在 `docs/驗收紀錄.md` 最上方新增「# 墨頁 PDF 0.21.0 驗收紀錄」一節（原 0.20.1 內容保留在下方，標題降一級），記錄：全套測試結果（通過數）、Task 11 驗收腳本輸出、目視檢查結論、安裝與煙霧測試結果、使用者實機操作結果。

- [ ] **Step 5：Commit**

```bash
git add pyproject.toml src/pdf_editor/__init__.py packaging/installer.iss docs/使用說明.md docs/驗收紀錄.md
git commit -m "發布：昇版至 0.21.0 收錄直接編輯既有圖片與去除白底並記錄驗收結果" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
