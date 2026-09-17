from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel,
    QTextEdit, QDoubleSpinBox, QPushButton, QFileDialog, QColorDialog,
    QComboBox, QCheckBox)
from PySide6.QtGui import QColor
import pymupdf
from pdf_editor.engine.fonts import default_font, system_fonts

class TextPanel(QWidget):
    format_requested=Signal()

    # 數值欄位停止調整後才套用，避免每按一次箭頭或每打一個數字就寫入文件。
    NUMERIC_DELAY_MS=400

    def __init__(self):
        super().__init__()
        self._loading=False
        self.modified=False
        self._format_timer=QTimer(self)
        self._format_timer.setSingleShot(True)
        self._format_timer.setInterval(self.NUMERIC_DELAY_MS)
        self._format_timer.timeout.connect(self.format_requested)
        layout=QVBoxLayout(self)
        heading=QLabel("文字編輯")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        self.info=QLabel("點選文件中的文字開始編輯。")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.text=QTextEdit()
        self.text.setMaximumHeight(140)
        layout.addWidget(self.text)
        self.text.hide()
        self.text.textChanged.connect(self._refresh_glyph_warning)
        form=QFormLayout()
        self.size=QDoubleSpinBox()
        self.size.setRange(1,300)
        form.addRow("字級",self.size)
        self.alignment=QComboBox()
        for label,value in [("靠左","left"),("水平置中","hcenter"),("水平及垂直置中","center")]:
            self.alignment.addItem(label,value)
        form.addRow("文字對齊",self.alignment)
        self.box=[]
        for name in ["左側 X","頂端 Y","寬度","高度"]:
            spin=QDoubleSpinBox()
            spin.setRange(0,20000)
            spin.setDecimals(2)
            form.addRow(name,spin)
            self.box.append(spin)
        for spin in [self.size,*self.box]:
            # 鍵盤輸入時不逐字觸發，按 Enter 或離開欄位才更新數值。
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self.mark_numeric_modified)
        self.alignment.currentIndexChanged.connect(self.mark_modified)
        layout.addLayout(form)
        self._font_path = str(default_font())
        self.font_combo = QComboBox()
        self.font_combo.addItem("內建 Noto Sans CJK TC", self._font_path)
        for name, path in system_fonts():
            self.font_combo.addItem(name, path)
        self.font_combo.currentIndexChanged.connect(self._on_font_combo)
        form.addRow("字型", self.font_combo)
        self.bold = QCheckBox("粗體")
        self.bold.toggled.connect(self.mark_modified)
        form.addRow("文字樣式", self.bold)
        self.glyph_warning = QLabel()
        self.glyph_warning.setObjectName("hint")
        self.glyph_warning.setWordWrap(True)
        self.glyph_warning.hide()
        layout.addWidget(self.glyph_warning)
        self.font_label = QLabel("替代字型：Noto Sans CJK TC")
        self.font_label.setWordWrap(True)
        self.font_label.setMinimumHeight(60)
        layout.addWidget(self.font_label)
        font_button=QPushButton("選擇字型檔")
        font_button.clicked.connect(self.choose_font)
        layout.addWidget(font_button)
        fallback=QPushButton("使用內建中文字型")
        fallback.clicked.connect(self.use_default_font)
        layout.addWidget(fallback)
        self.color=(0,0,0)
        color_button=QPushButton("文字顏色")
        color_button.clicked.connect(self.choose_color)
        layout.addWidget(color_button)
        hint=QLabel("單位：PDF 點（72 點 = 1 英吋）\n"
            "文字變長時會自動加寬；表格內會自動縮小字級。\n"
            "Shift+Enter 可換行。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        self.setEnabled(False)

    @property
    def font_path(self):
        return self._font_path

    @font_path.setter
    def font_path(self, value):
        self._font_path = value
        if hasattr(self, "font_combo"):
            index = self.font_combo.findData(value)
            if index >= 0:
                previous = self._loading
                self._loading = True
                self.font_combo.setCurrentIndex(index)
                self._loading = previous

    def _on_font_combo(self, *_):
        if self._loading:
            return
        self._font_path = self.font_combo.currentData()
        if self._font_path:
            self.font_label.setText("選用字型：" + self.font_combo.currentText())
        self.mark_modified()
        self._refresh_glyph_warning()

    def _refresh_glyph_warning(self):
        text = self.text.toPlainText()
        if not text.strip() or not self._font_path:
            self.glyph_warning.hide()
            return
        try:
            font = pymupdf.Font(fontfile=self._font_path)
            missing = [c for c in text
                if not c.isspace() and not font.has_glyph(ord(c))]
        except Exception:
            self.glyph_warning.setText("無法讀取字型檔")
            self.glyph_warning.show()
            return
        if missing:
            shown = "、".join(missing[:10]) + ("…" if len(missing) > 10 else "")
            self.glyph_warning.setText("此字型缺少部分字元：" + shown)
            self.glyph_warning.show()
        else:
            self.glyph_warning.hide()

    def choose_font(self):
        path, _ = QFileDialog.getOpenFileName(self, "選擇字型", "", "字型 (*.ttf *.otf)")
        if not path:
            return
        index = self.font_combo.findData(path)
        if index < 0:
            self.font_combo.addItem("自訂字型檔…", path)
            index = self.font_combo.count() - 1
        self.font_combo.setCurrentIndex(index)
        self._font_path = path
        self.font_label.setText("選用字型：" + path.split("/")[-1])
        self.mark_modified()
        self._refresh_glyph_warning()

    def use_default_font(self):
        self.font_path = str(default_font())
        self.font_label.setText("替代字型：Noto Sans CJK TC（完整繁中文字元）")
        self.glyph_warning.hide()
        self.mark_modified()

    def choose_color(self):
        c=QColorDialog.getColor(QColor.fromRgbF(*self.color),self)
        if c.isValid():
            self.color=(c.redF(),c.greenF(),c.blueF())
            self.mark_modified()

    def mark_modified(self,*args):
        if self._loading:
            return
        self.modified=True
        self._format_timer.stop()
        self.format_requested.emit()

    def mark_numeric_modified(self,*args):
        if self._loading:
            return
        self.modified=True
        self._format_timer.start()

    def flush_pending_format(self):
        """立即送出尚在等待中的數值變更。"""
        if self._format_timer.isActive():
            self._format_timer.stop()
            self.format_requested.emit()

    def cancel_pending_format(self):
        self._format_timer.stop()

    def set_run(self,run):
        self._loading=True
        self.setEnabled(run.editable)
        self.info.setText("請直接在頁面文字框輸入。" if run.editable else run.reason)
        self.text.setPlainText(run.text)
        self.size.setValue(run.size)
        self.color=run.color
        self.alignment.setCurrentIndex(0)
        # 文字框貼齊原文字；文字變長時由引擎自動加寬，不再預留會碰到鄰行的空間。
        self.set_rect(run.rect)
        self.bold.setChecked(bool(getattr(run,"bold",False)))
        self._format_timer.stop()
        self._loading=False
        self.modified=False

    def set_insertion(self, rect, size=11, centered=False):
        self._loading=True
        self.setEnabled(True)
        self.info.setText("請直接在頁面文字框輸入。")
        self.text.clear()
        self.size.setValue(size)
        self.color=(0,0,0)
        self.alignment.setCurrentIndex(2 if centered else 0)
        self.set_rect(rect)
        self.bold.setChecked(False)
        self._loading=False
        self.modified=False

    def set_rect(self, rect):
        x0,y0,x1,y1=rect
        previous=self._loading
        self._loading=True
        for spin,value in zip(self.box,(x0,y0,x1-x0,y1-y0)):
            spin.setValue(value)
        self._loading=previous

    def set_alignment(self,index):
        previous=self._loading
        self._loading=True
        self.alignment.setCurrentIndex(index)
        self._loading=previous

    def rect(self):
        x,y,w,h=(s.value() for s in self.box)
        return x,y,x+w,y+h
