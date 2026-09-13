import csv
import io
import math
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from .engine.fonts import default_font
from .errors import EditorError


OCR_LANGUAGE = "chi_tra+eng"
_TSV_COLUMNS = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext"
)
@dataclass(frozen=True)
class OcrWord:
    text: str
    confidence: float
    pixel_rect: tuple[int, int, int, int]


@dataclass(frozen=True)
class OcrResult:
    pdf: bytes
    processed_pages: tuple[int, ...]
    skipped_pages: tuple[int, ...]
    word_count: int


def parse_tsv(tsv: str, minimum_confidence: float = 35.0) -> tuple[OcrWord, ...]:
    required = {"left", "top", "width", "height", "conf", "text"}
    words: list[OcrWord] = []

    try:
        reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError

        for row in reader:
            if not row or None in row:
                raise ValueError
            left = int(row["left"])
            top = int(row["top"])
            width = int(row["width"])
            height = int(row["height"])
            confidence = float(row["conf"])
            if not math.isfinite(confidence) or width <= 0 or height <= 0:
                raise ValueError
            text_value = row["text"]
            if text_value is None:
                raise ValueError
            text = text_value.strip()
            if not text:
                continue
            if confidence >= minimum_confidence:
                words.append(OcrWord(text, confidence, (left, top, width, height)))
    except (csv.Error, KeyError, TypeError, ValueError):
        raise EditorError("OCR_OUTPUT", "OCR 引擎回傳的資料格式無效。")

    return tuple(words)


def recognize_image(image: Image.Image, tessdata: Path, language: str = OCR_LANGUAGE) -> str:
    """以封裝的 Tesseract 引擎辨識單張 RGB 圖像。"""
    try:
        import tesserocr

        with tesserocr.PyTessBaseAPI(path=str(tessdata), lang=language) as api:
            api.SetImage(image)
            api.Recognize()
            tsv = api.GetTSVText(0)
            # Windows 版 Tesseract 5.5.2 的 API 輸出可能只有資料列。
            first_line = tsv.splitlines()[0] if tsv.splitlines() else ""
            if first_line.split("\t", 1)[0].isdigit():
                return _TSV_COLUMNS + "\n" + tsv
            return tsv
    except Exception as exc:
        raise EditorError("OCR_ENGINE", "OCR 引擎無法啟動，請重新安裝墨頁 PDF。") from exc


def _validate_pages(pages: tuple[int, ...], page_count: int) -> tuple[int, ...]:
    selected = tuple(pages)
    if (not selected or len(selected) != len(set(selected)) or
            any(not isinstance(page, int) or not 0 <= page < page_count for page in selected)):
        raise EditorError("RANGE", "選取頁碼無效。")
    return selected


def _page_has_text(page: pymupdf.Page) -> bool:
    return len("".join(page.get_text().split())) >= 10


def _pixel_point_to_page(page: pymupdf.Page, left: float, top: float,
        scale: float) -> pymupdf.Point:
    return pymupdf.Point(left / scale, top / scale) * page.derotation_matrix


def _insert_invisible_words(page: pymupdf.Page, words: tuple[OcrWord, ...], scale: float) -> None:
    font_path = default_font()
    page.insert_font(fontname="ocrnoto", fontfile=str(font_path))
    font = pymupdf.Font(fontfile=str(font_path))
    for word in words:
        left, top, width, height = word.pixel_rect
        size = height / scale / (font.ascender - font.descender)
        text_width = font.text_length(word.text, fontsize=size)
        available_width = width / scale
        if text_width > available_width:
            size *= available_width / text_width
        baseline = _pixel_point_to_page(page, left,
            top + font.ascender * size * scale, scale)
        page.insert_text(baseline, word.text, fontname="ocrnoto", fontsize=size,
            render_mode=3, overlay=True, rotate=page.rotation)


def ocr_pages(pdf: bytes, pages: tuple[int, ...], tessdata: Path, dpi: int = 300,
        recognizer=recognize_image) -> OcrResult:
    """在記憶體副本完成所有頁面的 OCR，成功後才回傳新的 PDF。"""
    try:
        resolution = float(dpi)
    except (TypeError, ValueError) as exc:
        raise EditorError("DPI", "解析度不是有效數值。") from exc
    if not math.isfinite(resolution) or resolution <= 0:
        raise EditorError("DPI", "解析度不是有效數值。")

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        selected = _validate_pages(pages, doc.page_count)
        scale = resolution / 72
        processed: list[int] = []
        skipped: list[int] = []
        word_count = 0

        for page_number in selected:
            page = doc[page_number]
            if _page_has_text(page):
                skipped.append(page_number)
                continue

            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            try:
                words = parse_tsv(recognizer(image, tessdata, OCR_LANGUAGE))
            finally:
                image.close()
            if not words:
                raise EditorError("OCR_EMPTY", "OCR 找不到可用文字，請確認掃描品質。")
            _insert_invisible_words(page, words, scale)
            processed.append(page_number)
            word_count += len(words)

        if not processed:
            return OcrResult(pdf, (), tuple(skipped), 0)
        return OcrResult(doc.tobytes(garbage=4, deflate=True), tuple(processed),
            tuple(skipped), word_count)
