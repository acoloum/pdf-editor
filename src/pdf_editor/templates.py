import json
import math
from pathlib import Path

from pdf_editor.errors import EditorError
from pdf_editor.page_decorations import POSITIONS


class HeaderFooterTemplateStore:
    def __init__(self,path):
        self.path=Path(path)

    def all(self):
        try:
            raw=json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError,OSError,json.JSONDecodeError):
            return ()
        result=[]
        for item in raw if isinstance(raw,list) else ():
            try:
                name=str(item["name"]).strip()
                text=str(item["text"]).strip()
                position=str(item["position"])
                size=float(item["font_size"])
            except (KeyError,TypeError,ValueError):
                continue
            if name and text and position in POSITIONS and math.isfinite(size) and 4<=size<=72:
                result.append({"name":name,"text":text,"position":position,
                    "font_size":size})
        return tuple(result)

    def save(self,name,text,position,font_size):
        name=str(name).strip()
        text=str(text).strip()
        try:
            size=float(font_size)
        except (TypeError,ValueError) as exc:
            raise EditorError("TEMPLATE","範本字級無效。") from exc
        if not name or not text or position not in POSITIONS or not math.isfinite(size) or not 4<=size<=72:
            raise EditorError("TEMPLATE","請填寫範本名稱、文字、位置與有效字級。")
        items=[item for item in self.all() if item["name"]!=name]
        items.append({"name":name,"text":text,"position":position,"font_size":size})
        self._write(items)

    def delete(self,name):
        self._write([item for item in self.all() if item["name"]!=str(name)])

    def _write(self,items):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix(self.path.suffix+".tmp")
        try:
            temporary.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding="utf-8")
            temporary.replace(self.path)
        except OSError as exc:
            raise EditorError("TEMPLATE","無法儲存頁首頁尾範本。") from exc
