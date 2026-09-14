# 可持久編輯圖章實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓新加入與安全轉換的既有 PNG 圖章在另存、關閉及重開後，仍可選取、移動、縮放與刪除。

**Architecture:** 另存時產生兩種表示：對所有閱讀器可見的已繪入圖章 PDF，以及儲存乾淨基底、PNG 資產與圖層 JSON 的內嵌工作層。重開時只有驗證成功的工作層會還原成 Overlay；未含工作層或工作層失效的檔案維持一般靜態 PDF。舊圖章必須由使用者從安全候選清單中明確選取，才可抽離為圖層。

**Tech Stack:** Python 3.10、PyMuPDF 1.28.2、Pillow 12.3.0、PySide6 Essentials 6.8.3、pytest、pytest-qt。

**Spec:** `docs/superpowers/specs/2026-09-14-persistent-editable-stamps-design.md`

## Global Constraints

- 使用者介面、錯誤訊息、註解與提交訊息均使用繁體中文。
- 來源 PDF 不得覆寫；輸出延續既有暫存檔及原子重新命名流程。
- 可見 PDF 必須在未安裝墨頁 PDF 的閱讀器中正常顯示圖章。
- 工作層毀損、版本不支援或被第三方軟體移除時，安全退回靜態 PDF。
- 舊影像僅在全文件唯一使用、單頁唯一矩形且未達頁面面積 90% 時列入候選。
- 每個程式任務依序完成失敗測試、最小實作、通過測試與提交。

---

## 檔案結構

- 建立：`src/pdf_editor/persistent_overlays.py`，負責工作層內嵌、讀取、驗證與資產還原。
- 建立：`src/pdf_editor/legacy_overlay_conversion.py`，負責舊影像候選掃描與安全轉換。
- 建立：`src/pdf_editor/ui/legacy_stamp_dialog.py`，負責候選縮圖與選取。
- 修改：`model.py`、`assets.py`、`document/history.py`、`document/session.py`、`document/save.py`。
- 修改：`ui/main_window.py`、`docs/使用說明.md`、`docs/驗收紀錄.md`。
- 建立或修改：`tests/test_persistent_overlays.py`、`tests/test_legacy_overlay_conversion.py`、`tests/test_overlay.py`、`tests/test_session.py`、`tests/test_ui.py`、`tests/test_workflows.py`。

### Task 1: 定義並驗證 PDF 內嵌工作層

**Files:**

- Create: `src/pdf_editor/persistent_overlays.py`
- Modify: `src/pdf_editor/model.py`
- Modify: `src/pdf_editor/assets.py`
- Create: `tests/test_persistent_overlays.py`

**Interfaces:**

- Produces: `PersistentOverlayBundle(base_pdf: bytes, overlays: tuple[Overlay, ...])`。
- Produces: `embed_workspace(base_pdf: bytes, overlays: tuple[Overlay, ...]) -> bytes`。
- Produces: `load_workspace(pdf: bytes, asset_root: Path) -> PersistentOverlayBundle | None`。
- Produces: `AssetStore.import_png_bytes(content: bytes) -> Path`。

- [ ] **Step 1: 寫入工作層往返的失敗測試**

```python
def test_embedded_workspace_restores_base_and_overlay_assets(tmp_path, pdf_bytes):
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (20, 10), (0, 60, 255, 180)).save(stamp)
    layer = Overlay("章-1", 0, str(stamp), (100, 110, 140, 130), 15)

    restored = load_workspace(embed_workspace(pdf_bytes, (layer,)), tmp_path / "assets")

    assert restored is not None
    assert restored.base_pdf == pdf_bytes
    assert restored.overlays[0].id == "章-1"
    assert Path(restored.overlays[0].asset_path).read_bytes() == stamp.read_bytes()
```

- [ ] **Step 2: 執行測試並確認失敗**

Run: `pytest tests/test_persistent_overlays.py::test_embedded_workspace_restores_base_and_overlay_assets -v`

Expected: FAIL，因為 `persistent_overlays` 尚不存在。

- [ ] **Step 3: 建立資料模型與資產位元匯入**

```python
@dataclass(frozen=True)
class PersistentOverlayBundle:
    base_pdf: bytes
    overlays: tuple[Overlay, ...]

class AssetStore:
    def import_png_bytes(self, content: bytes) -> Path:
        with Image.open(io.BytesIO(content)) as image:
            if image.format != "PNG" or image.width * image.height > 25_000_000:
                raise EditorError("IMAGE", "內嵌圖章不是有效 PNG 或超過大小限制。")
        return self._store_rgba_png(content)
```

將既有 `import_png()` 改為共用 `_store_rgba_png()`，以相同的 RGBA 正規化及 SHA-256 名稱儲存檔案或內嵌資產。

- [ ] **Step 4: 實作內嵌格式與驗證**

```python
WORKSPACE_VERSION = 1
MANIFEST_NAME = "moye-pdf/workspace.json"
BASE_NAME = "moye-pdf/base.pdf"

def embed_workspace(base_pdf, overlays):
    visible = flatten_overlays(base_pdf, overlays)
    with pymupdf.open(stream=visible, filetype="pdf") as doc:
        doc.embfile_add(BASE_NAME, base_pdf, filename="base.pdf")
        doc.embfile_add(MANIFEST_NAME, manifest_bytes, filename="workspace.json")
        for name, content in assets.items():
            doc.embfile_add(name, content, filename=Path(name).name)
        return doc.tobytes(garbage=4, deflate=True)
```

manifest 為 UTF-8 JSON，包含 `version`、`base_sha256`、`page_count`、`id`、`page`、`rect`、`angle`、`asset_name` 與 `asset_sha256`。讀取端逐項驗證 JSON、版本、雜湊、PNG、基底 PDF 與頁數；沒有 manifest 回傳 `None`，有 manifest 但失效時拋出 `EditorError("WORKSPACE", "圖章工作層無法驗證，已改以靜態 PDF 開啟。")`。

- [ ] **Step 5: 寫入錯誤條件測試並驗證**

```python
def test_workspace_with_tampered_asset_is_rejected(tmp_path, pdf_bytes):
    with pytest.raises(EditorError, match="工作層無法驗證"):
        load_workspace(_embed_with_replaced_asset(pdf_bytes, b"not-a-png"), tmp_path / "assets")

def test_plain_pdf_has_no_workspace(tmp_path, pdf_bytes):
    assert load_workspace(pdf_bytes, tmp_path / "assets") is None
```

Run: `pytest tests/test_persistent_overlays.py -v`

Expected: PASS，涵蓋往返、靜態檔、毀損資產、頁數不符與未知版本。

- [ ] **Step 6: 提交工作層核心**

```bash
git add src/pdf_editor/model.py src/pdf_editor/assets.py src/pdf_editor/persistent_overlays.py tests/test_persistent_overlays.py
git commit -m "功能：保存可持久編輯圖章工作層"
```

### Task 2: 接入文件開啟、歷程與另存

**Files:**

- Modify: `src/pdf_editor/document/history.py`
- Modify: `src/pdf_editor/document/session.py`
- Modify: `src/pdf_editor/document/save.py`
- Modify: `tests/test_session.py`
- Modify: `tests/test_overlay.py`

**Interfaces:**

- Consumes: `load_workspace()`、`embed_workspace()`、`PersistentOverlayBundle`。
- Produces: `History.replace_initial(pdf, overlays)`。
- Produces: `DocumentSession.open()` 可還原已驗證工作層。

- [ ] **Step 1: 寫入另存、重開與移動的失敗測試**

```python
def test_saved_stamp_reopens_as_editable_overlay(source_path, tmp_path):
    stamp = tmp_path / "stamp.png"
    Image.new("RGBA", (20, 20), (0, 0, 255, 180)).save(stamp)
    target = tmp_path / "可重編.pdf"
    with DocumentSession.open(source_path) as session:
        session.set_overlays((Overlay("章", 0, str(stamp), (200, 200, 240, 240)),))
        save_as(session, target)

    with DocumentSession.open(target) as reopened:
        original = reopened.overlays[0]
        reopened.set_overlays((replace(original, rect=(260, 200, 300, 240)),))
        assert reopened.overlays[0].rect == (260, 200, 300, 240)
```

- [ ] **Step 2: 執行測試並確認失敗**

Run: `pytest tests/test_session.py::test_saved_stamp_reopens_as_editable_overlay -v`

Expected: FAIL，因為重開後 `overlays` 為空。

- [ ] **Step 3: 實作工作階段復原**

```python
class History:
    def __init__(self, pdf: bytes, overlays: tuple[Overlay, ...] = ()):
        self.push(pdf, overlays)

    def replace_initial(self, pdf: bytes, overlays: tuple[Overlay, ...]):
        assert self.index == 0 and len(self.items) == 1
        path = self.items[0][0]
        path.write_bytes(pdf)
        self.items[0] = (path, tuple(overlays), fingerprint(pdf, overlays))

class DocumentSession:
    @classmethod
    def open(cls, path, password=None):
        visible, access = unlock_pdf(path.read_bytes(), password)
        obj = cls()
        obj.history = History(visible)
        workspace = _load_workspace_or_none(visible, obj.history.root / "assets")
        if workspace:
            obj.history.replace_initial(workspace.base_pdf, workspace.overlays)
        obj.access = access
        return obj
```

`_load_workspace_or_none()` 只捕捉 `WORKSPACE`，把提示存入 `open_notice`；主視窗開檔後顯示提示。不能在還原圖層時額外呼叫 `push()`，避免第一次 Ctrl+Z 讓章消失。

- [ ] **Step 4: 實作含圖層的另存與重影測試**

```python
def save_as(session, target, overwrite=False):
    output = embed_workspace(session.pdf, session.overlays) if session.overlays else session.pdf
    return write_pdf(output, target, overwrite, (session.source,))

def test_reopen_then_resave_has_one_visible_stamp(source_path, tmp_path):
    stamp = tmp_path / "半透明章.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 120)).save(stamp)
    first, second = tmp_path / "first.pdf", tmp_path / "second.pdf"
    with DocumentSession.open(source_path) as session:
        session.set_overlays((Overlay("章", 0, str(stamp), (200, 200, 240, 240)),))
        save_as(session, first)
    with DocumentSession.open(first) as reopened:
        save_as(reopened, second)
    with pymupdf.open(first) as a, pymupdf.open(second) as b:
        assert a[0].get_pixmap().samples == b[0].get_pixmap().samples
```

- [ ] **Step 5: 執行測試並提交**

Run: `pytest tests/test_session.py tests/test_overlay.py tests/test_persistent_overlays.py -v`

Expected: PASS，保存、重開、移動、復原與多次另存均通過。

```bash
git add src/pdf_editor/document/history.py src/pdf_editor/document/session.py src/pdf_editor/document/save.py tests/test_session.py tests/test_overlay.py
git commit -m "功能：重開 PDF 後恢復可編輯圖章"
```

### Task 3: 掃描與安全轉換既有圖章影像

**Files:**

- Create: `src/pdf_editor/legacy_overlay_conversion.py`
- Modify: `src/pdf_editor/model.py`
- Create: `tests/test_legacy_overlay_conversion.py`

**Interfaces:**

- Produces: `LegacyImageCandidate(xref, page, rect, png, width, height)`。
- Produces: `find_convertible_images(pdf: bytes) -> tuple[LegacyImageCandidate, ...]`。
- Produces: `convert_legacy_image(pdf, candidate, asset_root) -> tuple[bytes, Overlay]`。

- [ ] **Step 1: 寫入唯一影像候選的失敗測試**

```python
def test_finds_single_use_stamp_but_rejects_logo_reused_on_two_pages():
    candidates = find_convertible_images(_pdf_with_unique_stamp_and_reused_logo())

    assert [(item.page, item.rect) for item in candidates] == [(0, (210, 260, 270, 300))]
    assert candidates[0].width == 60
    assert candidates[0].height == 40
```

- [ ] **Step 2: 執行測試並確認失敗**

Run: `pytest tests/test_legacy_overlay_conversion.py::test_finds_single_use_stamp_but_rejects_logo_reused_on_two_pages -v`

Expected: FAIL，因為候選掃描函式尚不存在。

- [ ] **Step 3: 實作安全候選掃描**

```python
def find_convertible_images(pdf):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        uses = _image_uses(doc)
        return tuple(
            _candidate_from_use(doc, xref, page, rect)
            for xref, locations in uses.items()
            if len(locations) == 1 and _is_not_page_scan(doc[locations[0][0]], locations[0][1])
        )
```

`_image_uses()` 使用 `page.get_images(full=True)` 收集 xref，再以 `page.get_image_rects(xref)` 收集實際矩形。矩形面積達裁切頁面 90% 以上就排除；以 `doc.extract_image(xref)["image"]` 取出資產，必要時由 Pillow 正規化成 RGBA PNG。

- [ ] **Step 4: 寫入抽離舊影像且不留殘影的失敗測試**

```python
def test_convert_legacy_stamp_removes_base_image_and_returns_overlay(tmp_path):
    source = _pdf_with_unique_stamp()
    candidate = find_convertible_images(source)[0]

    base_pdf, layer = convert_legacy_image(source, candidate, tmp_path / "assets")

    assert _blue_stamp_pixels(base_pdf) == 0
    assert _blue_stamp_pixels(flatten_overlays(base_pdf, (layer,))) > 0
    assert layer.rect == candidate.rect
```

- [ ] **Step 5: 實作原子轉換交易**

```python
def convert_legacy_image(pdf, candidate, asset_root):
    current = {item.xref: item for item in find_convertible_images(pdf)}
    if current.get(candidate.xref) != candidate:
        raise EditorError("STAMP_CONVERSION", "圖章候選已變更，請重新掃描後再選取。")
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        doc[candidate.page].delete_image(candidate.xref)
        base_pdf = doc.tobytes(garbage=4, deflate=True)
    _verify_pdf(base_pdf)
    asset = AssetStore(asset_root).import_png_bytes(candidate.png)
    return base_pdf, Overlay(uuid.uuid4().hex, candidate.page, str(asset), candidate.rect)
```

在 `delete_image()` 前再次確認 xref 仍是全文件唯一使用。基底無法重開、原影像仍存在或資產無效時，拋出 `STAMP_CONVERSION` 且不得回傳部分結果。

- [ ] **Step 6: 寫入拒絕測試、驗證並提交**

```python
def test_full_page_scan_is_not_a_convertible_stamp():
    assert find_convertible_images(_single_scanned_page_pdf()) == ()

def test_stale_candidate_cannot_change_pdf(tmp_path):
    with pytest.raises(EditorError, match="候選已變更"):
        convert_legacy_image(_pdf_with_reused_image(), stale_candidate, tmp_path / "assets")
```

Run: `pytest tests/test_legacy_overlay_conversion.py -v`

Expected: PASS，候選、重複影像拒絕、整頁掃描拒絕、轉換外觀與無殘影均通過。

```bash
git add src/pdf_editor/model.py src/pdf_editor/legacy_overlay_conversion.py tests/test_legacy_overlay_conversion.py
git commit -m "功能：安全轉換既有圖章影像"
```

### Task 4: 候選選取對話框與主視窗流程

**Files:**

- Create: `src/pdf_editor/ui/legacy_stamp_dialog.py`
- Modify: `src/pdf_editor/ui/main_window.py`
- Modify: `tests/test_ui.py`

**Interfaces:**

- Consumes: `find_convertible_images()` 與 `convert_legacy_image()`。
- Produces: 工具列動作 `convert_stamp`，標示「轉換既有圖章」。
- Produces: `LegacyStampDialog(candidates, parent)`，以 `selected_candidate` 回傳選取候選。

- [ ] **Step 1: 寫入對話框的失敗測試**

```python
def test_legacy_stamp_dialog_requires_explicit_candidate(qtbot):
    candidates = (_candidate(page=0), _candidate(page=2))
    dialog = LegacyStampDialog(candidates)
    qtbot.addWidget(dialog)

    assert not dialog.ok_button.isEnabled()
    dialog.list.setCurrentRow(1)
    assert dialog.ok_button.isEnabled()
    assert dialog.selected_candidate == candidates[1]
```

- [ ] **Step 2: 執行測試並確認失敗**

Run: `pytest tests/test_ui.py::test_legacy_stamp_dialog_requires_explicit_candidate -v`

Expected: FAIL，因為 `LegacyStampDialog` 尚不存在。

- [ ] **Step 3: 實作候選選取對話框**

```python
class LegacyStampDialog(QDialog):
    def __init__(self, candidates, parent=None):
        super().__init__(parent)
        self.list = QListWidget()
        self._candidates = tuple(candidates)
        # 每列縮圖標示頁碼、X、Y、寬與高，未選取前確認鈕停用。

    @property
    def selected_candidate(self):
        row = self.list.currentRow()
        return self._candidates[row] if row >= 0 else None
```

文字必須說明只顯示可安全轉換的單一影像；Logo、掃描頁與重複影像不列出。候選為空時不開對話框，改顯示狀態訊息。

- [ ] **Step 4: 寫入主視窗轉換流程的失敗測試**

```python
def test_window_converts_selected_legacy_stamp_into_layer(qtbot, source_path, monkeypatch):
    window = _open_window(qtbot, source_path)
    monkeypatch.setattr(main_window, "find_convertible_images", lambda pdf: (_candidate(),))
    monkeypatch.setattr(main_window.LegacyStampDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    window.convert_legacy_stamp()

    qtbot.waitUntil(lambda: len(window.session.overlays) == 1)
    assert window.layer_id == window.session.overlays[0].id
```

- [ ] **Step 5: 接入工具列、背景工作與歷程提交**

```python
def convert_legacy_stamp(self):
    if not self.session or self.busy or not self.session.access.can_edit:
        return
    self.jobs.submit(find_convertible_images, (self.session.pdf,), self._show_stamp_candidates, self.error)

def _apply_converted_stamp(self, result):
    base_pdf, layer = result
    self.session.apply_state(base_pdf, self.session.overlays + (layer,))
    self.select_layer(layer.id)
    self.request_render("已轉換為可編輯圖章。")
```

候選掃描與轉換都在背景工作中執行；以 token 與 revision 防止舊結果套用到新文件，並在完成或錯誤時還原 `busy` 與控制項。

- [ ] **Step 6: 執行介面測試並提交**

Run: `pytest tests/test_ui.py -k "legacy_stamp or overlay" -v`

Expected: PASS，涵蓋動作啟用、未選取不能確認、選取後建立圖層、候選為空提示與取消不修改文件。

```bash
git add src/pdf_editor/ui/legacy_stamp_dialog.py src/pdf_editor/ui/main_window.py tests/test_ui.py
git commit -m "介面：加入既有圖章轉換流程"
```

### Task 5: 端對端驗證、文件與 Windows smoke test

**Files:**

- Modify: `tests/test_workflows.py`
- Modify: `docs/使用說明.md`
- Modify: `docs/驗收紀錄.md`

**Interfaces:**

- Consumes: Tasks 1-4 的保存、重開、轉換與介面流程。
- Produces: 可重複端對端驗收與明確的使用限制說明。

- [ ] **Step 1: 寫入完整流程的失敗測試**

```python
def test_convert_save_close_reopen_move_and_export(tmp_path):
    stamp = tmp_path / "舊統編章.png"
    Image.new("RGBA", (30, 20), (0, 40, 255, 255)).save(stamp)
    legacy = tmp_path / "舊章.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=500, height=400)
        page.insert_text((40, 60), "統編章轉換測試", fontsize=16)
        page.insert_image((200, 200, 230, 220), filename=stamp)
        legacy.write_bytes(doc.tobytes())

    with DocumentSession.open(legacy) as session:
        candidate = find_convertible_images(session.pdf)[0]
        base, layer = convert_legacy_image(session.pdf, candidate, session.history.root / "assets")
        session.apply_state(base, (layer,))
        save_as(session, tmp_path / "第一次另存.pdf")

    with DocumentSession.open(tmp_path / "第一次另存.pdf") as reopened:
        assert len(reopened.overlays) == 1
        moved = replace(reopened.overlays[0], rect=(300, 200, 330, 220))
        reopened.set_overlays((moved,))
        save_as(reopened, tmp_path / "第二次另存.pdf")

    with pymupdf.open(tmp_path / "第二次另存.pdf") as output:
        pix = output[0].get_pixmap()
        assert pix.pixel(215, 210)[:3] == (255, 255, 255)
        assert pix.pixel(315, 210)[:3] == (0, 40, 255)
```

- [ ] **Step 2: 執行測試並確認失敗**

Run: `pytest tests/test_workflows.py::test_convert_save_close_reopen_move_and_export -v`

Expected: FAIL，在 Tasks 1-4 完整串接前無法通過。

- [ ] **Step 3: 更新文件與驗收紀錄**

在「圖章與簽名」章節加入另存重開仍可編輯、舊圖章轉換步驟、唯一影像限制、掃描頁／Logo／向量章不支援，以及第三方最佳化可能移除工作層的警告。驗收紀錄新增重開拖曳、復原／重做、像素比對及無候選拒絕結果。

- [ ] **Step 4: 執行完整自動測試**

Run: `pytest -v`

Expected: PASS，零失敗。

- [ ] **Step 5: 建置 Windows 安裝程式並實際 smoke test**

Run: `powershell -ExecutionPolicy Bypass -File scripts/build.ps1`

Expected: 成功產生安裝程式；於隔離資料夾安裝後，以含藍色統編 PNG 的測試 PDF 執行「轉換既有圖章、另存、關閉、重開、拖曳、再另存」，並渲染輸出確認新舊位置沒有殘影或重影。

- [ ] **Step 6: 驗證 Git 變更並提交**

Run: `git diff --check && git status --short && git log --oneline -5`

Expected: 無空白錯誤，只包含本功能檔案與預期提交。

```bash
git add tests/test_workflows.py docs/使用說明.md docs/驗收紀錄.md
git commit -m "文件：補充可編輯圖章操作與驗收"
```

## 執行前最終對照

- 工作層保存乾淨基底、資產與圖層描述：Task 1、Task 2。
- 新圖章重開後可編輯且不重影：Task 2、Task 5。
- 舊統編章由使用者選取候選後轉換：Task 3、Task 4、Task 5。
- 不安全影像不會誤改：Task 3、Task 4。
- 外部閱讀器保有可見章、毀損工作層安全回退：Task 1、Task 5。
