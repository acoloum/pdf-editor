from pathlib import Path

import pymupdf
import pytest

from pdf_editor.errors import EditorError
from pdf_editor.search import find_text
from pdf_editor.page_images import export_pages_as_png


def _three_page_pdf():
    with pymupdf.open() as doc:
        for index in range(3):
            page=doc.new_page(width=300,height=200)
            page.insert_text((40,80),f"PAGE {index+1} SEARCH",fontsize=18)
        return doc.tobytes()


def test_find_text_returns_page_and_rectangle_for_every_match():
    matches=find_text(_three_page_pdf(),"SEARCH")

    assert [match.page for match in matches]==[0,1,2]
    assert all(match.rect[2]>match.rect[0] and match.rect[3]>match.rect[1]
        for match in matches)


def test_find_text_rejects_empty_query():
    with pytest.raises(EditorError) as error:
        find_text(_three_page_pdf(),"  ")

    assert error.value.code=="SEARCH"


def test_export_selected_pages_as_png_uses_actual_page_numbers(tmp_path):
    paths=export_pages_as_png(_three_page_pdf(),(0,2),tmp_path,"報告",144)

    assert [path.name for path in paths]==["報告-第001頁.png","報告-第003頁.png"]
    pix=pymupdf.Pixmap(str(paths[0]))
    assert (pix.width,pix.height)==(600,400)


def test_export_pages_as_png_does_not_overwrite_existing_file(tmp_path):
    existing=tmp_path/"報告-第001頁.png"
    existing.write_bytes(b"existing")

    with pytest.raises(EditorError) as error:
        export_pages_as_png(_three_page_pdf(),(0,1),tmp_path,"報告",150)

    assert error.value.code=="EXISTS"
    assert existing.read_bytes()==b"existing"
    assert not (tmp_path/"報告-第002頁.png").exists()
