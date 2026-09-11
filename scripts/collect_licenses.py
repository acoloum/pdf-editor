from pathlib import Path
from importlib import metadata
import shutil
import urllib.request
import hashlib
import json
import sys

root=Path(__file__).resolve().parents[1]
dest=root/"licenses"
dest.mkdir(exist_ok=True)
manifest=[]
for name in ["PyMuPDF","PySide6-Essentials","shiboken6","Pillow","fonttools","psutil","PyInstaller"]:
    distribution=metadata.distribution(name)
    component_dest=dest/name
    if component_dest.exists():
        shutil.rmtree(component_dest)
    entry={"name":name,"version":distribution.version,
        "license":distribution.metadata.get("License-Expression") or distribution.metadata.get("License"),
        "sources":distribution.metadata.get_all("Project-URL",[])}
    manifest.append(entry)
    for item in distribution.files or []:
        if any(word in item.name.lower() for word in ("license","copying","notice")):
            path=Path(distribution.locate_file(item))
            if path.is_file():
                target=dest/name/str(item).replace("/", "_").replace("\\","_")
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(path,target)
shutil.copy2(Path(sys.base_prefix)/"LICENSE.txt",dest/"Python-LICENSE.txt")
shutil.copy2(root.parent/"inno-setup/License.txt",dest/"Inno-Setup-6.4.3.txt")
urls={
    "AGPL-3.0.txt":"https://www.gnu.org/licenses/agpl-3.0.txt",
    "LGPL-3.0.txt":"https://www.gnu.org/licenses/lgpl-3.0.txt",
    "GPL-3.0.txt":"https://www.gnu.org/licenses/gpl-3.0.txt",
    "MuPDF-COPYING":"https://raw.githubusercontent.com/ArtifexSoftware/mupdf/1.28.2/COPYING",
}
for name,url in urls.items():
    try:
        with urllib.request.urlopen(url,timeout=45) as response:
            (dest/name).write_bytes(response.read())
    except Exception as exc:
        print(name,type(exc).__name__)
font=root/"resources/fonts/NotoSansCJKtc-Regular.otf"
manifest.append({"name":"NotoSansCJKtc-Regular.otf","license":"SIL OFL 1.1",
    "sha256":hashlib.sha256(font.read_bytes()).hexdigest(),
    "source":"https://github.com/notofonts/noto-cjk/tree/main/Sans/OTF/TraditionalChinese"})
(dest/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
