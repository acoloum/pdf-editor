"""記憶體、重複讀檔與背景工作資料量的回歸測試。"""
import sys

import pymupdf
import pytest

from pdf_editor.document.session import DocumentSession
from pdf_editor.engine import render as render_engine
from pdf_editor.engine import text as text_engine
from pdf_editor.engine.fonts import similar_font, system_fonts
from pdf_editor.ui.main_window import MainWindow


@pytest.fixture
def cell_pdf(tmp_path):
    doc = pymupdf.open()
    for index in range(3):
        page = doc.new_page(width=300, height=200)
        page.draw_rect((20, 20, 120, 50))
        page.insert_text((30, 40), f"CELL {index}", fontsize=10)
    path = tmp_path / "儲存格.pdf"
    path.write_bytes(doc.tobytes())
    doc.close()
    return path


def test_cell_cache_does_not_retain_document_bytes(cell_pdf):
    data = cell_pdf.read_bytes()
    text_engine.clear_cell_cache()
    key = text_engine.document_key(data)
    for page in range(3):
        text_engine.find_table_cell(data, page, (25, 25, 60, 45), key)
    held = sum(sys.getsizeof(item) for item in text_engine._CELL_CACHE)
    # 快取只保留識別碼與頁碼，不保留 PDF 內容。
    assert len(text_engine._CELL_CACHE) <= text_engine._CELL_CACHE_LIMIT
    assert held < len(data)
    assert all(isinstance(entry[0], str) for entry in text_engine._CELL_CACHE)


def test_session_reads_document_once_per_revision(cell_pdf, monkeypatch):
    session = DocumentSession.open(cell_pdf)
    try:
        reads = []
        original = type(session.history.current[0]).read_bytes
        monkeypatch.setattr(type(session.history.current[0]), "read_bytes",
            lambda self: reads.append(self) or original(self))
        first = session.pdf
        again = session.pdf
        assert first is again
        assert len(reads) == 1
        with pymupdf.open(stream=first, filetype="pdf") as doc:
            doc[0].insert_text((40, 120), "NEW", fontsize=10)
            edited = doc.tobytes()
        session.apply_pdf(edited)
        changed = session.pdf
        # 內容變更後才重新讀檔。
        assert len(reads) == 2
        assert changed == edited
    finally:
        session.saved_fingerprint = session.history.current[2]
        session.close()


def test_render_and_thumbnail_accept_a_file_path(cell_pdf):
    # 背景工作可直接讀檔，不必把整份文件複製到另一個程序。
    data = render_engine.render_page(str(cell_pdf), 0, 1.0)
    assert data["count"] == 3
    assert data["runs"]
    assert render_engine.thumbnail(str(cell_pdf), 1)
    assert render_engine.render_page(cell_pdf.read_bytes(), 0, 1.0)["count"] == 3


def test_window_sends_paths_to_background_jobs(qtbot, cell_pdf, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    submitted = []
    original = window.jobs.submit
    monkeypatch.setattr(window.jobs, "submit",
        lambda fn, args, ok, fail: submitted.append((fn.__name__, args)) or original(fn, args, ok, fail))
    try:
        window.open_document(cell_pdf)
        qtbot.waitUntil(lambda: window.page_data is not None, timeout=30000)
        jobs = {name: args for name, args in submitted}
        assert isinstance(jobs["render_page"][0], str)
        assert isinstance(jobs["thumbnail"][0], str)
    finally:
        window.session.saved_fingerprint = window.session.history.current[2]
        window.close()


def test_font_lookups_are_cached():
    system_fonts.cache_clear()
    first = system_fonts()
    assert first is system_fonts()
    assert isinstance(first, tuple)
    if sys.platform == "win32":
        # 相近字型逐字檢查涵蓋範圍很慢，同樣的字集必須沿用結果。
        assert similar_font("TimesNewRomanPSMT", "316") is similar_font("TimesNewRomanPSMT", "613")


def test_icons_are_cached_and_loaded_after_window_is_shown(qtbot):
    from pdf_editor.ui.style import glyph_icon, themed_glyph_icon
    themed_glyph_icon.cache_clear()
    first = glyph_icon("open")
    assert first is glyph_icon("open")
    # 每個圖示只保留一般與停用兩種狀態。
    assert len(first.availableSizes(first.Mode.Normal)) >= 1
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        assert window._pending_icons
        assert window.actions["open"].icon().isNull()
        window.show()
        qtbot.waitUntil(lambda: not window._pending_icons, timeout=5000)
        assert not window.actions["open"].icon().isNull()
    finally:
        window.close()


def test_subset_cache_is_pruned_to_the_limit(monkeypatch, tmp_path):
    from pdf_editor.engine import fonts
    monkeypatch.setattr(fonts, "_SUBSET_CACHE_DIR", tmp_path)
    for index in range(5):
        (tmp_path / f"font{index}.otf").write_bytes(b"x" * 1000)
    removed = fonts.prune_subset_cache(limit=2500)
    remaining = sorted(path.name for path in tmp_path.glob("*"))
    assert removed == 3
    assert len(remaining) == 2


def test_history_limits_total_size(monkeypatch, tmp_path):
    from pdf_editor.document import history as history_module
    monkeypatch.setattr(history_module, "MAX_TOTAL_BYTES", 3000)
    blob = b"%PDF-1.4" + b"0" * 1000
    history = history_module.History(blob)
    try:
        for index in range(6):
            history.push(blob + bytes([index]), ())
        used = sum(item[0].stat().st_size for item in history.items)
        assert used <= 3000
        assert len(history.items) >= 2
        assert history.index == len(history.items) - 1
    finally:
        history.close()
