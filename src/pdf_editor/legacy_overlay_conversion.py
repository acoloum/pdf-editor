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
                if _is_not_page_scan(document[page_number], rect):
                    candidates.append(_candidate_from_use(document, xref, page_number, rect))
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
                location = (page_number, _rect_tuple(rect))
                if location not in uses.setdefault(xref, []):
                    uses[xref].append(location)
    return uses


def _rect_tuple(rect) -> tuple[float, float, float, float]:
    return tuple(int(value) if float(value).is_integer() else float(value) for value in rect)


def _candidate_from_use(document, xref: int, page: int, rect) -> LegacyImageCandidate:
    extracted = document.extract_image(xref)
    png, width, height = _normalize_png(extracted["image"])
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
