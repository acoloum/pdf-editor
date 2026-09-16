# 字型粗體與系統字型支援實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓文字編輯支援「粗體」（真實粗體檔優先、模擬粗體備援）與「系統字型下拉選單」（全部列舉 Windows 字型＋即時缺字警告）。

**Architecture:** `engine/fonts.py` 新增 `system_fonts()`（winreg 列舉登錄檔）與 `resolve_bold()`（家族名＋Bold 查詢、檔名備援）。模型 `TextInsertion`/`TextReplacement` 尾端加 `bold: bool = False`。`engine/text.py` 在 `bold=True` 時先解析最終字型檔，真粗體檔直接嵌入、無檔則以 `render_mode=2`＋`border_width` 模擬。`ui/text_panel.py` 以 QComboBox 列系統字型、QCheckBox 切換粗體、QLabel 顯示缺字警告。

**Tech Stack:** Python 3.12、PyMuPDF 1.28.2、PySide6 Essentials 6.8.3、pytest、pytest-qt、winreg（標準庫，僅 Windows）。

**Spec:** `docs/superpowers/specs/2026-09-15-font-bold-system-design.md`

## Global Constraints

- 介面、錯誤訊息、註解與提交訊息均使用繁體中文。
- TDD：每個任務依序為「寫失敗測試 → 確認失敗 → 最小實作 → 確認通過 → 提交」。
- Windows 限定測試以 `@pytest.mark.skipif(sys.platform != "win32", ...)` 保護；mock 登錄檔的測試以 `monkeypatch.setattr(fonts.sys, "platform", "win32")` 使平台無關。
- 不新增任何打包資源；安裝檔大小不得增加。
- 既有「選擇字型檔…」「使用內建中文字型」按鈕與缺字時自動改用 Noto 的行為全部保留。
- 所有測試需可在無顯示環境執行（pytest-qt offscreen）。

---

## 檔案結構

- 修改：`src/pdf_editor/engine/fonts.py` — 新增 `system_fonts()`、`resolve_bold()` 與輔助函式。
- 修改：`src/pdf_editor/model.py:17-38` — 兩資料類別尾端加 `bold: bool = False`。
- 修改：`src/pdf_editor/engine/text.py:6,65-87,108-116,163,172-183` — `_new_text_shape` 加 `fake_bold`；`_resolve_bold` 輔助；`insert_text`/`replace_text` 使用有效路徑。
- 修改：`src/pdf_editor/ui/text_panel.py` — 選單、粗體、缺字警告。
- 修改：`src/pdf_editor/ui/main_window.py:1236,1240,1265,1272,1285,1516` — 6 處請求建構加粗體旗標。
- 修改：`tests/test_fonts.py`、`tests/test_text.py`、`tests/test_ui.py`。
- 修改：`docs/使用說明.md`。

---

## Chunk 1: 系統字型列舉與粗體檔解析

### Task 1: `engine/fonts.py` 的 `system_fonts()` 與 `resolve_bold()`

**Files:**
- Modify: `src/pdf_editor/engine/fonts.py`
- Modify: `tests/test_fonts.py`

**Interfaces:**
- `system_fonts() -> list[tuple[str, str]]`：列舉 Windows 系統字型，回傳 `(顯示名稱, 絕對路徑)`，依名稱排序；非 Windows 或登錄檔失敗時回傳空清單。
- `resolve_bold(base_path: str) -> str | None`：回傳 `base_path` 的同族粗體檔；已是粗體回傳自身；找不到回 `None`。

- [ ] **Step 1: 寫入 `system_fonts()` 的失敗測試**（追加至 `tests/test_fonts.py`）

```python
import os
import sys
import pytest

@pytest.mark.skipif(sys.platform != "win32", reason="僅 Windows 有登錄檔字型索引")
def test_system_fonts_returns_existing_font_files():
    fonts_list = fonts.system_fonts()
    assert fonts_list
    for name, path in fonts_list:
        assert name.strip()
        assert path.lower().endswith((".ttf", ".otf", ".ttc"))
        assert os.path.exists(path)

def test_system_fonts_filters_fon_and_missing(monkeypatch):
    fake = [("點陣字 (TrueType)", "vgasys.fon"),
        ("系統字型 (TrueType)", "missing.ttf")]
    monkeypatch.setattr(fonts, "_font_registry_values", lambda: fake)
    monkeypatch.setattr(fonts.sys, "platform", "win32")
    assert fonts.system_fonts() == []

def test_system_fonts_resolves_relative_path(monkeypatch, tmp_path):
    font_file = tmp_path / "demo.ttf"
    font_file.write_bytes(b"fake")
    monkeypatch.setattr(fonts, "_font_registry_values",
        lambda: [("Demo Font (TrueType)", "demo.ttf")])
    monkeypatch.setattr(fonts, "_font_directories", lambda: [tmp_path])
    monkeypatch.setattr(fonts.sys, "platform", "win32")
    assert fonts.system_fonts() == [("Demo Font", str(font_file))]

def test_system_fonts_handles_registry_error(monkeypatch):
    def boom():
        raise OSError("拒絕存取")
    monkeypatch.setattr(fonts, "_font_registry_values", boom)
    monkeypatch.setattr(fonts.sys, "platform", "win32")
    assert fonts.system_fonts() == []
```

> 註：`tests/test_fonts.py` 目前只有 `import` 既有兩行（`from pdf_editor.engine import fonts`、`from pdf_editor.engine.text import extract_runs`）；依上述內容補 `import os`、`import sys`、`import pytest`。測試直接使用既有 `fonts` 別名，不新增 `fonts_engine`。

- [ ] **Step 2: 執行測試確認失敗**

Run: `pytest tests/test_fonts.py -v`
Expected: FAIL — `AttributeError: module 'pdf_editor.engine.fonts' has no attribute 'system_fonts'`。

- [ ] **Step 3: 實作 `system_fonts()` 與輔助函式**

於 `src/pdf_editor/engine/fonts.py` 頂部（`import sys` 區段）補 `import os`，然後於檔案尾端追加：

```python
def _font_registry_values():
    """回傳登錄檔字型鍵的（名稱, 檔名）序列；HKCU 優先並覆蓋同名。"""
    import winreg
    keys = (
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    )
    entries = {}
    for hive, subkey in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                index = 0
                while True:
                    try:
                        name, filename, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    entries[name] = filename
        except OSError:
            continue
    return list(entries.items())


def _font_directories():
    """回傳候選字型目錄（%WINDIR%\\Fonts 與 %LOCALAPPDATA% 使用者字型）。"""
    root = Path(os.environ.get("WINDIR", r"C:\Windows"))
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"
    return (root / "Fonts", local)


def _clean_font_name(name):
    """去除登錄檔名稱的括號註記，如「細明體 (TrueType)」→「細明體」。"""
    return name.split("(")[0].strip()


def system_fonts():
    """列舉 Windows 已安裝字型。回傳（顯示名稱, 絕對路徑），依名稱排序。"""
    if sys.platform != "win32":
        return []
    result = {}
    for raw_name, filename in _font_registry_values():
        name = _clean_font_name(raw_name)
        if not name:
            continue
        path = Path(filename) if os.path.isabs(filename) else next(
            (base / filename for base in _font_directories()
             if (base / filename).exists()), None)
        if path is None or path.suffix.lower() not in (".ttf", ".otf", ".ttc"):
            continue
        result[name] = str(path)
    return sorted(result.items())
```

- [ ] **Step 4: 執行測試確認通過**

Run: `pytest tests/test_fonts.py -v`
Expected: 全部 PASS（含既有 `test_embedded_font_is_rejected_when_unicode_cannot_roundtrip`）。

- [ ] **Step 5: 寫入 `resolve_bold()` 的失敗測試**（追加至 `tests/test_fonts.py`）

```python
def test_resolve_bold_finds_family_bold(monkeypatch):
    monkeypatch.setattr(fonts, "system_fonts", lambda: [
        ("Demo Sans", r"C:\Windows\Fonts\demo.ttf"),
        ("Demo Sans Bold", r"C:\Windows\Fonts\demobd.ttf")])
    assert fonts.resolve_bold(r"C:\Windows\Fonts\demo.ttf") == r"C:\Windows\Fonts\demobd.ttf"

def test_resolve_bold_already_bold_returns_base(monkeypatch):
    monkeypatch.setattr(fonts, "system_fonts",
        lambda: [("Demo Sans Bold", r"C:\Windows\Fonts\demobd.ttf")])
    assert fonts.resolve_bold(r"C:\Windows\Fonts\demobd.ttf") == r"C:\Windows\Fonts\demobd.ttf"

def test_resolve_bold_returns_none_without_bold(monkeypatch):
    monkeypatch.setattr(fonts, "system_fonts",
        lambda: [("Demo Serif", r"C:\Windows\Fonts\demoserif.ttf")])
    assert fonts.resolve_bold(r"C:\Windows\Fonts\demoserif.ttf") is None

def test_resolve_bold_none_when_bold_file_missing(monkeypatch):
    monkeypatch.setattr(fonts, "system_fonts", lambda: [
        ("Demo Sans", r"C:\Windows\Fonts\demo.ttf"),
        ("Demo Sans Bold", r"C:\Windows\Fonts\missingbold.ttf")])
    assert fonts.resolve_bold(r"C:\Windows\Fonts\demo.ttf") is None
```

> 註：`resolve_bold` 測試直接 monkeypatch `system_fonts` 回傳完整路徑，不依賴平台，不需 `skipif`。曾規劃「以暫存目錄偽裝粗體檔」的測試改以 mock 取代（避免在 CI 寫入 `C:\Windows\Fonts`）。

- [ ] **Step 6: 執行測試確認失敗**

Run: `pytest tests/test_fonts.py -k resolve_bold -v`
Expected: FAIL — `AttributeError: module 'pdf_editor.engine.fonts' has no attribute 'resolve_bold'`。

- [ ] **Step 7: 實作 `resolve_bold()`**

於 `src/pdf_editor/engine/fonts.py` 尾端追加：

```python
_STYLE_SUFFIXES = ("Bold Italic", "ExtraBold", "Bold", "Italic",
    "Semibold", "Light", "Medium", "Black")


def _base_family(name):
    """剝除名稱尾部的既定變體詞，取得家族名；由長到短避免殘留片語。"""
    lowered = name.lower()
    for suffix in _STYLE_SUFFIXES:
        if lowered.endswith(suffix.lower()):
            return name[:-len(suffix)].strip()
    return name


def resolve_bold(base_path):
    """尋找 base_path 的同族粗體檔；已是粗體回傳自身；找不到回傳 None。"""
    base = str(base_path)
    fonts_list = system_fonts()
    if any(path == base and "bold" in name.lower() for name, path in fonts_list):
        return base
    family = next((name for name, path in fonts_list if path == base), None)
    if family:
        index = {name.lower(): path for name, path in fonts_list}
        candidate = index.get(_base_family(family) + " bold")
        if candidate and os.path.exists(candidate):
            return candidate
    directory = Path(base).parent
    if not directory.is_dir():
        return None
    for candidate in directory.iterdir():
        if not candidate.is_file() or candidate == Path(base):
            continue
        if candidate.suffix.lower() not in (".ttf", ".otf", ".ttc"):
            continue
        stem = candidate.stem.lower()
        if "bold" in stem or stem.endswith(("bd", "b")):
            return str(candidate)
    return None
```

- [ ] **Step 8: 執行測試確認通過**

Run: `pytest tests/test_fonts.py -v`
Expected: 全部 PASS。

- [ ] **Step 9: 提交**

```bash
$env:GIT_MASTER='1'
git add src/pdf_editor/engine/fonts.py tests/test_fonts.py
git commit -m "功能：新增系統字型列舉與粗體檔解析"
```

## Chunk 2: 模型與渲染引擎的粗體支援

### Task 2: 模型新增 `bold` 欄位

**Files:**
- Modify: `src/pdf_editor/model.py:17-38`
- Modify: `tests/test_text.py`

- [ ] **Step 1: 寫入預設值向後相容的失敗測試**（追加至 `tests/test_text.py`）

```python
def test_text_request_bold_defaults_to_false(pdf_bytes, font_path):
    run = next(r for r in extract_runs(pdf_bytes, 0) if "品質" in r.text)
    insertion = editor_model.TextInsertion(
        hashlib.sha256(pdf_bytes).hexdigest(), 0, "新增",
        (40, 55, 390, 105), font_path, 16, (0, 0, 0))
    replacement = TextReplacement(
        hashlib.sha256(pdf_bytes).hexdigest(), 0, run.id, "取代",
        (40, 55, 390, 105), font_path, 16, (0, 0, 0))
    assert insertion.bold is False
    assert replacement.bold is False
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `pytest tests/test_text.py -k bold -v`
Expected: FAIL — `AttributeError: 'TextInsertion' object has no attribute 'bold'`。

- [ ] **Step 3: 實作模型欄位**

於 `src/pdf_editor/model.py` 的 `TextReplacement`（第 17-28 行）與 `TextInsertion`（第 29-38 行）兩資料類別尾端（`alignment: str = "left"` 之後）各加一行：

```python
    bold: bool = False
```

- [ ] **Step 4: 執行測試確認通過**

Run: `pytest tests/test_text.py -k bold -v`
Expected: PASS。

### Task 3: `engine/text.py` 模擬粗體與真粗體檔

**Files:**
- Modify: `src/pdf_editor/engine/text.py`
- Modify: `tests/test_text.py`

- [ ] **Step 1: 寫入粗體分支的失敗測試**（追加至 `tests/test_text.py`）

```python
def test_insert_bold_without_real_bold_file_uses_fake_bold(
        pdf_bytes, font_path, monkeypatch):
    monkeypatch.setattr("pdf_editor.engine.text.resolve_bold", lambda path: None)
    request = editor_model.TextInsertion(
        hashlib.sha256(pdf_bytes).hexdigest(), 0, "粗體測試",
        (40, 55, 390, 105), font_path, 16, (0, 0, 0), "left", True)
    out = text_engine.insert_text(pdf_bytes, request)
    with pymupdf.open(stream=out) as doc:
        assert "粗體測試" in doc[0].get_text()
        assert b"2 Tr" in doc[0].read_contents()
    run = next(r for r in extract_runs(out, 0) if "粗體測試" in r.text)
    assert run.size == 16


def test_insert_bold_uses_real_bold_file_when_available(
        pdf_bytes, font_path, monkeypatch):
    monkeypatch.setattr("pdf_editor.engine.text.resolve_bold",
        lambda path: font_path)
    request = editor_model.TextInsertion(
        hashlib.sha256(pdf_bytes).hexdigest(), 0, "真粗體",
        (40, 55, 390, 105), font_path, 16, (0, 0, 0), "left", True)
    out = text_engine.insert_text(pdf_bytes, request)
    with pymupdf.open(stream=out) as doc:
        assert "真粗體" in doc[0].get_text()
        assert b"2 Tr" not in doc[0].read_contents()


def test_replace_text_bold_branch_renders_render_mode_two(
        pdf_bytes, font_path, monkeypatch):
    monkeypatch.setattr("pdf_editor.engine.text.resolve_bold", lambda path: None)
    run = next(r for r in extract_runs(pdf_bytes, 0) if "品質" in r.text)
    request = TextReplacement(
        hashlib.sha256(pdf_bytes).hexdigest(), 0, run.id, "加粗取代",
        (40, 55, 390, 105), font_path, 16, (0, 0, 0), "left", True)
    out = text_engine.replace_text(pdf_bytes, request)
    with pymupdf.open(stream=out) as doc:
        assert "加粗取代" in doc[0].get_text()
        assert b"2 Tr" in doc[0].read_contents()


def test_insert_multi_line_bold_smoke(pdf_bytes, font_path, monkeypatch):
    monkeypatch.setattr("pdf_editor.engine.text.resolve_bold", lambda path: None)
    request = editor_model.TextInsertion(
        hashlib.sha256(pdf_bytes).hexdigest(), 0, "第一行\n第二行",
        (40, 55, 390, 200), font_path, 12, (0, 0, 0), "center", True)
    out = text_engine.insert_text(pdf_bytes, request)
    with pymupdf.open(stream=out) as doc:
        assert "第一行" in doc[0].get_text() and "第二行" in doc[0].get_text()
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `pytest tests/test_text.py -k bold -v`
Expected: FAIL — 粗體旗標未接上（`insert_text` 未讀 `request.bold`，內容流無 `2 Tr`）。

- [ ] **Step 3: 實作渲染引擎粗體支援**

於 `src/pdf_editor/engine/text.py`：

1. 第 6 行 import 改為（第 4 行已匯入 `TextRun, TextReplacement, TextInsertion`，與 `resolve_bold` 無衝突）：

```python
from pdf_editor.engine.fonts import checked_font, resolve_bold
```

2. 新增輔助函式 `_resolve_bold`（放在 `_font_resource_name` 之後、`_new_text_shape` 之前）：

```python
def _resolve_bold(request):
    """依請求的粗體旗標解析最終字型檔與是否模擬粗體。"""
    if not getattr(request, "bold", False):
        return request.font_path, False
    bold_path = resolve_bold(request.font_path)
    if bold_path:
        try:
            pymupdf.Font(fontfile=bold_path)
        except Exception:
            return request.font_path, True
        return bold_path, False
    return request.font_path, True
```

3. `_new_text_shape`（第 65-87 行）改為：

```python
def _new_text_shape(page, rect, text, font_path, font, size, color, alignment, fontname,
        fake_bold=False):
    """建立文字形狀；單行文字以基準線寫入，避免窄表格列被誤判為溢位。"""
    box = pymupdf.Rect(rect)
    page.insert_font(fontname=fontname, fontfile=font_path)
    shape = page.new_shape()
    stroke = dict(render_mode=2, fill=color, border_width=size * 0.04) if fake_bold else {}
    if "\n" not in text:
        width = font.text_length(text, fontsize=size)
        if width > box.width + 0.01 or size > box.height + 0.01:
            raise EditorError("TEXT_OVERFLOW", "文字超出範圍，請擴大文字框或減小字級。")
        x = box.x0 if alignment == "left" else box.x0 + (box.width - width) / 2
        if alignment == "center":
            baseline = box.y0 + (box.height - (font.ascender - font.descender) * size) / 2 \
                + font.ascender * size
        else:
            baseline = box.y0 + font.ascender * size
        shape.insert_text((x, baseline), text, fontname=fontname, fontsize=size,
            color=color, **stroke)
        return shape
    placement = _vertical_centered_rect(box, text, size) if alignment == "center" else box
    remaining = shape.insert_textbox(placement, text, fontname=fontname,
        fontsize=size, color=color, align=ALIGNMENTS[alignment], **stroke)
    if remaining < 0:
        raise EditorError("TEXT_OVERFLOW", "文字超出範圍，請擴大文字框或減小字級。")
    return shape
```

4. `insert_text`（第 108-116 行）改為：

```python
    font_path, fake_bold = _resolve_bold(request)
    font = checked_font(font_path, request.text)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[request.page]
        bounds = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
        if not bounds.contains(rect):
            raise EditorError("GEOMETRY", "文字框必須位於頁面內。")
        shape = _new_text_shape(page, rect, request.text, font_path, font,
            request.size, request.color, request.alignment,
            _font_resource_name("insertion", font_path, request.text), fake_bold)
        shape.commit()
        doc.subset_fonts(fallback=True)
        return doc.tobytes(garbage=4, deflate=True)
```

5. `replace_text`（第 163、172-175、182-183 行）改為：

```python
    font_path, fake_bold = _resolve_bold(request)
    font = checked_font(font_path, request.text) if request.text else None
```
（第 172-175 行）
```python
        if request.text:
            resource_name = _font_resource_name("replacement", font_path, request.text)
            _new_text_shape(page, rect, request.text, font_path, font,
                request.size, request.color, request.alignment, resource_name, fake_bold)
```
（第 182-183 行）
```python
        shape = _new_text_shape(page, rect, request.text, font_path, font,
            request.size, request.color, request.alignment, resource_name, fake_bold)
```

> 註：`replace_text` 內原變數為 `request.font_path`，一律改為局部 `font_path`（可能已解析為真粗體檔）；`resource_name` 需在兩處共用。

- [ ] **Step 4: 執行測試確認通過**

Run: `pytest tests/test_text.py -v`
Expected: 全部 PASS（含既有測試）。

- [ ] **Step 5: 提交**

```bash
$env:GIT_MASTER='1'
git add src/pdf_editor/model.py src/pdf_editor/engine/text.py tests/test_text.py
git commit -m "功能：文字插入與替換支援粗體（真粗體檔與模擬粗體）"
```

## Chunk 3: 側邊欄與主視窗 UI

### Task 4: `ui/text_panel.py` 字型選單、粗體與缺字警告

**Files:**
- Modify: `src/pdf_editor/ui/text_panel.py`
- Modify: `tests/test_ui.py`

- [ ] **Step 1: 寫入 UI 行為的失敗測試**（追加至 `tests/test_ui.py`；沿用既有 `TextPanel`、`qtbot`、`QSignalSpy` 匯入）

```python
def test_text_panel_font_combo_has_default_noto_first(qtbot):
    panel = TextPanel()
    qtbot.addWidget(panel)
    assert panel.font_combo.itemData(0) == str(default_font())
    assert panel.font_path == str(default_font())


def test_text_panel_bold_checkbox_emits_format(qtbot):
    panel = TextPanel()
    qtbot.addWidget(panel)
    spy = QSignalSpy(panel.format_requested)
    panel.bold.setChecked(True)
    assert spy.count() == 1


def test_text_panel_shows_glyph_warning_for_missing_chars(qtbot, monkeypatch):
    import types
    panel = TextPanel()
    qtbot.addWidget(panel)
    panel.text.setPlainText("缺字測試")
    monkeypatch.setattr("pdf_editor.ui.text_panel.pymupdf.Font",
        lambda **_: types.SimpleNamespace(has_glyph=lambda code: False))
    panel._refresh_glyph_warning()
    assert not panel.glyph_warning.isHidden()
    assert "缺少" in panel.glyph_warning.text()


def test_text_panel_hides_glyph_warning_when_complete(qtbot, monkeypatch):
    import types
    panel = TextPanel()
    qtbot.addWidget(panel)
    panel.text.setPlainText("完整文字")
    monkeypatch.setattr("pdf_editor.ui.text_panel.pymupdf.Font",
        lambda **_: types.SimpleNamespace(has_glyph=lambda code: True))
    panel._refresh_glyph_warning()
    assert panel.glyph_warning.isHidden()


def test_text_panel_shows_load_failure_warning(qtbot, monkeypatch):
    panel = TextPanel()
    qtbot.addWidget(panel)
    panel.text.setPlainText("測試")
    monkeypatch.setattr("pdf_editor.ui.text_panel.pymupdf.Font",
        lambda **_: (_ for _ in ()).throw(RuntimeError("無法開啟")))
    panel._refresh_glyph_warning()
    assert "無法讀取字型檔" in panel.glyph_warning.text()
```

> 註：`tests/test_ui.py` 頂部補充 `from pdf_editor.engine.fonts import default_font`。缺字警告以 monkeypatch `pymupdf.Font` 控制 `has_glyph` 結果，不依賴實際字型缺字樣本（內建 Noto 對繁體中文完整）。

- [ ] **Step 2: 執行測試確認失敗**

Run: `pytest tests/test_ui.py -k text_panel -v`
Expected: FAIL — `AttributeError: 'TextPanel' object has no attribute 'font_combo'`。

- [ ] **Step 3: 實作 `text_panel.py`**

1. import 區（第 1-4 行）改為：

```python
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel,
    QTextEdit, QDoubleSpinBox, QPushButton, QFileDialog, QColorDialog,
    QComboBox, QCheckBox)
from PySide6.QtGui import QColor
import pymupdf
from pdf_editor.engine.fonts import default_font, system_fonts
```

2. `__init__`（原第 47 行 `self.font_path=str(default_font())`）改為選單＋粗體＋警告列；`form` 加入選單與粗體，`glyph_warning` 加入 `layout`（在 `font_label` 之前）：

```python
        self._font_path = str(default_font())
        self.font_combo = QComboBox()
        self.font_combo.addItem("內建 Noto Sans CJK TC", self._font_path)
        for name, path in system_fonts():
            self.font_combo.addItem(name, path)
        self.font_combo.currentIndexChanged.connect(self._on_font_combo)
        form.addRow("字型", self.font_combo)
        self.bold = QCheckBox("粗體")
        self.bold.toggled.connect(self.mark_modified)
        form.addRow("文字樣式", self.bold)
        self.glyph_warning = QLabel()
        self.glyph_warning.setObjectName("hint")
        self.glyph_warning.setWordWrap(True)
        self.glyph_warning.hide()
        layout.addWidget(self.glyph_warning)
        self.font_label = QLabel("替代字型：Noto Sans CJK TC")
        self.font_label.setWordWrap(True)
        self.font_label.setMinimumHeight(60)
        layout.addWidget(self.font_label)
```

> 佈局注意：`form`（第 27-46 行）內含字級、對齊與框位；`font_combo` 與 `bold` 加入 `form`（在框位之後），`glyph_warning` 與 `font_label` 加入 `layout`。既有第 48-57 行的 `font_label`、`font_button`、`fallback` 維持原位不動（其後程式碼不需更動）。

3. 新增 `font_path` property 與 `_on_font_combo`、`_refresh_glyph_warning`（放在 `choose_font` 之前）：

```python
    @property
    def font_path(self):
        return self._font_path

    @font_path.setter
    def font_path(self, value):
        self._font_path = value
        if hasattr(self, "font_combo"):
            index = self.font_combo.findData(value)
            if index >= 0:
                previous = self._loading
                self._loading = True
                self.font_combo.setCurrentIndex(index)
                self._loading = previous

    def _on_font_combo(self, *_):
        if self._loading:
            return
        self._font_path = self.font_combo.currentData()
        if self._font_path:
            self.font_label.setText("選用字型：" + self.font_combo.currentText())
        self.mark_modified()
        self._refresh_glyph_warning()

    def _refresh_glyph_warning(self):
        text = self.text.toPlainText()
        if not text.strip() or not self._font_path:
            self.glyph_warning.hide()
            return
        try:
            font = pymupdf.Font(fontfile=self._font_path)
            missing = [c for c in text
                if not c.isspace() and not font.has_glyph(ord(c))]
        except Exception:
            self.glyph_warning.setText("無法讀取字型檔")
            self.glyph_warning.show()
            return
        if missing:
            shown = "、".join(missing[:10]) + ("…" if len(missing) > 10 else "")
            self.glyph_warning.setText("此字型缺少部分字元：" + shown)
            self.glyph_warning.show()
        else:
            self.glyph_warning.hide()
```

4. `text.textChanged` 連到缺字檢查：於 `self.text.hide()`（第 26 行）之後加入（避開初始化早期的無意義觸發）：

```python
        self.text.textChanged.connect(self._refresh_glyph_warning)
```

5. `choose_font`（第 83-88 行）改為：

```python
    def choose_font(self):
        path, _ = QFileDialog.getOpenFileName(self, "選擇字型", "", "字型 (*.ttf *.otf)")
        if not path:
            return
        index = self.font_combo.findData(path)
        if index < 0:
            self.font_combo.addItem("自訂字型檔…", path)
            index = self.font_combo.count() - 1
        self.font_combo.setCurrentIndex(index)
        self._font_path = path
        self.font_label.setText("選用字型：" + path.split("/")[-1])
        self.mark_modified()
        self._refresh_glyph_warning()
```

> 註：`setCurrentIndex` 會觸發 `_on_font_combo`（`_loading` 為 False）再設一次 `_font_path` 與 `_refresh_glyph_warning`；此處直接設定無害（同步）。

6. `use_default_font`（第 90-93 行）改為：

```python
    def use_default_font(self):
        self.font_path = str(default_font())
        self.font_label.setText("替代字型：Noto Sans CJK TC（完整繁中文字元）")
        self.glyph_warning.hide()
        self.mark_modified()
```

7. `set_run`（第 117 行）與 `set_insertion`（第 129 行）在 `self._loading=False` 之前各加一行（進入新編輯目標時重置粗體；`_loading=True` 期間 `setChecked` 觸發的 `mark_modified` 會被擋下，不會誤標 `modified`）：

```python
        self.bold.setChecked(False)
```

- [ ] **Step 4: 執行測試確認通過**

Run: `pytest tests/test_ui.py -k text_panel -v`
Expected: 全部 PASS（含既有 `test_text_panel_has_alignment_choices`、`test_text_panel_tracks_user_format_changes`）。

### Task 5: `ui/main_window.py` 6 處建構點傳入粗體旗標

**Files:**
- Modify: `src/pdf_editor/ui/main_window.py:1236,1240,1265,1272,1285,1516`
- Modify: `tests/test_ui.py`

- [ ] **Step 1: 寫入整合驗證的失敗測試**（追加至 `tests/test_ui.py`）

```python
def test_window_inline_edit_applies_bold_when_checked(qtbot, source_path, monkeypatch):
    monkeypatch.setattr("pdf_editor.engine.text.resolve_bold", lambda path: None)
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.show()
        window.open_document(source_path)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        run = next(item for item in window.page_data["runs"] if "品質" in item.text)
        window.select_run(run)
        window.text_panel.bold.setChecked(True)
        window.canvas.inline_editor.setText("粗體變更")
        qtbot.keyClick(window.canvas.inline_editor, Qt.Key.Key_Return)
        qtbot.waitUntil(lambda: not window.busy and window.session.dirty, timeout=30000)
        with pymupdf.open(stream=window.session.pdf) as doc:
            assert "粗體變更" in doc[0].get_text()
            assert b"2 Tr" in doc[0].read_contents()
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()
```

> 註：此測試驗證 `commit_inline_text` 的 `TextReplacement` 建構點（第 1240 行）確實傳遞 `p.bold.isChecked()`。monkeypatch `resolve_bold` 固定回 `None` 以確保走模擬粗體（`2 Tr`）。既有 `test_window_delete_selected_text_is_undoable`（`delete_run`）與 `test_original_font_available_for_selection`（`select_run` 後面板狀態）繼續作為 `delete_run`／`set_run` 整合煙霧。
>
> **2026-09-15 使用者核准的偏離（B1）**：因 `bold.toggled → mark_modified → format_requested → apply_text_format`（既有點選樣式立即套用行為）會在勾選當下即提交並銷毀 inline_editor，測試順序改為「先 `inline_editor.setText("粗體測試")`、再 `text_panel.bold.setChecked(True)`」，並移除 `keyClick(keyReturn)`；斷言與測試意圖不變。
>
> **2026-09-15 使用者核准的偏離（B2）**：測試文字由「粗體變更」改為「粗體測試」。原因：PyMuPDF 1.28.2 的 `subset_fonts(fallback=True)` 會將「更」(U+66F4) 對映為私用區 U+F901，導致 `get_text()` 文字提取斷言永遠失敗（已實證為上游缺陷）；「粗體測試」四字經實證可正常 roundtrip。

- [ ] **Step 2: 執行測試確認失敗**

Run: `pytest tests/test_ui.py -k bold -v`
Expected: FAIL — 內容流無 `2 Tr`（建構點尚未傳粗體旗標）。

- [ ] **Step 3: 修改 6 處建構點**

於 `src/pdf_editor/ui/main_window.py`（已逐一核對原始碼，`p=self.text_panel`）：

1. 第 1236-1237 行（`commit_inline_text` 新增文字）：

```python
            request=TextInsertion(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
                text,target_rect,font_path,p.size.value(),p.color,p.alignment.currentData(),
                p.bold.isChecked())
```

2. 第 1240-1241 行（`commit_inline_text` 替換）：

```python
        request=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
            run.id,text,target_rect,font_path,p.size.value(),p.color,p.alignment.currentData(),
            p.bold.isChecked())
```

3. 第 1265-1267 行（`preview_from_panel` 新增）：

```python
            req=TextInsertion(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
                p.text.toPlainText(),p.rect(),p.font_path,p.size.value(),p.color,
                p.alignment.currentData(), p.bold.isChecked())
```

4. 第 1272-1274 行（`preview_from_panel` 替換）：

```python
        req=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),
            self.page,self.run.id,p.text.toPlainText(),p.rect(),p.font_path,p.size.value(),
            p.color, p.alignment.currentData(), p.bold.isChecked())
```

5. 第 1285-1287 行（`move_run`）：

```python
        request=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
            run.id,p.text.toPlainText(),p.rect(),p.font_path,p.size.value(),p.color,
            p.alignment.currentData(), p.bold.isChecked())
```

6. 第 1516-1517 行（`delete_run`，粗體固定 False）：

```python
        request=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),
            self.page,run.id,"",run.rect,p.font_path,run.size,run.color,"left",False)
```

- [ ] **Step 4: 確認全部測試通過**

Run: `pytest -q`
Expected: 全數 PASS（294＋新增，約 305）。

- [ ] **Step 5: 提交**

```bash
$env:GIT_MASTER='1'
git add src/pdf_editor/ui/text_panel.py src/pdf_editor/ui/main_window.py tests/test_ui.py
git commit -m "介面：文字側邊欄加入系統字型選單、粗體與缺字警告"
```

## Chunk 4: 文件與驗收

### Task 6: 更新使用說明

**Files:**
- Modify: `docs/使用說明.md`

- [ ] **Step 1: 更新字型操作段落**

在「文字編輯」章節中，找到現有「替代字型」相關說明並替換為：

> 字型：側邊欄可從下拉選單選擇內建 Noto Sans CJK TC 或系統已安裝字型；亦可按「選擇字型檔…」挑選 .ttf/.otf 字型檔，或按「使用內建中文字型」回到內建字型。
> 粗體：勾選「粗體」後，套用之文字優先使用該字型的真實粗體（如微軟正黑體 Bold）；沒有真實粗體檔的字型（如細明體）以 PDF 模擬粗體呈現。
> 缺字警告：字型缺少目前文字的部分字元時，側邊欄會即時顯示「此字型缺少部分字元：…」；您仍可套用，程式會自動改用內建中文字型避免缺字。

- [ ] **Step 2: 執行測試確認無回歸**

Run: `pytest -q`
Expected: 全數 PASS。

- [ ] **Step 3: 提交**

```bash
$env:GIT_MASTER='1'
git add docs/使用說明.md
git commit -m "文件：說明系統字型選單、粗體與缺字警告"
```

### Task 7: 端對端手動驗收

**Files:** 無（僅執行與檢查）

- [ ] **Step 1: 啟動程式執行驗收**

Run: `$env:PYTHONPATH='src'; work\build-venv312\Scripts\python.exe -m pdf_editor`

驗收項目：
1. 開啟測試 PDF，點選文字進入編輯；字型選單可見「內建 Noto Sans CJK TC」與大量系統字型。
2. 選「微軟正黑體」並勾選「粗體」，輸入文字套用 → 輸出為真實粗體（內容流無 `2 Tr`）。
3. 選「細明體」並勾選「粗體」，套用 → 模擬粗體生效（PDF 閱讀器顯示加粗）。
4. 輸入含字型缺字的字元時，警告列出現「此字型缺少部分字元：…」。
5. 移動已編輯文字後粗體樣式保留；刪除文字無異常。
6. 另存 PDF 後以閱讀器（如 Edge/PDF Viewer）開啟確認粗體外觀。
7. 非 Windows 環境僅顯示內建 Noto（若可測試）。

- [ ] **Step 2: 提交任何必要的修正**

若驗收發現問題，依「修正：」前綴提交修正並重新執行 `pytest -q`。