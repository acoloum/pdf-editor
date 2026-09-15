import os
import sys
import pytest
from pdf_editor.engine import fonts
from pdf_editor.engine.text import extract_runs


def test_embedded_font_is_rejected_when_unicode_cannot_roundtrip(
    pdf_bytes, tmp_path, monkeypatch
):
    run = next(item for item in extract_runs(pdf_bytes, 0) if "品質" in item.text)
    monkeypatch.setattr(fonts, "_roundtrips_text", lambda path, text: False)

    assert fonts.embedded_font(pdf_bytes, 0, run, tmp_path) is None


@pytest.mark.skipif(sys.platform != "win32", reason="僅 Windows 有登錄檔字型索引")
def test_system_fonts_returns_existing_font_files():
    fonts_list = fonts.system_fonts()
    assert fonts_list
    for name, path in fonts_list:
        assert name.strip()
        assert path.lower().endswith((".ttf", ".otf", ".ttc"))
        assert os.path.exists(path)


def test_system_fonts_filters_fon_and_missing(monkeypatch):
    fake = [("點陣字 (TrueType)", "vgasys.fon"),
        ("系統字型 (TrueType)", "missing.ttf")]
    monkeypatch.setattr(fonts, "_font_registry_values", lambda: fake)
    monkeypatch.setattr(fonts.sys, "platform", "win32")
    assert fonts.system_fonts() == []


def test_system_fonts_resolves_relative_path(monkeypatch, tmp_path):
    font_file = tmp_path / "demo.ttf"
    font_file.write_bytes(b"fake")
    monkeypatch.setattr(fonts, "_font_registry_values",
        lambda: [("Demo Font (TrueType)", "demo.ttf")])
    monkeypatch.setattr(fonts, "_font_directories", lambda: [tmp_path])
    monkeypatch.setattr(fonts.sys, "platform", "win32")
    assert fonts.system_fonts() == [("Demo Font", str(font_file))]


def test_system_fonts_handles_registry_error(monkeypatch):
    def boom():
        raise OSError("拒絕存取")
    monkeypatch.setattr(fonts, "_font_registry_values", boom)
    monkeypatch.setattr(fonts.sys, "platform", "win32")
    assert fonts.system_fonts() == []


def test_resolve_bold_finds_family_bold(monkeypatch):
    monkeypatch.setattr(fonts, "system_fonts", lambda: [
        ("Demo Sans", r"C:\Windows\Fonts\demo.ttf"),
        ("Demo Sans Bold", r"C:\Windows\Fonts\demobd.ttf")])
    monkeypatch.setattr(fonts.os.path, "exists", lambda p: p == r"C:\Windows\Fonts\demobd.ttf")
    assert fonts.resolve_bold(r"C:\Windows\Fonts\demo.ttf") == r"C:\Windows\Fonts\demobd.ttf"


def test_resolve_bold_already_bold_returns_base(monkeypatch):
    monkeypatch.setattr(fonts, "system_fonts",
        lambda: [("Demo Sans Bold", r"C:\Windows\Fonts\demobd.ttf")])
    assert fonts.resolve_bold(r"C:\Windows\Fonts\demobd.ttf") == r"C:\Windows\Fonts\demobd.ttf"

def test_resolve_bold_returns_none_without_bold(monkeypatch):
    monkeypatch.setattr(fonts, "system_fonts",
        lambda: [("Demo Serif", r"C:\Windows\Fonts\demoserif.ttf")])
    assert fonts.resolve_bold(r"C:\Windows\Fonts\demoserif.ttf") is None

def test_resolve_bold_none_when_bold_file_missing(monkeypatch):
    monkeypatch.setattr(fonts, "system_fonts", lambda: [
        ("Demo Sans", r"C:\Windows\Fonts\demo.ttf"),
        ("Demo Sans Bold", r"C:\Windows\Fonts\missingbold.ttf")])
    assert fonts.resolve_bold(r"C:\Windows\Fonts\demo.ttf") is None


