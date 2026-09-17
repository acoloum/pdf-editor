from pathlib import Path
import pymupdf
from PySide6.QtCore import Qt,QSize
from PySide6.QtGui import QIcon,QPixmap
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,
    QListWidget,QListWidgetItem,QAbstractItemView,QFileDialog,QTextEdit,QSpinBox,
    QDoubleSpinBox,QCheckBox,QMessageBox,QInputDialog,QLineEdit,QFormLayout,
    QComboBox,QTabWidget,QWidget)
from pdf_editor.engine.inspection import unlock_pdf
from pdf_editor.engine.render import thumbnail
from pdf_editor.ui.style import thumbnail_icon
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
                                item.setIcon(thumbnail_icon(pix))
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


class CropPagesDialog(QDialog):
    def __init__(self,page_count,parent=None):
        super().__init__(parent)
        self.setWindowTitle("裁切選取頁面")
        self.resize(390,300)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel(f"將相同裁切邊距套用到 {page_count} 個選取頁面。單位為 PDF 點。"))
        form=QFormLayout()
        self.values=[]
        for label in ("左側","上方","右側","下方"):
            value=QDoubleSpinBox()
            value.setRange(0,10000)
            value.setDecimals(2)
            value.setSuffix(" 點")
            form.addRow(label,value)
            self.values.append(value)
        layout.addLayout(form)
        hint=QLabel("裁切只改變可見頁面範圍，可使用 Ctrl+Z 復原。")
        hint.setObjectName("hint")
        layout.addWidget(hint)
        buttons=QHBoxLayout()
        cancel=QPushButton("取消")
        cancel.clicked.connect(self.reject)
        apply=QPushButton("套用裁切")
        apply.setObjectName("primary")
        apply.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        layout.addLayout(buttons)

    def margins(self):
        return tuple(value.value() for value in self.values)


class PageDecorationDialog(QDialog):
    def __init__(self,page_count,parent=None):
        super().__init__(parent)
        self.setWindowTitle("頁碼與浮水印")
        self.resize(480,430)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel(f"設定將套用到 {page_count} 個選取頁面，並可使用 Ctrl+Z 復原。"))
        self.tabs=QTabWidget()
        self.tabs.addTab(self._page_number_tab(),"頁碼")
        self.tabs.addTab(self._text_watermark_tab(),"文字浮水印")
        self.tabs.addTab(self._image_watermark_tab(),"圖片浮水印")
        layout.addWidget(self.tabs)
        buttons=QHBoxLayout()
        cancel=QPushButton("取消")
        cancel.clicked.connect(self.reject)
        apply=QPushButton("套用到選取頁面")
        apply.setObjectName("primary")
        apply.clicked.connect(self.finish)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        layout.addLayout(buttons)

    def _page_number_tab(self):
        tab=QWidget()
        form=QFormLayout(tab)
        self.number_start=QSpinBox()
        self.number_start.setRange(-999999,999999)
        self.number_start.setValue(1)
        self.number_prefix=QLineEdit()
        self.number_suffix=QLineEdit()
        self.number_position=QComboBox()
        for label,value in (("頁尾靠左","bottom_left"),("頁尾置中","bottom_center"),
                ("頁尾靠右","bottom_right"),("頁首靠左","top_left"),
                ("頁首置中","top_center"),("頁首靠右","top_right")):
            self.number_position.addItem(label,value)
        self.number_position.setCurrentIndex(1)
        self.number_size=QDoubleSpinBox()
        self.number_size.setRange(4,72)
        self.number_size.setValue(10)
        self.number_size.setSuffix(" 點")
        form.addRow("起始號碼",self.number_start)
        form.addRow("前置文字",self.number_prefix)
        form.addRow("後置文字",self.number_suffix)
        form.addRow("位置",self.number_position)
        form.addRow("字級",self.number_size)
        return tab

    def _text_watermark_tab(self):
        tab=QWidget()
        form=QFormLayout(tab)
        self.watermark_text=QLineEdit("機密")
        self.watermark_size=QDoubleSpinBox()
        self.watermark_size.setRange(8,200)
        self.watermark_size.setValue(48)
        self.watermark_size.setSuffix(" 點")
        self.watermark_opacity=QSpinBox()
        self.watermark_opacity.setRange(1,100)
        self.watermark_opacity.setValue(20)
        self.watermark_opacity.setSuffix(" %")
        self.watermark_angle=QSpinBox()
        self.watermark_angle.setRange(-180,180)
        self.watermark_angle.setValue(-45)
        self.watermark_angle.setSuffix("°")
        form.addRow("文字",self.watermark_text)
        form.addRow("字級",self.watermark_size)
        form.addRow("不透明度",self.watermark_opacity)
        form.addRow("旋轉角度",self.watermark_angle)
        return tab

    def _image_watermark_tab(self):
        tab=QWidget()
        form=QFormLayout(tab)
        row=QWidget()
        row_layout=QHBoxLayout(row)
        row_layout.setContentsMargins(0,0,0,0)
        self.image_path=QLineEdit()
        self.image_path.setReadOnly(True)
        choose=QPushButton("選擇圖片")
        choose.clicked.connect(self.choose_image)
        row_layout.addWidget(self.image_path)
        row_layout.addWidget(choose)
        self.image_width=QSpinBox()
        self.image_width.setRange(1,95)
        self.image_width.setValue(40)
        self.image_width.setSuffix(" %")
        self.image_opacity=QSpinBox()
        self.image_opacity.setRange(1,100)
        self.image_opacity.setValue(25)
        self.image_opacity.setSuffix(" %")
        self.image_angle=QSpinBox()
        self.image_angle.setRange(-180,180)
        self.image_angle.setValue(0)
        self.image_angle.setSuffix("°")
        form.addRow("圖片",row)
        form.addRow("頁面寬度占比",self.image_width)
        form.addRow("不透明度",self.image_opacity)
        form.addRow("旋轉角度",self.image_angle)
        return tab

    def choose_image(self):
        path,_=QFileDialog.getOpenFileName(self,"選擇浮水印圖片","",
            "圖片 (*.png *.jpg *.jpeg)")
        if path:
            self.image_path.setText(path)

    def selection(self):
        index=self.tabs.currentIndex()
        if index==0:
            return "page_number",{
                "start":self.number_start.value(),
                "prefix":self.number_prefix.text(),
                "suffix":self.number_suffix.text(),
                "position":self.number_position.currentData(),
                "font_size":self.number_size.value(),
            }
        if index==1:
            text=self.watermark_text.text().strip()
            if not text:
                raise EditorError("WATERMARK","請輸入浮水印文字。")
            return "text_watermark",{
                "text":text,
                "font_size":self.watermark_size.value(),
                "opacity":self.watermark_opacity.value()/100,
                "angle":self.watermark_angle.value(),
            }
        path=self.image_path.text()
        if not path:
            raise EditorError("IMAGE","請選擇浮水印圖片。")
        return "image_watermark",{
            "image_path":path,
            "width_percent":self.image_width.value(),
            "opacity":self.image_opacity.value()/100,
            "angle":self.image_angle.value(),
        }

    def finish(self):
        try:
            self.selection()
            self.accept()
        except EditorError as exc:
            QMessageBox.warning(self,"設定不完整",str(exc))


class HeaderFooterTemplatesDialog(QDialog):
    def __init__(self,store,page_count,parent=None):
        super().__init__(parent)
        self.store=store
        self.setWindowTitle("頁首頁尾範本")
        self.resize(620,430)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"將頁首頁尾套用到 {page_count} 個選取頁面。可使用 {{page}} 代表頁碼、{{pages}} 代表總頁數。"))
        body=QHBoxLayout()
        self.templates=QListWidget()
        self.templates.setMinimumWidth(170)
        self.templates.currentRowChanged.connect(self.load_selected)
        body.addWidget(self.templates)
        editor=QWidget()
        form=QFormLayout(editor)
        self.name=QLineEdit()
        self.name.setPlaceholderText("例如：公司標準頁尾")
        self.text=QLineEdit()
        self.text.setPlaceholderText("例如：內部文件  第 {page} / {pages} 頁")
        self.position=QComboBox()
        for label,value in (("頁首靠左","top_left"),("頁首置中","top_center"),
                ("頁首靠右","top_right"),("頁尾靠左","bottom_left"),
                ("頁尾置中","bottom_center"),("頁尾靠右","bottom_right")):
            self.position.addItem(label,value)
        self.position.setCurrentIndex(4)
        self.font_size=QDoubleSpinBox()
        self.font_size.setRange(4,72)
        self.font_size.setValue(10)
        self.font_size.setSuffix(" 點")
        form.addRow("範本名稱",self.name)
        form.addRow("文字",self.text)
        form.addRow("位置",self.position)
        form.addRow("字級",self.font_size)
        template_buttons=QHBoxLayout()
        save=QPushButton("儲存範本")
        save.clicked.connect(self.save_template)
        delete=QPushButton("刪除範本")
        delete.clicked.connect(self.delete_template)
        template_buttons.addWidget(save)
        template_buttons.addWidget(delete)
        form.addRow(template_buttons)
        body.addWidget(editor,1)
        layout.addLayout(body)
        hint=QLabel("套用後可使用 Ctrl+Z 復原；範本保存在這台電腦。")
        hint.setObjectName("hint")
        layout.addWidget(hint)
        buttons=QHBoxLayout()
        cancel=QPushButton("取消")
        cancel.clicked.connect(self.reject)
        apply=QPushButton("套用到選取頁面")
        apply.setObjectName("primary")
        apply.clicked.connect(self.finish)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        layout.addLayout(buttons)
        self.reload_templates()

    def reload_templates(self,selected_name=None):
        self.templates.blockSignals(True)
        self.templates.clear()
        for item in self.store.all():
            entry=QListWidgetItem(item["name"])
            entry.setData(Qt.ItemDataRole.UserRole,item)
            self.templates.addItem(entry)
            if item["name"]==selected_name:
                self.templates.setCurrentItem(entry)
        self.templates.blockSignals(False)
        if selected_name is None and self.templates.count():
            self.templates.setCurrentRow(0)
        if self.templates.currentItem() is not None:
            self.load_selected(self.templates.currentRow())

    def load_selected(self,row):
        item=self.templates.item(row)
        if item is None:
            return
        data=item.data(Qt.ItemDataRole.UserRole)
        self.name.setText(data["name"])
        self.text.setText(data["text"])
        index=self.position.findData(data["position"])
        if index>=0:
            self.position.setCurrentIndex(index)
        self.font_size.setValue(data["font_size"])

    def selection(self):
        text=self.text.text().strip()
        if not text:
            raise EditorError("HEADER_FOOTER","請輸入頁首頁尾文字。")
        return {"text":text,"position":self.position.currentData(),
            "font_size":self.font_size.value()}

    def save_template(self):
        try:
            values=self.selection()
            self.store.save(self.name.text(),values["text"],values["position"],
                values["font_size"])
            self.reload_templates(self.name.text().strip())
        except EditorError as exc:
            QMessageBox.warning(self,"無法儲存範本",str(exc))

    def delete_template(self):
        item=self.templates.currentItem()
        if item is None:
            return
        self.store.delete(item.text())
        self.reload_templates()
        self.name.clear()
        self.text.clear()

    def finish(self):
        try:
            self.selection()
            self.accept()
        except EditorError as exc:
            QMessageBox.warning(self,"設定不完整",str(exc))
