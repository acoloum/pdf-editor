# 墨頁 PDF OCR 文字辨識實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將左側選取的掃描頁面轉成保留原貌、可搜尋、可反白及可復原的繁中英文 OCR PDF。

**Architecture:** `ocr.py` 負責解析 Tesseract TSV、座標換算及交易式寫回 PDF；`ocr_assets.py` 只負責定位封裝模型。主視窗先固定未另存圖層，再把 OCR 工作送至既有程序池，成功後一次提交 `DocumentSession` 歷史。

**Tech Stack:** Python 3.12、PyMuPDF 1.28.2、Pillow 12.3.0、tesserocr 2.10.0、Tesseract 5.5.2、tessdata_fast。

**Spec:** `docs/superpowers/specs/2026-09-12-ocr-page-comparison-design.md`

## Global Constraints

- Windows 11 x64，完全離線執行，不需帳號或付費服務。
- 預設辨識語言固定為 `chi_tra+eng`，渲染解析度固定為 300 DPI，最低信心值為 35。
- 已有至少十個非空白可擷取字元的頁面直接跳過。
- 多頁辨識全部完成後才提交；錯誤或無結果時不留下部分變更。
- 原始掃描畫面、頁面尺寸及來源檔保持不變，OCR 文字使用 PDF 不可見渲染模式。
- 所有使用者介面、錯誤訊息、程式註解及 commit 訊息使用繁體中文。

---

### Task 1: OCR 資料模型與 TSV 解析

**Files:**
- Create: `src/pdf_editor/ocr.py`
- Create: `tests/test_ocr.py`

**Interfaces:**
- Produces: `OcrWord(text: str, confidence: float, pixel_rect: tuple[int,int,int,int])`
- Produces: `OcrResult(pdf: bytes, processed_pages: tuple[int,...], skipped_pages: tuple[int,...], word_count: int)`
- Produces: `parse_tsv(tsv: str, minimum_confidence: float = 35.0) -> tuple[OcrWord,...]`

- [ ] **Step 1: Write the failing TSV parser tests**

```python
def test_parse_tsv_keeps_words_at_or_above_minimum_confidence():
    tsv=("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
         "5\t1\t1\t1\t1\t1\t40\t60\t80\t20\t92.4\t材料\n"
         "5\t1\t1\t1\t1\t2\t130\t60\t50\t20\t34.9\t雜點\n")
    words=parse_tsv(tsv)
    assert words==(OcrWord("材料",92.4,(40,60,120,80)),)

def test_parse_tsv_rejects_missing_columns():
    with pytest.raises(EditorError) as error:
        parse_tsv("text\n材料\n")
    assert error.value.code=="OCR_OUTPUT"
```

- [ ] **Step 2: Run the parser tests and verify RED**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_ocr.py -q`

Expected: collection fails because `pdf_editor.ocr` does not exist.

- [ ] **Step 3: Implement the parser and immutable result types**

Use `csv.DictReader(io.StringIO(tsv), delimiter="\t")`. Require `left`, `top`, `width`, `height`, `conf`, and `text`; trim text; reject non-finite confidence and non-positive boxes; keep confidence `>= minimum_confidence`. Raise `EditorError("OCR_OUTPUT", "OCR 引擎回傳的資料格式無效。")` for malformed output.

- [ ] **Step 4: Run parser tests and verify GREEN**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_ocr.py -q`

Expected: parser tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/pdf_editor/ocr.py tests/test_ocr.py
git commit -m "feat: 加入 OCR 結果解析"
```

### Task 2: 交易式 OCR 文字層寫入

**Files:**
- Modify: `src/pdf_editor/ocr.py`
- Modify: `tests/test_ocr.py`

**Interfaces:**
- Consumes: `parse_tsv()`、`OcrWord`、`OcrResult`
- Produces: `recognize_image(image: PIL.Image.Image, tessdata: Path, language: str = "chi_tra+eng") -> str`
- Produces: `ocr_pages(pdf: bytes, pages: tuple[int,...], tessdata: Path, dpi: int = 300, recognizer=recognize_image) -> OcrResult`

- [ ] **Step 1: Write failing PDF behavior tests**

```python
def test_ocr_pages_adds_searchable_invisible_text_without_changing_page_pixels(scanned_pdf,tmp_path):
    def recognizer(image,tessdata,language):
        return make_tsv(("材料測試報告",90,(100,120,420,170)))
    before=render_png(scanned_pdf)
    result=ocr_pages(scanned_pdf,(0,),tmp_path,recognizer=recognizer)
    assert "材料測試報告" in pymupdf.open(stream=result.pdf)[0].get_text()
    assert render_png(result.pdf)==before
    assert result.processed_pages==(0,)
    assert result.word_count==1

def test_ocr_pages_skips_page_that_already_has_text(pdf_bytes,tmp_path):
    result=ocr_pages(pdf_bytes,(0,),tmp_path,recognizer=lambda *args: pytest.fail())
    assert result.processed_pages==()
    assert result.skipped_pages==(0,)
    assert result.pdf==pdf_bytes

def test_ocr_pages_does_not_return_partial_pdf_when_later_page_has_no_result(two_scan_pdf,tmp_path):
    calls=iter((make_tsv(("第一頁",90,(10,10,100,40))), make_tsv()))
    with pytest.raises(EditorError) as error:
        ocr_pages(two_scan_pdf,(0,1),tmp_path,recognizer=lambda *args: next(calls))
    assert error.value.code=="OCR_EMPTY"
```

- [ ] **Step 2: Run PDF behavior tests and verify RED**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_ocr.py -q`

Expected: tests fail because `ocr_pages` and `recognize_image` are missing.

- [ ] **Step 3: Implement OCR rendering and invisible text insertion**

Open a copy with `pymupdf.open(stream=pdf, filetype="pdf")`. Validate unique zero-based pages. For each eligible page, render RGB at `dpi / 72`, call the recognizer, and convert each TSV box from rotated pixel coordinates to PDF points with `page.derotation_matrix`. Insert Noto Sans CJK TC once per page and use `page.insert_text(..., render_mode=3, overlay=True)`; derive font size from box height and shrink it when `pymupdf.Font.text_length()` exceeds box width. Return `doc.tobytes(garbage=4, deflate=True)` only after every selected page succeeds.

`recognize_image()` lazily imports `tesserocr`, opens `PyTessBaseAPI(path=str(tessdata), lang=language)`, sets the PIL image, calls `Recognize()`, and returns `GetTSVText(0)`. Convert import, initialization and recognition failures to `EditorError("OCR_ENGINE", "OCR 引擎無法啟動，請重新安裝墨頁 PDF。")`.

- [ ] **Step 4: Run OCR core tests and full regression**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_ocr.py -q`

Expected: all OCR tests pass.

Run: `work\build-venv312\Scripts\python.exe -m pytest -q`

Expected: existing 146 tests plus new OCR tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/pdf_editor/ocr.py tests/test_ocr.py
git commit -m "feat: 寫入可搜尋 OCR 文字層"
```

### Task 3: OCR 執行環境與授權封裝

**Files:**
- Create: `src/pdf_editor/ocr_assets.py`
- Create: `packaging/requirements-ocr-win.txt`
- Create: `resources/tesseract/tessdata/chi_tra.traineddata`
- Create: `resources/tesseract/tessdata/eng.traineddata`
- Create: `licenses/Apache-2.0.txt`
- Modify: `packaging/pdf_editor.spec`
- Modify: `scripts/build.ps1`
- Modify: `scripts/collect_licenses.py`
- Modify: `licenses/manifest.json`
- Create: `tests/test_ocr_assets.py`

**Interfaces:**
- Produces: `ocr_tessdata_path() -> Path`
- Produces: `validate_ocr_assets() -> Path`

- [ ] **Step 1: Write failing asset validation tests**

```python
def test_validate_ocr_assets_requires_both_languages(monkeypatch,tmp_path):
    monkeypatch.setattr("pdf_editor.ocr_assets.resource_root",lambda:tmp_path)
    with pytest.raises(EditorError) as error:
        validate_ocr_assets()
    assert error.value.code=="OCR_ASSETS"
```

- [ ] **Step 2: Run asset test and verify RED**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_ocr_assets.py -q`

Expected: collection fails because `pdf_editor.ocr_assets` does not exist.

- [ ] **Step 3: Add pinned runtime and language models**

`packaging/requirements-ocr-win.txt` contains:

```text
cysignals==1.12.6
tesserocr @ https://github.com/simonflueckiger/tesserocr-windows_build/releases/download/tesserocr-v2.10.0-tesseract-5.5.2/tesserocr-2.10.0-cp312-cp312-win_amd64.whl#sha256=e05d41a2b0e6f38f3a5195d05a73674d72152a775d1b8ebe481ca9306f94d27a
```

Download language files from tessdata_fast commit `87416418657359cb625c412a48b6e1d6d41c29bd`. Verify `chi_tra.traineddata` SHA-256 `529c5b5797d64b126065cd55f2bb4c7fd7b15790798091b1ff259941a829330b` and `eng.traineddata` SHA-256 `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2` before placing them under `resources/tesseract/tessdata`.

Implement `validate_ocr_assets()` by requiring both non-empty model files and returning the tessdata directory. Extend the PyInstaller hidden imports with `tesserocr` and `cysignals`; the existing `resources` data rule includes both models. Make `build.ps1` install `packaging/requirements-ocr-win.txt` before tests. Add tesserocr, cysignals, Tesseract, Leptonica and tessdata_fast entries and their MIT/Apache notices to the license manifest.

- [ ] **Step 4: Verify assets and frozen import**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_ocr_assets.py tests/test_ocr.py -q`

Expected: all OCR asset and core tests pass.

Run: `work\build-venv312\Scripts\python.exe -c "import tesserocr; print(tesserocr.tesseract_version())"`

Expected: output begins with `tesseract 5.5.2`.

- [ ] **Step 5: Commit**

```powershell
git add src/pdf_editor/ocr_assets.py packaging/requirements-ocr-win.txt packaging/pdf_editor.spec scripts/build.ps1 scripts/collect_licenses.py resources/tesseract licenses tests/test_ocr_assets.py
git commit -m "build: 封裝離線繁中英文 OCR 引擎"
```

### Task 4: 主視窗 OCR 操作與復原整合

**Files:**
- Modify: `src/pdf_editor/ui/main_window.py`
- Modify: `tests/test_ui.py`

**Interfaces:**
- Consumes: `ocr_pages()`、`validate_ocr_assets()`、`OcrResult`
- Produces: `MainWindow.run_ocr()` and `MainWindow.apply_ocr_to_pages(pages: tuple[int,...])`

- [ ] **Step 1: Write failing UI tests**

```python
def test_window_exposes_ocr_action_for_selected_editable_pages(qtbot,source_path):
    window=MainWindow(); qtbot.addWidget(window)
    window.open_document(source_path)
    qtbot.waitUntil(lambda:window.page_data is not None,timeout=30000)
    assert window.actions["ocr"].text()=="OCR 文字辨識"
    assert window.actions["ocr"].isEnabled()

def test_window_commits_ocr_as_one_undoable_change(qtbot,scan_path,monkeypatch):
    monkeypatch.setattr("pdf_editor.ui.main_window.ocr_pages",fake_ocr_pages)
    window=MainWindow(); qtbot.addWidget(window); window.open_document(scan_path)
    before=window.session.pdf
    window.apply_ocr_to_pages((0,))
    qtbot.waitUntil(lambda:not window.busy,timeout=30000)
    assert window.session.pdf!=before
    window.history_step(False)
    assert window.session.pdf==before
```

- [ ] **Step 2: Run UI tests and verify RED**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_ui.py -q -k ocr`

Expected: tests fail because the OCR action and methods do not exist.

- [ ] **Step 3: Add one-click OCR action and background workflow**

Add `("ocr", "OCR 文字辨識", self.run_ocr, None)` to the document toolbar. Enable it only when the document is editable, not busy, and at least one thumbnail is selected. `run_ocr()` calls `apply_ocr_to_pages(self.selected_page_indices())`. The latter flattens overlays, validates assets, sets the status text to `正在辨識 N 頁…`, and submits `ocr_pages` to `Jobs`.

On success with processed pages, call `session.apply_state(result.pdf, ())`, clear stale search state, rerender the current page and thumbnails, and show `OCR 完成：辨識 X 頁，跳過 Y 頁，共 Z 個文字區段。`. If all pages were skipped, do not alter history and show the skipped count.

- [ ] **Step 4: Run UI and full regression tests**

Run: `work\build-venv312\Scripts\python.exe -m pytest tests/test_ui.py -q -k "ocr or search"`

Expected: OCR and existing search tests pass.

Run: `work\build-venv312\Scripts\python.exe -m pytest -q`

Expected: all tests pass with no failures.

- [ ] **Step 5: Commit**

```powershell
git add src/pdf_editor/ui/main_window.py tests/test_ui.py
git commit -m "feat: 加入選取頁面 OCR 操作"
```
