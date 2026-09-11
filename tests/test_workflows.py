import hashlib
import io

import pymupdf
from PIL import Image

from pdf_editor.document.save import save_as
from pdf_editor.document.session import DocumentSession
from pdf_editor.engine.text import replace_text
from pdf_editor.model import Overlay
from pdf_editor.pages import split_pages
from test_text import request_for


def test_文字修改圖章儲存復原重做及拆分(source_path, font_path, tmp_path):
    原始雜湊 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    圖章路徑 = tmp_path / "圖章.png"
    圖片 = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    for x in range(5, 20):
        for y in range(4, 16):
            圖片.putpixel((x, y), (180, 20, 20, 230))
    圖片.save(圖章路徑)

    with DocumentSession.open(source_path) as 工作階段:
        已修改 = replace_text(
            工作階段.pdf,
            request_for(工作階段.pdf, font_path),
        )
        工作階段.apply_pdf(已修改)
        圖章 = Overlay("stamp", 0, str(圖章路徑), (220, 210, 300, 250), 0)
        工作階段.set_overlays((圖章,))

        第一份 = save_as(工作階段, tmp_path / "第一份.pdf")
        工作階段.undo()
        assert 工作階段.overlays == ()
        工作階段.redo()
        assert 工作階段.overlays == (圖章,)
        第二份 = save_as(工作階段, tmp_path / "第二份.pdf")

    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == 原始雜湊
    with pymupdf.open(第一份) as a, pymupdf.open(第二份) as b:
        assert "檢測合格" in a[0].get_text()
        assert a[0].get_pixmap().samples == b[0].get_pixmap().samples
        拆分結果 = split_pages(第二份.read_bytes(), ((0,),))
    with pymupdf.open(stream=拆分結果[0], filetype="pdf") as doc:
        assert doc.page_count == 1
        assert "檢測合格" in doc[0].get_text()
