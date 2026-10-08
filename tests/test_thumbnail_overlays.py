"""縮圖要畫出該頁的圖層（圖章、簽名、轉成圖層的既有圖片），並在圖層變動後更新。"""
import io
from dataclasses import replace

import pymupdf
import pytest
from PIL import Image, ImageChops

from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.engine.render import thumbnail
from pdf_editor.model import Overlay
from pdf_editor.ui.main_window import MainWindow
import pdf_editor.ui.main_window as main_window
import pdf_editor.ui.page_actions as page_actions


@pytest.fixture
def red_stamp(tmp_path):
    path = tmp_path / "紅章.png"
    Image.new("RGBA", (80, 40), (255, 0, 0, 255)).save(path)
    return path


@pytest.fixture
def white_backed_stamp(tmp_path):
    """白底紅框的掃描章：去除白底後才看得到底下的頁面內容。"""
    path = tmp_path / "白底章.png"
    image = Image.new("RGBA", (100, 60), (255, 255, 255, 255))
    for x in range(100):
        for y in range(60):
            if x < 8 or x >= 92 or y < 8 or y >= 52:
                image.putpixel((x, y), (200, 20, 20, 255))
    image.save(path)
    return path


def _rgb(png):
    with Image.open(io.BytesIO(png)) as image:
        return image.convert("RGB")


def _pixel(png, x, y):
    with Image.open(io.BytesIO(png)) as image:
        return image.convert("RGB").getpixel((x, y))


def _is_red(rgb):
    red, green, blue = rgb
    return red > 200 and green < 80 and blue < 80


def _thumb_point(page_x, page_y, page_size=500):
    # 縮圖長邊為 110 像素；測試頁面為 500×400。
    scale = 110 / page_size
    return round(page_x * scale), round(page_y * scale)


def test_thumbnail_draws_layers_on_that_page(source_path, red_stamp):
    layer = Overlay("章", 0, str(red_stamp), (200, 200, 280, 240))
    point = _thumb_point(240, 220)

    assert not _is_red(_pixel(thumbnail(str(source_path), 0), *point))
    assert _is_red(_pixel(thumbnail(str(source_path), 0, (layer,)), *point))


def test_thumbnail_ignores_layers_of_other_pages(multi_page_path, red_stamp):
    other = Overlay("別頁章", 1, str(red_stamp), (100, 60, 180, 100))
    assert thumbnail(str(multi_page_path), 0, (other,)) == thumbnail(str(multi_page_path), 0)


@pytest.mark.parametrize("page_rotation", (0, 90))
@pytest.mark.parametrize("angle,remove_white", ((0, False), (0, True), (30, False), (30, True)))
def test_thumbnail_matches_flattened_output(tmp_path, pdf_bytes, white_backed_stamp,
        page_rotation, angle, remove_white):
    """縮圖與輸出共用同一套轉換規則（含旋轉角度與去除白底）。"""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        document[0].set_rotation(page_rotation)
        pdf = document.tobytes()
    path = tmp_path / "旋轉頁.pdf"
    path.write_bytes(pdf)
    layer = Overlay("章", 0, str(white_backed_stamp), (60, 50, 160, 110), angle, remove_white)

    expected = _rgb(thumbnail(flatten_overlays(pdf, (layer,)), 0))
    actual = _rgb(thumbnail(str(path), 0, (layer,)))
    assert actual.size == expected.size
    # 平面化會重新壓縮整份 PDF，文字反鋸齒可能差 1～2 階；圖層本身必須一致。
    assert max(ImageChops.difference(actual, expected).getextrema(), key=lambda e: e[1])[1] <= 3
    # 確認比對的區域確實有畫上圖層（避免兩邊都沒畫而誤判一致）。
    assert ImageChops.difference(actual, _rgb(thumbnail(str(path), 0))).getbbox() is not None


def test_remove_white_shows_page_content_in_thumbnail(source_path, white_backed_stamp):
    # 圖層中央是白色，蓋在頁面淺藍色圖片上；去除白底後縮圖應看得到淺藍色。
    layer = Overlay("章", 0, str(white_backed_stamp), (60, 50, 160, 110))
    point = _thumb_point(110, 80)

    covered = _pixel(thumbnail(str(source_path), 0, (layer,)), *point)
    knocked_out = _pixel(thumbnail(str(source_path), 0, (replace(layer, remove_white=True),)), *point)

    assert covered == (255, 255, 255)
    assert knocked_out != (255, 255, 255)
    assert knocked_out[2] > knocked_out[0]


# ── 介面：縮圖工作帶入圖層，圖層變動後重新產生受影響頁面的縮圖 ──

def _run_jobs_synchronously(monkeypatch):
    """背景工作改為同步執行，並記錄每次縮圖的頁碼、圖層與產生的 PNG。"""
    thumbnails = []

    def submit(self, function, arguments, success, failure):
        try:
            result = function(*arguments)
        except Exception as exc:
            failure((getattr(exc, "code", "ERROR"), str(exc), ()))
            return
        if function is page_actions.thumbnail:
            thumbnails.append((arguments[1], tuple(arguments[2]), result))
        success(result)

    monkeypatch.setattr(main_window.Jobs, "submit", submit)
    return thumbnails


def _open(qtbot, path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_document(path)
    return window


def _close(window):
    if window.session is not None:
        window.discard_pending_conversion()
        window.session.saved_fingerprint = window.session.history.current[2]
    window.close()


def _layers_on(window, page):
    return tuple(layer for layer in window.session.overlays if layer.page == page)


def _add_layer(window, layer):
    window.session.set_overlays(window.session.overlays + (layer,))
    window.select_layer(layer.id)
    window.request_render()


def test_thumbnail_jobs_receive_only_that_pages_layers(qtbot, multi_page_path, red_stamp,
        monkeypatch):
    thumbnails = _run_jobs_synchronously(monkeypatch)
    window = _open(qtbot, multi_page_path)
    try:
        first = Overlay("一", 0, str(red_stamp), (20, 20, 100, 60))
        second = Overlay("二", 1, str(red_stamp), (30, 30, 110, 70))
        window.session.set_overlays((first, second))
        thumbnails.clear()

        window.queue_thumbnails()

        assert sorted((page, layers) for page, layers, _png in thumbnails) == [
            (0, (first,)), (1, (second,)), (2, ())]
    finally:
        _close(window)


def test_moving_layer_refreshes_its_page_thumbnail(qtbot, multi_page_path, red_stamp,
        monkeypatch):
    thumbnails = _run_jobs_synchronously(monkeypatch)
    window = _open(qtbot, multi_page_path)
    try:
        window.goto_page(1)
        layer = Overlay("章", 1, str(red_stamp), (20, 20, 100, 60))
        _add_layer(window, layer)
        thumbnails.clear()

        moved = replace(layer, rect=(180, 120, 260, 160))
        window.move_layer(moved)

        assert [(page, layers) for page, layers, _png in thumbnails] == [(1, (moved,))]
        # 縮圖上的章要出現在新位置（300×200 頁面，縮圖寬 110 像素）。
        png = thumbnails[0][2]
        assert _is_red(_pixel(png, *_thumb_point(220, 140, 300)))
        assert not _is_red(_pixel(png, *_thumb_point(60, 40, 300)))
    finally:
        _close(window)


def test_toggling_remove_white_refreshes_thumbnail(qtbot, multi_page_path, white_backed_stamp,
        monkeypatch):
    thumbnails = _run_jobs_synchronously(monkeypatch)
    window = _open(qtbot, multi_page_path)
    try:
        layer = Overlay("章", 0, str(white_backed_stamp), (20, 20, 120, 80))
        _add_layer(window, layer)
        thumbnails.clear()

        window.set_layer_remove_white(True)

        assert [(page, layers) for page, layers, _png in thumbnails] == [
            (0, (replace(layer, remove_white=True),))]
    finally:
        _close(window)


def test_deleting_layer_refreshes_its_page_thumbnail(qtbot, multi_page_path, red_stamp,
        monkeypatch):
    thumbnails = _run_jobs_synchronously(monkeypatch)
    window = _open(qtbot, multi_page_path)
    try:
        window.goto_page(2)
        _add_layer(window, Overlay("章", 2, str(red_stamp), (20, 20, 100, 60)))
        thumbnails.clear()

        window.delete_layer()

        assert [(page, layers) for page, layers, _png in thumbnails] == [(2, ())]
    finally:
        _close(window)


def test_importing_stamp_refreshes_current_page_thumbnail(qtbot, multi_page_path, red_stamp,
        monkeypatch):
    thumbnails = _run_jobs_synchronously(monkeypatch)
    window = _open(qtbot, multi_page_path)
    try:
        thumbnails.clear()

        window.import_layer(red_stamp, False)

        assert [(page, layers) for page, layers, _png in thumbnails] == [
            (0, window.session.overlays)]
    finally:
        _close(window)


def test_undo_and_redo_refresh_thumbnail_layers(qtbot, multi_page_path, red_stamp,
        monkeypatch):
    thumbnails = _run_jobs_synchronously(monkeypatch)
    window = _open(qtbot, multi_page_path)
    try:
        layer = Overlay("章", 0, str(red_stamp), (20, 20, 100, 60))
        _add_layer(window, layer)
        moved = replace(layer, rect=(180, 120, 260, 160))
        window.move_layer(moved)
        thumbnails.clear()

        window.history_step(False)
        assert (0, (layer,)) in [(page, layers) for page, layers, _png in thumbnails]

        thumbnails.clear()
        window.history_step(True)
        assert (0, (moved,)) in [(page, layers) for page, layers, _png in thumbnails]
    finally:
        _close(window)


def _image_page_pdf(tmp_path):
    """一頁含一張可點選編輯的既有圖片。"""
    buffer = io.BytesIO()
    Image.new("RGB", (60, 40), (0, 60, 255)).save(buffer, format="PNG")
    path = tmp_path / "含圖片.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=500, height=400)
        page.insert_text((40, 80), "KEEP ME", fontsize=16)
        page.insert_image((210, 260, 270, 300), stream=buffer.getvalue())
        path.write_bytes(document.tobytes(garbage=4, deflate=True))
    return path


def test_clicked_image_conversion_and_revert_refresh_thumbnail(qtbot, tmp_path, monkeypatch):
    thumbnails = _run_jobs_synchronously(monkeypatch)
    window = _open(qtbot, _image_page_pdf(tmp_path))
    try:
        window.canvas.image_clicked.emit(window.page_data["images"][0])
        layer = window.session.overlays[0]
        assert (0, (layer,)) in [(page, layers) for page, layers, _png in thumbnails]

        thumbnails.clear()
        window.canvas.background_clicked.emit()
        assert window.session.overlays == ()
        assert [(page, layers) for page, layers, _png in thumbnails] == [(0, ())]
    finally:
        _close(window)
