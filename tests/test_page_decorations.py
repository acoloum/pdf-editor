import io

import pymupdf
import pytest
from PIL import Image

from pdf_editor.errors import EditorError
from pdf_editor.page_decorations import (crop_pages,add_page_numbers,
    add_text_watermark,add_image_watermark,add_header_footer)


def _three_page_pdf():
    with pymupdf.open() as doc:
        for index in range(3):
            page=doc.new_page(width=300,height=200)
            page.insert_text((40,80),f"PAGE {index+1}",fontsize=18)
        return doc.tobytes()


def test_crop_pages_applies_margins_only_to_selected_pages():
    result=crop_pages(_three_page_pdf(),(0,2),(10,20,30,40))

    with pymupdf.open(stream=result,filetype="pdf") as doc:
        assert (doc[0].cropbox.width,doc[0].cropbox.height)==pytest.approx((260,140))
        assert (doc[1].cropbox.width,doc[1].cropbox.height)==pytest.approx((300,200))
        assert (doc[2].cropbox.width,doc[2].cropbox.height)==pytest.approx((260,140))


def test_crop_pages_rejects_margins_that_remove_entire_page():
    with pytest.raises(EditorError) as error:
        crop_pages(_three_page_pdf(),(0,),(160,0,160,0))

    assert error.value.code=="CROP"


def test_add_page_numbers_uses_selected_page_sequence(font_path):
    result=add_page_numbers(_three_page_pdf(),(0,2),5,"頁碼 ","","bottom_center",12,
        font_path)

    with pymupdf.open(stream=result,filetype="pdf") as doc:
        texts=[page.get_text().replace("\xa0"," ") for page in doc]
        assert "頁碼 5" in texts[0]
        assert "頁碼" not in texts[1]
        assert "頁碼 6" in texts[2]


def test_add_page_numbers_rejects_unknown_position(font_path):
    with pytest.raises(EditorError) as error:
        add_page_numbers(_three_page_pdf(),(0,),1,"","","middle",10,font_path)

    assert error.value.code=="POSITION"


def test_add_header_footer_replaces_document_page_fields(font_path):
    result=add_header_footer(_three_page_pdf(),(0,2),
        "檢驗報告  第 {page} / {pages} 頁","top_center",11,font_path)

    with pymupdf.open(stream=result,filetype="pdf") as doc:
        texts=[page.get_text().replace("\xa0"," ") for page in doc]
        assert "檢驗報告  第 1 / 3 頁" in texts[0]
        assert "檢驗報告" not in texts[1]
        assert "檢驗報告  第 3 / 3 頁" in texts[2]


def test_add_text_watermark_inserts_image_only_on_selected_pages(font_path):
    result=add_text_watermark(_three_page_pdf(),(0,2),"內部文件",32,0.25,-45,font_path)

    with pymupdf.open(stream=result,filetype="pdf") as doc:
        assert doc[0].get_images(full=True)
        assert not doc[1].get_images(full=True)
        assert doc[2].get_images(full=True)


def test_add_image_watermark_centers_transparent_image(tmp_path):
    image=Image.new("RGBA",(120,60),(20,100,180,220))
    path=tmp_path/"watermark.png"
    image.save(path)

    result=add_image_watermark(_three_page_pdf(),(1,),path,40,0.3,20)

    with pymupdf.open(stream=result,filetype="pdf") as doc:
        assert not doc[0].get_images(full=True)
        assert doc[1].get_images(full=True)
        assert not doc[2].get_images(full=True)


def test_add_image_watermark_rejects_missing_file(tmp_path):
    with pytest.raises(EditorError) as error:
        add_image_watermark(_three_page_pdf(),(0,),tmp_path/"missing.png",40,0.3,0)

    assert error.value.code=="IMAGE"
