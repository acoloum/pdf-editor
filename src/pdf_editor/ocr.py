import csv
import io
import math
from dataclasses import dataclass

from .errors import EditorError


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
        reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
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
            text = row["text"].strip()
            if not text:
                continue
            if confidence >= minimum_confidence:
                words.append(OcrWord(text, confidence, (left, top, width, height)))
    except (csv.Error, KeyError, TypeError, ValueError):
        raise EditorError("OCR_OUTPUT", "OCR 引擎回傳的資料格式無效。")

    return tuple(words)
