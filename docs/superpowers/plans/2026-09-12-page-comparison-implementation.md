# 墨頁 PDF 頁面比較實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將目前頁面與另一份 PDF 指定頁面並排顯示，標示像素差異並計算可重現的相似度。

**Architecture:** `comparison.py` 是不依賴 Qt 的像素比較核心；`comparison_dialog.py` 只顯示三張同步大小預覽及頁碼控制。主視窗負責解鎖比較文件、固定目前尚未另存的圖層及把每次比較送到背景程序池。

**Tech Stack:** Python 3.12、PyMuPDF 1.28.2、Pillow 12.3.0、PySide6 Essentials 6.8.3。

**Spec:** `docs/superpowers/specs/2026-09-12-ocr-page-comparison-design.md`

## Global Constraints

- 比較基準固定為目前工作階段頁面，並包含尚未另存的文字、圖章及簽名。
- 比較來源為另一份 PDF 的指定頁面；加密文件沿用現有密碼解鎖流程。
- 固定以 144 DPI、RGB 白底渲染；差異門檻為 24。
- 比較頁等比例縮放後置中於基準頁大小的白色畫布。
- 差異使用半透明紅色覆蓋；相似度顯示至小數點後一位。
- 比較流程唯讀，不改變 `DocumentSession.revision`、PDF 或復原歷史。
- 所有使用者介面、錯誤訊息、程式註解及 commit 訊息使用繁體中文。

---

### Task 1: 頁面比較核心

**Files:**
- Create: `src/pdf_editor/comparison.py`
- Create: `tests/test_comparison.py`

**Interfaces:**
- Produces: `PageComparison(base_png: bytes, comparison_png: bytes, difference_png: bytes, similarity: float, changed_pixels: int, total_pixels: int, base_page: int, comparison_page: int)`
- Produces: `compare_pages(base_pdf: bytes, base_page: int, comparison_pdf: bytes, comparison_page: int, dpi: int = 144, threshold: int = 24) -> PageComparison`

- [ ] **Step 1: Write failing comparison tests**

```python
def test_identical_pages_have_one_hundred_percent_similarity(simple_pdf):
    result=compare_pages(simple_pdf,0,simple_pdf,0)
    assert result.similarity==100.0
    assert result.changed_pixels==0

def test_changed_block_is_marked_red(simple_pdf,changed_pdf):
    result=compare_pages(simple_pdf,0,changed_pdf,0)
    assert 0.0<result.similarity<100.0
    image=Image.open(io.BytesIO(result.difference_png)).convert("RGB")
    assert any(r>g+30 and r>b+30 for r,g,b in image.getdata())

def test_small_channel_difference_under_threshold_is_ignored(monkeypatch,simple_pdf):
    monkeypatch.setattr("pdf_editor.comparison._render",render_two_images_differing_by_24)
    result=compare_pages(simple_pdf,0,simple_pdf,0,threshold=24)
    assert result.changed_pixels==0
```

- [ ] **Step 2: Run comparison tests and verify RED**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_comparison.py -q`

Expected: collection fails because `pdf_editor.comparison` does not exist.

- [ ] **Step 3: Implement aligned rendering and red difference image**

Render the base page to RGB with PyMuPDF at `dpi / 72`. Render the comparison page, use `ImageOps.contain()` to fit it within the base image dimensions, and paste it centered on a white canvas. Reject invalid page indexes, DPI outside 72–300, and threshold outside 0–255 with `EditorError("COMPARE_RANGE", ...)`.

Use `ImageChops.difference()`, combine the RGB channels with `ImageChops.lighter()`, and map values `> threshold` to a binary mask. Count changed pixels from the mask histogram. Create the difference image by blending the base image with solid red at 45% only under that mask. Encode all images as PNG bytes and compute `similarity = round((total_pixels - changed_pixels) * 100 / total_pixels, 1)`.

- [ ] **Step 4: Run comparison core tests and full regression**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_comparison.py -q`

Expected: all comparison core tests pass.

Run: `work\build-venv312\Scripts\python.exe -m pytest -q`

Expected: all existing and new tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/pdf_editor/comparison.py tests/test_comparison.py
git commit -m "feat: 加入頁面像素差異比較"
```

### Task 2: 三欄頁面比較視窗

**Files:**
- Create: `src/pdf_editor/ui/comparison_dialog.py`
- Create: `tests/test_comparison_dialog.py`

**Interfaces:**
- Consumes: `PageComparison`
- Produces: `ComparisonDialog(comparison_page_count: int, base_page: int, parent=None)`
- Produces: signal `page_requested(int)` with zero-based page index
- Produces: methods `set_busy(bool)` and `set_result(PageComparison)`

- [ ] **Step 1: Write failing dialog tests**

```python
def test_comparison_dialog_shows_three_images_and_similarity(qtbot,page_comparison):
    dialog=ComparisonDialog(3,0); qtbot.addWidget(dialog)
    dialog.set_result(page_comparison)
    assert dialog.base_image.pixmap() is not None
    assert dialog.comparison_image.pixmap() is not None
    assert dialog.difference_image.pixmap() is not None
    assert dialog.summary.text()=="相似度 87.5%｜差異 125 / 1,000 像素"

def test_comparison_page_spin_emits_zero_based_page(qtbot):
    dialog=ComparisonDialog(3,0); qtbot.addWidget(dialog)
    with qtbot.waitSignal(dialog.page_requested) as signal:
        dialog.page_spin.setValue(3)
    assert signal.args==[2]
```

- [ ] **Step 2: Run dialog tests and verify RED**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_comparison_dialog.py -q`

Expected: collection fails because `comparison_dialog.py` does not exist.

- [ ] **Step 3: Implement the comparison dialog**

Create a 1320 x 760 `QDialog`. The top row contains `基準：第 N 頁`, a comparison `QSpinBox` from 1 to the other document page count, and `summary`. Below it, create three equal `QScrollArea` columns titled `目前頁面`, `比較頁面`, and `差異標示`, each containing a centered `QLabel` with scaled contents disabled. `set_result()` loads PNG bytes into `QPixmap`, scales each to a maximum width of 400 while keeping aspect ratio, updates the summary and re-enables the page spin. `set_busy(True)` disables the spin and changes the summary to `正在比較頁面…`.

- [ ] **Step 4: Run dialog tests**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_comparison_dialog.py -q`

Expected: all dialog tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/pdf_editor/ui/comparison_dialog.py tests/test_comparison_dialog.py
git commit -m "feat: 加入三欄頁面比較視窗"
```

### Task 3: 主視窗比較工作流程

**Files:**
- Modify: `src/pdf_editor/ui/main_window.py`
- Modify: `tests/test_ui.py`

**Interfaces:**
- Consumes: `compare_pages()`、`ComparisonDialog`、`unlock_pdf()`、`flatten_overlays()`
- Produces: `MainWindow.choose_comparison_pdf()`、`MainWindow.open_comparison(comparison_pdf: bytes)`、`MainWindow.request_comparison_page(page: int)`

- [ ] **Step 1: Write failing main-window tests**

```python
def test_window_exposes_page_comparison_for_open_document(qtbot,source_path):
    window=MainWindow(); qtbot.addWidget(window); window.open_document(source_path)
    qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
    assert window.actions["compare"].text()=="頁面比較"
    assert window.actions["compare"].isEnabled()

def test_comparison_does_not_change_session_revision(qtbot,source_path,pdf_bytes):
    window=MainWindow(); qtbot.addWidget(window); window.open_document(source_path)
    revision=window.session.revision
    window.open_comparison(pdf_bytes)
    qtbot.waitUntil(lambda:window.comparison_dialog is not None,timeout=30000)
    assert window.session.revision==revision
```

- [ ] **Step 2: Run main-window comparison tests and verify RED**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_ui.py -q -k comparison`

Expected: tests fail because the comparison action and methods do not exist.

- [ ] **Step 3: Add file selection, password handling and stale-result protection**

Add `("compare", "頁面比較", self.choose_comparison_pdf, None)` beside OCR in the document toolbar. Enable it whenever a document is open and no background job is active. `choose_comparison_pdf()` uses `QFileDialog`, reads bytes, and calls `unlock_pdf`; on `PASSWORD`, ask with `QInputDialog` and retry.

`open_comparison()` flattens current overlays into a base snapshot, records `session.revision` and `token`, creates the dialog with the other page count, connects `page_requested` to `request_comparison_page`, and requests page zero. Each request increments `comparison_serial`, disables the spin, and submits `compare_pages(base_snapshot, base_page, comparison_pdf, requested_page)` to `Jobs`. The callback updates only when token, revision, dialog identity and serial still match. Closing the dialog clears stored comparison bytes and references. Do not call any `session.apply_*` method.

- [ ] **Step 4: Run UI and full regression tests**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_ui.py tests/test_comparison_dialog.py -q -k "comparison or compare"`

Expected: comparison UI tests pass.

Run: `work\build-venv312\Scripts\python.exe -m pytest -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/pdf_editor/ui/main_window.py tests/test_ui.py
git commit -m "feat: 整合頁面比較操作"
```

### Task 4: 0.14.0 文件、實際 PDF 驗收與 Windows 發布

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/pdf_editor/__init__.py`
- Modify: `packaging/installer.iss`
- Modify: `README.md`
- Modify: `docs/使用說明.md`
- Modify: `docs/驗收紀錄.md`
- Create: `work/original-analysis/v140_acceptance.py` (ignored verification helper)

**Interfaces:**
- Consumes: finished OCR and page comparison features
- Produces: version 0.14.0 installer, documentation, acceptance record, preview, source archive and SHA-256 list

- [ ] **Step 1: Add actual-document acceptance script before release edits**

Create a one-page image-only scan from `115.01.02-2014-F.pdf`, run the packaged OCR engine, reopen the result, and require `find_text()` to find at least one known heading fragment. Create a second PDF with a visible black rectangle over a known blank area, call `compare_pages()`, require similarity below 100 and at least one red pixel in the changed area. Open the main window with the OCR result, capture a screenshot with the search result and comparison dialog visible, and assert the session revision is unchanged by comparison.

- [ ] **Step 2: Run acceptance and complete release documentation**

Run: `work\build-venv312\Scripts\python.exe work/original-analysis/v140_acceptance.py`

Expected: exit 0 with recognized text, nonzero changed pixels, a similarity below 100%, unchanged comparison revision and screenshot path.

Update all three version declarations to `0.14.0`. Add one-click OCR, searchable invisible text, automatic existing-text skip, comparison workflow, pixel threshold, limitations and third-party licenses to README and user guide. Record the fresh full-suite count and actual acceptance values in the acceptance record.

- [ ] **Step 3: Run complete verification and build installer**

Run: `work\build-venv312\Scripts\python.exe -m pytest -q`

Expected: all tests pass.

Run: `.\scripts\build.ps1 -InnoCompiler 'C:\Users\bihro\Documents\Codex\2026-09-11\new-chat\work\inno-setup\ISCC.exe' -PythonExecutable 'work\build-venv312\Scripts\python.exe'`

Expected: tests, PyInstaller and Inno Setup all exit 0.

- [ ] **Step 4: Install, smoke-test and publish artifacts**

Silently install `dist/installer/LocalPDFEditor-Setup.exe` into a verified directory under `work/v140-install`. Launch the installed executable with the image-only test PDF, invoke OCR through the test harness, verify searchable text, open comparison and capture a screenshot. Close all main and worker processes, silently uninstall, and require the install directory to disappear.

Copy the installer, user guide, acceptance record and preview into `C:\Users\bihro\Documents\Codex\2026-09-11\new-chat\outputs`; create `墨頁PDF-原始碼.zip` from `HEAD`; regenerate `墨頁PDF-SHA256.txt`; verify every listed hash and confirm the source archive contains `version = "0.14.0"`.

- [ ] **Step 5: Commit release documentation**

```powershell
git add pyproject.toml src/pdf_editor/__init__.py packaging/installer.iss README.md docs/使用說明.md docs/驗收紀錄.md
git commit -m "docs: 發布 OCR 與頁面比較功能 0.14.0"
```
