"""掃描並安全轉換可單獨抽離的既有 PDF 圖章。"""

import io
import re
import uuid

import pymupdf
from PIL import Image, ImageChops

from pdf_editor.assets import AssetStore
from pdf_editor.errors import EditorError
from pdf_editor.model import EditableImage, LegacyImageCandidate, Overlay


# 外觀比對容許的誤差：一般取樣值差距須 ≤ 2；刪除再放回影像時，
# 附近線條的反鋸齒可能出現零星差異，因此允許極少量取樣值稍大。
_SAMPLE_TOLERANCE = 2
_MAX_SAMPLE_DIFFERENCE = 12
_MAX_OUTLIER_RATIO = 0.0005


def find_convertible_images(pdf: bytes) -> tuple[LegacyImageCandidate, ...]:
    """只回傳全文件僅出現一次、且非整頁掃描的影像。"""
    try:
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            uses = _image_uses(document)
            annotation_xrefs = _annotation_reachable_xrefs(document)
            candidates = []
            for xref, locations in uses.items():
                if len(locations) != 1 or xref in annotation_xrefs:
                    continue
                page_number, rect = locations[0]
                if _use_rejection(document[page_number], xref, rect):
                    continue
                try:
                    candidate = _candidate_from_use(document, xref, page_number, rect)
                except Exception:
                    continue
                if _is_placeholder_size(candidate.width, candidate.height):
                    continue
                if _replacement_preserves_appearance(pdf, candidate):
                    candidates.append(candidate)
            return tuple(candidates)
    except EditorError:
        raise
    except Exception as exc:
        raise EditorError("STAMP_CONVERSION", "無法掃描文件中的既有圖章影像。") from exc


_CANDIDATE_CHANGED = "圖章候選已變更，請重新掃描後再選取。"
_IMAGE_CHANGED = "圖片位置已變更，請再點一次。"
_IMAGE_REUSED = "這張圖片在文件中重複使用，無法單獨編輯。"


def convert_legacy_image(pdf: bytes, candidate: LegacyImageCandidate, asset_root) -> tuple[bytes, Overlay]:
    """原子地抽離候選影像，成功時才建立資產與工作層。"""
    try:
        current = {item.xref: item for item in find_convertible_images(pdf)}
        if current.get(candidate.xref) != candidate:
            raise EditorError("STAMP_CONVERSION", _CANDIDATE_CHANGED)
        return _extract_candidate(
            pdf, candidate, asset_root, _CANDIDATE_CHANGED,
            lambda document: _image_uses(document).get(candidate.xref)
            == [(candidate.page, candidate.rect)],
        )
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
            candidate = _candidate_for_click(document, image)
        return _extract_candidate(
            pdf, candidate, asset_root, _IMAGE_CHANGED,
            lambda document: _used_only_at(document, image) is None,
        )
    except EditorError as exc:
        if exc.code == "STAMP_CONVERSION":
            raise
        raise EditorError("STAMP_CONVERSION", "圖片轉換失敗，文件未被變更。") from exc
    except Exception as exc:
        raise EditorError("STAMP_CONVERSION", "圖片轉換失敗，文件未被變更。") from exc


def _candidate_for_click(document, image: EditableImage) -> LegacyImageCandidate:
    """確認點選的圖片只在該頁的該位置使用且可安全編輯；不符時丟出原因。"""
    page = document[image.page]
    info = next((item for item in page.get_images(full=True) if item[0] == image.xref), None)
    # 已抽離圖片留下的透明占位圖（或已不存在的圖片）：畫面資料過時，請使用者重新點選。
    if info is None or _is_placeholder_size(info[2], info[3]):
        raise EditorError("STAMP_CONVERSION", _IMAGE_CHANGED)
    placements = page.get_image_rects(image.xref, transform=True)
    problem = _reuse_problem(document, image, placements)
    if problem:
        raise EditorError("STAMP_CONVERSION", problem)
    problem = _use_rejection(page, image.xref, image.rect, placements)
    if problem:
        raise EditorError("STAMP_CONVERSION", problem)
    return _candidate_from_use(document, image.xref, image.page, image.rect)


def _used_only_at(document, image: EditableImage) -> str | None:
    """實際刪除前的再次確認；不符時回傳原因。"""
    placements = document[image.page].get_image_rects(image.xref, transform=True)
    return _reuse_problem(document, image, placements)


def _reuse_problem(document, image: EditableImage, placements) -> str | None:
    """只靠資源引用與該頁的繪製位置判斷是否單獨使用；不符時回傳原因。

    不掃描所有頁面的影像位置，大型掃描檔才不會因此變慢。
    """
    for number in range(document.page_count):
        if number != image.page and any(
                item[0] == image.xref for item in document.get_page_images(number, full=True)):
            return _IMAGE_REUSED
    if len(placements) > 1 or image.xref in _annotation_reachable_xrefs(document):
        return _IMAGE_REUSED
    # 頁面資料可能已過時；位置不符時不猜測，避免改到別張圖。
    if [_rect_tuple(rect) for rect, _matrix in placements] != [image.rect]:
        return _IMAGE_CHANGED
    return None


_REFERENCE = re.compile(r"(\d+)\s+0\s+R")


def _annotation_reachable_xrefs(document) -> set[int]:
    """各頁註解外觀串流（含其資源與表單 XObject）遞迴引用到的物件編號。

    刪除影像會替換該物件，若註解外觀也用到它，註解就會變空白；
    因此這類影像一律視為重複使用。只檢視有註解的頁面，且不做渲染。
    """
    pending = []
    for number in range(document.page_count):
        kind, value = document.xref_get_key(document[number].xref, "Annots")
        if kind == "null":
            continue
        if kind == "xref":
            value = document.xref_object(int(value.split()[0]))
        for annotation in _REFERENCE.findall(value):
            ap_kind, ap_value = document.xref_get_key(int(annotation), "AP")
            if ap_kind == "xref":
                pending.append(int(ap_value.split()[0]))
            elif ap_kind == "dict":
                pending.extend(int(item) for item in _REFERENCE.findall(ap_value))
    reached = set()
    while pending:
        xref = pending.pop()
        if xref in reached or not 0 < xref < document.xref_length():
            continue
        reached.add(xref)
        pending.extend(int(item) for item in _REFERENCE.findall(document.xref_object(xref)))
    return reached


def _extract_candidate(pdf: bytes, candidate: LegacyImageCandidate, asset_root,
                       changed_message: str, still_single_use) -> tuple[bytes, Overlay]:
    """從副本移除影像並驗證外觀；全部通過後才建立資產。"""
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        # 在實際刪除前重新確認此影像仍僅有一個使用位置。
        if not still_single_use(document):
            raise EditorError("STAMP_CONVERSION", changed_message)
        document[candidate.page].delete_image(candidate.xref)
        base_pdf = _base_bytes(document)

    _verify_pdf(base_pdf, candidate)
    if not _replacement_from_base_preserves_appearance(pdf, base_pdf, candidate):
        raise EditorError(
            "STAMP_CONVERSION",
            "這張圖片受其他內容或繪圖狀態影響，無法安全編輯。",
        )
    asset = AssetStore(asset_root).import_png_bytes(candidate.png)
    return base_pdf, Overlay(uuid.uuid4().hex, candidate.page, str(asset), candidate.rect)


def _base_bytes(document) -> bytes:
    """輸出抽離影像後的基底。

    只移除未被引用的物件、不重新編號（garbage=1）：其他物件（例如註解）的編號
    在轉換前後保持一致，未改動就還原時，畫面上的註解仍對得到原文件；
    再次點選同頁其他圖片時，也不會用到已改號的舊編號。
    """
    return document.tobytes(garbage=1, deflate=True)


def _is_placeholder_size(width: int, height: int) -> bool:
    """delete_image 會在原處留下 1×1 透明占位圖；這種圖片不提供編輯。"""
    return width <= 1 or height <= 1


def editable_images_on_page(document, page_number: int) -> tuple[EditableImage, ...]:
    """快速列出本頁可直接點選編輯的圖片；不做渲染比對，轉換時會再完整把關。"""
    page = document[page_number]
    images = {
        image[0]: image for image in page.get_images(full=True)
        if image[0] > 0 and not _is_placeholder_size(image[2], image[3])
    }
    if not images:
        return ()
    scan_sizes = _scan_image_sizes(page)
    # 其他頁面的資源引用同一張圖，就當作重複使用，不提供直接點選。
    elsewhere = {
        image[0]
        for number in range(document.page_count) if number != page_number
        for image in document.get_page_images(number, full=True)
    }
    found = []
    for xref in sorted(images.keys() - elsewhere):
        # 整頁掃描影像的位置查詢最耗時，先用像素尺寸略過。
        if (images[xref][2], images[xref][3]) in scan_sizes:
            continue
        # 位置與擺放矩陣一次取得，避免重複解碼影像。
        placements = page.get_image_rects(xref, transform=True)
        if len(placements) != 1:
            continue
        rect = _rect_tuple(placements[0][0])
        if _use_rejection(page, xref, rect, placements) is None:
            found.append(EditableImage(xref, page_number, rect))
    return tuple(found)


def _scan_image_sizes(page) -> set[tuple[int, int]]:
    """本頁涵蓋幾乎整頁的影像像素尺寸；get_image_info 不解碼影像，速度很快。"""
    page_area = page.cropbox.get_area()
    return {
        (info["width"], info["height"])
        for info in page.get_image_info()
        if pymupdf.Rect(info["bbox"]).get_area() >= page_area * 0.9
    }


def _image_uses(document) -> dict[int, list[tuple[int, tuple[float, float, float, float]]]]:
    uses = {}
    for page_number, page in enumerate(document):
        xrefs = {image[0] for image in page.get_images(full=True) if image[0] > 0}
        for xref in xrefs:
            for rect in page.get_image_rects(xref):
                uses.setdefault(xref, []).append((page_number, _rect_tuple(rect)))
    return uses


def _rect_tuple(rect) -> tuple[float, float, float, float]:
    return tuple(int(value) if float(value).is_integer() else float(value) for value in rect)


def _candidate_from_use(document, xref: int, page: int, rect) -> LegacyImageCandidate:
    image_info = next(
        (item for item in document[page].get_images(full=True) if item[0] == xref),
        None,
    )
    if image_info is None:
        raise ValueError("找不到影像資源")
    pixmap = pymupdf.Pixmap(document, xref)
    if image_info[1] > 0:
        mask = pymupdf.Pixmap(document, image_info[1])
        pixmap = pymupdf.Pixmap(pixmap, mask)
    png, width, height = _normalize_png(pixmap.tobytes("png"))
    return LegacyImageCandidate(xref, page, rect, png, width, height)


def _normalize_png(image_bytes: bytes) -> tuple[bytes, int, int]:
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
        normalized = io.BytesIO()
        image.convert("RGBA").save(normalized, format="PNG")
        return normalized.getvalue(), width, height


def _use_rejection(page, xref: int, rect, placements=None) -> str | None:
    """影像不適合單獨編輯時回傳原因，可編輯則回傳 None。

    placements 為已取得的 get_image_rects(xref, transform=True) 結果；未提供時才查詢。
    """
    if pymupdf.Rect(rect).get_area() >= page.cropbox.get_area() * 0.9:
        return "整頁掃描的圖片無法單獨編輯。"
    if placements is None:
        placements = page.get_image_rects(xref, transform=True)
    if not _has_axis_aligned_placement(placements, rect):
        return "旋轉或傾斜擺放的圖片無法單獨編輯。"
    return None


def _has_axis_aligned_placement(placements, rect) -> bool:
    """目前只接受未旋轉、未鏡射且未斜切的軸向影像。"""
    matching = [matrix for found_rect, matrix in placements if _rect_tuple(found_rect) == rect]
    if len(matching) != 1:
        return False
    a, b, c, d, _e, _f = matching[0]
    return a > 0 and d > 0 and abs(b) <= 1e-6 and abs(c) <= 1e-6


def _replacement_preserves_appearance(pdf: bytes, candidate: LegacyImageCandidate) -> bool:
    try:
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            document[candidate.page].delete_image(candidate.xref)
            base_pdf = _base_bytes(document)
        return _replacement_from_base_preserves_appearance(pdf, base_pdf, candidate)
    except Exception:
        return False


def _replacement_from_base_preserves_appearance(
        original_pdf: bytes, base_pdf: bytes, candidate: LegacyImageCandidate) -> bool:
    try:
        replacement = _insert_candidate(base_pdf, candidate)
        return _rendered_region_matches(original_pdf, replacement, candidate)
    except Exception:
        return False


def _insert_candidate(base_pdf: bytes, candidate: LegacyImageCandidate) -> bytes:
    """以正式平面化流程相同的取樣方式重建候選影像。"""
    with pymupdf.open(stream=base_pdf, filetype="pdf") as document:
        document[candidate.page].insert_image(
            candidate.rect, stream=candidate.png, keep_proportion=False
        )
        return document.tobytes(garbage=4, deflate=True)


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


def _verify_pdf(base_pdf: bytes, candidate: LegacyImageCandidate) -> None:
    try:
        with pymupdf.open(stream=base_pdf, filetype="pdf") as document:
            if not 0 <= candidate.page < document.page_count:
                raise ValueError("頁面不存在")
            extracted = document.extract_image(candidate.xref)
            if not extracted:
                return
            png, width, height = _normalize_png(extracted["image"])
            if (png, width, height) == (candidate.png, candidate.width, candidate.height):
                raise ValueError("原影像仍存在")
    except Exception as exc:
        raise EditorError("STAMP_CONVERSION", "無法驗證已抽離圖章的基底文件。") from exc
