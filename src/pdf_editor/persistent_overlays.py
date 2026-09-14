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
    """判斷內嵌檔是否全屬本程式保留的工作層命名空間。"""
    names = set(document.embfile_names())
    return (
        bool(names)
        and MANIFEST_NAME in names
        and BASE_NAME in names
        and all(
            name in {MANIFEST_NAME, BASE_NAME} or name.startswith(ASSET_PREFIX)
            for name in names
        )
    )


def embed_workspace(base_pdf: bytes, overlays: tuple[Overlay, ...]) -> bytes:
    """輸出可直接顯示，且附帶可還原圖章資料的 PDF。"""
    assets, manifest_overlays = _prepare_assets(overlays)
    base_sha256 = _sha256(base_pdf)
    page_count = _pdf_page_count(base_pdf)
    manifest = {
        "version": WORKSPACE_VERSION,
        "base_sha256": base_sha256,
        "page_count": page_count,
        "overlays": manifest_overlays,
    }
    visible = flatten_overlays(base_pdf, overlays)
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
            asset_store = AssetStore(asset_root)
            existing_assets = set(asset_store.root.iterdir())
            overlays = tuple(
                _restore_overlay(
                    item,
                    names,
                    document,
                    asset_store,
                    manifest["page_count"],
                    existing_assets,
                    created_assets,
                )
                for item in manifest["overlays"]
            )
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
            "version", "base_sha256", "page_count", "overlays"
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
    page_count: int,
    existing_assets: set[Path],
    created_assets: set[Path],
) -> Overlay:
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
        content = document.embfile_get(asset_name)
        if _sha256(content) != asset_sha256:
            raise ValueError()
        path = asset_store.import_png_bytes(content)
        if path not in existing_assets:
            created_assets.add(path)
        return Overlay(identifier, page, str(path), tuple(rect), angle)
    except Exception as exc:
        raise EditorError("WORKSPACE", _WORKSPACE_ERROR) from exc


def _pdf_page_count(pdf: bytes) -> int:
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        if document.page_count < 1:
            raise ValueError()
        return document.page_count


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _remove_created_assets(paths: set[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
