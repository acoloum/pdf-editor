"""圈選物件的批次編輯：一次刪除或移動同一頁的多段文字與註解，只產生一個新版本。"""
import hashlib
import math

import pymupdf

from pdf_editor.engine.fonts import (checked_font, default_font, embedded_font, similar_font,
    subset_font)
from pdf_editor.engine.text import (_font_resource_name, _new_text_shape, _resolve_bold,
    extract_runs, fit_text_box, overlaps)
from pdf_editor.errors import EditorError
from pdf_editor.model import SelectionEdit, TextReplacement


def _preview(text):
    text = text.strip()
    return text if len(text) <= 12 else text[:12] + "…"


def _run_font(pdf, page, run, folder):
    """移動文字時沿用原字型；無法使用時改用相近字型，最後才用內建中文字型。"""
    candidates = []
    try:
        original = embedded_font(pdf, page, run, folder)
    except Exception:
        original = None
    if original:
        candidates.append(str(original))
    similar = similar_font(run.font_name, run.text)
    if similar:
        candidates.append(similar[1])
    candidates.append(str(default_font()))
    for path in candidates:
        try:
            return path, checked_font(path, run.text)
        except EditorError:
            continue
    raise EditorError("FONT_MISSING_GLYPH", f"找不到能顯示「{_preview(run.text)}」的字型。")


def _shift_annotation(doc, page, annotation, dx, dy):
    """以頁面座標平移註解；螢光與底線改寫 QuadPoints，其餘類型改寫外框。"""
    xref = annotation.xref
    kind, raw = doc.xref_get_key(xref, "QuadPoints")
    if kind == "array":
        values = [float(value) for value in raw.strip("[]").split()]
        # QuadPoints 存的是 PDF 座標（y 軸向上），頁面座標往下移等於 y 值變小。
        shifted = [value + (dx if index % 2 == 0 else -dy) for index, value in enumerate(values)]
        doc.xref_set_key(xref, "QuadPoints", "[" + " ".join(f"{value:g}" for value in shifted) + "]")
        annotation = page.load_annot(xref)
    else:
        annotation.set_rect(annotation.rect + (dx, dy, dx, dy))
    annotation.update()


def edit_selection(pdf: bytes, request: SelectionEdit, font_folder: str) -> bytes:
    if hashlib.sha256(pdf).hexdigest() != request.document_hash:
        raise EditorError("STALE", "文件已變更，請重新圈選。")
    offset = request.offset
    if offset is not None and (len(offset) != 2 or not all(math.isfinite(v) for v in offset)):
        raise EditorError("GEOMETRY", "移動距離無效。")
    runs = extract_runs(pdf, request.page)
    by_id = {run.id: run for run in runs}
    if any(run_id not in by_id for run_id in request.run_ids):
        raise EditorError("STALE", "選取的文字已變更，請重新圈選。")
    selected = [by_id[run_id] for run_id in dict.fromkeys(request.run_ids)]
    blocked = next((run for run in selected if not run.editable), None)
    if blocked:
        raise EditorError("UNSUPPORTED_TEXT", f"「{_preview(blocked.text)}」無法安全修改。")
    chosen = {run.id for run in selected}
    others = [run.rect for run in runs if run.id not in chosen]
    for run in selected:
        # 移除舊文字會清掉範圍內的所有文字；碰到未選取的文字時停止，避免誤刪。
        if any(overlaps(run.rect, other) for other in others):
            raise EditorError("OVERLAP",
                f"「{_preview(run.text)}」與未選取的文字重疊，請一併圈選或個別修改。")
    placements = []
    if offset is not None and selected:
        dx, dy = offset
        shifted = {run.id: (run.rect[0] + dx, run.rect[1] + dy, run.rect[2] + dx,
            run.rect[3] + dy) for run in selected}
        for run in selected:
            font_path, font = _run_font(pdf, request.page, run, font_folder)
            bold_path, fake_bold = _resolve_bold(TextReplacement(request.document_hash,
                request.page, run.id, run.text, shifted[run.id], font_path, run.size, run.color,
                "left", run.bold))
            if bold_path != font_path:
                try:
                    font_path, font = bold_path, checked_font(bold_path, run.text)
                except EditorError:
                    fake_bold = True
            placements.append((run, shifted[run.id], font_path, font, fake_bold))
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        if not 0 <= request.page < doc.page_count:
            raise EditorError("RANGE", "頁碼超出文件範圍。")
        page = doc[request.page]
        annotations = {item.xref: item for item in page.annots() or ()}
        if selected and any(item.type[0] == pymupdf.PDF_ANNOT_REDACT
                for item in annotations.values()):
            raise EditorError("REDACTIONS", "此頁有尚未套用的遮蔽標記，無法修改。")
        targets = []
        for xref in dict.fromkeys(request.annotation_xrefs):
            if xref not in annotations:
                raise EditorError("STALE", "選取的註解已變更，請重新圈選。")
            targets.append(annotations[xref])
        bounds = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
        # 先計算所有新位置並檢查，全部通過才動手修改。
        texts = []
        for run, rect, font_path, font, fake_bold in placements:
            neighbours = others + [other for other_run, other, *_ in placements
                if other_run.id != run.id]
            fitted, size = fit_text_box(rect, run.text, font, run.size, "expand", "left",
                neighbours, bounds)
            fitted = pymupdf.Rect(fitted)
            if not bounds.contains(fitted):
                raise EditorError("GEOMETRY", f"「{_preview(run.text)}」移動後超出頁面範圍。")
            if any(overlaps(fitted, other) for other in others):
                raise EditorError("OVERLAP",
                    f"「{_preview(run.text)}」移動後會與其他文字重疊，請換個位置。")
            texts.append((run, fitted, size, font_path, font, fake_bold))
        if offset is not None:
            dx, dy = offset
            for item in targets:
                if not page.rect.contains(item.rect + (dx, dy, dx, dy)):
                    raise EditorError("GEOMETRY", "註解移動後超出頁面範圍。")
        for item in targets:
            if offset is None:
                page.delete_annot(item)
            else:
                _shift_annotation(doc, page, item, *offset)
        if selected:
            for run in selected:
                page.add_redact_annot(pymupdf.Rect(run.rect), fill=False, cross_out=False)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        needs_subset = False
        for run, rect, size, font_path, font, fake_bold in texts:
            embed_path = subset_font(font_path, run.text)
            needs_subset = needs_subset or embed_path == font_path
            shape = _new_text_shape(page, rect, run.text, embed_path, font, size, run.color,
                "left", _font_resource_name("moved", font_path, run.text), fake_bold)
            shape.commit()
        if needs_subset:
            doc.subset_fonts(fallback=True)
        return doc.tobytes(garbage=4, deflate=True)
