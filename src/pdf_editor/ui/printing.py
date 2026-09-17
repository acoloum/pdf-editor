"""以點陣方式列印 PDF 頁面，圖章與簽名會先合併到頁面上。"""
import pymupdf
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QTransform

MAX_PRINT_DPI = 300


def print_pages(printer, pdf, pages, progress=None):
    """將指定頁面輸出到印表機；progress(index, total) 回傳 False 時中止。

    回傳實際送出的頁數。
    """
    pages = list(pages)
    if not pages:
        return 0
    painter = QPainter()
    if not painter.begin(printer):
        raise RuntimeError("無法開始列印，請確認印表機設定。")
    printed = 0
    try:
        dpi = max(72, min(MAX_PRINT_DPI, printer.resolution()))
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            for index, page_index in enumerate(pages):
                if progress is not None and progress(index, len(pages)) is False:
                    break
                if index:
                    printer.newPage()
                page = doc[page_index]
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                image = QImage(pix.samples, pix.width, pix.height, pix.stride,
                    QImage.Format.Format_RGB888).copy()
                target = QRectF(printer.pageLayout().paintRectPixels(printer.resolution()))
                target.moveTo(0, 0)
                # 頁面方向與紙張方向不同時自動旋轉，讓內容盡量放大。
                if (image.width() > image.height()) != (target.width() > target.height()):
                    image = image.transformed(QTransform().rotate(90), Qt.TransformationMode.SmoothTransformation)
                scale = min(target.width() / image.width(), target.height() / image.height())
                width, height = image.width() * scale, image.height() * scale
                rect = QRectF(target.x() + (target.width() - width) / 2,
                    target.y() + (target.height() - height) / 2, width, height)
                painter.drawImage(rect, image)
                printed += 1
    finally:
        painter.end()
    return printed
