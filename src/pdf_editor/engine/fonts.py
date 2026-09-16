import hashlib
import os
from pathlib import Path
import sys
import tempfile
import pymupdf
from pdf_editor.errors import EditorError

def resource_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))

def default_font():
    return resource_root() / "resources/fonts/NotoSansCJKtc-Regular.otf"

_SUBSET_CACHE_DIR: Path | None = None

def _subset_cache_dir():
    """回傳字型子集快取目錄（%LOCALAPPDATA%\\LocalPDFEditor\\fontsubsets）；
    避免每次編輯都把完整巨量 CJK 字型嵌入 PDF 再全表子集化。"""
    global _SUBSET_CACHE_DIR
    if _SUBSET_CACHE_DIR is None:
        base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
        _SUBSET_CACHE_DIR = base / "LocalPDFEditor" / "fontsubsets"
        _SUBSET_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _SUBSET_CACHE_DIR

def subset_font(font_path, text):
    """將 font_path 子集化至僅含 text 所需字元，回傳子集檔路徑。

    編輯文字時若直接嵌入完整字型（如 16MB 的 Noto CJK），
    doc.subset_fonts 被迫對巨量字型做全表子集化（通常 1 秒以上）；
    預先以 fontTools 只保留本次文字用到的字元，嵌入的是數 KB 的小字型，
    之後就不需要再呼叫 doc.subset_fonts。子集結果依 (字型, 字元集) 快取，
    同樣文字重複編輯時直接沿用。子集化失敗時回傳原路徑，由呼叫端改走原本的
    完整嵌入＋subset_fonts 流程。
    """
    if not text:
        return str(font_path)
    chars = sorted({c for c in text if not c.isspace()})
    if not chars:
        return str(font_path)
    source = str(font_path)
    key = hashlib.sha256(f"{source}\0{''.join(chars)}".encode("utf-8")).hexdigest()[:24]
    suffix = Path(source).suffix.lower() or ".otf"
    target = _subset_cache_dir() / f"{key}{suffix}"
    if target.exists() and target.stat().st_size > 0:
        return str(target)
    try:
        from fontTools.subset import Options, Subsetter
        from fontTools.ttLib import TTFont
        options = Options()
        options.font_number = 0
        options.notdef_outline = True
        options.recommended_glyphs = False
        options.layout_features = ["*"]
        options.name_IDs = ["*"]
        options.name_legacy = True
        options.drop_tables = ["meta", "FFTM", "FDSC", "fmtx",
            "mort", "morx", "prop", "trak", "gasp"]
        subsetter = Subsetter(options=options)
        font = TTFont(source, fontNumber=0)
        subsetter.populate(text="".join(chars))
        subsetter.subset(font)
        # 先寫臨時檔再以 os.replace 原子搬移，避免多執行緒同時寫入競態。
        fd, tmp = tempfile.mkstemp(suffix=suffix, dir=_subset_cache_dir())
        os.close(fd)
        font.save(tmp)
        font.close()
        os.replace(tmp, target)
        return str(target)
    except Exception:
        try:
            font.close()  # noqa: F821 子集化失敗時確保釋放資源
        except Exception:
            pass
        return source

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



