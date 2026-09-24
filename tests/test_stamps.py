"""圖章：沿用上次大小與多頁連續蓋章。"""

import pymupdf
import pytest
from PIL import Image

from pdf_editor.ui.main_window import MainWindow
from pdf_editor.ui.settings import AppSettings
from pdf_editor.ui.stamp_pages_dialog import StampPagesDialog


@pytest.fixture
def stamp_png(tmp_path):
    path = tmp_path / "印章.png"
    Image.new("RGBA", (200, 100), (210, 35, 45, 210)).save(path)
    return path


def _open(qtbot, path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_document(path)
    qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
    return window


def _close(window):
    if window.session is not None:
        window.session.saved_fingerprint = window.session.history.current[2]
    window.close()


def test_new_stamp_uses_the_last_adjusted_size(qtbot, multi_page_path, stamp_png):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(stamp_png, False)
        first = window.session.overlays[-1]
        # 使用者調整大小與位置後，尺寸會被記住。
        resized = first.__class__(first.id, first.page, first.asset_path, (60, 70, 160, 120), 0)
        window.move_layer(resized)
        qtbot.waitUntil(lambda: not window.busy, timeout=30000)
        assert AppSettings().stamp_geometry(first.asset_path) == (60, 70, 100, 50)

        window.goto_page(1)
        window.import_layer(stamp_png, False)
        second = window.session.overlays[-1]
        assert second.page == 1
        assert second.rect == pytest.approx((60, 70, 160, 120))
    finally:
        _close(window)


def test_new_stamp_keeps_image_ratio_when_reusing_width(qtbot, multi_page_path, stamp_png, tmp_path):
    window = _open(qtbot, multi_page_path)
    tall = tmp_path / "直式章.png"
    Image.new("RGBA", (100, 200), (20, 120, 200, 210)).save(tall)
    try:
        window.import_layer(stamp_png, False)
        wide = window.session.overlays[-1]
        window.move_layer(wide.__class__(wide.id, wide.page, wide.asset_path, (20, 20, 80, 50), 0))
        qtbot.waitUntil(lambda: not window.busy, timeout=30000)

        window.import_layer(tall, False)
        layer = window.session.overlays[-1]
        width = layer.rect[2] - layer.rect[0]
        height = layer.rect[3] - layer.rect[1]
        # 沿用寬度，高度依新圖的比例重新計算（放不下時才會再縮）。
        assert width == pytest.approx(60)
        assert height == pytest.approx(120)
    finally:
        _close(window)


def test_stamp_pages_dialog_page_selection(qtbot):
    dialog = StampPagesDialog(5, 1, (0, 2, 4))
    qtbot.addWidget(dialog)
    assert dialog.pages() == (0, 2, 4)
    dialog.all_pages.setChecked(True)
    assert dialog.pages() == (0, 1, 2, 3, 4)
    dialog.after_current.setChecked(True)
    assert dialog.pages() == (2, 3, 4)
    dialog.range_pages.setChecked(True)
    dialog.first.setValue(4)
    dialog.last.setValue(2)
    assert dialog.pages() == (1, 2, 3)
    assert dialog.first.isEnabled()
    dialog.all_pages.setChecked(True)
    assert not dialog.first.isEnabled()


def test_stamp_to_pages_copies_layer_to_every_page(qtbot, multi_page_path, stamp_png, monkeypatch):
    window = _open(qtbot, multi_page_path)
    try:
        window.import_layer(stamp_png, False)
        layer = window.session.overlays[-1]
        monkeypatch.setattr(StampPagesDialog, "exec", lambda self: self.all_pages.setChecked(True) or 1)

        window.stamp_to_pages()

        pages = sorted(item.page for item in window.session.overlays)
        assert pages == [0, 1, 2]
        for item in window.session.overlays:
            assert item.asset_path == layer.asset_path
            assert item.rect == layer.rect
        qtbot.waitUntil(lambda: "已在 2 頁蓋上相同圖章" in window.statusBar().currentMessage(),
            timeout=30000)
    finally:
        _close(window)


def test_stamp_to_pages_skips_duplicates_and_pages_that_are_too_small(
        qtbot, tmp_path, stamp_png, monkeypatch):
    doc = pymupdf.open()
    doc.new_page(width=400, height=300)
    doc.new_page(width=400, height=300)
    doc.new_page(width=80, height=60)
    path = tmp_path / "混合尺寸.pdf"
    path.write_bytes(doc.tobytes())
    doc.close()
    window = _open(qtbot, path)
    try:
        window.import_layer(stamp_png, False)
        monkeypatch.setattr(StampPagesDialog, "exec", lambda self: self.all_pages.setChecked(True) or 1)
        window.stamp_to_pages()
        assert sorted(item.page for item in window.session.overlays) == [0, 1]
        qtbot.waitUntil(lambda: "超出頁面範圍 1 頁（第 3 頁）" in window.statusBar().currentMessage(),
            timeout=30000)

        # 再執行一次：已有相同圖章的頁面會被略過。
        window.select_layer(window.session.overlays[0].id)
        window.stamp_to_pages()
        assert "已有相同圖章" in window.statusBar().currentMessage()
        assert len(window.session.overlays) == 2
    finally:
        _close(window)


def test_stamp_pages_action_requires_a_selected_stamp(qtbot, multi_page_path, stamp_png):
    window = _open(qtbot, multi_page_path)
    try:
        assert not window.actions["stamp_pages"].isEnabled()
        window.import_layer(stamp_png, False)
        window.refresh_actions()
        assert window.actions["stamp_pages"].isEnabled()
        assert window.overlay_panel.stamp_pages.isEnabled()
        assert window.actions["stamp_pages"] in window.stamp_menu_button.menu().actions()
    finally:
        _close(window)
