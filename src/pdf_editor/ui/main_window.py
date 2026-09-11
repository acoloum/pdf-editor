from pathlib import Path
from dataclasses import replace
import hashlib
import uuid
import pymupdf
from PySide6.QtCore import Qt,QStandardPaths,QSize
from PySide6.QtGui import QAction,QKeySequence,QIcon,QPixmap
from PySide6.QtWidgets import QMainWindow,QWidget,QVBoxLayout,QLabel,QSplitter,QListWidget,QListWidgetItem,QToolBar,QFileDialog,QMessageBox,QInputDialog,QLineEdit,QStackedWidget,QComboBox,QSpinBox,QScrollArea,QListView
from pdf_editor.document.session import DocumentSession
from pdf_editor.document.save import write_pdf,publish_batch
from pdf_editor.engine.render import render_page,thumbnail
from pdf_editor.engine.text import replace_text
from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.engine.fonts import default_font,embedded_font
from pdf_editor.model import TextReplacement,Overlay
from pdf_editor.errors import EditorError
from pdf_editor.workers import Jobs
from pdf_editor.assets import AssetStore
from pdf_editor.pages import merge_pages,split_pages
from pdf_editor.ui.canvas import Canvas
from pdf_editor.ui.text_panel import TextPanel
from pdf_editor.ui.overlay_panel import OverlayPanel
from pdf_editor.ui.signature_dialog import SignatureDialog
from pdf_editor.ui.page_dialogs import MergeDialog,SplitDialog
from pdf_editor.ui.style import STYLE

def export_document(pdf,layers,target,source,overwrite):
    data=flatten_overlays(pdf,layers) if layers else pdf
    return str(write_pdf(data,Path(target),overwrite,(Path(source),)))

def export_merge(sources,order,target,paths):
    return str(write_pdf(merge_pages(sources,order),Path(target),False,tuple(Path(p) for p in paths)))

def export_split(pdf,layers,groups,folder,source):
    data=flatten_overlays(pdf,layers) if layers else pdf
    documents=split_pages(data,groups)
    targets=tuple(Path(folder)/f"拆分_{i+1:03}.pdf" for i in range(len(groups)))
    return tuple(str(p) for p in publish_batch(documents,targets,(Path(source),)))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("墨頁 PDF")
        self.resize(1320,850)
        self.setStyleSheet(STYLE)
        self.session=None
        self.page_data=None
        self.page_count=0
        self.page=0
        self.scale=1.25
        self.preview=None
        self.run=None
        self.layer_id=None
        self.token=0
        self.busy=False
        self.render_serial=0
        self.closed=False
        self.jobs=Jobs(self)
        self.asset_root=Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))/"assets"
        self.assets=AssetStore(self.asset_root)
        toolbar=QToolBar("文件工具",self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.actions={}
        for name,label,handler,key in [
            ("open","開啟 PDF",self.choose_open,"Ctrl+O"),
            ("save","另存新檔",self.save,"Ctrl+Shift+S"),
            ("undo","復原",lambda:self.history_step(False),"Ctrl+Z"),
            ("redo","重做",lambda:self.history_step(True),"Ctrl+Y"),
            ("stamp","蓋章",self.add_stamp,None),
            ("signature","手寫簽名",self.add_signature,None),
            ("collection","常用圖章",self.add_collection,None),
            ("merge","合併",self.merge,None),
            ("split","拆分",self.split,None)]:
            action=QAction(label,self)
            action.triggered.connect(handler)
            if key:
                action.setShortcut(QKeySequence(key))
            toolbar.addAction(action)
            self.actions[name]=action
        toolbar.addSeparator()
        self.page_spin=QSpinBox()
        self.page_spin.setPrefix("第 ")
        self.page_spin.setSuffix(" 頁")
        self.page_spin.setRange(1,1)
        self.page_spin.valueChanged.connect(lambda n:self.goto_page(n-1))
        toolbar.addWidget(self.page_spin)
        zoom=QComboBox()
        zoom.addItems(["75%","100%","125%","150%","200%"])
        zoom.setCurrentText("125%")
        zoom.currentTextChanged.connect(self.change_zoom)
        toolbar.addWidget(zoom)
        splitter=QSplitter()
        self.thumbs=QListWidget()
        self.thumbs.setIconSize(QSize(110,140))
        self.thumbs.setViewMode(QListView.ViewMode.IconMode)
        self.thumbs.setFlow(QListView.Flow.TopToBottom)
        self.thumbs.setWrapping(False)
        self.thumbs.setWordWrap(True)
        self.thumbs.setGridSize(QSize(145,165))
        self.thumbs.setMinimumWidth(160)
        self.thumbs.setMaximumWidth(220)
        self.thumbs.currentRowChanged.connect(self.goto_page)
        splitter.addWidget(self.thumbs)
        self.canvas=Canvas()
        self.canvas.run_selected.connect(self.select_run)
        self.canvas.layer_selected.connect(self.select_layer)
        self.canvas.layer_moved.connect(self.move_layer)
        splitter.addWidget(self.canvas)
        self.panels=QStackedWidget()
        self.text_panel=TextPanel()
        self.text_panel.preview_requested.connect(self.preview_from_panel)
        self.text_panel.apply_requested.connect(self.apply_preview)
        self.text_panel.cancel_requested.connect(self.cancel_preview)
        self.overlay_panel=OverlayPanel()
        self.overlay_panel.update_requested.connect(self.update_layer)
        self.overlay_panel.delete_requested.connect(self.delete_layer)
        self.panels.addWidget(self.text_panel)
        self.panels.addWidget(self.overlay_panel)
        self.panels.setMinimumWidth(280)
        self.panels.setMaximumWidth(350)
        panel_scroll=QScrollArea()
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setWidget(self.panels)
        panel_scroll.setMinimumWidth(295)
        panel_scroll.setMaximumWidth(370)
        splitter.addWidget(panel_scroll)
        splitter.setSizes([170,820,310])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("開啟 PDF 開始編輯。文件全程在本機處理。")
        self.refresh_actions()

    def refresh_actions(self):
        active=self.session is not None
        edit=active and self.session.access.can_edit and not self.busy
        for name in ("save","stamp","signature","collection"):
            self.actions[name].setEnabled(edit)
        self.actions["undo"].setEnabled(edit and self.session.can_undo)
        self.actions["redo"].setEnabled(edit and self.session.can_redo)
        self.actions["split"].setEnabled(active and self.session.access.can_reorganize and not self.busy)
        self.actions["open"].setEnabled(not self.busy)
        self.actions["merge"].setEnabled(not self.busy)
        self.text_panel.apply_button.setEnabled(edit and self.preview is not None)
        self.canvas.setEnabled(not self.busy)
        self.overlay_panel.setEnabled(edit)
        self.text_panel.setEnabled(edit and self.run is not None and self.run.editable)

    def error(self,error):
        self.busy=False
        self.refresh_actions()
        code,message,completed=error
        if completed:
            message+="\n已完成：\n"+"\n".join(completed)
        QMessageBox.warning(self,"無法完成操作",message)
        self.statusBar().showMessage(message.split("\n")[0])

    def confirm_leave(self):
        if self.busy:
            return False
        if self.session and self.session.dirty:
            result=QMessageBox.question(self,"尚未儲存","是否儲存目前變更？",
                QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel)
            if result==QMessageBox.StandardButton.Save:
                self.save()
                return False
            return result==QMessageBox.StandardButton.Discard
        return True

    def choose_open(self):
        if not self.confirm_leave():
            return
        name,_=QFileDialog.getOpenFileName(self,"開啟 PDF","","PDF (*.pdf)")
        if name:
            try:
                self.open_document(Path(name))
            except Exception as exc:
                self.error((getattr(exc,"code","OPEN"),str(exc),()))

    def open_document(self,path):
        try:
            session=DocumentSession.open(Path(path))
        except EditorError as exc:
            if exc.code!="PASSWORD":
                raise
            password,ok=QInputDialog.getText(self,"PDF 密碼","密碼：",QLineEdit.EchoMode.Password)
            if not ok:
                return
            session=DocumentSession.open(Path(path),password)
        if self.session:
            self.session.close()
        self.session=session
        self.token+=1
        self.preview=None
        self.run=None
        self.page_data=None
        self.page=0
        self.text_panel.setEnabled(False)
        with pymupdf.open(stream=session.pdf) as doc:
            self.page_count=len(doc)
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1,self.page_count)
        self.page_spin.setValue(1)
        self.page_spin.blockSignals(False)
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        for i in range(self.page_count):
            self.thumbs.addItem(QListWidgetItem(f"第 {i+1} 頁"))
        self.thumbs.blockSignals(False)
        self.setWindowTitle(f"{session.source.name} — 墨頁 PDF")
        self.refresh_actions()
        self.request_render()
        self.queue_thumbnail(0,self.token,session.revision)

    def queue_thumbnail(self,index,token,revision):
        if self.closed or not self.session or token!=self.token or revision!=self.session.revision or index>=self.page_count:
            return
        def done(png):
            if self.closed or token!=self.token or not self.session or revision!=self.session.revision:
                return
            pix=QPixmap()
            pix.loadFromData(png)
            item=self.thumbs.item(index)
            if item:
                item.setIcon(QIcon(pix))
            self.queue_thumbnail(index+1,token,revision)
        self.jobs.submit(thumbnail,(self.session.pdf,index),done,lambda err:None)

    def goto_page(self,page):
        if not self.session or not 0<=page<self.page_count or page==self.page:
            return
        self.page=page
        self.run=None
        self.text_panel.setEnabled(False)
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(page+1)
        self.page_spin.blockSignals(False)
        self.request_render()

    def change_zoom(self,text):
        self.scale=float(text.rstrip("%"))/100
        self.request_render()

    def request_render(self):
        if not self.session:
            return
        self.render_serial+=1
        serial,token=self.render_serial,self.token
        self.statusBar().showMessage("正在更新頁面…")
        data=self.preview[1] if self.preview else self.session.pdf
        layers=self.session.overlays
        def done(result):
            if self.closed or token!=self.token or serial!=self.render_serial:
                return
            self.page_data=result
            try:
                self.canvas.display(result,layers)
            except EditorError as exc:
                self.error((exc.code,str(exc),()))
            status=self.session.access.reason or ("預覽中，尚未套用" if self.preview else
                "有未儲存變更" if self.session.dirty else "可編輯")
            self.statusBar().showMessage(f"第 {self.page+1} / {self.page_count} 頁  ·  {status}")
        pixel_ratio=max(2.0,float(self.canvas.devicePixelRatioF()))
        self.jobs.submit(render_page,(data,self.page,self.scale,pixel_ratio),done,self.error)

    def select_run(self,run):
        if not self.session or not self.session.access.can_edit or self.preview:
            return
        self.run=run
        self.panels.setCurrentWidget(self.text_panel)
        self.text_panel.set_run(run)
        self.text_panel.font_path=str(default_font())
        self.text_panel.font_label.setText("替代字型：Noto Sans CJK TC（預覽確認）")
        original=embedded_font(self.session.pdf,self.page,run,self.session.history.root)
        if original:
            self.text_panel.font_path=str(original)
            self.text_panel.font_label.setText("原字型："+run.font_name+"；缺字時請使用內建中文字型。")
        self.refresh_actions()

    def preview_from_panel(self):
        if not self.run or not self.session or self.busy:
            return
        p=self.text_panel
        req=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),
            self.page,self.run.id,p.text.toPlainText(),p.rect(),p.font_path,p.size.value(),p.color)
        self.preview_replacement(req)

    def preview_replacement(self,request):
        if self.busy:
            return
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage("正在建立文字預覽…")
        def done(pdf):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.preview=(revision,pdf)
            self.refresh_actions()
            self.request_render()
        self.jobs.submit(replace_text,(self.session.pdf,request),done,self.error)

    def cancel_preview(self):
        self.preview=None
        self.refresh_actions()
        self.request_render()

    def apply_preview(self):
        if self.preview and self.preview[0]==self.session.revision:
            self.session.apply_pdf(self.preview[1])
            self.preview=None
            self.text_panel.setEnabled(False)
            self.refresh_actions()
            self.request_render()
            self.queue_thumbnail(0,self.token,self.session.revision)

    def history_step(self,redo):
        if not self.session or self.busy:
            return
        self.preview=None
        self.session.redo() if redo else self.session.undo()
        self.text_panel.setEnabled(False)
        self.refresh_actions()
        self.request_render()

    def save(self):
        if not self.session or self.busy:
            return
        if self.preview:
            QMessageBox.information(self,"尚有預覽","請先套用或取消文字預覽，再另存新檔。")
            return
        if self.session.password_used:
            if QMessageBox.question(self,"輸出保護","另存的文件不保留密碼保護，是否繼續？")!=QMessageBox.StandardButton.Yes:
                return
        name,_=QFileDialog.getSaveFileName(self,"另存新檔",
            str(self.session.source.with_name(self.session.source.stem+"-已編輯.pdf")),"PDF (*.pdf)")
        if not name:
            return
        target=Path(name)
        if target.suffix.lower()!=".pdf":
            target=target.with_suffix(".pdf")
        revision=self.session.revision
        fingerprint=self.session.history.current[2]
        self.busy=True
        self.refresh_actions()
        def done(path):
            self.busy=False
            if self.session.revision==revision:
                self.session.saved_fingerprint=fingerprint
            self.refresh_actions()
            self.statusBar().showMessage("已儲存："+path)
        self.jobs.submit(export_document,(self.session.pdf,self.session.overlays,str(target),
            str(self.session.source),target.exists()),done,self.error)

    def add_stamp(self):
        name,_=QFileDialog.getOpenFileName(self,"匯入圖章或簽名","","PNG (*.png)")
        if name:
            self.import_layer(Path(name),True)

    def add_collection(self):
        name,_=QFileDialog.getOpenFileName(self,"選擇常用圖章",str(self.asset_root),"PNG (*.png)")
        if name:
            self.import_layer(Path(name),True)

    def add_signature(self):
        dialog=SignatureDialog(self)
        if dialog.exec():
            path=self.session.history.root/"signature.png"
            path.write_bytes(dialog.png_bytes())
            self.import_layer(path,dialog.collect.isChecked())

    def import_layer(self,path,persistent):
        if not self.session or self.preview:
            return
        try:
            store=self.assets if persistent else AssetStore(self.session.history.root/"assets")
            copied=store.import_png(path,persistent)
            from PIL import Image
            with Image.open(copied) as image:
                ratio=image.height/image.width
            width=min(150,self.page_data["bounds"][2]*0.4)
            height=min(width*ratio,self.page_data["bounds"][3]*0.4)
            layer=Overlay(uuid.uuid4().hex,self.page,str(copied),(40,40,40+width,40+height),0)
            self.session.set_overlays(self.session.overlays+(layer,))
            self.layer_id=layer.id
            self.select_layer(layer.id)
            self.refresh_actions()
            self.request_render()
        except Exception as exc:
            self.error((getattr(exc,"code","IMAGE"),str(exc),()))

    def select_layer(self,id):
        self.layer_id=id
        layer=next((o for o in self.session.overlays if o.id==id),None)
        if layer:
            self.panels.setCurrentWidget(self.overlay_panel)
            self.overlay_panel.set_layer(layer)

    def move_layer(self,layer):
        try:
            # 先驗證新位置，失敗時還原畫布而不寫入歷史。
            flatten_overlays(self.session.pdf,(layer,))
            self.session.set_overlays(tuple(layer if o.id==layer.id else o for o in self.session.overlays))
            self.select_layer(layer.id)
            self.refresh_actions()
        except Exception as exc:
            self.error((getattr(exc,"code","GEOMETRY"),str(exc),()))
        self.request_render()

    def update_layer(self):
        layer=next((o for o in self.session.overlays if o.id==self.layer_id),None)
        if layer:
            x,y,w,h,angle=(s.value() for s in self.overlay_panel.fields)
            self.move_layer(replace(layer,rect=(x,y,x+w,y+h),angle=angle))

    def delete_layer(self):
        self.session.set_overlays(tuple(o for o in self.session.overlays if o.id!=self.layer_id))
        self.panels.setCurrentWidget(self.text_panel)
        self.refresh_actions()
        self.request_render()

    def merge(self):
        dialog=MergeDialog(self)
        if not dialog.exec():
            return
        name,_=QFileDialog.getSaveFileName(self,"合併輸出","合併.pdf","PDF (*.pdf)")
        if name:
            self.busy=True
            self.refresh_actions()
            self.jobs.submit(export_merge,(tuple(dialog.sources),dialog.order(),name,
                tuple(str(p) for p in dialog.paths)),self.export_done,self.error)

    def split(self):
        if self.preview:
            QMessageBox.information(self,"尚有預覽","請先套用或取消文字預覽。")
            return
        dialog=SplitDialog(self.page_count,self)
        if dialog.exec():
            folder=QFileDialog.getExistingDirectory(self,"選擇輸出資料夾")
            if folder:
                self.busy=True
                self.refresh_actions()
                self.jobs.submit(export_split,(self.session.pdf,self.session.overlays,
                    dialog.selections(),folder,str(self.session.source)),self.export_done,self.error)

    def export_done(self,result):
        self.busy=False
        self.refresh_actions()
        self.statusBar().showMessage("輸出完成："+(result if isinstance(result,str) else f"{len(result)} 份 PDF"))

    def closeEvent(self,event):
        if not self.confirm_leave():
            event.ignore()
            return
        self.closed=True
        self.jobs.close()
        if self.session:
            self.session.close()
            self.session=None
        event.accept()
