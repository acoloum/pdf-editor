from pdf_editor.engine import fonts
from pdf_editor.engine.text import extract_runs


def test_embedded_font_is_rejected_when_unicode_cannot_roundtrip(
    pdf_bytes, tmp_path, monkeypatch
):
    run = next(item for item in extract_runs(pdf_bytes, 0) if "品質" in item.text)
    monkeypatch.setattr(fonts, "_roundtrips_text", lambda path, text: False)

    assert fonts.embedded_font(pdf_bytes, 0, run, tmp_path) is None
