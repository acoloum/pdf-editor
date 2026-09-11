from PySide6.QtCore import Qt,QBuffer,QIODevice,Signal
from PySide6.QtGui import QImage,QPainter,QPen,QColor
from PySide6.QtWidgets import QWidget,QDialog,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QCheckBox

class SignaturePad(QWidget):
    changed=Signal()
    def __init__(self):
        super().__init__()
        self.setFixedSize(600,220)
        self.image=QImage(600,220,QImage.Format.Format_ARGB32)
        self.image.fill(Qt.GlobalColor.transparent)
        self.last=None
        self.has_ink=False

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.fillRect(self.rect(),QColor("white"))
        painter.drawImage(0,0,self.image)

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.last=event.position().toPoint()

    def mouseMoveEvent(self,event):
        if self.last is not None:
            point=event.position().toPoint()
            painter=QPainter(self.image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#152840"),3,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            painter.drawLine(self.last,point)
            painter.end()
            self.last=point
            self.has_ink=True
            self.update()
            self.changed.emit()

    def mouseReleaseEvent(self,event):
        self.last=None

    def clear(self):
        self.image.fill(Qt.GlobalColor.transparent)
        self.has_ink=False
        self.update()
        self.changed.emit()

class SignatureDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowTitle("手寫簽名")
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel("請在下方手寫簽名，確認後可調整位置與大小。"))
        self.pad=SignaturePad()
        layout.addWidget(self.pad)
        self.collect=QCheckBox("收藏簽名供下次使用")
        layout.addWidget(self.collect)
        row=QHBoxLayout()
        clear=QPushButton("清除")
        clear.clicked.connect(self.pad.clear)
        row.addWidget(clear)
        row.addStretch()
        cancel=QPushButton("取消")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.accept_button=QPushButton("使用簽名")
        self.accept_button.setObjectName("primary")
        self.accept_button.setEnabled(False)
        self.accept_button.clicked.connect(self.accept)
        row.addWidget(self.accept_button)
        self.pad.changed.connect(lambda:self.accept_button.setEnabled(self.pad.has_ink))
        layout.addLayout(row)

    def png_bytes(self):
        if not self.pad.has_ink:
            return b""
        buffer=QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.pad.image.save(buffer,"PNG")
        return bytes(buffer.data())

