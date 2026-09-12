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
license_overrides={"cysignals":"LGPL-3.0-or-later"}
source_overrides={
    "tesserocr":["Source, https://github.com/sirfz/tesserocr"],
    "cysignals":["Source, https://github.com/sagemath/cysignals"],
}
for name in ["PyMuPDF","PySide6-Essentials","shiboken6","Pillow","fonttools","psutil","PyInstaller",
             "tesserocr","cysignals"]:
    distribution=metadata.distribution(name)
    component_dest=dest/name
    if component_dest.exists():
        shutil.rmtree(component_dest)
    entry={"name":name,"version":distribution.version,
        "license":license_overrides.get(name, distribution.metadata.get("License-Expression") or distribution.metadata.get("License")),
        "sources":source_overrides.get(name, distribution.metadata.get_all("Project-URL",[]))}
    manifest.append(entry)
    for item in distribution.files or []:
        if any(word in item.name.lower() for word in ("license","copying","notice")):
            path=Path(distribution.locate_file(item))
            if path.is_file():
                target=dest/name/str(item).replace("/", "_").replace("\\","_")
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(path,target)
shutil.copy2(Path(sys.base_prefix)/"LICENSE.txt",dest/"Python-LICENSE.txt")
leptonica_license=dest/"leptonica-license.txt"
leptonica_license_sha256="4d3065116f182e29760af0c901d5dbb2e1e16c42765dfc24e69b26805e2acb1e"
if not leptonica_license.is_file():
    raise FileNotFoundError("找不到 Leptonica 1.87.0 的授權文字。")
if hashlib.sha256(leptonica_license.read_bytes()).hexdigest() != leptonica_license_sha256:
    raise ValueError("Leptonica 1.87.0 的授權文字雜湊不符。")
inno_license=root.parent/"inno-setup/License.txt"
if inno_license.is_file():
    shutil.copy2(inno_license,dest/"Inno-Setup-6.4.3.txt")
urls={
    "AGPL-3.0.txt":"https://www.gnu.org/licenses/agpl-3.0.txt",
    "LGPL-3.0.txt":"https://www.gnu.org/licenses/lgpl-3.0.txt",
    "GPL-3.0.txt":"https://www.gnu.org/licenses/gpl-3.0.txt",
    "MuPDF-COPYING":"https://raw.githubusercontent.com/ArtifexSoftware/mupdf/1.28.2/COPYING",
}
for name,url in urls.items():
    if (dest/name).is_file():
        continue
    try:
        with urllib.request.urlopen(url,timeout=45) as response:
            (dest/name).write_bytes(response.read())
    except Exception as exc:
        print(name,type(exc).__name__)
font=root/"resources/fonts/NotoSansCJKtc-Regular.otf"
manifest.append({"name":"NotoSansCJKtc-Regular.otf","license":"SIL OFL 1.1",
    "sha256":hashlib.sha256(font.read_bytes()).hexdigest(),
    "source":"https://github.com/notofonts/noto-cjk/tree/main/Sans/OTF/TraditionalChinese"})
manifest.extend([
    {"name":"Tesseract OCR","version":"5.5.2","license":"Apache-2.0",
     "source":"https://github.com/tesseract-ocr/tesseract"},
    {"name":"Leptonica","version":"1.87.0","license":"BSD-2-Clause",
     "sha256":leptonica_license_sha256,
     "source":"http://www.leptonica.org/"},
    {"name":"tessdata_fast","commit":"87416418657359cb625c412a48b6e1d6d41c29bd",
     "license":"Apache-2.0","source":"https://github.com/tesseract-ocr/tessdata_fast",
     "models":{
         "chi_tra.traineddata":"529c5b5797d64b126065cd55f2bb4c7fd7b15790798091b1ff259941a829330b",
         "eng.traineddata":"7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
     }},
])
(dest/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
