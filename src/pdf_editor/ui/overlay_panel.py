from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QLabel,QDoubleSpinBox,QPushButton

class OverlayPanel(QWidget):
    update_requested=Signal()
    delete_requested=Signal()
    def __init__(self):
        super().__init__()
        layout=QVBoxLayout(self)
        title=QLabel("圖章／簽名")
        title.setObjectName("heading")
        layout.addWidget(title)
        layout.addWidget(QLabel("可直接拖曳，也可輸入位置與大小。"))
        form=QFormLayout()
        self.fields=[]
        for text in ["左側 X","頂端 Y","寬度","高度","旋轉角度"]:
            spin=QDoubleSpinBox()
            spin.setRange(0,20000 if text!="旋轉角度" else 359)
            form.addRow(text,spin)
            self.fields.append(spin)
        layout.addLayout(form)
        apply=QPushButton("套用位置與大小")
        apply.clicked.connect(self.update_requested)
        layout.addWidget(apply)
        delete=QPushButton("刪除圖章／簽名")
        delete.clicked.connect(self.delete_requested)
        layout.addWidget(delete)
        layout.addStretch()

    def set_layer(self,layer):
        r=layer.rect
        for s,v in zip(self.fields,(r[0],r[1],r[2]-r[0],r[3]-r[1],layer.angle)):
            s.setValue(v)

