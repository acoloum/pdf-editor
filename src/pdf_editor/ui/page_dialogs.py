from pathlib import Path
import pymupdf
from PySide6.QtCore import Qt,QSize
from PySide6.QtGui import QIcon,QPixmap
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QListWidget,QListWidgetItem,QAbstractItemView,QFileDialog,QTextEdit,QSpinBox,QCheckBox,QMessageBox,QInputDialog,QLineEdit
from pdf_editor.engine.inspection import unlock_pdf
from pdf_editor.engine.render import thumbnail
from pdf_editor.pages import parse_group,fixed_groups
from pdf_editor.errors import EditorError

class MergeDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowTitle("合併 PDF")
        self.resize(630,600)
        self.sources=[]
        self.paths=[]
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel("加入 PDF 後拖曳頁面排序。輸出不重建書籤及文件內部連結。"))
        self.list=QListWidget()
        self.list.setIconSize(QSize(75,100))
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        layout.addWidget(self.list)
        add=QPushButton("加入 PDF")
        add.clicked.connect(self.add_files)
        layout.addWidget(add)
        remove=QPushButton("移除選取頁面")
        remove.clicked.connect(lambda:self.list.takeItem(self.list.currentRow()))
        layout.addWidget(remove)
        self.confirm=QCheckBox("我已確認頁面順序，並了解書籤與內部連結不會重建")
        layout.addWidget(self.confirm)
        button=QPushButton("選擇輸出位置")
        button.clicked.connect(self.finish)
        layout.addWidget(button)

    def add_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,"加入 PDF","","PDF (*.pdf)")
        for name in paths:
            try:
                raw=Path(name).read_bytes()
                try:
                    data,access=unlock_pdf(raw)
                except EditorError as exc:
                    if exc.code!="PASSWORD":
                        raise
                    pw,ok=QInputDialog.getText(self,"PDF 密碼",Path(name).name,QLineEdit.EchoMode.Password)
                    if not ok:
                        continue
                    data,access=unlock_pdf(raw,pw)
                if not access.can_reorganize:
                    raise EditorError("READ_ONLY",access.reason)
                si=len(self.sources)
                self.sources.append(data)
                self.paths.append(Path(name))
                with pymupdf.open(stream=data) as doc:
                    for pi in range(len(doc)):
                        item=QListWidgetItem(f"{Path(name).name} — 第 {pi+1} 頁")
                        item.setData(Qt.ItemDataRole.UserRole,(si,pi))
                        self.list.addItem(item)
                        # 縮圖在背景產生，對話框關閉後不回填。
                        def done(png, item=item):
                            if self.isVisible():
                                pix=QPixmap()
                                pix.loadFromData(png)
                                item.setIcon(QIcon(pix))
                        self.parent().jobs.submit(thumbnail,(data,pi),done,lambda err:None)
            except Exception as exc:
                QMessageBox.warning(self,"無法加入",str(exc))

    def finish(self):
        if self.list.count() and self.confirm.isChecked():
            self.accept()
        else:
            QMessageBox.information(self,"請確認","請加入頁面並勾選確認。")

    def order(self):
        return tuple(tuple(self.list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.list.count()))

class SplitDialog(QDialog):
    def __init__(self,count,parent=None):
        super().__init__(parent)
        self.count=count
        self.setWindowTitle("拆分 PDF")
        self.resize(440,370)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel(f"目前 {count} 頁。每行產生一份檔案，例如："))
        self.groups=QTextEdit()
        self.groups.setPlaceholderText("1-3\n5\n8-10")
        layout.addWidget(self.groups)
        self.fixed=QCheckBox("改為固定頁數拆分")
        layout.addWidget(self.fixed)
        self.size=QSpinBox()
        self.size.setRange(1,count)
        self.size.setValue(1)
        layout.addWidget(self.size)
        self.preview=QLabel()
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        self.confirm=QCheckBox("了解輸出不重建書籤及文件內部連結")
        layout.addWidget(self.confirm)
        check=QPushButton("預覽檔名與頁數")
        check.clicked.connect(self.show_preview)
        layout.addWidget(check)
        button=QPushButton("選擇輸出資料夾")
        button.clicked.connect(self.finish)
        layout.addWidget(button)

    def selections(self):
        if self.fixed.isChecked():
            return fixed_groups(self.count,self.size.value())
        lines=self.groups.toPlainText().splitlines()
        if not lines:
            raise EditorError("RANGE","請輸入頁面群組。")
        return tuple(parse_group(line,self.count) for line in lines)

    def show_preview(self):
        try:
            groups=self.selections()
            self.preview.setText("\n".join(f"拆分_{i+1:03}.pdf：{len(g)} 頁" for i,g in enumerate(groups)))
            return True
        except EditorError as exc:
            QMessageBox.warning(self,"頁碼錯誤",str(exc))
            return False

    def finish(self):
        if self.show_preview() and self.confirm.isChecked():
            self.accept()

