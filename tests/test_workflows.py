import hashlib
import io
from dataclasses import replace

import pymupdf
from PIL import Image

from pdf_editor.document.save import save_as
from pdf_editor.document.session import DocumentSession
from pdf_editor.engine.text import replace_text
from pdf_editor.legacy_overlay_conversion import (
    convert_legacy_image,
    find_convertible_images,
)
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


def test_轉換另存關閉重開移動並再次匯出(tmp_path):
    """防止舊章重開後失去工作層，或移動後在原位置留下殘影。"""
    圖章路徑 = tmp_path / "舊統編章.png"
    Image.new("RGBA", (30, 20), (0, 40, 255, 255)).save(圖章路徑)
    舊文件路徑 = tmp_path / "舊章.pdf"
    with pymupdf.open() as 文件:
        頁面 = 文件.new_page(width=500, height=400)
        頁面.insert_text((40, 60), "LEGACY STAMP CONVERSION", fontsize=16)
        頁面.insert_image((200, 200, 230, 220), filename=圖章路徑)
        舊文件路徑.write_bytes(文件.tobytes())

    with DocumentSession.open(舊文件路徑) as 工作階段:
        候選 = find_convertible_images(工作階段.pdf)[0]
        基底, 圖層 = convert_legacy_image(
            工作階段.pdf,
            候選,
            工作階段.history.root / "assets",
        )
        工作階段.apply_state(基底, (圖層,))
        第一份路徑 = save_as(工作階段, tmp_path / "第一次另存.pdf")

    with DocumentSession.open(第一份路徑) as 重開工作階段:
        assert len(重開工作階段.overlays) == 1
        已移動 = replace(
            重開工作階段.overlays[0],
            rect=(300, 200, 330, 220),
        )
        重開工作階段.set_overlays((已移動,))
        第二份路徑 = save_as(重開工作階段, tmp_path / "第二次另存.pdf")

    with pymupdf.open(第二份路徑) as 輸出文件:
        像素圖 = 輸出文件[0].get_pixmap(alpha=False)
        assert 像素圖.pixel(215, 210)[:3] == (255, 255, 255)
        assert 像素圖.pixel(315, 210)[:3] == (0, 40, 255)
