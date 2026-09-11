import math
import os
import tempfile
from pathlib import Path

import pymupdf

from pdf_editor.errors import EditorError


def export_pages_as_png(pdf,pages,folder,stem,dpi=150):
    target_folder=Path(folder)
    if not target_folder.is_dir():
        raise EditorError("FOLDER","輸出資料夾不存在。")
    try:
        resolution=float(dpi)
    except (TypeError,ValueError) as exc:
        raise EditorError("DPI","解析度不是有效數值。") from exc
    if not math.isfinite(resolution) or not 72<=resolution<=600:
        raise EditorError("DPI","PNG 解析度必須介於 72 至 600 DPI。")
    selected=tuple(pages)
    temporary=[]
    completed=[]
    try:
        with pymupdf.open(stream=pdf,filetype="pdf") as doc:
            if (not selected or len(selected)!=len(set(selected)) or
                    any(not isinstance(page,int) or not 0<=page<doc.page_count
                        for page in selected)):
                raise EditorError("RANGE","選取頁碼無效。")
            targets=tuple(target_folder/f"{stem}-第{page+1:03}頁.png" for page in selected)
            if any(path.exists() for path in targets):
                raise EditorError("EXISTS","PNG 輸出檔已存在，請更換資料夾或移除舊檔。")
            scale=resolution/72
            for page,target in zip(selected,targets):
                pix=doc[page].get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False)
                fd,name=tempfile.mkstemp(prefix=".pdf-editor-",suffix=".png",
                    dir=target_folder)
                os.close(fd)
                temp=Path(name)
                temporary.append(temp)
                temp.write_bytes(pix.tobytes("png"))
            for temp,target in zip(temporary,targets):
                os.rename(temp,target)
                completed.append(target)
            return tuple(completed)
    except EditorError:
        raise
    except Exception as exc:
        raise EditorError("IMAGE_EXPORT","頁面圖片輸出失敗，請檢查資料夾權限與可用空間。") from exc
    finally:
        for temp in temporary:
            temp.unlink(missing_ok=True)
