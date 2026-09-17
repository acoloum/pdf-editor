"""浮在畫布右上角的全文搜尋列，按 Ctrl+F 顯示、Esc 關閉。"""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QToolButton

from pdf_editor.ui.style import glyph_icon


class SearchBar(QFrame):
    closed = Signal()

    MARGIN = 12

    def __init__(self, host, previous_action, next_action):
        super().__init__(host)
        self.setObjectName("searchBar")
        self.host = host
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.setSpacing(4)
        icon = QLabel()
        icon.setPixmap(glyph_icon("search", 16).pixmap(16, 16))
        layout.addWidget(icon)
        self.input = QLineEdit()
        self.input.setPlaceholderText("搜尋全文，按 Enter")
        self.input.setMinimumWidth(220)
        self.input.installEventFilter(self)
        layout.addWidget(self.input)
        self.count = QLabel("0 / 0")
        self.count.setMinimumWidth(48)
        self.count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.count)
        for action in (previous_action, next_action):
            button = QToolButton()
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            layout.addWidget(button)
        close = QToolButton()
        close.setText("✕")
        close.setToolTip("關閉搜尋（Esc）")
        close.clicked.connect(self.close_bar)
        layout.addWidget(close)
        host.installEventFilter(self)
        self.hide()

    def open_bar(self):
        self.show()
        self.raise_()
        self.reposition()
        self.input.setFocus()
        self.input.selectAll()

    def close_bar(self):
        self.hide()
        self.closed.emit()

    def reposition(self):
        self.adjustSize()
        x = max(self.MARGIN, self.host.width() - self.width() - self.MARGIN - 14)
        self.move(x, self.MARGIN)

    def eventFilter(self, watched, event):
        if watched is self.host and event.type() == QEvent.Type.Resize and self.isVisible():
            self.reposition()
        if (watched is self.input and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape):
            self.close_bar()
            return True
        return super().eventFilter(watched, event)
