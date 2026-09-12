"""頁面比較結果的三欄預覽視窗。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pdf_editor.comparison import PageComparison


class ComparisonDialog(QDialog):
    """顯示基準頁、比較頁與差異標示。"""

    page_requested = Signal(int)

    def __init__(self, comparison_page_count: int, base_page: int, parent=None):
        super().__init__(parent)
        self.comparison_page_count = max(0, comparison_page_count)
        self.setWindowTitle("頁面比較")
        self.resize(1320, 760)

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.base_page_label = QLabel(f"基準：第 {base_page + 1} 頁")
        controls.addWidget(self.base_page_label)
        controls.addWidget(QLabel("比較：第"))
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, max(1, self.comparison_page_count))
        self.page_spin.setEnabled(self.comparison_page_count > 0)
        controls.addWidget(self.page_spin)
        controls.addWidget(QLabel("頁"))
        controls.addStretch()
        self.summary = QLabel()
        controls.addWidget(self.summary)
        layout.addLayout(controls)

        columns = QHBoxLayout()
        self.column_titles = []
        self.scroll_areas = []
        images = []
        for title in ("目前頁面", "比較頁面", "差異標示"):
            column = QWidget()
            column_layout = QVBoxLayout(column)
            title_label = QLabel(title)
            title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column_layout.addWidget(title_label)

            image = QLabel()
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setScaledContents(False)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
            scroll.setWidget(image)
            column_layout.addWidget(scroll)

            self.column_titles.append(title_label)
            self.scroll_areas.append(scroll)
            images.append(image)
            columns.addWidget(column, 1)
        layout.addLayout(columns, 1)

        self.base_image, self.comparison_image, self.difference_image = images
        if self.comparison_page_count == 0:
            self.summary.setText("比較文件沒有可用頁面。")
        self.page_spin.valueChanged.connect(self._request_page)

    def _request_page(self, page: int):
        if self.comparison_page_count > 0:
            self.page_requested.emit(page - 1)

    def set_busy(self, busy: bool):
        """切換比較作業進行中的控制狀態。"""
        self.page_spin.setEnabled(not busy and self.comparison_page_count > 0)
        if busy:
            self.summary.setText("正在比較頁面…")

    def set_result(self, result: PageComparison):
        """載入三張 PNG 預覽並顯示比較摘要。"""
        pixmaps = []
        for data in (result.base_png, result.comparison_png, result.difference_png):
            pixmap = QPixmap()
            if not pixmap.loadFromData(data) or pixmap.isNull():
                self._show_invalid_image_error()
                return
            if pixmap.width() > 400:
                pixmap = pixmap.scaledToWidth(
                    400, Qt.TransformationMode.SmoothTransformation)
            pixmaps.append(pixmap)

        for label, pixmap in zip((self.base_image, self.comparison_image,
                                  self.difference_image), pixmaps):
            label.setPixmap(pixmap)
        self.summary.setText(
            f"相似度 {result.similarity:.1f}%｜差異 "
            f"{result.changed_pixels:,} / {result.total_pixels:,} 像素")
        self.page_spin.setEnabled(self.comparison_page_count > 0)

    def _show_invalid_image_error(self):
        for label in (self.base_image, self.comparison_image, self.difference_image):
            label.clear()
        self.summary.setText("無法顯示頁面比較影像。")
        self.page_spin.setEnabled(self.comparison_page_count > 0)
