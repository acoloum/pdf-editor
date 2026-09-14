"""掃描並安全轉換可單獨抽離的既有 PDF 圖章。"""

import io
import uuid

import pymupdf
from PIL import Image

from pdf_editor.assets import AssetStore
from pdf_editor.errors import EditorError
from pdf_editor.model import LegacyImageCandidate, Overlay


def find_convertible_images(pdf: bytes) -> tuple[LegacyImageCandidate, ...]:
    """只回傳全文件僅出現一次、且非整頁掃描的影像。"""
    try:
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            uses = _image_uses(document)
            candidates = []
            for xref, locations in uses.items():
                if len(locations) != 1:
                    continue
                page_number, rect = locations[0]
                if not _is_not_page_scan(document[page_number], rect):
                    continue
                if not _has_supported_placement(document[page_number], xref, rect):
                    continue
                try:
                    candidate = _candidate_from_use(document, xref, page_number, rect)
                except Exception:
                    continue
                if _replacement_preserves_appearance(pdf, candidate):
                    candidates.append(candidate)
            return tuple(candidates)
    except EditorError:
        raise
    except Exception as exc:
        raise EditorError("STAMP_CONVERSION", "無法掃描文件中的既有圖章影像。") from exc


def convert_legacy_image(pdf: bytes, candidate: LegacyImageCandidate, asset_root) -> tuple[bytes, Overlay]:
    """原子地抽離候選影像，成功時才建立資產與工作層。"""
    try:
        current = {item.xref: item for item in find_convertible_images(pdf)}
        if current.get(candidate.xref) != candidate:
            raise EditorError("STAMP_CONVERSION", "圖章候選已變更，請重新掃描後再選取。")

        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            # 在實際刪除前重新確認此 xref 仍僅有一個使用位置。
            uses = _image_uses(document)
            if uses.get(candidate.xref) != [(candidate.page, candidate.rect)]:
                raise EditorError("STAMP_CONVERSION", "圖章候選已變更，請重新掃描後再選取。")
            document[candidate.page].delete_image(candidate.xref)
            base_pdf = document.tobytes(garbage=4, deflate=True)

        _verify_pdf(base_pdf, candidate)
        if not _replacement_from_base_preserves_appearance(pdf, base_pdf, candidate):
            raise EditorError(
                "STAMP_CONVERSION",
                "既有圖章受其他內容或繪圖狀態影響，無法安全轉換。",
            )
        asset = AssetStore(asset_root).import_png_bytes(candidate.png)
        return base_pdf, Overlay(uuid.uuid4().hex, candidate.page, str(asset), candidate.rect)
    except EditorError as exc:
        if exc.code == "STAMP_CONVERSION":
            raise
        raise EditorError("STAMP_CONVERSION", "既有圖章轉換失敗，文件未被變更。") from exc
    except Exception as exc:
        raise EditorError("STAMP_CONVERSION", "既有圖章轉換失敗，文件未被變更。") from exc


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


def _is_not_page_scan(page, rect) -> bool:
    image_rect = pymupdf.Rect(rect)
    cropbox = page.cropbox
    return image_rect.get_area() < cropbox.get_area() * 0.9


def _has_supported_placement(page, xref: int, rect) -> bool:
    """目前只接受未旋轉、未鏡射且未斜切的軸向影像。"""
    matching = [
        matrix
        for found_rect, matrix in page.get_image_rects(xref, transform=True)
        if _rect_tuple(found_rect) == rect
    ]
    if len(matching) != 1:
        return False
    a, b, c, d, _e, _f = matching[0]
    return a > 0 and d > 0 and abs(b) <= 1e-6 and abs(c) <= 1e-6


def _replacement_preserves_appearance(pdf: bytes, candidate: LegacyImageCandidate) -> bool:
    try:
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            document[candidate.page].delete_image(candidate.xref)
            base_pdf = document.tobytes(garbage=4, deflate=True)
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
    if samples[0][0] <= 0 or samples[0][1] <= 0 or not samples[0][3]:
        return False
    first, second = samples[0][3], samples[1][3]
    return all(abs(a - b) <= 2 for a, b in zip(first, second))


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
