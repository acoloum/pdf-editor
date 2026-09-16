import io
from PySide6.QtCore import Qt,QBuffer,QIODevice,QRectF,Signal
from PySide6.QtGui import QImage,QPainter,QPen,QColor
from PySide6.QtWidgets import QWidget,QDialog,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QCheckBox

class SignaturePad(QWidget):
    changed=Signal()
    # 以高倍率錄製筆跡，放大到 PDF 或高 DPI 螢幕時仍保持清晰。
    SUPERSAMPLE=4
    PADDING=12

    def __init__(self):
        super().__init__()
        self.setFixedSize(600,220)
        factor=self.SUPERSAMPLE
        self.image=QImage(600*factor,220*factor,QImage.Format.Format_ARGB32)
        self.image.fill(Qt.GlobalColor.transparent)
        self.last=None
        self.has_ink=False

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(),QColor("white"))
        painter.drawImage(QRectF(self.rect()),self.image)

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.last=event.position()

    def mouseMoveEvent(self,event):
        if self.last is not None:
            point=event.position()
            painter=QPainter(self.image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(self.SUPERSAMPLE,self.SUPERSAMPLE)
            painter.setPen(QPen(QColor("#152840"),3,Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
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

    def ink_image(self):
        """回傳裁掉四周空白的高解析度簽名影像。"""
        from PIL import Image
        buffer=QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.image.save(buffer,"PNG")
        with Image.open(io.BytesIO(bytes(buffer.data()))) as image:
            bbox=image.getchannel("A").getbbox()
            if bbox is None:
                return None
            pad=self.PADDING*self.SUPERSAMPLE
            bbox=(max(0,bbox[0]-pad),max(0,bbox[1]-pad),
                min(image.width,bbox[2]+pad),min(image.height,bbox[3]+pad))
            return image.crop(bbox)

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
        image=self.pad.ink_image()
        if image is None:
            return b""
        buffer=io.BytesIO()
        image.save(buffer,format="PNG")
        return buffer.getvalue()
