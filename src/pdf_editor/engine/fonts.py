import os
from pathlib import Path
import sys
import pymupdf
from pdf_editor.errors import EditorError

def resource_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))

def default_font():
    return resource_root() / "resources/fonts/NotoSansCJKtc-Regular.otf"

def checked_font(path, text):
    try:
        font = pymupdf.Font(fontfile=str(path))
    except Exception as exc:
        raise EditorError("FONT_INVALID", "無法讀取字型檔。") from exc
    if any(not font.has_glyph(ord(c)) for c in text if not c.isspace()):
        raise EditorError("FONT_MISSING_GLYPH", "字型缺少部分字元，請更換字型。")
    return font

def _roundtrips_text(path, text):
    """確認嵌入字型重新寫入後仍能保留相同的 Unicode 文字。"""
    try:
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_font(fontname="probe", fontfile=str(path))
            page.insert_text((40, 60), text, fontname="probe", fontsize=12)
            return page.get_text().strip() == text.strip()
    except Exception:
        return False

def embedded_font(pdf, page, run, folder):
    import hashlib
    def normalized(name):
        return name.split('+')[-1].replace(' ', '').replace('-', '').lower()
    with pymupdf.open(stream=pdf, filetype='pdf') as doc:
        for item in doc[page].get_fonts(full=True):
            if normalized(item[3]) != normalized(run.font_name):
                continue
            name, extension, kind, content = doc.extract_font(item[0])
            if content and extension in ('otf', 'ttf', 'cff'):
                path = Path(folder) / (hashlib.sha256(content).hexdigest() + '.' + extension)
                path.write_bytes(content)
                try:
                    checked_font(path, run.text)
                    if _roundtrips_text(path, run.text):
                        return path
                except EditorError:
                    pass
    return None


def _font_registry_values():
    """回傳登錄檔字型鍵的（名稱, 檔名）序列；HKCU 優先並覆蓋同名。"""
    import winreg
    keys = (
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    )
    entries = {}
    for hive, subkey in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                index = 0
                while True:
                    try:
                        name, filename, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    entries[name] = filename
        except OSError:
            continue
    return list(entries.items())


def _font_directories():
    r"""回傳候選字型目錄（%WINDIR%\Fonts 與 %LOCALAPPDATA% 使用者字型）。"""
    root = Path(os.environ.get("WINDIR", r"C:\Windows"))
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"
    return (root / "Fonts", local)


def _clean_font_name(name):
    """去除登錄檔名稱的括號註記，如「細明體 (TrueType)」→「細明體」。"""
    return name.split("(")[0].strip()


def system_fonts():
    """列舉 Windows 已安裝字型。回傳（顯示名稱, 絕對路徑），依名稱排序。"""
    if sys.platform != "win32":
        return []
    result = {}
    try:
        raw_values = _font_registry_values()
    except Exception:
        return []
    for raw_name, filename in raw_values:
        name = _clean_font_name(raw_name)
        if not name:
            continue
        path = Path(filename) if os.path.isabs(filename) else next(
            (base / filename for base in _font_directories()
             if (base / filename).exists()), None)
        if path is None or path.suffix.lower() not in (".ttf", ".otf", ".ttc"):
            continue
        result[name] = str(path)
    return sorted(result.items())


_STYLE_SUFFIXES = ("Bold Italic", "ExtraBold", "Bold", "Italic",
    "Semibold", "Light", "Medium", "Black")


def _base_family(name):
    """剝除名稱尾部的既定變體詞，取得家族名；由長到短避免殘留片語。"""
    lowered = name.lower()
    for suffix in _STYLE_SUFFIXES:
        if lowered.endswith(suffix.lower()):
            return name[:-len(suffix)].strip()
    return name


def resolve_bold(base_path):
    """尋找 base_path 的同族粗體檔；已是粗體回傳自身；找不到回傳 None。"""
    base = str(base_path)
    fonts_list = system_fonts()
    if any(path == base and "bold" in name.lower() for name, path in fonts_list):
        return base
    family = next((name for name, path in fonts_list if path == base), None)
    if family:
        index = {name.lower(): path for name, path in fonts_list}
        candidate = index.get((_base_family(family) + " bold").lower())
        if candidate and os.path.exists(candidate):
            return candidate
    directory = Path(base).parent
    if not directory.is_dir():
        return None
    base_stem = Path(base).stem.lower()
    for candidate in directory.iterdir():
        if not candidate.is_file() or candidate == Path(base):
            continue
        if candidate.suffix.lower() not in (".ttf", ".otf", ".ttc"):
            continue
        stem = candidate.stem.lower()
        if stem.startswith(base_stem) and ("bold" in stem or stem.endswith(("bd", "b"))):
            return str(candidate)
    return None



