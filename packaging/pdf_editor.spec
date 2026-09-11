from pathlib import Path
root = Path(SPECPATH).parent
a = Analysis([str(root / "packaging/launcher.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(root / "resources"), "resources"), (str(root / "licenses"), "licenses"),
           (str(root / "docs"), "docs")],
    hiddenimports=["fontTools.subset", "fontTools.ttLib", "PIL.PngImagePlugin"],
    excludes=["PySide6.QtWebEngineCore","PySide6.QtQml","PySide6.QtQuick",
              "tkinter","pytest","pypdf"],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name="LocalPDFEditor",
    console=False,debug=False,upx=False)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name="LocalPDFEditor")

