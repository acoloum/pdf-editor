from pathlib import Path
import json

import pymupdf
import pytest
from PIL import Image

from pdf_editor.errors import EditorError
from pdf_editor.model import Overlay
from pdf_editor.persistent_overlays import (
    ASSET_PREFIX,
    MANIFEST_NAME,
    embed_workspace,
    load_workspace,
)


def test_embedded_workspace_restores_base_and_overlay_assets(tmp_path, pdf_bytes):
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (20, 10), (0, 60, 255, 180)).save(stamp)
    layer = Overlay("章-1", 0, str(stamp), (100, 110, 140, 130), 15)

    restored = load_workspace(embed_workspace(pdf_bytes, (layer,)), tmp_path / "assets")

    assert restored is not None
    assert restored.base_pdf == pdf_bytes
    assert restored.overlays[0].id == "章-1"
    assert Path(restored.overlays[0].asset_path).read_bytes() == stamp.read_bytes()


def test_embedded_workspace_keeps_stamp_visible_to_standard_pdf_reader(tmp_path, pdf_bytes):
    stamp = tmp_path / "可見章.png"
    Image.new("RGBA", (20, 10), (0, 60, 255, 255)).save(stamp)
    workspace = embed_workspace(
        pdf_bytes, (Overlay("章-1", 0, str(stamp), (100, 110, 140, 130)),)
    )

    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        pixmap = document[0].get_pixmap(alpha=False)
        offset = 120 * pixmap.stride + 120 * pixmap.n
        pixel = pixmap.samples[offset : offset + 3]

    assert pixel[0] < 10
    assert 50 <= pixel[1] <= 70
    assert pixel[2] > 245


def _embed_with_replaced_asset(tmp_path, pdf_bytes, replacement):
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (20, 10), (0, 60, 255, 180)).save(stamp)
    try:
        workspace = embed_workspace(
            pdf_bytes, (Overlay("章-1", 0, str(stamp), (100, 110, 140, 130)),)
        )
    finally:
        stamp.unlink()
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        asset_name = next(
            name for name in document.embfile_names() if name.startswith(ASSET_PREFIX)
        )
        files = {
            name: replacement if name == asset_name else document.embfile_get(name)
            for name in document.embfile_names()
        }
        with pymupdf.open() as tampered:
            tampered.insert_pdf(document)
            for name, content in files.items():
                tampered.embfile_add(name, content, filename=Path(name).name)
            return tampered.tobytes(garbage=4, deflate=True)


def test_workspace_with_tampered_asset_is_rejected(tmp_path, pdf_bytes):
    with pytest.raises(EditorError, match="工作層無法驗證"):
        load_workspace(
            _embed_with_replaced_asset(tmp_path, pdf_bytes, b"not-a-png"),
            tmp_path / "assets",
        )


def test_plain_pdf_has_no_workspace(tmp_path, pdf_bytes):
    assert load_workspace(pdf_bytes, tmp_path / "assets") is None


@pytest.mark.parametrize("manifest_change", ["page_count", "version"])
def test_workspace_with_invalid_manifest_is_rejected(tmp_path, pdf_bytes, manifest_change):
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (20, 10), (0, 60, 255, 180)).save(stamp)
    workspace = embed_workspace(
        pdf_bytes, (Overlay("章-1", 0, str(stamp), (100, 110, 140, 130)),)
    )
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        manifest = document.embfile_get(MANIFEST_NAME).decode("utf-8")
        if manifest_change == "page_count":
            manifest = manifest.replace('"page_count":1', '"page_count":2')
        else:
            manifest = manifest.replace('"version":1', '"version":999')
        document.embfile_del(MANIFEST_NAME)
        document.embfile_add(MANIFEST_NAME, manifest.encode("utf-8"), filename="workspace.json")
        invalid = document.tobytes(garbage=4, deflate=True)

    with pytest.raises(EditorError, match="工作層無法驗證"):
        load_workspace(invalid, tmp_path / "assets")


def _embed_with_replaced_manifest(workspace, replacement):
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        files = {
            name: replacement if name == MANIFEST_NAME else document.embfile_get(name)
            for name in document.embfile_names()
        }
        with pymupdf.open() as tampered:
            tampered.insert_pdf(document)
            for name, content in files.items():
                tampered.embfile_add(name, content, filename=Path(name).name)
            return tampered.tobytes(garbage=4, deflate=True)


@pytest.mark.parametrize(
    "field,value",
    [("page", 1), ("rect", [100, float("nan"), 140, 130]), ("angle", float("nan"))],
)
def test_workspace_with_invalid_overlay_geometry_is_rejected(tmp_path, pdf_bytes, field, value):
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (20, 10), (0, 60, 255, 180)).save(stamp)
    workspace = embed_workspace(
        pdf_bytes, (Overlay("章-1", 0, str(stamp), (100, 110, 140, 130)),)
    )
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        manifest = json.loads(document.embfile_get(MANIFEST_NAME).decode("utf-8"))
    manifest["overlays"][0][field] = value
    invalid = _embed_with_replaced_manifest(
        workspace, json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    )

    with pytest.raises(EditorError, match="工作層無法驗證"):
        load_workspace(invalid, tmp_path / "assets")


def test_workspace_failure_removes_assets_created_by_this_load(tmp_path, pdf_bytes):
    first = tmp_path / "第一章.png"
    second = tmp_path / "第二章.png"
    Image.new("RGBA", (20, 10), (0, 60, 255, 180)).save(first)
    Image.new("RGBA", (20, 10), (255, 60, 0, 180)).save(second)
    workspace = embed_workspace(
        pdf_bytes,
        (
            Overlay("章-1", 0, str(first), (100, 110, 140, 130)),
            Overlay("章-2", 0, str(second), (150, 110, 190, 130)),
        ),
    )
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        manifest = json.loads(document.embfile_get(MANIFEST_NAME).decode("utf-8"))
    manifest["overlays"][1]["page"] = 1
    invalid = _embed_with_replaced_manifest(
        workspace, json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    )
    asset_root = tmp_path / "assets"

    with pytest.raises(EditorError, match="工作層無法驗證"):
        load_workspace(invalid, asset_root)

    assert not asset_root.exists() or not list(asset_root.iterdir())


@pytest.mark.parametrize(
    "rect",
    [
        [140, 110, 100, 130],
        [100, 130, 140, 110],
        [-1, 110, 140, 130],
        [100, 110, 501, 130],
    ],
)
def test_workspace_with_non_renderable_overlay_rect_is_rejected(
        tmp_path, pdf_bytes, rect):
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (20, 10), (0, 60, 255, 180)).save(stamp)
    workspace = embed_workspace(
        pdf_bytes, (Overlay("章-1", 0, str(stamp), (100, 110, 140, 130)),)
    )
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        manifest = json.loads(document.embfile_get(MANIFEST_NAME).decode("utf-8"))
    manifest["overlays"][0]["rect"] = rect
    invalid = _embed_with_replaced_manifest(
        workspace, json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    )

    with pytest.raises(EditorError, match="工作層無法驗證"):
        load_workspace(invalid, tmp_path / "assets")


def test_workspace_with_unreferenced_attachment_is_rejected(tmp_path, pdf_bytes):
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (20, 10), (0, 60, 255, 180)).save(stamp)
    workspace = embed_workspace(
        pdf_bytes, (Overlay("章-1", 0, str(stamp), (100, 110, 140, 130)),)
    )
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        document.embfile_add("客戶資料.bin", b"customer-attachment", filename="客戶資料.bin")
        changed = document.tobytes(garbage=4, deflate=True)

    with pytest.raises(EditorError, match="工作層無法驗證"):
        load_workspace(changed, tmp_path / "assets")


def test_workspace_with_valid_but_mismatched_overlay_rect_is_rejected(
        tmp_path, pdf_bytes):
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (20, 10), (0, 60, 255, 180)).save(stamp)
    workspace = embed_workspace(
        pdf_bytes, (Overlay("章-1", 0, str(stamp), (100, 110, 140, 130)),)
    )
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        manifest = json.loads(document.embfile_get(MANIFEST_NAME).decode("utf-8"))
    manifest["overlays"][0]["rect"] = [200, 110, 240, 130]
    invalid = _embed_with_replaced_manifest(
        workspace, json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    )

    with pytest.raises(EditorError, match="工作層無法驗證"):
        load_workspace(invalid, tmp_path / "assets")


@pytest.mark.parametrize("change", ["new_page", "same_page_text"])
def test_workspace_with_changed_visible_snapshot_is_rejected(
        tmp_path, pdf_bytes, change):
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (20, 10), (0, 60, 255, 180)).save(stamp)
    workspace = embed_workspace(
        pdf_bytes, (Overlay("章-1", 0, str(stamp), (100, 110, 140, 130)),)
    )
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        if change == "new_page":
            document.new_page(width=500, height=400).insert_text((40, 40), "EXTERNAL PAGE")
        else:
            document[0].insert_text((40, 330), "EXTERNAL TEXT")
        changed = document.tobytes(garbage=4, deflate=True)

    with pytest.raises(EditorError, match="工作層無法驗證"):
        load_workspace(changed, tmp_path / "assets")


def test_workspace_detects_one_native_pixel_resource_translation(tmp_path):
    stripes = Image.new("1", (200, 200))
    shifted = Image.new("1", (200, 200))
    for x in range(200):
        for y in range(200):
            stripes.putpixel((x, y), x % 2)
            shifted.putpixel((x, y), (x - 1) % 2)
    stripe_path = tmp_path / "交錯直條.png"
    shifted_path = tmp_path / "平移一直條.png"
    stripes.save(stripe_path)
    shifted.save(shifted_path)
    with pymupdf.open() as base:
        page = base.new_page(width=300, height=300)
        page.insert_image(
            (100, 100, 150, 150), filename=stripe_path, keep_proportion=False
        )
        base_pdf = base.tobytes(garbage=4, deflate=True)
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (10, 10), (0, 60, 255, 255)).save(stamp)
    workspace = embed_workspace(
        base_pdf, (Overlay("章", 0, str(stamp), (20, 20, 30, 30)),)
    )
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        before_pixels = document[0].get_pixmap(alpha=True).samples
        stripe_xref = next(
            image[0] for image in document[0].get_images(full=True)
            if image[2:4] == (200, 200)
        )
        document[0].replace_image(stripe_xref, filename=shifted_path)
        assert document[0].get_pixmap(alpha=True).samples == before_pixels
        modified = document.tobytes(garbage=4, deflate=True)

    with pytest.raises(EditorError, match="工作層無法驗證"):
        load_workspace(modified, tmp_path / "assets")


@pytest.mark.parametrize("change", ["add", "delete"])
def test_workspace_rejects_external_annotation_set_changes(tmp_path, change):
    with pymupdf.open() as base:
        page = base.new_page(width=300, height=220)
        page.add_text_annot((60, 60), "ORIGINAL NOTE")
        base_pdf = base.tobytes(garbage=4, deflate=True)
    stamp = tmp_path / "章.png"
    Image.new("RGBA", (10, 10), (0, 60, 255, 255)).save(stamp)
    workspace = embed_workspace(
        base_pdf, (Overlay("章", 0, str(stamp), (180, 140, 200, 160)),)
    )
    with pymupdf.open(stream=workspace, filetype="pdf") as document:
        page = document[0]
        if change == "add":
            page.add_text_annot((100, 60), "ADDED NOTE")
        else:
            page.delete_annot(next(page.annots()))
        modified = document.tobytes(garbage=4, deflate=True)

    with pytest.raises(EditorError, match="工作層無法驗證"):
        load_workspace(modified, tmp_path / "assets")
