"""將可編輯圖章工作層嵌入已平面化的 PDF。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

import pymupdf

from pdf_editor.assets import AssetStore
from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.errors import EditorError
from pdf_editor.model import Overlay


WORKSPACE_VERSION = 1
MANIFEST_NAME = "moye-pdf/workspace.json"
BASE_NAME = "moye-pdf/base.pdf"
ASSET_PREFIX = "moye-pdf/assets/"
_WORKSPACE_ERROR = "圖章工作層無法驗證，已改以靜態 PDF 開啟。"


@dataclass(frozen=True)
class PersistentOverlayBundle:
    base_pdf: bytes
    overlays: tuple[Overlay, ...]


def has_only_workspace_embedded_files(document) -> bool:
    """只接受經 manifest 完整引用且可驗證的工作層內嵌檔。"""
    try:
        names = set(document.embfile_names())
        if MANIFEST_NAME not in names or BASE_NAME not in names:
            return False
        base_pdf = document.embfile_get(BASE_NAME)
        manifest = _validate_manifest(document.embfile_get(MANIFEST_NAME), base_pdf)
        if manifest["visible_sha256"] != _visible_fingerprint(document):
            return False
        page_bounds = _pdf_page_bounds(base_pdf)
        referenced_assets = {
            _validated_overlay_data(
                item, names, document, page_bounds
            )[4]
            for item in manifest["overlays"]
        }
        return names == {
            MANIFEST_NAME,
            BASE_NAME,
            *referenced_assets,
        }
    except Exception:
        return False


def _validated_overlay_data(item: object, names: set[str], document, page_bounds):
    """驗證圖層描述及資產內容，供載入與附件分類共用。"""
    try:
        if not isinstance(item, dict) or set(item) != {
            "id", "page", "rect", "angle", "asset_name", "asset_sha256"
        }:
            raise ValueError()
        identifier = item["id"]
        page = item["page"]
        rect = item["rect"]
        angle = item["angle"]
        asset_name = item["asset_name"]
        asset_sha256 = item["asset_sha256"]
        page_count = len(page_bounds)
        if (
            not isinstance(identifier, str)
            or not isinstance(page, int)
            or isinstance(page, bool)
            or not 0 <= page < page_count
            or not isinstance(rect, list)
            or len(rect) != 4
            or not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                for value in rect
            )
            or not isinstance(angle, (int, float))
            or isinstance(angle, bool)
            or not math.isfinite(angle)
            or not isinstance(asset_name, str)
            or not asset_name.startswith(ASSET_PREFIX)
            or not isinstance(asset_sha256, str)
            or asset_name not in names
        ):
            raise ValueError()
        x0, y0, x1, y1 = rect
        page_width, page_height = page_bounds[page]
        if not (
            0 <= x0 < x1 <= page_width
            and 0 <= y0 < y1 <= page_height
            and _rotated_rect_is_inside_page(rect, angle, page_width, page_height)
        ):
            raise ValueError()
        content = document.embfile_get(asset_name)
        if _sha256(content) != asset_sha256:
            raise ValueError()
        return identifier, page, tuple(rect), angle, asset_name, content
    except Exception as exc:
        raise EditorError("WORKSPACE", _WORKSPACE_ERROR) from exc


def embed_workspace(base_pdf: bytes, overlays: tuple[Overlay, ...]) -> bytes:
    """輸出可直接顯示，且附帶可還原圖章資料的 PDF。"""
    assets, manifest_overlays = _prepare_assets(overlays)
    base_sha256 = _sha256(base_pdf)
    page_count = _pdf_page_count(base_pdf)
    visible = flatten_overlays(base_pdf, overlays)
    manifest = {
        "version": WORKSPACE_VERSION,
        "base_sha256": base_sha256,
        "page_count": page_count,
        "visible_sha256": _visible_fingerprint(visible),
        "overlays": manifest_overlays,
    }
    with pymupdf.open(stream=visible, filetype="pdf") as document:
        document.embfile_add(BASE_NAME, base_pdf, filename="base.pdf")
        document.embfile_add(
            MANIFEST_NAME,
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            filename="workspace.json",
        )
        for name, content in assets.items():
            document.embfile_add(name, content, filename=Path(name).name)
        return document.tobytes(garbage=4, deflate=True)


def load_workspace(pdf: bytes, asset_root: Path) -> PersistentOverlayBundle | None:
    """驗證並還原工作層；沒有工作層的 PDF 維持一般開啟流程。"""
    created_assets: set[Path] = set()
    try:
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            names = set(document.embfile_names())
            if MANIFEST_NAME not in names:
                return None
            manifest_bytes = document.embfile_get(MANIFEST_NAME)
            base_pdf = document.embfile_get(BASE_NAME)
            manifest = _validate_manifest(manifest_bytes, base_pdf)
            if manifest["visible_sha256"] != _visible_fingerprint(document):
                raise EditorError("WORKSPACE", _WORKSPACE_ERROR)
            page_bounds = _pdf_page_bounds(base_pdf)
            referenced_assets = {
                _validated_overlay_data(item, names, document, page_bounds)[4]
                for item in manifest["overlays"]
            }
            if names != {MANIFEST_NAME, BASE_NAME, *referenced_assets}:
                raise EditorError("WORKSPACE", _WORKSPACE_ERROR)
            asset_store = AssetStore(asset_root)
            existing_assets = set(asset_store.root.iterdir())
            overlays = tuple(
                _restore_overlay(
                    item,
                    names,
                    document,
                    asset_store,
                    page_bounds,
                    existing_assets,
                    created_assets,
                )
                for item in manifest["overlays"]
            )
            reconstructed = flatten_overlays(base_pdf, overlays)
            if _visible_fingerprint(reconstructed) != manifest["visible_sha256"]:
                raise EditorError("WORKSPACE", _WORKSPACE_ERROR)
            return PersistentOverlayBundle(base_pdf, overlays)
    except EditorError:
        _remove_created_assets(created_assets)
        raise
    except Exception as exc:
        _remove_created_assets(created_assets)
        raise EditorError("WORKSPACE", _WORKSPACE_ERROR) from exc


def _prepare_assets(overlays: tuple[Overlay, ...]):
    assets: dict[str, bytes] = {}
    manifest_overlays: list[dict[str, object]] = []
    for overlay in overlays:
        content = Path(overlay.asset_path).read_bytes()
        digest = _sha256(content)
        name = f"{ASSET_PREFIX}{digest}.png"
        assets[name] = content
        manifest_overlays.append(
            {
                "id": overlay.id,
                "page": overlay.page,
                "rect": list(overlay.rect),
                "angle": overlay.angle,
                "asset_name": name,
                "asset_sha256": digest,
            }
        )
    return assets, manifest_overlays


def _validate_manifest(manifest_bytes: bytes, base_pdf: bytes) -> dict:
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if not isinstance(manifest, dict) or set(manifest) != {
            "version", "base_sha256", "page_count", "visible_sha256", "overlays"
        }:
            raise ValueError()
        if manifest["version"] != WORKSPACE_VERSION:
            raise ValueError()
        if not isinstance(manifest["base_sha256"], str) or (
            manifest["base_sha256"] != _sha256(base_pdf)
        ):
            raise ValueError()
        if not isinstance(manifest["page_count"], int) or isinstance(manifest["page_count"], bool):
            raise ValueError()
        if manifest["page_count"] != _pdf_page_count(base_pdf):
            raise ValueError()
        if (
            not isinstance(manifest["visible_sha256"], str)
            or len(manifest["visible_sha256"]) != 64
        ):
            raise ValueError()
        if not isinstance(manifest["overlays"], list):
            raise ValueError()
        return manifest
    except Exception as exc:
        raise EditorError("WORKSPACE", _WORKSPACE_ERROR) from exc


def _restore_overlay(
    item: object,
    names: set[str],
    document,
    asset_store: AssetStore,
    page_bounds,
    existing_assets: set[Path],
    created_assets: set[Path],
) -> Overlay:
    try:
        identifier, page, rect, angle, _asset_name, content = _validated_overlay_data(
            item, names, document, page_bounds
        )
        path = asset_store.import_png_bytes(content)
        if path not in existing_assets:
            created_assets.add(path)
        return Overlay(identifier, page, str(path), rect, angle)
    except Exception as exc:
        raise EditorError("WORKSPACE", _WORKSPACE_ERROR) from exc


def _pdf_page_count(pdf: bytes) -> int:
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        if document.page_count < 1:
            raise ValueError()
        return document.page_count


def _pdf_page_bounds(pdf: bytes):
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        if document.page_count < 1:
            raise ValueError()
        return tuple((page.cropbox.width, page.cropbox.height) for page in document)


def _rotated_rect_is_inside_page(rect, angle, page_width, page_height) -> bool:
    """確認旋轉後的圖章外框仍完整位於頁面可渲染範圍。"""
    x0, y0, x1, y1 = rect
    radians = math.radians(angle % 360)
    rotated_width = abs((x1 - x0) * math.cos(radians)) + abs(
        (y1 - y0) * math.sin(radians)
    )
    rotated_height = abs((x1 - x0) * math.sin(radians)) + abs(
        (y1 - y0) * math.cos(radians)
    )
    center_x, center_y = (x0 + x1) / 2, (y0 + y1) / 2
    return (
        center_x - rotated_width / 2 >= -1e-6
        and center_y - rotated_height / 2 >= -1e-6
        and center_x + rotated_width / 2 <= page_width + 1e-6
        and center_y + rotated_height / 2 <= page_height + 1e-6
    )


def _visible_fingerprint(pdf_or_document) -> str:
    """以頁面幾何與渲染像素辨識可見快照，不受附件位元差異影響。"""
    owns_document = isinstance(pdf_or_document, (bytes, bytearray))
    document = (
        pymupdf.open(stream=pdf_or_document, filetype="pdf")
        if owns_document else pdf_or_document
    )
    try:
        digest = hashlib.sha256()
        digest.update(str(document.page_count).encode("ascii"))
        for page in document:
            geometry = (
                tuple(page.mediabox), tuple(page.cropbox), page.rotation,
            )
            digest.update(repr(geometry).encode("ascii"))
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=True)
            digest.update(f"{pixmap.width}:{pixmap.height}:{pixmap.n}".encode("ascii"))
            digest.update(pixmap.samples)
        return digest.hexdigest()
    finally:
        if owns_document:
            document.close()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _remove_created_assets(paths: set[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
