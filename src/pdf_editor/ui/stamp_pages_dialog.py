"""選擇要連續蓋章的頁面範圍。"""
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QRadioButton, QSpinBox, QVBoxLayout)


class StampPagesDialog(QDialog):
    def __init__(self, page_count, current_page, selected_pages=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("蓋章到多個頁面")
        self.page_count = page_count
        self.current_page = current_page
        self.selected_pages = tuple(selected_pages)
        layout = QVBoxLayout(self)
        hint = QLabel("以目前圖章的大小、角度與位置，蓋到選擇的頁面。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.group = QButtonGroup(self)
        self.all_pages = QRadioButton("全部頁面")
        self.after_current = QRadioButton(f"第 {current_page + 1} 頁之後的所有頁面")
        self.range_pages = QRadioButton("指定範圍")
        self.thumbnail_pages = QRadioButton(
            f"左側縮圖已選取的 {len(self.selected_pages)} 頁")
        self.thumbnail_pages.setEnabled(len(self.selected_pages) > 1)
        for index, button in enumerate((self.all_pages, self.after_current, self.range_pages,
                self.thumbnail_pages)):
            self.group.addButton(button, index)
            layout.addWidget(button)
        row = QHBoxLayout()
        self.first = QSpinBox()
        self.last = QSpinBox()
        for spin, value in ((self.first, current_page + 1), (self.last, page_count)):
            spin.setRange(1, page_count)
            spin.setValue(value)
        row.addWidget(QLabel("從"))
        row.addWidget(self.first)
        row.addWidget(QLabel("頁到"))
        row.addWidget(self.last)
        row.addWidget(QLabel("頁"))
        row.addStretch()
        layout.addLayout(row)
        self.skip_existing = QCheckBox("略過已有相同圖章的頁面")
        self.skip_existing.setChecked(True)
        layout.addWidget(self.skip_existing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("蓋章")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        (self.thumbnail_pages if self.thumbnail_pages.isEnabled() else self.all_pages).setChecked(True)
        self.group.idToggled.connect(lambda _id, _checked: self.sync_range_fields())
        self.sync_range_fields()

    def sync_range_fields(self):
        for spin in (self.first, self.last):
            spin.setEnabled(self.range_pages.isChecked())

    def pages(self):
        """回傳選擇的頁碼（0 起算）。"""
        if self.thumbnail_pages.isChecked():
            return tuple(self.selected_pages)
        if self.after_current.isChecked():
            return tuple(range(self.current_page + 1, self.page_count))
        if self.range_pages.isChecked():
            first, last = sorted((self.first.value(), self.last.value()))
            return tuple(range(first - 1, last))
        return tuple(range(self.page_count))
