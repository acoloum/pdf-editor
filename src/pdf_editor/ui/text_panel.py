from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QLabel,QTextEdit,QDoubleSpinBox,QPushButton,QFileDialog,QColorDialog,QComboBox
from PySide6.QtGui import QColor
from pdf_editor.engine.fonts import default_font

class TextPanel(QWidget):
    preview_requested=Signal()
    apply_requested=Signal()
    cancel_requested=Signal()

    def __init__(self):
        super().__init__()
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
        layout.addLayout(form)
        self.font_path=str(default_font())
        self.font_label=QLabel("替代字型：Noto Sans CJK TC")
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
        self.preview_button=QPushButton("預覽變更")
        self.preview_button.setObjectName("primary")
        self.preview_button.clicked.connect(self.preview_requested)
        layout.addWidget(self.preview_button)
        self.preview_button.hide()
        self.apply_button=QPushButton("套用預覽")
        self.apply_button.clicked.connect(self.apply_requested)
        self.apply_button.setEnabled(False)
        layout.addWidget(self.apply_button)
        self.apply_button.hide()
        self.cancel_button=QPushButton("取消預覽")
        self.cancel_button.clicked.connect(self.cancel_requested)
        layout.addWidget(self.cancel_button)
        self.cancel_button.hide()
        hint=QLabel("單位：PDF 點（72 點 = 1 英吋）\n文字過長時請擴大文字框或減小字級。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        self.setEnabled(False)

    def choose_font(self):
        path,_=QFileDialog.getOpenFileName(self,"選擇字型","","字型 (*.ttf *.otf)")
        if path:
            self.font_path=path
            self.font_label.setText("選用字型："+path.split("/")[-1])

    def use_default_font(self):
        self.font_path=str(default_font())
        self.font_label.setText("替代字型：Noto Sans CJK TC（完整繁中文字元）")

    def choose_color(self):
        c=QColorDialog.getColor(QColor.fromRgbF(*self.color),self)
        if c.isValid():
            self.color=(c.redF(),c.greenF(),c.blueF())

    def set_run(self,run):
        self.setEnabled(run.editable)
        self.info.setText("請直接在頁面文字框輸入。" if run.editable else run.reason)
        self.text.setPlainText(run.text)
        self.size.setValue(run.size)
        self.color=run.color
        self.alignment.setCurrentIndex(0)
        x0,y0,x1,y1=run.rect
        self.set_rect((x0,y0,x1+10,y1+run.size*0.5))

    def set_insertion(self, rect, size=11, centered=False):
        self.setEnabled(True)
        self.info.setText("請直接在頁面文字框輸入。")
        self.text.clear()
        self.size.setValue(size)
        self.color=(0,0,0)
        self.alignment.setCurrentIndex(2 if centered else 0)
        self.set_rect(rect)

    def set_rect(self, rect):
        x0,y0,x1,y1=rect
        for spin,value in zip(self.box,(x0,y0,x1-x0,y1-y0)):
            spin.setValue(value)

    def rect(self):
        x,y,w,h=(s.value() for s in self.box)
        return x,y,x+w,y+h
