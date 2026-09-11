import pytest

from pdf_editor.errors import EditorError
from pdf_editor.templates import HeaderFooterTemplateStore


def test_header_footer_templates_can_save_replace_reload_and_delete(tmp_path):
    path=tmp_path/"頁首頁尾.json"
    store=HeaderFooterTemplateStore(path)

    store.save("公司頁尾","內部文件  {page}/{pages}","bottom_center",10)
    store.save("公司頁尾","品管文件  {page}/{pages}","bottom_right",9)

    loaded=HeaderFooterTemplateStore(path)
    assert loaded.all()==({"name":"公司頁尾","text":"品管文件  {page}/{pages}",
        "position":"bottom_right","font_size":9.0},)
    loaded.delete("公司頁尾")
    assert HeaderFooterTemplateStore(path).all()==()


def test_header_footer_template_rejects_empty_content(tmp_path):
    store=HeaderFooterTemplateStore(tmp_path/"頁首頁尾.json")

    with pytest.raises(EditorError) as error:
        store.save("空白範本","  ","top_left",10)

    assert error.value.code=="TEMPLATE"
