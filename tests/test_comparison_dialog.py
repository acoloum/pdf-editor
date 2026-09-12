import io

import pytest
from PIL import Image

from pdf_editor.comparison import PageComparison
from pdf_editor.ui.comparison_dialog import ComparisonDialog


def png_bytes(size, color):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def page_comparison():
    return PageComparison(
        base_png=png_bytes((800, 200), "white"),
        comparison_png=png_bytes((200, 800), "gray"),
        difference_png=png_bytes((320, 240), "red"),
        similarity=87.5,
        changed_pixels=125,
        total_pixels=1000,
        base_page=0,
        comparison_page=2,
    )


def test_comparison_dialog_shows_three_images_and_similarity(qtbot, page_comparison):
    dialog = ComparisonDialog(3, 0)
    qtbot.addWidget(dialog)

    dialog.set_result(page_comparison)

    assert dialog.base_image.pixmap() is not None
    assert dialog.comparison_image.pixmap() is not None
    assert dialog.difference_image.pixmap() is not None
    assert dialog.summary.text() == "相似度 87.5%｜差異 125 / 1,000 像素"


def test_comparison_page_spin_emits_zero_based_page(qtbot):
    dialog = ComparisonDialog(3, 0)
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(dialog.page_requested) as signal:
        dialog.page_spin.setValue(3)

    assert signal.args == [2]


def test_comparison_dialog_builds_requested_layout_and_scales_previews(qtbot, page_comparison):
    dialog = ComparisonDialog(3, 1)
    qtbot.addWidget(dialog)

    dialog.set_result(page_comparison)

    assert dialog.size().width() == 1320
    assert dialog.size().height() == 760
    assert dialog.base_page_label.text() == "基準：第 2 頁"
    assert dialog.page_spin.minimum() == 1
    assert dialog.page_spin.maximum() == 3
    assert [label.text() for label in dialog.column_titles] == [
        "目前頁面", "比較頁面", "差異標示"]
    assert dialog.base_image.pixmap().size().toTuple() == (400, 100)
    assert dialog.comparison_image.pixmap().size().toTuple() == (200, 800)
    assert dialog.difference_image.pixmap().size().toTuple() == (320, 240)
    assert all(not label.hasScaledContents() for label in (
        dialog.base_image, dialog.comparison_image, dialog.difference_image))


def test_busy_state_disables_page_choice_until_result_arrives(qtbot, page_comparison):
    dialog = ComparisonDialog(3, 0)
    qtbot.addWidget(dialog)

    dialog.set_busy(True)

    assert not dialog.page_spin.isEnabled()
    assert dialog.summary.text() == "正在比較頁面…"

    dialog.set_result(page_comparison)

    assert dialog.page_spin.isEnabled()


def test_invalid_png_clears_all_previews_and_reports_error(qtbot, page_comparison):
    dialog = ComparisonDialog(3, 0)
    qtbot.addWidget(dialog)
    dialog.set_result(page_comparison)
    invalid_result = PageComparison(
        base_png=b"not a PNG",
        comparison_png=page_comparison.comparison_png,
        difference_png=page_comparison.difference_png,
        similarity=page_comparison.similarity,
        changed_pixels=page_comparison.changed_pixels,
        total_pixels=page_comparison.total_pixels,
        base_page=page_comparison.base_page,
        comparison_page=page_comparison.comparison_page,
    )

    dialog.set_result(invalid_result)

    assert dialog.base_image.pixmap().isNull()
    assert dialog.comparison_image.pixmap().isNull()
    assert dialog.difference_image.pixmap().isNull()
    assert dialog.summary.text() == "無法顯示頁面比較影像。"
    assert dialog.page_spin.isEnabled()


def test_zero_page_comparison_document_has_no_selectable_page(qtbot):
    dialog = ComparisonDialog(0, 0)
    qtbot.addWidget(dialog)

    assert not dialog.page_spin.isEnabled()
    assert dialog.summary.text() == "比較文件沒有可用頁面。"
