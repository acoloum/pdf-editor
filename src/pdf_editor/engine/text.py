import functools
import hashlib
import math
import pymupdf
from pdf_editor.model import TextRun, TextReplacement, TextInsertion
from pdf_editor.errors import EditorError
from pdf_editor.engine.fonts import checked_font, resolve_bold, subset_font

ALIGNMENTS = {"left": pymupdf.TEXT_ALIGN_LEFT, "hcenter": pymupdf.TEXT_ALIGN_CENTER,
    "center": pymupdf.TEXT_ALIGN_CENTER}

def _image_covers_rect(img_info, rect):
    """圖像是否大面積覆蓋矩形（視為裝飾底圖而非儲存格）。"""
    inter = pymupdf.Rect(img_info["bbox"]) & rect
    if inter.is_empty:
        return False
    return inter.get_area() / rect.get_area() > 0.5

# 儲存格分析結果依（文件識別碼, 頁碼）快取；鍵不保留 PDF 內容，
# 否則每個版本的完整位元組都會被快取抓住不放（8.5 MB 文件曾佔用上百 MB）。
_CELL_CACHE = {}
_CELL_CACHE_LIMIT = 2


def document_key(pdf: bytes) -> str:
    return hashlib.sha256(pdf).hexdigest()


def clear_cell_cache():
    _CELL_CACHE.clear()


def page_cell_index(pdf: bytes, page: int, key: str | None = None):
    """分析整頁的表格儲存格與可用繪圖框，每頁只做一次。

    表格偵測每次約需上百毫秒；依頁快取後，同頁點選不同文字只需查表。
    key 由呼叫端提供文件識別碼時可省下雜湊計算。
    """
    cache_key = (key or document_key(pdf), page)
    cached = _CELL_CACHE.get(cache_key)
    if cached is not None:
        _CELL_CACHE[cache_key] = _CELL_CACHE.pop(cache_key)
        return cached
    result = _analyse_page_cells(pdf, page)
    _CELL_CACHE[cache_key] = result
    while len(_CELL_CACHE) > _CELL_CACHE_LIMIT:
        _CELL_CACHE.pop(next(iter(_CELL_CACHE)))
    return result


def _analyse_page_cells(pdf: bytes, page: int):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        current = doc[page]
        try:
            tables = current.find_tables().tables
        except (AttributeError, RuntimeError):
            tables = ()
        cells = tuple(tuple(cell) for table in tables for cell in table.cells if cell is not None)
        page_area = current.rect.width * current.rect.height
        images = current.get_image_info()
        drawings = []
        for drawing in current.get_drawings():
            candidate = pymupdf.Rect(drawing["rect"])
            # 排除空框、整頁背景，以及被圖像大面積覆蓋的裝飾框（標題橫幅、LOGO 底框等）。
            if candidate.is_empty or candidate.width * candidate.height > page_area / 4:
                continue
            if any(_image_covers_rect(img, candidate) for img in images):
                continue
            drawings.append(tuple(candidate))
        # 記錄各行文字位置，用來排除同時容納多行文字的合併儲存格。
        texts = tuple(tuple(line["bbox"])
            for block in current.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            if "".join(span["text"] for span in line["spans"]).strip())
        return cells, tuple(drawings), texts


def find_table_cell(pdf: bytes, page: int, rect,
        key: str | None = None) -> tuple[float, float, float, float] | None:
    """找出包含文字中心點的表格儲存格，找不到時回傳 None。

    儲存格內若還有其他行文字（例如上下兩行共用一個合併儲存格），以整格當文字框
    會蓋到另一行，因此不視為此文字的儲存格。
    """
    box = pymupdf.Rect(rect)
    point = pymupdf.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
    cells, drawings, texts = page_cell_index(pdf, page, key)
    others = [pymupdf.Rect(item) for item in texts if not overlaps(box, item)]

    def alone(cell):
        return not any(cell.contains(pymupdf.Point((o.x0 + o.x1) / 2, (o.y0 + o.y1) / 2))
            for o in others)

    candidates = [pymupdf.Rect(cell) for cell in cells if pymupdf.Rect(cell).contains(point)]
    if not candidates:
        # 以繪圖矩形備援偵測儲存格，用於 find_tables 無法解析的矮列或單格表格。
        candidates = [pymupdf.Rect(item) for item in drawings if pymupdf.Rect(item).contains(point)]
    candidates = [cell for cell in candidates if alone(cell)]
    if not candidates:
        return None
    cell = min(candidates, key=lambda item: item.width * item.height)
    inset = min(2.0, cell.width / 10, cell.height / 10)
    return (cell.x0 + inset, cell.y0 + inset, cell.x1 - inset, cell.y1 - inset)

FITS = ("none", "expand", "shrink")
MIN_SHRINK_RATIO = 0.7
# expand 加寬受阻時可縮小字級的下限（與同行其他文字的落差不明顯）。
MIN_EXPAND_SHRINK_RATIO = 0.85
LINE_HEIGHT = 1.25


def overlaps(first, second, tolerance=0.5):
    """兩矩形重疊超過容許值才算重疊；僅相鄰或邊界相碰的文字不視為衝突。"""
    a, b = pymupdf.Rect(first), pymupdf.Rect(second)
    return (min(a.x1, b.x1) - max(a.x0, b.x0) > tolerance
        and min(a.y1, b.y1) - max(a.y0, b.y0) > tolerance)


def _text_extent(font, text, size):
    lines = text.split("\n")
    width = max(font.text_length(line, fontsize=size) for line in lines)
    # 多行文字依字型的上下緣計算行高，與 insert_textbox 的排版一致。
    line_height = max(size * LINE_HEIGHT, size * (font.ascender - font.descender))
    # insert_textbox 在最後一行下方另保留下緣空間。
    height = size if len(lines) == 1 else len(lines) * line_height - size * font.descender + 1
    return width, height


def fit_text_box(rect, text, font, size, fit, alignment, obstacles, bounds):
    """依 fit 設定調整文字框或字級，回傳（文字框, 字級）。

    expand：向右（置中時向兩側）加寬、向下加高，但不跨越其他文字與頁面邊界；
    寬度仍不夠時字級最多縮小到原本的 85%。
    shrink：維持文字框，字級最多縮小到原本的 70%。
    """
    box = pymupdf.Rect(rect)
    width, height = _text_extent(font, text, size)
    fits = width <= box.width + 0.01 and height <= box.height + 0.01
    if fits or fit == "none" or not text:
        return tuple(box), size
    if fit == "shrink":
        ratio = min(box.width / width if width else 1, box.height / height if height else 1)
        new_size = math.floor(size * ratio * 10) / 10
        if new_size >= size * MIN_SHRINK_RATIO:
            return tuple(box), new_size
        raise EditorError("TEXT_OVERFLOW",
            f"文字太長，縮小到 {size * MIN_SHRINK_RATIO:.1f} 點仍放不進儲存格，請縮短文字。")
    margin = 2.0
    need_height = max(box.height, height + 0.5)
    band = [pymupdf.Rect(o) for o in obstacles
        if min(o[3], box.y0 + need_height) - max(o[1], box.y0) > 0.5]
    right = min([o.x0 for o in band if o.x0 >= box.x1 - 0.5] + [bounds.x1 - margin])
    left = max([o.x1 for o in band if o.x1 <= box.x0 + 0.5] + [bounds.x0 + margin])
    if alignment in ("hcenter", "center"):
        center = (box.x0 + box.x1) / 2
        available = min(center - left, right - center) * 2
    else:
        available = right - box.x0
    if width > available + 0.01:
        # 左右緊貼其他文字（例如一行被拆成多段後改中間一段）時無處加寬，
        # 改為小幅縮小字級；縮太多會與同行文字落差明顯，因此設下限。
        new_size = math.floor(size * max(0, available) / width * 10) / 10
        if new_size < size * MIN_EXPAND_SHRINK_RATIO:
            side = "左右" if alignment in ("hcenter", "center") else "右側"
            raise EditorError("TEXT_OVERFLOW",
                f"文字太長：需要 {width:.0f} 點寬，{side}只剩 {max(0, available):.0f} 點，"
                f"縮小到 {size * MIN_EXPAND_SHRINK_RATIO:.1f} 點仍放不下。"
                "請縮短文字、減小字級或手動調整文字框。")
        size = new_size
        width, height = _text_extent(font, text, size)
        need_height = max(box.height, height + 0.5)
    # 右側多留 1 點避免字尾貼線；空間不足時省略這段留白。
    need_width = max(box.width, min(width + 1.0, available))
    if alignment in ("hcenter", "center"):
        x0, x1 = center - need_width / 2, center + need_width / 2
    else:
        x0, x1 = box.x0, box.x0 + need_width
    column = [pymupdf.Rect(o) for o in obstacles
        if min(o[2], x1) - max(o[0], x0) > 0.5 and o[1] >= box.y1 - 0.5]
    bottom = min([o.y0 for o in column] + [bounds.y1 - margin])
    if box.y0 + need_height > bottom + 0.01:
        raise EditorError("TEXT_OVERFLOW",
            f"文字行數太多：需要 {need_height:.0f} 點高，下方只剩 {max(0, bottom - box.y0):.0f} 點。"
            "請減少行數或減小字級。")
    return (x0, box.y0, x1, box.y0 + need_height), size


def _vertical_centered_rect(rect, text, size):
    box = pymupdf.Rect(rect)
    lines = max(1, text.count("\n") + 1)
    content_height = min(box.height, lines * size * 2.0)
    top = box.y0 + max(0, (box.height - content_height) / 2)
    return pymupdf.Rect(box.x0, top, box.x1, top + content_height)

def _font_resource_name(prefix, font_path, text):
    """依字型與內容產生資源名稱，避免重用已子集化且缺字的字型。"""
    token = hashlib.sha256(f"{font_path}\0{text}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{token}"

# 描邊模擬粗體的線寬（點）。0.01 時墨水量僅增加約 19%，遠比真粗體（約 +50%）細，
# 使用者反映看不出粗體；0.045 約 +65%，中文筆畫仍分明（0.07 以上會糊成一團）。
# 不改用其他家族的系統粗體：實測微軟正黑體粗體比內建 Noto 一般字還細，
# 換家族既改變字形外觀，也不會比較粗。
FAKE_BOLD_WIDTH = 0.045


def _resolve_bold(request):
    """依請求的粗體旗標解析最終字型檔與是否模擬粗體。

    優先使用同家族的真正粗體字型，找不到時以描邊模擬。
    """
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

def _new_text_shape(page, rect, text, font_path, font, size, color, alignment, fontname,
        fake_bold=False):
    """建立文字形狀；單行文字以基準線寫入，避免窄表格列被誤判為溢位。"""
    box = pymupdf.Rect(rect)
    page.insert_font(fontname=fontname, fontfile=font_path)
    shape = page.new_shape()
    # 模擬粗體：render_mode=2（fill+stroke），線寬為固定點數。
    # 不可改用與字級成比例的值（如 size*0.04）：PyMuPDF 對 CJK Type0 字型
    # 會再依字級放大 stroke 寬度，字會糊成一團黑塊。
    stroke = (dict(render_mode=2, fill=color, border_width=FAKE_BOLD_WIDTH)
        if fake_bold else {})
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

def insert_text(pdf: bytes, request: TextInsertion) -> bytes:
    if hashlib.sha256(pdf).hexdigest() != request.document_hash:
        raise EditorError("STALE", "文件已變更，請重新指定文字位置。")
    if not request.text.strip():
        raise EditorError("TEXT_EMPTY", "請輸入要新增的文字。")
    if not math.isfinite(request.size) or not 1 <= request.size <= 300:
        raise EditorError("SIZE", "字級必須介於 1 與 300。")
    if len(request.text) > 10000:
        raise EditorError("TEXT_OVERFLOW", "文字過長，請縮短內容。")
    if request.alignment not in ALIGNMENTS:
        raise EditorError("ALIGNMENT", "文字對齊方式無效。")
    if request.fit not in FITS:
        raise EditorError("GEOMETRY", "文字框調整方式無效。")
    if not all(math.isfinite(v) for v in request.rect + request.color):
        raise EditorError("GEOMETRY", "位置或色彩數值無效。")
    rect = pymupdf.Rect(request.rect)
    if rect.is_empty:
        raise EditorError("GEOMETRY", "文字框不可為空。")
    others = [run.rect for run in extract_runs(pdf, request.page)]
    font_path, fake_bold = _resolve_bold(request)
    font = checked_font(font_path, request.text)
    embed_path = subset_font(font_path, request.text)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[request.page]
        bounds = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
        fitted, size = fit_text_box(rect, request.text, font, request.size, request.fit,
            request.alignment, others, bounds)
        rect = pymupdf.Rect(fitted)
        if any(overlaps(rect, other) for other in others):
            raise EditorError("OVERLAP", "文字框與其他文字重疊，請移動或縮小文字框。")
        if not bounds.contains(rect):
            raise EditorError("GEOMETRY", "文字框必須位於頁面內。")
        shape = _new_text_shape(page, rect, request.text, embed_path, font,
            size, request.color, request.alignment,
            _font_resource_name("insertion", font_path, request.text), fake_bold)
        shape.commit()
        # 已以預子集字型嵌入，無需再對整份文件做全表子集化；
        # 唯有子集化失敗（embed_path 退回原字型）時保留原流程。
        if embed_path == font_path:
            doc.subset_fonts(fallback=True)
        return doc.tobytes(garbage=4, deflate=True)

BOLD_NAME_HINTS = ("bold", "black", "heavy", "semibold", "demibold", "粗")


def _span_is_bold(span):
    name = str(span.get("font", "")).lower()
    return bool(span.get("flags", 0) & pymupdf.TEXT_FONT_BOLD) or any(hint in name for hint in BOLD_NAME_HINTS)


# 同一行被拆到不同區塊時的合併條件，以字級為單位：基線差、片段間距上限、
# 以及間距大到視為原本有空格的門檻。表格相鄰欄位的文字間隔遠大於上限，不會被合併。
JOIN_BASELINE_TOLERANCE = 0.1
JOIN_MAX_GAP = 0.25
JOIN_SPACE_GAP = 0.15


def _join_split_lines(pieces):
    """把基線相同、首尾相接的片段合成一行，回傳依原順序排列的片段群組。

    材證產生程式常先寫整頁中文、再寫英文數字，同一行因此落在不同區塊；
    不合併的話只能一段一段改，中間的片段左右緊貼其他文字而無處加寬。
    """
    groups, buckets = [], {}
    for piece in sorted(pieces, key=lambda item: item["rect"].x0):
        target = None
        key = round(piece["baseline"])
        if piece["editable"]:
            candidates = [group for k in (key - 1, key, key + 1) for group in buckets.get(k, ())]
            for group in candidates:
                last = group[-1]
                size = max(last["size"], piece["size"])
                gap = piece["rect"].x0 - last["rect"].x1
                if (abs(last["baseline"] - piece["baseline"]) <= size * JOIN_BASELINE_TOLERANCE
                        and -0.5 <= gap <= size * JOIN_MAX_GAP
                        and min(last["size"], piece["size"]) >= size * 0.75):
                    target = group
                    break
        if target is None:
            target = []
            groups.append(target)
            if piece["editable"]:
                buckets.setdefault(key, []).append(target)
        target.append(piece)
    return sorted(groups, key=lambda group: group[0]["order"])


def extract_runs(pdf: bytes, page: int) -> list[TextRun]:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        pieces = []
        for bi, block in enumerate(doc[page].get_text("dict")["blocks"]):
            for li, line in enumerate(block.get("lines", [])):
                spans = line["spans"]
                text = "".join(span["text"] for span in spans)
                if not text.strip():
                    continue
                rect = pymupdf.Rect(spans[0]["bbox"])
                for span in spans[1:]:
                    rect |= pymupdf.Rect(span["bbox"])
                editable = line["dir"] == (1.0, 0.0) and "\ufffd" not in text
                pieces.append({"id": f"{bi}:{li}", "order": len(pieces), "spans": spans,
                    "text": text, "rect": rect, "baseline": spans[0]["origin"][1],
                    "size": max(span["size"] for span in spans), "editable": editable})
        result = []
        for group in _join_split_lines(pieces):
            text, rect = group[0]["text"], pymupdf.Rect(group[0]["rect"])
            for previous, piece in zip(group, group[1:]):
                gap = piece["rect"].x0 - previous["rect"].x1
                if (gap >= piece["size"] * JOIN_SPACE_GAP
                        and not text[-1].isspace() and not piece["text"][0].isspace()):
                    text += " "
                text += piece["text"]
                rect |= piece["rect"]
            spans = [span for piece in group for span in piece["spans"]]
            editable = all(piece["editable"] for piece in group)
            color = spans[0]["color"]
            # 過半文字為粗體（字型旗標或字型名稱）時視為粗體段落。
            bold_chars = sum(len(span["text"]) for span in spans if _span_is_bold(span))
            result.append(TextRun("+".join(piece["id"] for piece in group), text, tuple(rect),
                spans[0]["font"], max(piece["size"] for piece in group), editable,
                None if editable else "此區段方向或文字編碼不支援修改。",
                (((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255),
                bold_chars * 2 > len(text)))
        return result

def replace_text(pdf: bytes, request: TextReplacement) -> bytes:
    if hashlib.sha256(pdf).hexdigest() != request.document_hash:
        raise EditorError("STALE", "文件已變更，請重新選取文字。")
    if not math.isfinite(request.size) or not 1 <= request.size <= 300:
        raise EditorError("SIZE", "字級必須介於 1 與 300。")
    if len(request.text) > 10000:
        raise EditorError("TEXT_OVERFLOW", "文字過長，請縮短內容。")
    if request.alignment not in ALIGNMENTS:
        raise EditorError("ALIGNMENT", "文字對齊方式無效。")
    if request.fit not in FITS:
        raise EditorError("GEOMETRY", "文字框調整方式無效。")
    if not all(math.isfinite(v) for v in request.rect + request.color):
        raise EditorError("GEOMETRY", "位置或色彩數值無效。")
    runs = extract_runs(pdf, request.page)
    run = next((r for r in runs if r.id == request.run_id), None)
    if not run or not run.editable:
        raise EditorError("UNSUPPORTED_TEXT", "此文字無法安全修改。")
    rect = pymupdf.Rect(request.rect)
    old_rect = pymupdf.Rect(run.rect)
    if rect.is_empty:
        raise EditorError("GEOMETRY", "文字框不可為空。")
    others = [other.rect for other in runs if other.id != run.id]
    if any(overlaps(old_rect, other) for other in others):
        raise EditorError("OVERLAP", "原文字與其他文字重疊，無法安全修改。")
    font_path, fake_bold = _resolve_bold(request)
    font = checked_font(font_path, request.text) if request.text else None
    # 以 fontTools 預先子集化：嵌入僅含本次文字字元的小字型，
    # 免除後續 doc.subset_fonts 對完整巨量 CJK 字型的全表子集化作業。
    embed_path = subset_font(font_path, request.text) if request.text else font_path
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[request.page]
        if page.first_annot:
            if any(a.type[0] == pymupdf.PDF_ANNOT_REDACT for a in page.annots()):
                raise EditorError("REDACTIONS", "此頁有尚未套用的遮蔽標記，無法修改。")
        bounds = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
        size = request.size
        if request.text:
            fitted, size = fit_text_box(rect, request.text, font, request.size, request.fit,
                request.alignment, others, bounds)
            rect = pymupdf.Rect(fitted)
        if any(overlaps(rect, other) for other in others):
            raise EditorError("OVERLAP", "文字框與其他文字重疊，請移動或縮小文字框。")
        if not bounds.contains(rect):
            raise EditorError("GEOMETRY", "文字框必須位於頁面內。")
        if request.text:
            resource_name = _font_resource_name("replacement", font_path, request.text)
            _new_text_shape(page, rect, request.text, embed_path, font,
                size, request.color, request.alignment, resource_name, fake_bold)
        page.add_redact_annot(old_rect, fill=False, cross_out=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        if not request.text:
            return doc.tobytes(garbage=4, deflate=True)
        # 移除後重新建立形狀，避免使用已失效的頁面資源。
        shape = _new_text_shape(page, rect, request.text, embed_path, font,
            size, request.color, request.alignment, resource_name, fake_bold)
        shape.commit()
        # 已以預子集字型嵌入，無需再對整份文件做全表子集化；
        # 唯有子集化失敗（embed_path 退回原字型）時保留原流程。
        if embed_path == font_path:
            doc.subset_fonts(fallback=True)
        return doc.tobytes(garbage=4, deflate=True)
