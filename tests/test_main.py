import pytest

from pdf_editor.__main__ import (
    _acceptance_report,
    _finish_acceptance,
    _recognize_for_acceptance,
)


def test_acceptance_report_records_search_comparison_and_revision():
    report = _acceptance_report("材料", 3, "相似度 99.3%", 1, 1)

    assert "搜尋文字=材料" in report
    assert "搜尋命中=3" in report
    assert "比較結果=相似度 99.3%" in report
    assert "revision=1 -> 1" in report


@pytest.mark.parametrize(
    ("match_count", "revision_before", "revision_after"),
    ((0, 1, 1), (3, 1, 2)),
)
def test_acceptance_report_rejects_failed_acceptance(
        match_count, revision_before, revision_after):
    with pytest.raises(AssertionError):
        _acceptance_report("材料", match_count, "相似度 99.3%",
                           revision_before, revision_after)


def test_finish_acceptance_closes_dirty_smoke_session_without_prompt():
    class Session:
        saved_fingerprint = "舊值"
        history = type("History", (), {"current": (b"pdf", (), "新值")})()

        @property
        def dirty(self):
            return self.saved_fingerprint != self.history.current[2]

    class Window:
        session = Session()

        def close(self):
            assert self.session.saved_fingerprint == "新值"
            self.closed = True

    class App:
        def exit(self, code):
            self.exit_code = code

    window = Window()
    app = App()

    _finish_acceptance(window, app, 0)

    assert window.closed
    assert app.exit_code == 0


def test_recognize_for_acceptance_runs_packaged_ocr_and_writes_pdf(
        monkeypatch, tmp_path):
    source = tmp_path / "scan.pdf"
    output = tmp_path / "ocr.pdf"
    source.write_bytes(b"scan")

    class Result:
        pdf = b"searchable"
        word_count = 7

    monkeypatch.setattr("pdf_editor.ocr_assets.validate_ocr_assets",
                        lambda: tmp_path / "tessdata")
    monkeypatch.setattr(
        "pdf_editor.ocr.ocr_pages",
        lambda pdf, pages, tessdata: Result()
        if (pdf, pages, tessdata) == (b"scan", (0,), tmp_path / "tessdata")
        else pytest.fail("OCR 參數不正確"),
    )

    word_count = _recognize_for_acceptance(source, output)

    assert word_count == 7
    assert output.read_bytes() == b"searchable"
