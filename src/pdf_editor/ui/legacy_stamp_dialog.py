"""選取可安全轉換為工作層的既有 PDF 影像。"""

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class LegacyStampDialog(QDialog):
    """要求使用者明確選取一個可安全抽離的影像。"""

    def __init__(self, candidates, parent=None):
        super().__init__(parent)
        self._candidates = tuple(candidates)
        self.setWindowTitle("轉換既有圖章")
        self.resize(620, 430)

        layout = QVBoxLayout(self)
        explanation = QLabel(
            "僅列出可安全轉換、且在整份文件中只出現一次的單一影像。"
            "Logo、整頁掃描與重複使用的影像不會列出。"
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("hint")
        layout.addWidget(explanation)

        self.list = QListWidget()
        self.list.setIconSize(QSize(120, 90))
        for candidate in self._candidates:
            pixmap = QPixmap()
            pixmap.loadFromData(candidate.png)
            x0, y0, x1, y1 = candidate.rect
            item = QListWidgetItem(
                QIcon(pixmap),
                f"第 {candidate.page + 1} 頁｜X {x0:g}｜Y {y0:g}｜"
                f"寬 {x1 - x0:g}｜高 {y1 - y0:g}",
            )
            self.list.addItem(item)
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        self.ok_button = QPushButton("轉換為可編輯圖章")
        self.ok_button.setObjectName("primary")
        self.ok_button.setEnabled(False)
        self.ok_button.clicked.connect(self.accept)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)

        self.list.currentRowChanged.connect(
            lambda row: self.ok_button.setEnabled(0 <= row < len(self._candidates))
        )

    @property
    def selected_candidate(self):
        """回傳目前明確選取的候選；尚未選取時回傳 ``None``。"""
        row = self.list.currentRow()
        return self._candidates[row] if 0 <= row < len(self._candidates) else None
