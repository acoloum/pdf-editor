import pytest

from pdf_editor.errors import EditorError
from pdf_editor.ocr import OcrWord, parse_tsv


def test_parse_tsv_returns_word_above_minimum_confidence():
    tsv = "left\ttop\twidth\theight\tconf\ttext\n40\t60\t120\t80\t92.4\t材料\n"

    assert parse_tsv(tsv) == (OcrWord("材料", 92.4, (40, 60, 120, 80)),)


def test_parse_tsv_drops_word_below_minimum_confidence():
    tsv = "left\ttop\twidth\theight\tconf\ttext\n40\t60\t120\t80\t34.9\t材料\n"

    assert parse_tsv(tsv) == ()


def test_parse_tsv_rejects_missing_required_columns():
    tsv = "left\ttop\twidth\theight\tconf\n40\t60\t120\t80\t92.4\n"

    with pytest.raises(EditorError) as error:
        parse_tsv(tsv)

    assert error.value.code == "OCR_OUTPUT"
