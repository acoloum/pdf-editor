import io

import pymupdf
import pytest
from PIL import Image

from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.errors import EditorError
from pdf_editor.legacy_overlay_conversion import (
    convert_legacy_image,
    find_convertible_images,
)


def _png(size, color):
    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf_with_unique_stamp_and_reused_logo():
    stamp = _png((60, 40), (0, 60, 255, 255))
    logo = _png((20, 20), (255, 50, 50, 255))
    with pymupdf.open() as document:
        first = document.new_page(width=500, height=400)
        first.insert_image((210, 260, 270, 300), stream=stamp)
        first.insert_image((20, 20, 40, 40), stream=logo)
        second = document.new_page(width=500, height=400)
        second.insert_image((20, 20, 40, 40), stream=logo)
        return document.tobytes(garbage=4, deflate=True)


def _pdf_with_unique_stamp():
    stamp = _png((60, 40), (0, 60, 255, 255))
    with pymupdf.open() as document:
        page = document.new_page(width=500, height=400)
        page.insert_image((210, 260, 270, 300), stream=stamp)
        return document.tobytes(garbage=4, deflate=True)


def _pdf_with_reused_image():
    stamp = _png((60, 40), (0, 60, 255, 255))
    with pymupdf.open() as document:
        first = document.new_page(width=500, height=400)
        first.insert_image((210, 260, 270, 300), stream=stamp)
        second = document.new_page(width=500, height=400)
        second.insert_image((210, 260, 270, 300), stream=stamp)
        return document.tobytes(garbage=4, deflate=True)


def _single_scanned_page_pdf():
    scan = _png((500, 400), (0, 60, 255, 255))
    with pymupdf.open() as document:
        page = document.new_page(width=500, height=400)
        page.insert_image((0, 0, 500, 400), stream=scan)
        return document.tobytes(garbage=4, deflate=True)


def _pdf_with_image_drawn_twice_at_same_rect():
    stamp = _png((60, 40), (0, 60, 255, 255))
    with pymupdf.open() as document:
        page = document.new_page(width=500, height=400)
        page.insert_image((210, 260, 270, 300), stream=stamp)
        page.insert_image((210, 260, 270, 300), stream=stamp)
        return document.tobytes(garbage=4, deflate=True)


def _asymmetric_rgba_png():
    image = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    for x in range(40):
        for y in range(20):
            if x < 13:
                image.putpixel((x, y), (0, 40, 255, 230))
            elif y < 7:
                image.putpixel((x, y), (255, 40, 0, 120))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf_with_soft_mask_stamp():
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=220)
        page.insert_image((80, 70, 160, 110), stream=_asymmetric_rgba_png())
        return document.tobytes(garbage=4, deflate=True)


def _pdf_with_rotated_asymmetric_stamp():
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=220)
        page.insert_image(
            (80, 50, 120, 130), stream=_asymmetric_rgba_png(), rotate=90
        )
        return document.tobytes(garbage=4, deflate=True)


def _pdf_with_vector_covering_stamp():
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=220)
        page.insert_image((80, 70, 160, 110), stream=_png((80, 40), (0, 40, 255, 255)))
        page.draw_rect((110, 65, 170, 115), color=None, fill=(0, 1, 0))
        return document.tobytes(garbage=4, deflate=True)


def _render_samples(pdf):
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        pixmap = document[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        return pixmap.width, pixmap.height, pixmap.samples


def _blue_stamp_pixels(pdf):
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        pixmap = document[0].get_pixmap(alpha=False)
        samples = memoryview(pixmap.samples)
        return sum(
            samples[index] < 10 and 50 <= samples[index + 1] <= 70 and samples[index + 2] > 245
            for index in range(0, len(samples), pixmap.n)
        )


def test_finds_single_use_stamp_but_rejects_logo_reused_on_two_pages():
    candidates = find_convertible_images(_pdf_with_unique_stamp_and_reused_logo())

    assert [(item.page, item.rect) for item in candidates] == [(0, (210, 260, 270, 300))]
    assert candidates[0].width == 60
    assert candidates[0].height == 40


def test_convert_legacy_stamp_removes_base_image_and_returns_overlay(tmp_path):
    source = _pdf_with_unique_stamp()
    candidate = find_convertible_images(source)[0]

    base_pdf, layer = convert_legacy_image(source, candidate, tmp_path / "assets")

    assert _blue_stamp_pixels(base_pdf) == 0
    assert _blue_stamp_pixels(flatten_overlays(base_pdf, (layer,))) > 0
    assert layer.rect == candidate.rect


def test_full_page_scan_is_not_a_convertible_stamp():
    assert find_convertible_images(_single_scanned_page_pdf()) == ()


def test_same_image_drawn_twice_at_same_rect_is_not_a_convertible_stamp():
    assert find_convertible_images(_pdf_with_image_drawn_twice_at_same_rect()) == ()


def test_stale_candidate_cannot_change_pdf(tmp_path):
    stale_candidate = find_convertible_images(_pdf_with_unique_stamp())[0]

    with pytest.raises(EditorError, match="候選已變更") as error:
        convert_legacy_image(_pdf_with_reused_image(), stale_candidate, tmp_path / "assets")

    assert error.value.code == "STAMP_CONVERSION"
    assert not (tmp_path / "assets").exists()


def test_soft_mask_stamp_conversion_preserves_rendered_appearance(tmp_path):
    source = _pdf_with_soft_mask_stamp()
    candidate = find_convertible_images(source)[0]

    base_pdf, layer = convert_legacy_image(source, candidate, tmp_path / "assets")
    reconstructed = flatten_overlays(base_pdf, (layer,))

    original = _render_samples(source)
    converted = _render_samples(reconstructed)
    assert converted[:2] == original[:2]
    assert max(abs(a - b) for a, b in zip(converted[2], original[2])) <= 2


def test_rotated_asymmetric_image_is_not_offered_for_conversion():
    assert find_convertible_images(_pdf_with_rotated_asymmetric_stamp()) == ()


def test_image_covered_by_later_vector_is_not_offered_for_conversion():
    assert find_convertible_images(_pdf_with_vector_covering_stamp()) == ()
