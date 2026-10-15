"""圈選物件的批次編輯：一次刪除或移動同一頁的多段文字、註解與表格格線，只產生一個新版本。"""
from collections import Counter
from dataclasses import replace
import hashlib
import math

import pymupdf

from pdf_editor.engine.text import extract_runs, overlaps
from pdf_editor.errors import EditorError
from pdf_editor.model import SelectionEdit


def _preview(text):
    text = text.strip()
    return text if len(text) <= 12 else text[:12] + "…"


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


# 頁面上的向量圖形超過此數量時不提供圈選（例如工程圖），避免拖慢每次的頁面分析。
MAX_SELECTABLE_DRAWINGS = 5000


def _is_background(drawing):
    """只有填色、接近白色的圖形視為底色：留在原處，不列入圈選。

    白色底色移到別處會蓋住該處的內容，留在原處則看不出差異。
    """
    fill = drawing.get("fill")
    return drawing.get("type") == "f" and fill is not None and min(fill) >= 0.95


def _rect_key(rect):
    return tuple(round(float(value), 3) for value in rect)


def _drawing_key(drawing):
    return (drawing.get("type"), _rect_key(drawing["rect"]), drawing.get("fill"),
        drawing.get("color"), drawing.get("width"))


def content_drawings(pdf, page_number):
    """頁面內容本身的向量圖形，不含註解與表單欄位的外觀。

    get_drawings 會把螢光標記等註解的外觀也當成圖形；這些無法用遮蔽移除，
    因此另開一份副本、拿掉頁面的註解清單再擷取。
    """
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[page_number]
        if doc.xref_get_key(page.xref, "Annots")[0] != "null":
            doc.xref_set_key(page.xref, "Annots", "null")
            # 重新載入頁面才會套用；此時不可有其他地方持有這個頁面物件。
            page = doc.reload_page(page)
        return page.get_drawings()


def selectable_drawings(pdf, page_number):
    """頁面上可圈選的向量圖形（例如表格格線）外框，以頁面座標表示。"""
    drawings = content_drawings(pdf, page_number)
    if len(drawings) > MAX_SELECTABLE_DRAWINGS:
        return ()
    return tuple(tuple(drawing["rect"]) for drawing in drawings
        if not _is_background(drawing) and all(math.isfinite(v) for v in drawing["rect"]))


def _match_drawings(drawings, rects):
    """依外框找出選取的圖形；找不到時表示頁面已變更。"""
    pool = {}
    for drawing in drawings:
        if not _is_background(drawing):
            pool.setdefault(_rect_key(drawing["rect"]), []).append(drawing)
    matched = []
    for rect in rects:
        candidates = pool.get(_rect_key(rect))
        if not candidates:
            raise EditorError("STALE", "選取的格線已變更，請重新圈選。")
        matched.append(candidates.pop(0))
    return matched


def _probe(rect):
    """圖形中心的極小範圍：用「觸碰即移除」的遮蔽只移除該圖形本身。"""
    rect = pymupdf.Rect(rect)
    cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
    radius = max(0.05, min(rect.width, rect.height) * 0.25)
    return pymupdf.Rect(cx - radius, cy - radius, cx + radius, cy + radius)


def _redraw(page, drawings, dx, dy, overlay=True):
    """依 get_drawings 的內容重畫圖形，並平移 (dx, dy)。"""
    if not drawings:
        return
    shift = pymupdf.Point(dx, dy)
    shape = page.new_shape()
    for drawing in drawings:
        for item in drawing["items"]:
            kind = item[0]
            if kind == "l":
                shape.draw_line(item[1] + shift, item[2] + shift)
            elif kind == "re":
                shape.draw_rect(pymupdf.Rect(item[1]) + (dx, dy, dx, dy))
            elif kind == "qu":
                quad = item[1]
                shape.draw_quad(pymupdf.Quad(quad.ul + shift, quad.ur + shift,
                    quad.ll + shift, quad.lr + shift))
            elif kind == "c":
                shape.draw_bezier(*(point + shift for point in item[1:5]))
        line_cap = drawing.get("lineCap")
        shape.finish(fill=drawing.get("fill"), color=drawing.get("color"),
            width=drawing.get("width") or 0, dashes=drawing.get("dashes"),
            lineCap=max(line_cap) if line_cap else 0, lineJoin=drawing.get("lineJoin") or 0,
            even_odd=bool(drawing.get("even_odd")), closePath=bool(drawing.get("closePath")),
            fill_opacity=drawing.get("fill_opacity") or 1,
            stroke_opacity=drawing.get("stroke_opacity") or 1)
    shape.commit(overlay=overlay)


def _remove_drawings(page, drawings):
    """移除選取的圖形，回傳被連帶移除、需要在原位重畫的其他圖形。"""
    before = page.get_drawings()
    for drawing in drawings:
        page.add_redact_annot(_probe(drawing["rect"]), fill=False, cross_out=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
        graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
        text=pymupdf.PDF_REDACT_TEXT_NONE)
    removed = Counter(_drawing_key(d) for d in before) - Counter(
        _drawing_key(d) for d in page.get_drawings())
    wanted = Counter(_drawing_key(d) for d in drawings)
    if wanted - removed:
        raise EditorError("GRAPHICS", "部分格線無法移除，未修改文件。")
    extra = removed - wanted
    collateral = []
    for drawing in before:
        key = _drawing_key(drawing)
        if extra[key] > 0:
            extra[key] -= 1
            collateral.append(drawing)
    return collateral


def _selected_text_copy(pdf, page_number, unselected):
    """另開一份副本，該頁只留下選取的文字；其餘文字、圖片與向量圖形全部移除。"""
    copy = pymupdf.open(stream=pdf, filetype="pdf")
    page = copy[page_number]
    for run in unselected:
        page.add_redact_annot(pymupdf.Rect(run.rect), fill=False, cross_out=False)
    if unselected:
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    # 涵蓋整個頁面（含裁切框外）的遮蔽：移除所有圖片與線條，只保留文字。
    page.add_redact_annot(pymupdf.Rect(-1e4, -1e4, 1e4, 1e4), fill=False, cross_out=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE,
        graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED, text=pymupdf.PDF_REDACT_TEXT_NONE)
    return copy


def _place_original_text(doc, page, source, page_number, clip, dx, dy):
    """把副本中 clip 範圍的原始文字內容貼到新位置，沿用原本的字型與字形。"""
    source_page = source[page_number]
    rotation = page.rotation
    # 兩頁都暫時轉正，clip 與目標範圍才會和文字座標一致。
    if rotation:
        page.set_rotation(0)
        source_page.set_rotation(0)
    try:
        page.show_pdf_page(clip + (dx, dy, dx, dy), source, page_number, clip=clip)
    finally:
        if rotation:
            page.set_rotation(rotation)


def edit_selection(pdf: bytes, request: SelectionEdit) -> bytes:
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
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        if not 0 <= request.page < doc.page_count:
            raise EditorError("RANGE", "頁碼超出文件範圍。")
        page = doc[request.page]
        annotations = {item.xref: item for item in page.annots() or ()}
        drawings = []
        if request.drawing_rects:
            drawings = _match_drawings(content_drawings(pdf, request.page),
                request.drawing_rects)
        if (selected or drawings) and any(item.type[0] == pymupdf.PDF_ANNOT_REDACT
                for item in annotations.values()):
            raise EditorError("REDACTIONS", "此頁有尚未套用的遮蔽標記，無法修改。")
        targets = []
        for xref in dict.fromkeys(request.annotation_xrefs):
            if xref not in annotations:
                raise EditorError("STALE", "選取的註解已變更，請重新圈選。")
            targets.append(annotations[xref])
        bounds = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
        # 先檢查所有新位置，全部通過才動手修改。
        if offset is not None:
            dx, dy = offset
            for run in selected:
                moved = pymupdf.Rect(run.rect) + (dx, dy, dx, dy)
                if not (bounds + (-0.5, -0.5, 0.5, 0.5)).contains(moved):
                    raise EditorError("GEOMETRY", f"「{_preview(run.text)}」移動後超出頁面範圍。")
                if any(overlaps(moved, other) for other in others):
                    raise EditorError("OVERLAP",
                        f"「{_preview(run.text)}」移動後會與其他文字重疊，請換個位置。")
            for drawing in drawings:
                moved = pymupdf.Rect(drawing["rect"]) + (dx, dy, dx, dy)
                if not (bounds + (-0.5, -0.5, 0.5, 0.5)).contains(moved):
                    raise EditorError("GEOMETRY", "格線移動後超出頁面範圍。")
            for item in targets:
                if not page.rect.contains(item.rect + (dx, dy, dx, dy)):
                    raise EditorError("GEOMETRY", "註解移動後超出頁面範圍。")
        for item in targets:
            if offset is None:
                page.delete_annot(item)
            else:
                _shift_annotation(doc, page, item, *offset)
        # 移動文字時不重新排字：先在副本中只留下選取的文字，之後整塊貼到新位置。
        source = None
        if offset is not None and selected:
            source = _selected_text_copy(pdf, request.page,
                [run for run in runs if run.id not in chosen])
        if selected:
            for run in selected:
                page.add_redact_annot(pymupdf.Rect(run.rect), fill=False, cross_out=False)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        if drawings:
            collateral = _remove_drawings(page, drawings)
            # 連帶移除的圖形多為底色，放回原位並置於最底層，避免蓋住文字。
            _redraw(page, collateral, 0, 0, overlay=False)
            if offset is not None:
                _redraw(page, drawings, *offset)
        if source is not None:
            try:
                clip = pymupdf.Rect()
                for run in selected:
                    clip |= pymupdf.Rect(run.rect)
                # 略為放大，避免切掉超出文字外框的筆畫（例如下伸部或斜體）。
                _place_original_text(doc, page, source, request.page,
                    clip + (-2, -2, 2, 2), *offset)
            finally:
                source.close()
        return doc.tobytes(garbage=4, deflate=True)


def _same_rect(first, second, tolerance=0.5):
    return all(abs(a - b) <= tolerance for a, b in zip(first, second))


def edit_selection_with_images(pdf: bytes, request: SelectionEdit, images, asset_root):
    """整批編輯並一併處理圈選到的頁面原有圖片（例如公司章）；回傳 (PDF, 新圖層)。

    先刪除或移動文字、註解與格線，再把選取的圖片轉成圖層：移動時圖層跟著位移，
    刪除時不保留圖層（等於刪除圖片）。任何一張圖片無法安全轉換時，整批都不套用。
    """
    from pdf_editor.engine.overlay import flatten_overlays
    from pdf_editor.legacy_overlay_conversion import convert_image_at, editable_images_on_page

    if request.run_ids or request.annotation_xrefs or request.drawing_rects:
        result = edit_selection(pdf, request)
    elif hashlib.sha256(pdf).hexdigest() != request.document_hash:
        raise EditorError("STALE", "文件已變更，請重新圈選。")
    else:
        result = pdf
    layers = []
    if images:
        # 前一步輸出時物件會重新編號，依位置找回同一張圖片；轉換本身不會改動其他編號。
        with pymupdf.open(stream=result, filetype="pdf") as doc:
            current = editable_images_on_page(doc, request.page)
        for image in images:
            match = next((item for item in current if _same_rect(item.rect, image.rect)), None)
            if match is None:
                raise EditorError("STALE", "選取的圖片已變更，請重新圈選。")
            result, layer = convert_image_at(result, match, asset_root)
            layers.append(layer)
    if request.offset is not None and layers:
        dx, dy = request.offset
        layers = [replace(layer, rect=(layer.rect[0] + dx, layer.rect[1] + dy,
            layer.rect[2] + dx, layer.rect[3] + dy)) for layer in layers]
        # 先驗證新位置能正常輸出。
        flatten_overlays(result, tuple(layers))
    return result, (tuple(layers) if request.offset is not None else ())
