"""批次取代對話框：輸入多條「尋找 → 取代為」規則，先列出整份文件中符合的文字逐筆勾選，再一次套用。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from pdf_editor.batch_replace import ReplaceRule, find_candidates
from pdf_editor.errors import EditorError


def _preview(text, limit=40):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


class BatchReplaceDialog(QDialog):
    # 雙擊找到的項目時，要求主視窗切換到該頁。
    page_requested = Signal(int)

    def __init__(self, pdf, rules=(), parent=None):
        super().__init__(parent)
        self.pdf = pdf
        self.candidates = ()
        self.setWindowTitle("批次取代文字")
        self.resize(640, 620)
        # 先建立套用按鈕：加入初始規則時就會更新它的啟用狀態。
        self.apply_button = QPushButton("取代勾選項目")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "整格文字與「尋找」完全相同時才取代（忽略空白）；只包含其中一段的文字不會被修改。\n"
            "取代時沿用原本的字級與顏色，表格內的文字會放回儲存格正中央。"))
        self.rules = QTableWidget(0, 2)
        self.rules.setHorizontalHeaderLabels(["尋找（整格文字）", "取代為"])
        self.rules.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.rules.verticalHeader().setVisible(False)
        self.rules.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.rules, 2)
        for find, replace in rules:
            self.add_rule(find, replace)
        if not self.rules.rowCount():
            self.add_rule()
        self.rules.itemChanged.connect(self._rules_changed)
        rule_buttons = QHBoxLayout()
        add = QPushButton("新增規則")
        add.clicked.connect(lambda: self.add_rule(focus=True))
        remove = QPushButton("刪除選取規則")
        remove.clicked.connect(self.remove_selected_rules)
        self.find_button = QPushButton("尋找符合的文字")
        self.find_button.setDefault(True)
        self.find_button.clicked.connect(self.find)
        rule_buttons.addWidget(add)
        rule_buttons.addWidget(remove)
        rule_buttons.addStretch(1)
        rule_buttons.addWidget(self.find_button)
        layout.addLayout(rule_buttons)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["頁", "原文字", "改為", "說明"])
        self.results.setRootIsDecorated(False)
        self.results.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.results.itemChanged.connect(self._update_summary)
        self.results.itemDoubleClicked.connect(self._locate)
        layout.addWidget(self.results, 3)
        self.summary = QLabel("輸入規則後按「尋找符合的文字」。")
        layout.addWidget(self.summary)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def add_rule(self, find="", replace="", focus=False):
        row = self.rules.rowCount()
        self.rules.insertRow(row)
        self.rules.setItem(row, 0, QTableWidgetItem(find))
        self.rules.setItem(row, 1, QTableWidgetItem(replace))
        if focus:
            self.rules.setCurrentCell(row, 0)
            self.rules.editItem(self.rules.item(row, 0))
        self._rules_changed()
        return row

    def remove_selected_rules(self):
        rows = sorted({index.row() for index in self.rules.selectedIndexes()}, reverse=True)
        for row in rows:
            self.rules.removeRow(row)
        if not self.rules.rowCount():
            self.add_rule()
        self._rules_changed()

    def rule_pairs(self):
        """表格中的規則（尋找, 取代為），略過兩欄都空白的列。"""
        pairs = []
        for row in range(self.rules.rowCount()):
            cells = [self.rules.item(row, column) for column in (0, 1)]
            find, replace = (cell.text().strip() if cell else "" for cell in cells)
            if find or replace:
                pairs.append((find, replace))
        return pairs

    def _rules_changed(self, *_args):
        # 規則改變後，先前的尋找結果已不適用。
        if self.candidates:
            self.candidates = ()
            self.results.clear()
            self.summary.setText("規則已變更，請重新按「尋找符合的文字」。")
        self.apply_button.setEnabled(False)

    def find(self):
        rules = [ReplaceRule(find, replace) for find, replace in self.rule_pairs()]
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            candidates = find_candidates(self.pdf, rules)
        except EditorError as exc:
            self.summary.setText(str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.candidates = candidates
        self.results.blockSignals(True)
        self.results.clear()
        for index, candidate in enumerate(candidates):
            run = candidate.run
            item = QTreeWidgetItem([str(candidate.page + 1), _preview(run.text),
                _preview(candidate.new_text) if candidate.new_text else "（刪除）",
                "" if run.editable else (run.reason or "此文字無法安全修改。")])
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            item.setToolTip(1, run.text)
            if run.editable:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked)
            else:
                # 直書等無法安全修改的文字只列出說明，不能勾選。
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.results.addTopLevelItem(item)
        self.results.blockSignals(False)
        self._update_summary()

    def selected_candidates(self):
        selected = []
        for row in range(self.results.topLevelItemCount()):
            item = self.results.topLevelItem(row)
            if item.checkState(0) == Qt.CheckState.Checked:
                selected.append(self.candidates[item.data(0, Qt.ItemDataRole.UserRole)])
        return tuple(selected)

    def _update_summary(self, *_args):
        if not self.candidates:
            self.summary.setText("找不到內容完全相同的文字。請確認「尋找」與頁面上整格的文字一致。")
            self.apply_button.setEnabled(False)
            return
        selected = len(self.selected_candidates())
        skipped = sum(1 for candidate in self.candidates if not candidate.run.editable)
        pages = len({candidate.page for candidate in self.candidates})
        text = f"在 {pages} 頁找到 {len(self.candidates)} 筆，已勾選 {selected} 筆。雙擊項目可切換到該頁。"
        if skipped:
            text += f"（{skipped} 筆無法安全修改，已略過）"
        self.summary.setText(text)
        self.apply_button.setEnabled(selected > 0)

    def _locate(self, item, _column):
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if index is not None:
            self.page_requested.emit(self.candidates[index].page)
