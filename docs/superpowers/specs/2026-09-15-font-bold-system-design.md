# 墨頁 PDF 字型粗體與系統字型支援設計

日期：2026-09-15
目標版本：下一版（0.14.4）

## 目標

讓文字編輯功能具備兩種能力：

1. **粗體**：側邊欄新增「粗體」開關。套用時優先使用同字型的真實粗體檔（如 `msjhbd.ttc`、`arialbd.ttf`）；沒有真實粗體檔的字型（如細明體）則以 PDF 模擬粗體技術（填色＋筆畫）呈現。不新增任何打包資源，安裝檔大小維持不變。
2. **系統字型選單**：字型選擇由現行的「選擇字型檔」按鈕改為下拉選單，列舉 Windows 已安裝字型（含使用者安裝字型）。全部列出並以即時缺字警告提示不支援的字元，不靜態篩選選單內容。

## 使用流程

### 字型選單

文字編輯側邊欄的「替代字型」區塊改為下拉選單：

- 第一項固定為「內建 Noto Sans CJK TC」（值＝內建字型檔路徑，維持為預選項）。
- 其後列出系統已安裝字型（顯示名稱），每項的值為對應字型檔路徑。
- 既有「選擇字型檔…」按鈕保留（可選任意 .ttf/.otf 檔）；選到任意檔後選單切至「自訂字型檔…」顯示。
- 「使用內建中文字型」按鈕保留，行為等同切回選單第一項。

選單內容在啟動時建立一次並快取；讀取登錄檔或開啟字型檔失敗時，僅略過該字型，不影響其他選項。系統字型選項以 Windows 平台限定（`sys.platform == "win32"`），非 Windows 環境只顯示內建 Noto。

### 粗體開關

「字型」列下方新增「粗體」核取方塊。勾選後，預覽與套用皆以粗體呈現；取消勾選即恢復一般字重。

### 缺字警告

使用者輸入文字或變更字型時，側邊欄即時檢查該字型能否顯示目前文字的每個字元（空白字元除外）：

- 有缺字時顯示警告列：「此字型缺少部分字元：X、Y…」（以**一字**為一項，最多列出 10 個字元，超出以「…」表示）。
- 字型檔無法開啟（`pymupdf.Font(fontfile=path)` 拋例外）時，警告列統一顯示「無法讀取字型檔」，與缺字警告共用同一列。
- 目前文字為空時不顯示警告。
- 警告**不阻擋**預覽與套用：套用時仍沿用現有機制——`insert_text`／`replace_text` 的 glyph 檢查會拋出 `FONT_MISSING_GLYPH`，現行 `commit_inline_text` 與 `preview_from_panel` 的處理為自動改用內建 Noto；此行為保留，但缺字警告列可讓使用者在套用前先得知。

## 系統字型列舉與粗體檔解析

下列兩個函式均實作於 `engine/fonts.py`（與既有 `default_font`、`checked_font` 同檔，可共用 `sys`──`win32` 平台判定與 `EditorError` 匯入）。

### 列舉系統字型（`system_fonts()`）

以 `winreg` 讀取兩處登錄檔字型鍵，順序為 HKCU 優先（使用者自裝字型覆蓋同名系統字型），再補 HKLM：

- `HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts`
- `HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts`

每個登錄值為「名稱 → 檔名」。處理規則：

- 名稱去除後綴 `(TrueType)`、`(OpenType)`、`(TrueType Outline)` 等括號註記後作為顯示名稱。
- 檔名為相對路徑時，依序嘗試 `%WINDIR%\Fonts` 與 `%LOCALAPPDATA%\Microsoft\Windows\Fonts` 解析為絕對路徑。
- 過濾：副檔名非 `.ttf`／`.otf`／`.ttc` 者（如 `.fon` 點陣字型）排除；檔案不存在者排除。
- 回傳 `(名稱, 絕對路徑)` 清單，依名稱排序、去除重複同名項（同一檔重複註冊時併為一項）。
- 登錄檔無法開啟時回傳空清單（不拋例外）。

此清單即為選單內容來源；名稱與路徑對應一次取得，不需再以 `QFontDatabase` 反查。

### 粗體檔解析（`resolve_bold(base_path)`）

以「顯示名稱 ＋ Bold」規則從系統字型索引中找到同族粗體檔：

1. 由 `system_fonts()` 建立「名稱（小寫）→ 路徑」索引。
2. **已是粗體的情況**：回查 `base_path` 對應的登錄名稱，若名稱含「bold」字眼（如「Arial Bold (TrueType)」），代表使用者已直接選中粗體檔 → 直接回傳 `base_path`（不需再找粗體檔，也不做模擬粗體）。
3. **家族名提取**：否則以「style 後綴剝除」取得家族名——移除名稱尾部的既定變體詞（不分大小寫）：`Bold Italic`、`Bold`、`Italic`、`Light`、`Semibold`、`Medium`、`Black` 等，剩餘部分即家族名（如「Microsoft JhengHei Bold」→「Microsoft JhengHei」）。若剝除後為空，直接以原名稱查詢。剝除順序由長到短（先 `Bold Italic` 後 `Bold`、先 `ExtraBold` 後 `Bold`），避免殘留片語。
4. 以 `"<家族名> bold"` 查索引，命中且檔案存在即回傳該粗體檔路徑。
5. 若家族名查詢失敗，以檔名規則備援：在 `base_path` 同目錄尋找檔名（不含副檔名、轉小寫）符合 **`含 "bold"`** 或 **`以 "bd" 或 "b" 結尾`**（如 `arialbd`、`msjhbd`、`segoeuib`）的 `.ttf`／`.otf`／`.ttc` 檔案，命中回傳；避免使用寬鬆的 `*-b*` 泛匹配（可能誤配 `arial-black` 等非粗體）。
6. 兩階段皆無 → 回傳 `None`（表示走模擬粗體）。

`resolve_bold` 的輸入輸出只在「渲染引擎決定用哪個檔」時使用，不寫入模型。

## 資料模型

`model.py`：

- `TextInsertion` 尾端新增欄位 `bold: bool = False`。
- `TextReplacement` 尾端新增欄位 `bold: bool = False`。

欄位放在建構子最後，既有位置參數呼叫不需修改即可相容（現有呼叫皆為位置參數，見「主視窗串接」）。

## 渲染引擎

### 粗體開關流（`engine/text.py`）

`insert_text` 與 `replace_text` 讀取 `request.bold`：

1. 若 `bold` 為真，呼叫 `resolve_bold(request.font_path)`：
   - 命中 → `effective_path = 粗體檔`，`fake_bold = False`。
   - 未命中 → `effective_path = request.font_path`，`fake_bold = True`。
2. `checked_font(effective_path, text)` 維持既有 glyph 驗證（真粗體檔缺字同樣會被擋下並走現有 fallback）。
3. 傳入 `_new_text_shape` 的最終參數：
   - `font_path = effective_path`（用於 `page.insert_font` 與資源名稱，粗體檔與一般檔路徑不同即自然分離資源）。
   - `fake_bold` 標記僅影響繪製參數。

### 繪製（`_new_text_shape`）

新增 `fake_bold` 參數，設為真時：

- 單行路徑：`shape.insert_text(..., render_mode=2, fill=color, border_width=size*0.04)`。
- 多行路徑：`shape.insert_textbox(..., render_mode=2, fill=color, border_width=size*0.04)`。
- 未勾選粗體（或命中真粗體檔）時維持現行 `render_mode` 預設（0＝僅填色），不傳入 `fill`／`border_width`。

`color` 與 `fill` 必須相同，避免模擬粗體邊框與填充顏色不一致。溢位檢查以 `font.text_length()` 為準（模擬粗體額外寬度約 `border_width` 半量，遠小於檢查容差，不需調整）。

### 資源名稱

`_font_resource_name` 維持以 `font_path`＋`text` 為輸入。真粗體檔因路徑不同自動產生不同資源名；模擬粗體與一般字重共用同一嵌入字型檔（同一資源、同一子集），僅繪製指令不同，子集化安全。

## 主視窗串接（`ui/main_window.py`）

共有 6 處建立 `TextInsertion`／`TextReplacement` 的位置（已逐一核對原始碼），全部在尾端補上粗體旗標：

| # | 函式 | 行號 | 請求 | 粗體旗標 |
|---|------|------|------|----------|
| 1 | `commit_inline_text` | 1236 | `TextInsertion` | `p.bold.isChecked()` |
| 2 | `commit_inline_text` | 1240 | `TextReplacement` | `p.bold.isChecked()` |
| 3 | `preview_from_panel` | 1265 | `TextInsertion` | `p.bold.isChecked()` |
| 4 | `preview_from_panel` | 1272 | `TextReplacement` | `p.bold.isChecked()` |
| 5 | `move_run` | 1285 | `TextReplacement` | `p.bold.isChecked()`（套用目前面板設定，移動時保留格式） |
| 6 | `delete_run` | 1516 | `TextReplacement`（空字串） | 固定 `False`（刪除不涉及字重，避免依賴面板狀態） |

`apply_preview` 僅套用已預覽的 PDF，不建構請求，不需修改。

`set_run`／`set_insertion` 流程不變：粗體預設不勾選，不試圖解析既有文字的字重（與現行「改字型一律用當前選字型」語意一致）。

## 側邊欄（`ui/text_panel.py`）

- 字型選單：以 `QComboBox` 取代現行「font_label ＋ 選擇字型檔按鈕＋內建按鈕」區塊的一部份（三項皆保留，見使用流程）。選單項的值為字型檔絕對路徑字串。
- 粗體：`QCheckBox("粗體")`，`clicked`／`toggled` 連到 `mark_modified`。
- 缺字警告列：`QLabel`（`minimumHeight` 預留），初始隱藏；`text.textChanged` 與選單／自訂檔變更時觸發檢查。檢查使用 `pymupdf.Font(fontfile=path).has_glyph()`；目前文字為空時不顯示警告；字型檔載入失敗時顯示「無法讀取字型檔」（與「系統字型列舉與粗體檔解析」章節一致）。
- 選單快取：`system_fonts()` 僅在 `TextPanel` 初始化時呼叫一次。
- 「自訂字型檔…」狀態：`choose_font` 選到任意檔後，在選單插入（或選取）一個 `("自訂字型檔…", path)` 項目；再次開啟對話框選其他檔時更新該項。
- 粗體重置語意：切換字型**不**重置粗體勾選（保留目前粗體狀態）；僅 `set_run`／`set_insertion` 進入新編輯目標時重置為未勾選。

## 錯誤處理

- 登錄檔無法存取：`system_fonts()` 回傳空清單，UI 僅顯示內建 Noto（不彈錯誤）。
- 登錄檔指向不存在或無法由 PyMuPDF 開啟的檔案：該字型略過不列入選單（列舉階段不開啟每個檔，只有選到或檢查缺字時才開啟；開啟失敗視為「無法載入」，併入缺字警告列顯示「無法讀取字型檔」）。
- 缺字警告純屬提示，不阻擋操作；套用階段的 `FONT_MISSING_GLYPH` 沿用現行 fallback 至 Noto。
- `resolve_bold` 命中但粗體檔無法開啟：視為未命中，改走模擬粗體分支（不拋例外）。

## 測試與驗收

Windows 限定用例以 `@pytest.mark.skipif(sys.platform != "win32", ...)` 保護。

- `test_fonts.py`：
  - `system_fonts()` 於 Windows 回傳非空清單，且每個路徑 `os.path.exists` 為真。
  - 相對路徑正確解析至 `%WINDIR%\Fonts`。
  - 過濾 `.fon`／不存在的檔案（以偽造登錄值或 monkeypatch 驗證過濾邏輯）。
  - 登錄檔無法開啟時回傳空清單（monkeypatch `winreg.OpenKey` 拋例外）。
  - `resolve_bold`：以含 Bold 家族的字型（如 Microsoft JhengHei）驗證命中；對無粗體檔的家族驗證回傳 `None`；命中但檔案不存在時回傳 `None`。
- `test_text.py`：
  - `TextInsertion(... align, bold=False)` 預設值向後相容。
  - 模擬粗體分支：以內建 Noto 為 `font_path`、`bold=True` 插入，輸出內容流含 `2 Tr`（render mode 2）且文字往返一致。
  - 真粗體分支：以登錄檔解析到的粗體檔（Windows）或臨時複製的一般檔偽裝成「粗體路徑」插入，確認輸出文字往返一致且使用該路徑。
  - `replace_text` 的粗體分支：一般字重替換為粗體，驗證往返與 `2 Tr`。
  - 多行文字＋粗體（`insert_textbox`）煙霧測試：不拋例外、文字往返一致。
  - `move_run`／`delete_run` 的粗體旗標煙霧測試：`move_run` 產生的請求攜帶目前面板粗體狀態；`delete_run` 的請求 `bold=False` 且不拋例外。
- 缺字警告（`text_panel` 測試）：設文字含字型缺少的字元時警告列顯示；切回完整字型後隱藏。
- 端對端手動驗收：啟動程式，勾選粗體插入文字，另存後以 PDF 閱讀器檢視粗體外觀；選細明體＋粗體確認模擬粗體生效。

## 限制

- `.ttc` 集合檔僅讀取 Face 0（如細明體家族顯示為「細明體」而非「新細明體」），維持可接受近似。
- 模擬粗體為工程近似，品質略遜於真實粗體檔；對標楷體等手寫風格字型效果一般。模擬粗體的 `border_width = size * 0.04` 為經驗值（約 4% 字級），實作時可依視覺微調，不影響架構。
- 系統字型全部列出，選單可能達數百項；以即時缺字警告補足不支援字元的提示，不另行篩選或標記。
- 非 Windows 平台不列系統字型，僅內建 Noto。