from pathlib import Path
from dataclasses import replace
import hashlib
import uuid
import pymupdf
from PySide6.QtCore import Qt,QStandardPaths,QSize,Signal
from PySide6.QtGui import QAction,QKeySequence,QIcon,QPixmap
from PySide6.QtWidgets import QMainWindow,QWidget,QVBoxLayout,QLabel,QSplitter,QListWidget,QListWidgetItem,QToolBar,QFileDialog,QMessageBox,QInputDialog,QLineEdit,QStackedWidget,QComboBox,QSpinBox,QScrollArea,QListView,QMenu,QToolButton,QAbstractItemView
from pdf_editor.document.session import DocumentSession
from pdf_editor.document.save import write_pdf,publish_batch
from pdf_editor.engine.render import render_page,thumbnail
from pdf_editor.engine.inspection import unlock_pdf
from pdf_editor.engine.text import replace_text, insert_text, find_table_cell
from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.engine.fonts import default_font,embedded_font,checked_font
from pdf_editor.model import TextReplacement,TextInsertion,Overlay
from pdf_editor.errors import EditorError
from pdf_editor.workers import Jobs
from pdf_editor.assets import AssetStore
from pdf_editor.templates import HeaderFooterTemplateStore
from pdf_editor.search import find_text
from pdf_editor.ocr import ocr_pages
from pdf_editor.comparison import compare_pages
from pdf_editor.ocr_assets import validate_ocr_assets
from pdf_editor.page_images import export_pages_as_png
from pdf_editor.pages import (merge_pages,split_pages,move_pages,move_pages_to,rotate_pages,
    delete_pages,duplicate_pages,page_order_after_move,page_order_after_drop,insert_pages,
    insert_blank_page,extract_pages)
from pdf_editor.page_decorations import (crop_pages,add_page_numbers,
    add_text_watermark,add_image_watermark,add_header_footer)
from pdf_editor.annotations import (mark_text,add_text_note,delete_annotation,
    set_highlight_color)
from pdf_editor.ui.canvas import Canvas
from pdf_editor.ui.text_panel import TextPanel
from pdf_editor.ui.overlay_panel import OverlayPanel
from pdf_editor.ui.signature_dialog import SignatureDialog
from pdf_editor.ui.comparison_dialog import ComparisonDialog
from pdf_editor.ui.page_dialogs import (MergeDialog,SplitDialog,CropPagesDialog,
    PageDecorationDialog,HeaderFooterTemplatesDialog)
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


def export_extract(pdf,layers,pages,target,source):
    data=flatten_overlays(pdf,layers) if layers else pdf
    extracted=extract_pages(data,pages)
    return str(write_pdf(extracted,Path(target),False,(Path(source),)))


def export_page_images(pdf,layers,pages,folder,stem,dpi):
    data=flatten_overlays(pdf,layers) if layers else pdf
    return tuple(str(path) for path in export_pages_as_png(
        data,pages,Path(folder),stem,dpi))

def edit_page_document(pdf,layers,operation,pages,target=None):
    data=flatten_overlays(pdf,layers) if layers else pdf
    if operation=="move":
        return move_pages(data,pages,target)
    if operation=="move_to":
        return move_pages_to(data,pages,target)
    if operation=="rotate":
        return rotate_pages(data,pages,target)
    if operation=="delete":
        return delete_pages(data,pages)
    if operation=="duplicate":
        return duplicate_pages(data,pages)
    if operation=="blank":
        return insert_blank_page(data,pages[0])
    if operation=="insert":
        return insert_pages(data,target,pages[0])
    if operation=="crop":
        return crop_pages(data,pages,target)
    if operation=="page_number":
        return add_page_numbers(data,pages,target["start"],target["prefix"],
            target["suffix"],target["position"],target["font_size"],target["font_path"])
    if operation=="text_watermark":
        return add_text_watermark(data,pages,target["text"],target["font_size"],
            target["opacity"],target["angle"],target["font_path"])
    if operation=="image_watermark":
        return add_image_watermark(data,pages,target["image_path"],
            target["width_percent"],target["opacity"],target["angle"])
    if operation=="header_footer":
        return add_header_footer(data,pages,target["text"],target["position"],
            target["font_size"],target["font_path"])
    raise EditorError("PAGE_OPERATION","頁面操作無效。")


class ThumbnailList(QListWidget):
    pages_dropped=Signal(tuple,int)

    def dropEvent(self,event):
        if event.source() is not self:
            super().dropEvent(event)
            return
        pages=tuple(sorted(self.row(item) for item in self.selectedItems()))
        if not pages:
            event.ignore()
            return
        point=event.position().toPoint()
        index=self.indexAt(point)
        destination=self.count()
        if index.isValid():
            rect=self.visualRect(index)
            destination=index.row()+int(point.y()>=rect.center().y())
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.pages_dropped.emit(pages,destination)

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
        self.insertion_rect=None
        self.layer_id=None
        self.annotation=None
        self.crop_pages=()
        self.search_results=()
        self.search_index=-1
        self.search_revision=None
        self.token=0
        self.busy=False
        self.render_serial=0
        self.comparison_serial=0
        self.comparison_dialog=None
        self.comparison_pdf=None
        self.comparison_base_pdf=None
        self.comparison_base_page=None
        self.comparison_revision=None
        self.comparison_token=None
        self.closed=False
        self.jobs=Jobs(self)
        self.asset_root=Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))/"assets"
        self.assets=AssetStore(self.asset_root)
        self.header_footer_templates=HeaderFooterTemplateStore(
            self.asset_root.parent/"header-footer-templates.json")
        toolbar=QToolBar("文件工具",self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.actions={}
        for name,label,handler,key in [
            ("open","開啟 PDF",self.choose_open,"Ctrl+O"),
            ("save","另存新檔",self.save,"Ctrl+Shift+S"),
            ("undo","復原",lambda:self.history_step(False),"Ctrl+Z"),
            ("redo","重做",lambda:self.history_step(True),"Ctrl+Y"),
            ("add_text","新增文字",self.start_text_insertion,"Ctrl+T"),
            ("stamp","蓋章",self.add_stamp,None),
            ("signature","手寫簽名",self.add_signature,None),
            ("collection","常用圖章",self.add_collection,None),
            ("ocr","OCR 文字辨識",self.run_ocr,None),
            ("compare","頁面比較",self.choose_comparison_pdf,None),
            ("merge","合併",self.merge,None),
            ("split","拆分",self.split,None),
            ("page_marks","頁碼／浮水印",self.show_page_decoration_dialog,None),
            ("header_footer","頁首頁尾範本",self.show_header_footer_dialog,None)]:
            action=QAction(label,self)
            action.triggered.connect(handler)
            if key:
                action.setShortcut(QKeySequence(key))
            if name=="add_text":
                action.setCheckable(True)
            toolbar.addAction(action)
            self.actions[name]=action
        page_menu=QMenu(self)
        for name,label,handler,key in [
            ("page_up","選取頁面上移",lambda:self.move_current_page(-1),"Alt+Up"),
            ("page_down","選取頁面下移",lambda:self.move_current_page(1),"Alt+Down"),
            ("rotate_left","選取頁面向左旋轉",lambda:self.rotate_current_page(-90),"Ctrl+Shift+Left"),
            ("rotate_right","選取頁面向右旋轉",lambda:self.rotate_current_page(90),"Ctrl+Shift+Right"),
            ("duplicate_page","複製選取頁面",self.duplicate_selected_pages,"Ctrl+D"),
            ("blank_page","新增空白頁",self.add_blank_page,None),
            ("insert_pdf","插入另一份 PDF（全部頁面）…",self.insert_pdf_pages,None),
            ("extract_pages","抽取選取頁面另存…",self.extract_selected_pages,None),
            ("export_png","匯出選取頁面為 PNG…",self.export_selected_pages_png,None),
            ("direct_crop","直接拖曳裁切框",self.toggle_direct_crop,None),
            ("crop_page","精確輸入裁切邊距…",self.show_crop_dialog,None),
            ("delete_page","刪除選取頁面",self.delete_current_page,"Ctrl+Delete")]:
            action=QAction(label,self)
            action.triggered.connect(handler)
            action.setShortcut(QKeySequence(key))
            if name=="direct_crop":
                action.setCheckable(True)
            page_menu.addAction(action)
            self.actions[name]=action
        self.page_menu_button=QToolButton()
        self.page_menu_button.setText("頁面操作")
        self.page_menu_button.setMenu(page_menu)
        self.page_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(self.page_menu_button)
        markup_menu=QMenu(self)
        for name,label,handler in [
            ("highlight","螢光標記",lambda:self.apply_selected_markup("highlight")),
            ("underline","加底線",lambda:self.apply_selected_markup("underline")),
            ("text_note","文字註解",self.start_text_note),
            ("select_annotation","選取註解",self.start_annotation_selection),
            ("highlight_yellow","螢光色：黃色",lambda:self.change_highlight_color((1.0,0.84,0.18))),
            ("highlight_green","螢光色：綠色",lambda:self.change_highlight_color((0.30,0.78,0.48))),
            ("highlight_pink","螢光色：粉紅色",lambda:self.change_highlight_color((1.0,0.45,0.66))),
            ("highlight_blue","螢光色：藍色",lambda:self.change_highlight_color((0.32,0.67,1.0))),
            ("delete_annotation","刪除選取註解",self.delete_selected_annotation)]:
            action=QAction(label,self)
            action.triggered.connect(handler)
            if name in ("text_note","select_annotation"):
                action.setCheckable(True)
            markup_menu.addAction(action)
            self.actions[name]=action
        self.markup_menu_button=QToolButton()
        self.markup_menu_button.setText("標記註解")
        self.markup_menu_button.setMenu(markup_menu)
        self.markup_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(self.markup_menu_button)
        toolbar.addSeparator()
        self.page_spin=QSpinBox()
        self.page_spin.setPrefix("第 ")
        self.page_spin.setSuffix(" 頁")
        self.page_spin.setRange(1,1)
        self.page_spin.valueChanged.connect(lambda n:self.goto_page(n-1))
        toolbar.addWidget(self.page_spin)
        self.zoom=QComboBox()
        self.zoom.addItems(["適合頁面","適合寬度","75%","100%","125%","150%","200%"])
        self.zoom.setCurrentText("125%")
        self.zoom.currentTextChanged.connect(self.change_zoom)
        toolbar.addWidget(self.zoom)
        self.addToolBarBreak()
        search_toolbar=QToolBar("文件搜尋",self)
        search_toolbar.setMovable(False)
        self.addToolBar(search_toolbar)
        search_action=QAction("搜尋",self)
        search_action.setShortcut(QKeySequence("Ctrl+F"))
        search_action.triggered.connect(self.focus_search)
        search_toolbar.addAction(search_action)
        self.actions["search"]=search_action
        self.search_input=QLineEdit()
        self.search_input.setPlaceholderText("輸入全文搜尋文字後按 Enter")
        self.search_input.setMaximumWidth(360)
        self.search_input.returnPressed.connect(lambda:self.perform_search())
        search_toolbar.addWidget(self.search_input)
        previous=QAction("上一筆",self)
        previous.setShortcut(QKeySequence("Shift+F3"))
        previous.triggered.connect(lambda:self.next_search_result(-1))
        search_toolbar.addAction(previous)
        self.actions["search_previous"]=previous
        next_result=QAction("下一筆",self)
        next_result.setShortcut(QKeySequence("F3"))
        next_result.triggered.connect(self.next_search_result)
        search_toolbar.addAction(next_result)
        self.actions["search_next"]=next_result
        self.search_count=QLabel("0 / 0")
        search_toolbar.addWidget(self.search_count)
        splitter=QSplitter()
        self.thumbs=ThumbnailList()
        self.thumbs.setIconSize(QSize(110,140))
        self.thumbs.setViewMode(QListView.ViewMode.IconMode)
        self.thumbs.setFlow(QListView.Flow.TopToBottom)
        self.thumbs.setWrapping(False)
        self.thumbs.setWordWrap(True)
        self.thumbs.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.thumbs.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.thumbs.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.thumbs.setGridSize(QSize(145,165))
        self.thumbs.setMinimumWidth(160)
        self.thumbs.setMaximumWidth(220)
        self.thumbs.currentRowChanged.connect(self.goto_page)
        self.thumbs.itemSelectionChanged.connect(self.thumbnail_selection_changed)
        self.thumbs.pages_dropped.connect(self.move_selected_pages_to)
        splitter.addWidget(self.thumbs)
        self.canvas=Canvas()
        self.canvas.run_selected.connect(self.select_run)
        self.canvas.run_moved.connect(self.move_run)
        self.canvas.run_delete_requested.connect(self.delete_run)
        self.canvas.text_insertion_requested.connect(self.begin_text_insertion)
        self.canvas.text_insertion_cancelled.connect(self.cancel_text_insertion)
        self.canvas.inline_text_committed.connect(self.commit_inline_text)
        self.canvas.inline_text_cancelled.connect(self.cancel_inline_text)
        self.canvas.note_insertion_requested.connect(self.begin_text_note)
        self.canvas.note_insertion_cancelled.connect(self.cancel_text_note)
        self.canvas.annotation_selected.connect(self.select_annotation)
        self.canvas.annotation_delete_requested.connect(self.delete_selected_annotation)
        self.canvas.layer_selected.connect(self.select_layer)
        self.canvas.layer_moved.connect(self.move_layer)
        self.canvas.crop_requested.connect(self.apply_direct_crop)
        self.canvas.crop_cancelled.connect(self.cancel_direct_crop)
        splitter.addWidget(self.canvas)
        self.panels=QStackedWidget()
        self.text_panel=TextPanel()
        self.text_panel.preview_requested.connect(self.preview_from_panel)
        self.text_panel.apply_requested.connect(self.apply_preview)
        self.text_panel.cancel_requested.connect(self.cancel_preview)
        self.text_panel.format_requested.connect(self.apply_text_format)
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
        for name in ("save","add_text","stamp","signature","collection","page_marks",
                "header_footer"):
            self.actions[name].setEnabled(edit)
        self.actions["undo"].setEnabled(edit and self.session.can_undo)
        self.actions["redo"].setEnabled(edit and self.session.can_redo)
        self.actions["split"].setEnabled(active and self.session.access.can_reorganize and not self.busy)
        self.actions["open"].setEnabled(not self.busy)
        self.actions["merge"].setEnabled(not self.busy)
        manage=active and self.session.access.can_reorganize and not self.busy
        selected=set(self.selected_page_indices()) if active else set()
        ocr_selected=self.selected_thumbnail_indices() if active else ()
        can_up=any(page>0 and page-1 not in selected for page in selected)
        can_down=any(page<self.page_count-1 and page+1 not in selected for page in selected)
        self.actions["page_up"].setEnabled(manage and can_up)
        self.actions["page_down"].setEnabled(manage and can_down)
        self.actions["rotate_left"].setEnabled(manage and bool(selected))
        self.actions["rotate_right"].setEnabled(manage and bool(selected))
        self.actions["duplicate_page"].setEnabled(manage and bool(selected))
        self.actions["blank_page"].setEnabled(manage and bool(selected))
        self.actions["insert_pdf"].setEnabled(manage and bool(selected))
        self.actions["extract_pages"].setEnabled(manage and bool(selected))
        self.actions["export_png"].setEnabled(manage and bool(selected))
        self.actions["crop_page"].setEnabled(edit and bool(selected))
        self.actions["direct_crop"].setEnabled(edit and bool(selected))
        self.actions["delete_page"].setEnabled(manage and bool(selected) and len(selected)<self.page_count)
        self.actions["ocr"].setEnabled(edit and bool(ocr_selected))
        self.actions["compare"].setEnabled(active and not self.busy)
        self.page_menu_button.setEnabled(manage or edit)
        markup=edit and self.run is not None and self.run.editable
        self.actions["highlight"].setEnabled(markup)
        self.actions["underline"].setEnabled(markup)
        self.actions["text_note"].setEnabled(edit)
        self.actions["select_annotation"].setEnabled(edit)
        selected_annotation=edit and self.annotation is not None
        selected_highlight=selected_annotation and self.annotation.kind=="Highlight"
        for name in ("highlight_yellow","highlight_green","highlight_pink","highlight_blue"):
            self.actions[name].setEnabled(selected_highlight)
        self.actions["delete_annotation"].setEnabled(selected_annotation)
        self.markup_menu_button.setEnabled(edit)
        self.actions["search"].setEnabled(active and not self.busy)
        valid_search=active and not self.busy and bool(self.search_results) and self.search_revision==self.session.revision
        self.actions["search_previous"].setEnabled(valid_search)
        self.actions["search_next"].setEnabled(valid_search)
        self.search_input.setEnabled(active and not self.busy)
        self.thumbs.setEnabled(active and not self.busy)
        self.text_panel.apply_button.setEnabled(edit and self.preview is not None)
        self.canvas.setEnabled(not self.busy)
        self.overlay_panel.setEnabled(edit)
        self.text_panel.setEnabled(edit and ((self.run is not None and self.run.editable) or
            self.insertion_rect is not None))

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
        self.close_comparison()
        if self.session:
            self.session.close()
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.cancel_direct_crop()
        self.clear_search_results(True)
        self.session=session
        self.token+=1
        self.preview=None
        self.run=None
        self.annotation=None
        self.insertion_rect=None
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
        self.thumbs.setCurrentRow(0)
        self.thumbs.blockSignals(False)
        self.setWindowTitle(f"{session.source.name} — 墨頁 PDF")
        self.refresh_actions()
        self.request_render()
        self.queue_thumbnail(0,self.token,session.revision)

    def choose_comparison_pdf(self):
        if not self.session or self.busy:
            return
        name,_=QFileDialog.getOpenFileName(self,"選擇比較 PDF","","PDF (*.pdf)")
        if not name:
            return
        try:
            raw=Path(name).read_bytes()
            try:
                comparison_pdf,_=unlock_pdf(raw)
            except EditorError as exc:
                if exc.code!="PASSWORD":
                    raise
                password,ok=QInputDialog.getText(self,"PDF 密碼",Path(name).name,
                    QLineEdit.EchoMode.Password)
                if not ok:
                    return
                comparison_pdf,_=unlock_pdf(raw,password)
            self.open_comparison(comparison_pdf)
        except Exception as exc:
            self.error((getattr(exc,"code","OPEN"),str(exc),()))

    def open_comparison(self,comparison_pdf):
        if not self.session or self.busy:
            return
        base_pdf=flatten_overlays(self.session.pdf,self.session.overlays)
        with pymupdf.open(stream=comparison_pdf,filetype="pdf") as document:
            page_count=document.page_count
        self.close_comparison()
        dialog=ComparisonDialog(page_count,self.page,self)
        self.comparison_dialog=dialog
        self.comparison_pdf=comparison_pdf
        self.comparison_base_pdf=base_pdf
        self.comparison_base_page=self.page
        self.comparison_revision=self.session.revision
        self.comparison_token=self.token
        dialog.page_requested.connect(self.request_comparison_page)
        dialog.finished.connect(
            lambda _result,current=dialog:self.clear_comparison(current))
        dialog.destroyed.connect(
            lambda _object=None,current=dialog:self.clear_comparison(current))
        dialog.show()
        self.request_comparison_page(0)

    def request_comparison_page(self,page):
        dialog=self.comparison_dialog
        if dialog is None:
            return
        if not self.comparison_context_is_current(dialog):
            self.close_comparison()
            return
        if not 0<=page<dialog.comparison_page_count:
            return
        self.comparison_serial+=1
        serial=self.comparison_serial
        token=self.token
        revision=self.session.revision
        dialog.set_busy(True)

        def current_request():
            if (dialog is not self.comparison_dialog
                    or serial!=self.comparison_serial):
                return False
            if (not self.comparison_context_is_current(dialog)
                    or token!=self.token or revision!=self.session.revision):
                self.close_comparison()
                return False
            return True

        def done(result):
            if current_request():
                dialog.set_result(result)

        def failed(error):
            if current_request():
                self.show_comparison_error(dialog,error)

        self.jobs.submit(compare_pages,(self.comparison_base_pdf,self.comparison_base_page,
            self.comparison_pdf,page),done,failed)

    def show_comparison_error(self,dialog,error):
        _code,message,completed=error
        if completed:
            message+="\n已完成：\n"+"\n".join(completed)
        dialog.set_busy(False)
        dialog.summary.setText("比較失敗："+message.split("\n")[0])
        QMessageBox.warning(self,"無法比較頁面",message)
        self.statusBar().showMessage(message.split("\n")[0])

    def comparison_context_is_current(self,dialog):
        return (not self.closed and self.session is not None
            and dialog is self.comparison_dialog
            and self.comparison_pdf is not None
            and self.comparison_base_pdf is not None
            and self.comparison_base_page is not None
            and self.comparison_revision==self.session.revision
            and self.comparison_token==self.token)

    def clear_comparison(self,dialog):
        if dialog is not self.comparison_dialog:
            return
        self.comparison_serial+=1
        self.comparison_dialog=None
        self.comparison_pdf=None
        self.comparison_base_pdf=None
        self.comparison_base_page=None
        self.comparison_revision=None
        self.comparison_token=None

    def close_comparison(self):
        dialog=self.comparison_dialog
        if dialog is None:
            return
        self.clear_comparison(dialog)
        dialog.close()

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
        if self.thumbs.currentRow()!=page:
            self.thumbs.blockSignals(True)
            self.thumbs.setCurrentRow(page)
            self.thumbs.blockSignals(False)
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.cancel_direct_crop()
        self.run=None
        self.annotation=None
        self.insertion_rect=None
        self.text_panel.setEnabled(False)
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(page+1)
        self.page_spin.blockSignals(False)
        self.refresh_actions()
        self.request_render()

    def sync_page_navigation(self,selected=None,selected_pages=None):
        with pymupdf.open(stream=self.session.pdf,filetype="pdf") as doc:
            self.page_count=doc.page_count
        self.page=max(0,min(self.page if selected is None else selected,self.page_count-1))
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1,self.page_count)
        self.page_spin.setValue(self.page+1)
        self.page_spin.blockSignals(False)
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        for index in range(self.page_count):
            self.thumbs.addItem(QListWidgetItem(f"第 {index+1} 頁"))
        self.thumbs.setCurrentRow(self.page)
        if selected_pages:
            self.thumbs.clearSelection()
            for page in selected_pages:
                if 0<=page<self.page_count:
                    self.thumbs.item(page).setSelected(True)
        self.thumbs.blockSignals(False)
        self.thumbnail_selection_changed()
        self.page_data=None
        self.refresh_actions()
        self.request_render()
        self.queue_thumbnail(0,self.token,self.session.revision)

    def submit_page_operation(self,operation,pages,target,status,selected,selected_pages=None):
        content_operations={"crop","page_number","text_watermark","image_watermark",
            "header_footer"}
        allowed=(self.session.access.can_edit if self.session and operation in content_operations
            else self.session.access.can_reorganize if self.session else False)
        if not self.session or self.busy or not allowed:
            return
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.cancel_direct_crop()
        self.preview=None
        self.run=None
        self.annotation=None
        self.insertion_rect=None
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(status)
        def done(pdf):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.session.apply_state(pdf,())
            self.text_panel.setEnabled(False)
            self.sync_page_navigation(selected,selected_pages)
        self.jobs.submit(edit_page_document,
            (self.session.pdf,self.session.overlays,operation,pages,target),done,self.error)

    def selected_thumbnail_indices(self):
        return tuple(sorted(self.thumbs.row(item) for item in self.thumbs.selectedItems()))

    def selected_page_indices(self):
        selected=self.selected_thumbnail_indices()
        if selected:
            return selected
        return (self.page,) if self.session and 0<=self.page<self.page_count else ()

    def run_ocr(self):
        pages=self.selected_thumbnail_indices()
        if not pages:
            return
        self.apply_ocr_to_pages(pages)

    def apply_ocr_to_pages(self,pages):
        pages=tuple(pages)
        if (not self.session or self.busy or not self.session.access.can_edit or not pages):
            return
        try:
            pdf=flatten_overlays(self.session.pdf,self.session.overlays) if self.session.overlays else self.session.pdf
            tessdata=validate_ocr_assets()
        except EditorError as exc:
            self.error((exc.code,str(exc),()))
            return
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(f"正在辨識 {len(pages)} 頁…")
        def done(result):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            if result.processed_pages:
                self.session.apply_state(result.pdf,())
                self.clear_search_results()
                self.page_data=None
                summary=(f"OCR 完成：辨識 {len(result.processed_pages)} 頁，跳過 "
                    f"{len(result.skipped_pages)} 頁，共 {result.word_count} 個文字區段。")
                self.request_render(summary)
                self.queue_thumbnail(0,self.token,self.session.revision)
            else:
                self.refresh_actions()
                self.statusBar().showMessage(
                    f"OCR 完成：全部 {len(result.skipped_pages)} 頁已有文字，未建立變更。")
        self.jobs.submit(ocr_pages,(pdf,pages,tessdata),done,self.error)

    def thumbnail_selection_changed(self):
        selected=self.selected_page_indices()
        self.thumbs.setDragEnabled(bool(selected))
        self.refresh_actions()
        if len(selected)>1 and not self.busy:
            self.statusBar().showMessage(f"已選取 {len(selected)} 頁，可整組拖曳或使用頁面操作。")

    def move_current_page(self,offset):
        pages=self.selected_page_indices()
        if not pages:
            return
        order,moved=page_order_after_move(self.page_count,pages,offset)
        if order==tuple(range(self.page_count)):
            return
        current=order.index(self.page)
        direction="上移" if offset<0 else "下移"
        self.submit_page_operation("move",pages,offset,
            f"正在{direction} {len(pages)} 頁…",current,moved)

    def move_selected_pages_to(self,pages,destination):
        if not pages:
            return
        order,moved=page_order_after_drop(self.page_count,pages,destination)
        if order==tuple(range(self.page_count)):
            return
        current=order.index(self.page)
        self.submit_page_operation("move_to",pages,destination,
            f"正在拖曳移動 {len(pages)} 頁…",current,moved)

    def rotate_current_page(self,degrees):
        if degrees not in (-90,90):
            return
        direction="逆時針" if degrees<0 else "順時針"
        pages=self.selected_page_indices()
        self.submit_page_operation("rotate",pages,degrees,
            f"正在將 {len(pages)} 頁{direction}旋轉…",self.page,pages)

    def delete_current_page(self):
        pages=self.selected_page_indices()
        if not pages or len(pages)>=self.page_count:
            return
        remaining=[page for page in range(self.page_count) if page not in set(pages)]
        if self.page in remaining:
            selected=remaining.index(self.page)
        else:
            selected=min(sum(page<self.page for page in remaining),len(remaining)-1)
        self.submit_page_operation("delete",pages,None,
            f"正在刪除 {len(pages)} 頁…",selected,(selected,))

    def duplicate_selected_pages(self):
        pages=self.selected_page_indices()
        if not pages:
            return
        first=pages[-1]+1
        copies=tuple(range(first,first+len(pages)))
        current=first+pages.index(self.page) if self.page in pages else first
        self.submit_page_operation("duplicate",pages,None,
            f"正在複製 {len(pages)} 頁…",current,copies)

    def add_blank_page(self):
        pages=self.selected_page_indices()
        if not pages:
            return
        after=pages[-1]
        inserted=after+1
        self.submit_page_operation("blank",(after,),None,
            "正在新增空白頁…",inserted,(inserted,))

    def insert_pdf_pages(self):
        if not self.session or self.busy:
            return
        name,_=QFileDialog.getOpenFileName(self,"插入另一份 PDF","","PDF (*.pdf)")
        if not name:
            return
        try:
            raw=Path(name).read_bytes()
            try:
                data,access=unlock_pdf(raw)
            except EditorError as exc:
                if exc.code!="PASSWORD":
                    raise
                password,ok=QInputDialog.getText(self,"PDF 密碼",Path(name).name,
                    QLineEdit.EchoMode.Password)
                if not ok:
                    return
                data,access=unlock_pdf(raw,password)
            if not access.can_reorganize:
                raise EditorError("READ_ONLY",access.reason)
            self.apply_insert_pages(data)
        except Exception as exc:
            self.error((getattr(exc,"code","OPEN"),str(exc),()))

    def apply_insert_pages(self,source_pdf):
        pages=self.selected_page_indices()
        if not pages:
            return
        with pymupdf.open(stream=source_pdf,filetype="pdf") as source:
            count=source.page_count
        after=pages[-1]
        inserted=tuple(range(after+1,after+1+count))
        self.submit_page_operation("insert",(after,),source_pdf,
            f"正在插入 {count} 頁…",after+1,inserted)

    def extract_selected_pages(self):
        if not self.session or self.busy:
            return
        pages=self.selected_page_indices()
        if not pages:
            return
        suggested=self.session.source.with_name(self.session.source.stem+"-抽取.pdf")
        name,_=QFileDialog.getSaveFileName(self,"抽取選取頁面",str(suggested),"PDF (*.pdf)")
        if not name:
            return
        target=Path(name)
        if target.suffix.lower()!=".pdf":
            target=target.with_suffix(".pdf")
        self.extract_selected_to(target,pages)

    def extract_selected_to(self,target,pages=None):
        pages=tuple(pages) if pages is not None else self.selected_page_indices()
        if not self.session or self.busy or not pages:
            return
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(f"正在抽取 {len(pages)} 頁…")
        def done(path):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.refresh_actions()
            self.statusBar().showMessage("已抽取頁面："+path)
        self.jobs.submit(export_extract,(self.session.pdf,self.session.overlays,pages,
            str(target),str(self.session.source)),done,self.error)

    def export_selected_pages_png(self):
        if not self.session or self.busy:
            return
        folder=QFileDialog.getExistingDirectory(self,"選擇 PNG 輸出資料夾",
            str(self.session.source.parent))
        if not folder:
            return
        dpi,ok=QInputDialog.getInt(self,"PNG 解析度","DPI：",150,72,600,1)
        if ok:
            self.export_selected_png_to(Path(folder),dpi)

    def export_selected_png_to(self,folder,dpi,pages=None):
        pages=tuple(pages) if pages is not None else self.selected_page_indices()
        if not self.session or self.busy or not pages:
            return
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(f"正在將 {len(pages)} 頁輸出為 PNG…")
        def done(paths):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.refresh_actions()
            self.statusBar().showMessage(f"已輸出 {len(paths)} 張 PNG 至：{folder}")
        self.jobs.submit(export_page_images,(self.session.pdf,self.session.overlays,pages,
            str(folder),self.session.source.stem,dpi),done,self.error)

    def show_crop_dialog(self):
        pages=self.selected_page_indices()
        if not pages:
            return
        dialog=CropPagesDialog(len(pages),self)
        if dialog.exec():
            self.apply_page_crop(dialog.margins())

    def apply_page_crop(self,margins,pages=None):
        pages=tuple(pages) if pages is not None else self.selected_page_indices()
        if not pages:
            return
        self.submit_page_operation("crop",pages,margins,
            f"正在裁切 {len(pages)} 頁…",self.page,pages)

    def toggle_direct_crop(self,checked):
        if checked:
            self.start_direct_crop()
        else:
            self.canvas.cancel_crop()
            self.cancel_direct_crop()

    def start_direct_crop(self):
        if not self.page_data or not self.session or self.busy:
            self.actions["direct_crop"].setChecked(False)
            return
        pages=self.selected_page_indices()
        if not pages:
            self.actions["direct_crop"].setChecked(False)
            return
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.crop_pages=pages
        self.actions["direct_crop"].setChecked(True)
        self.canvas.start_crop(self.page_data["bounds"])
        self.statusBar().showMessage(
            f"拖曳綠色裁切框的邊線或四角；放開後套用到 {len(pages)} 個選取頁面。")

    def cancel_direct_crop(self):
        if self.canvas._crop_mode:
            self.canvas.cancel_crop()
        self.crop_pages=()
        self.actions["direct_crop"].setChecked(False)

    def apply_direct_crop(self,rect):
        pages=self.crop_pages
        if not pages or not self.page_data:
            self.cancel_direct_crop()
            return
        x0,y0,x1,y1=(float(value) for value in rect)
        bx0,by0,bx1,by1=self.page_data["bounds"]
        margins=(max(0,x0-bx0),max(0,y0-by0),max(0,bx1-x1),max(0,by1-y1))
        self.cancel_direct_crop()
        self.apply_page_crop(margins,pages)

    def show_page_decoration_dialog(self):
        pages=self.selected_page_indices()
        if not pages:
            return
        dialog=PageDecorationDialog(len(pages),self)
        if dialog.exec():
            operation,options=dialog.selection()
            self.apply_page_decoration(operation,options)

    def apply_page_decoration(self,operation,options):
        pages=self.selected_page_indices()
        if not pages or operation not in ("page_number","text_watermark","image_watermark"):
            return
        target=dict(options)
        if operation in ("page_number","text_watermark"):
            target["font_path"]=str(default_font())
        labels={"page_number":"頁碼","text_watermark":"文字浮水印",
            "image_watermark":"圖片浮水印"}
        self.submit_page_operation(operation,pages,target,
            f"正在加入{labels[operation]}至 {len(pages)} 頁…",self.page,pages)

    def show_header_footer_dialog(self):
        pages=self.selected_page_indices()
        if not pages:
            return
        dialog=HeaderFooterTemplatesDialog(self.header_footer_templates,len(pages),self)
        if dialog.exec():
            self.apply_header_footer(dialog.selection())

    def apply_header_footer(self,options):
        pages=self.selected_page_indices()
        if not pages:
            return
        target=dict(options)
        target["font_path"]=str(default_font())
        self.submit_page_operation("header_footer",pages,target,
            f"正在加入頁首頁尾至 {len(pages)} 頁…",self.page,pages)

    def change_zoom(self,text):
        if text=="適合頁面":
            self.fit_zoom("page")
            return
        if text=="適合寬度":
            self.fit_zoom("width")
            return
        self.scale=float(text.rstrip("%"))/100
        self.request_render()

    def fit_zoom(self,mode):
        if not self.page_data or mode not in ("page","width"):
            return
        base_width=self.page_data["display_size"][0]/self.scale
        base_height=self.page_data["display_size"][1]/self.scale
        available_width=max(100,self.canvas.viewport().width()-32)
        available_height=max(100,self.canvas.viewport().height()-32)
        scale=available_width/base_width
        if mode=="page":
            scale=min(scale,available_height/base_height)
        self.scale=max(0.1,min(5.0,scale))
        label="適合頁面" if mode=="page" else "適合寬度"
        self.zoom.blockSignals(True)
        self.zoom.setCurrentText(label)
        self.zoom.blockSignals(False)
        self.request_render()

    def focus_search(self):
        if not self.session:
            return
        self.search_input.setFocus()
        self.search_input.selectAll()

    def clear_search_results(self,clear_query=False):
        self.search_results=()
        self.search_index=-1
        self.search_revision=None
        self.search_count.setText("0 / 0")
        self.canvas.clear_search_result()
        if clear_query:
            self.search_input.clear()

    def perform_search(self,query=None):
        if not self.session or self.busy:
            return
        text=(self.search_input.text() if query is None else str(query)).strip()
        if not text:
            self.clear_search_results()
            self.statusBar().showMessage("請輸入要搜尋的文字。")
            return
        self.search_input.setText(text)
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage("正在搜尋整份文件…")
        def done(matches):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.search_results=matches
            self.search_index=0 if matches else -1
            self.search_revision=revision
            self.refresh_actions()
            if matches:
                self.show_search_result()
            else:
                self.search_count.setText("0 / 0")
                self.canvas.clear_search_result()
                self.statusBar().showMessage(f"找不到「{text}」。")
        self.jobs.submit(find_text,(self.session.pdf,text),done,self.error)

    def next_search_result(self,step=1):
        if (not self.session or not self.search_results or
                self.search_revision!=self.session.revision):
            self.clear_search_results()
            self.refresh_actions()
            return
        self.search_index=(self.search_index+step)%len(self.search_results)
        self.show_search_result()

    def show_search_result(self):
        if not self.search_results or not 0<=self.search_index<len(self.search_results):
            return
        match=self.search_results[self.search_index]
        self.search_count.setText(f"{self.search_index+1} / {len(self.search_results)}")
        if self.page!=match.page:
            self.goto_page(match.page)
        elif self.page_data and self.page_data["page"]==match.page:
            self.canvas.show_search_result(match.rect)
        self.statusBar().showMessage(
            f"搜尋結果 {self.search_index+1} / {len(self.search_results)}，第 {match.page+1} 頁")

    def request_render(self,completion_status=None):
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
                self.canvas.display(result,layers,self.layer_id)
                if (self.search_results and self.search_revision==self.session.revision and
                        self.search_results[self.search_index].page==self.page):
                    self.canvas.show_search_result(self.search_results[self.search_index].rect)
                if self.annotation is not None:
                    selected=next((item for item in result.get("annotations",())
                        if item.xref==self.annotation.xref),None)
                    self.annotation=selected
                    if selected is not None:
                        self.canvas.select_annotation(selected)
            except EditorError as exc:
                self.error((exc.code,str(exc),()))
            self.refresh_actions()
            if completion_status is not None:
                self.statusBar().showMessage(completion_status)
            else:
                status=self.session.access.reason or ("預覽中，尚未套用" if self.preview else
                    "有未儲存變更" if self.session.dirty else "可編輯")
                self.statusBar().showMessage(f"第 {self.page+1} / {self.page_count} 頁  ·  {status}")
        pixel_ratio=float(self.canvas.devicePixelRatioF())
        self.jobs.submit(render_page,(data,self.page,self.scale,pixel_ratio),done,self.error)

    def select_run(self,run):
        if not self.session or not self.session.access.can_edit or self.preview:
            return
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.clear_annotation_selection()
        self.annotation=None
        self.run=run
        self.insertion_rect=None
        self.panels.setCurrentWidget(self.text_panel)
        self.text_panel.set_run(run)
        self.text_panel.font_path=str(default_font())
        self.text_panel.font_label.setText("替代字型：Noto Sans CJK TC（完整繁中文字元）")
        original=embedded_font(self.session.pdf,self.page,run,self.session.history.root)
        if original:
            self.text_panel.font_path=str(original)
            self.text_panel.font_label.setText("原字型："+run.font_name+"；缺字時請使用內建中文字型。")
        cell=find_table_cell(self.session.pdf,self.page,run.rect)
        if cell:
            self.text_panel.set_rect(cell)
            self.text_panel.set_alignment(2)
        self.text_panel.info.setText("請直接在頁面文字框輸入；Enter 或點到別處套用，Esc 取消。")
        self.canvas.begin_inline_text(self.text_panel.rect(),run.text,run,run.size,
            self.text_panel.alignment.currentData())
        self.refresh_actions()

    def commit_inline_text(self,payload):
        if not self.session or self.busy:
            return
        run,text,rect=payload
        p=self.text_panel
        p.text.setPlainText(text)
        target_rect=tuple(rect or p.rect())
        if run is not None and text==run.text and not p.modified:
            self.refresh_actions()
            self.statusBar().showMessage("文字維持不變，可繼續使用標記或格式工具。")
            return
        font_path=p.font_path
        if text:
            try:
                checked_font(font_path,text)
            except EditorError as exc:
                if exc.code!="FONT_MISSING_GLYPH":
                    self.error((exc.code,str(exc),()))
                    return
                font_path=str(default_font())
                p.font_path=font_path
                p.font_label.setText("已自動改用內建中文字型，以完整顯示新文字。")
        if run is None:
            if not text:
                self.run=None
                self.insertion_rect=None
                self.text_panel.setEnabled(False)
                self.refresh_actions()
                self.request_render()
                self.statusBar().showMessage("已取消新增文字。")
                return
            request=TextInsertion(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
                text,target_rect,font_path,p.size.value(),p.color,p.alignment.currentData())
            self.apply_text_immediately(request,"正在新增文字…",insert_text)
            return
        request=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
            run.id,text,target_rect,font_path,p.size.value(),p.color,p.alignment.currentData())
        self.apply_text_immediately(request,"正在更新文字…")

    def apply_text_format(self):
        if not self.session or self.busy or self.preview or not self.run:
            return
        if self.canvas.inline_editor is not None:
            self.canvas.inline_editor.commit()
            return
        self.commit_inline_text((self.run,self.text_panel.text.toPlainText(),self.text_panel.rect()))

    def cancel_inline_text(self):
        self.run=None
        self.insertion_rect=None
        self.text_panel.setEnabled(False)
        self.refresh_actions()
        self.request_render()
        self.statusBar().showMessage("已取消文字編輯。")

    def preview_from_panel(self):
        if not self.session or self.busy:
            return
        p=self.text_panel
        if self.insertion_rect is not None:
            req=TextInsertion(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
                p.text.toPlainText(),p.rect(),p.font_path,p.size.value(),p.color,
                p.alignment.currentData())
            self.preview_replacement(req,insert_text)
            return
        if not self.run:
            return
        req=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),
            self.page,self.run.id,p.text.toPlainText(),p.rect(),p.font_path,p.size.value(),p.color,
            p.alignment.currentData())
        self.preview_replacement(req)

    def move_run(self,run):
        if not self.session or not run.editable or self.busy or self.preview:
            return
        self.canvas.cancel_inline_editor()
        self.run=run
        x0,y0,x1,y1=run.rect
        self.text_panel.set_rect((x0,y0,x1+10,y1+run.size*0.5))
        p=self.text_panel
        request=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
            run.id,p.text.toPlainText(),p.rect(),p.font_path,p.size.value(),p.color,
            p.alignment.currentData())
        self.apply_text_immediately(request,"正在移動文字…")

    def start_text_insertion(self,checked=True):
        if not checked:
            self.cancel_text_insertion()
            return
        if self.preview:
            self.actions["add_text"].setChecked(False)
            message="請先按「套用預覽」或「取消預覽」，再新增文字。"
            QMessageBox.information(self,"尚有文字預覽",message)
            self.statusBar().showMessage(message)
            return
        if not self.session or self.busy:
            self.actions["add_text"].setChecked(False)
            return
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.clear_annotation_selection()
        self.annotation=None
        self.run=None
        self.insertion_rect=None
        self.canvas.start_text_insertion()
        self.text_panel.setEnabled(False)
        self.statusBar().showMessage("請在頁面空白處或空白儲存格中點一下。")

    def cancel_text_insertion(self):
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.statusBar().showMessage("已取消新增文字。")

    def start_text_note(self,checked=True):
        if not checked:
            self.cancel_text_note()
            return
        if not self.session or self.busy or not self.session.access.can_edit:
            self.actions["text_note"].setChecked(False)
            return
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.clear_annotation_selection()
        self.annotation=None
        self.run=None
        self.insertion_rect=None
        self.text_panel.setEnabled(False)
        self.canvas.start_note_insertion()
        self.refresh_actions()
        self.statusBar().showMessage("請在頁面上點選文字註解的位置。")

    def cancel_text_note(self):
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.statusBar().showMessage("已取消文字註解。")

    def begin_text_note(self,position):
        self.actions["text_note"].setChecked(False)
        text,ok=QInputDialog.getMultiLineText(self,"新增文字註解","註解內容：")
        if not ok or not text.strip():
            self.statusBar().showMessage("已取消文字註解。")
            return
        self.submit_annotation(add_text_note,(self.page,position,text),"正在新增文字註解…")

    def start_annotation_selection(self,checked=True):
        if not checked:
            self.canvas.cancel_annotation_selection()
            self.statusBar().showMessage("已取消選取註解。")
            return
        if not self.session or self.busy or not self.session.access.can_edit:
            self.actions["select_annotation"].setChecked(False)
            return
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.annotation=None
        self.run=None
        self.insertion_rect=None
        self.text_panel.setEnabled(False)
        self.canvas.start_annotation_selection()
        self.refresh_actions()
        self.statusBar().showMessage("請直接點選頁面上的螢光、底線或文字註解。")

    def select_annotation(self,item):
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.cancel_inline_editor()
        self.canvas.clear_text_selection()
        self.canvas.select_annotation(item)
        self.annotation=item
        self.run=None
        self.insertion_rect=None
        self.text_panel.setEnabled(False)
        self.refresh_actions()
        names={"Highlight":"螢光標記","Underline":"底線","Text":"文字註解"}
        self.statusBar().showMessage(
            f"已選取{names.get(item.kind,'註解')}；按 Delete 可直接刪除。")

    def delete_selected_annotation(self,item=None):
        if hasattr(item,"xref"):
            self.annotation=item
        if not self.annotation or self.busy:
            return
        selected=self.annotation
        self.submit_annotation(delete_annotation,(self.page,selected.xref),"正在刪除註解…")

    def change_highlight_color(self,color):
        if not self.annotation or self.annotation.kind!="Highlight" or self.busy:
            return
        self.submit_annotation(set_highlight_color,
            (self.page,self.annotation.xref,color),"正在變更螢光標記顏色…")

    def apply_selected_markup(self,kind):
        if not self.run or self.busy:
            return
        run=self.run
        self.canvas.cancel_inline_editor()
        label="螢光標記" if kind=="highlight" else "底線"
        self.submit_annotation(mark_text,(self.page,run.rect,kind),f"正在加入{label}…")

    def submit_annotation(self,operation,args,status):
        if not self.session or self.busy:
            return
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(status)
        def done(pdf):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.session.apply_pdf(pdf)
            self.run=None
            self.annotation=None
            self.canvas.clear_text_selection()
            self.canvas.clear_annotation_selection()
            self.canvas.cancel_annotation_selection()
            self.actions["select_annotation"].setChecked(False)
            self.text_panel.setEnabled(False)
            self.page_data=None
            self.refresh_actions()
            self.request_render()
            self.queue_thumbnail(0,self.token,self.session.revision)
        self.jobs.submit(operation,(self.session.pdf,*args),done,self.error)

    def begin_text_insertion(self,position):
        if not self.session or self.busy or self.preview:
            return
        x,y=position
        self.actions["add_text"].setChecked(False)
        cell=find_table_cell(self.session.pdf,self.page,(x,y,x,y))
        if cell:
            rect=cell
        else:
            width,height=self.page_data["bounds"][2:]
            x=max(0,min(x,width-160))
            y=max(0,min(y,height-40))
            rect=(x,y,min(width,x+160),min(height,y+40))
        self.run=None
        self.insertion_rect=rect
        self.panels.setCurrentWidget(self.text_panel)
        self.text_panel.font_path=str(default_font())
        self.text_panel.font_label.setText("替代字型：Noto Sans CJK TC（完整繁中文字元）")
        self.text_panel.set_insertion(rect,centered=cell is not None)
        self.canvas.begin_inline_text(rect,"",None,self.text_panel.size.value(),
            self.text_panel.alignment.currentData())
        self.refresh_actions()

    def delete_run(self,run):
        if not self.session or not run.editable or self.busy or self.preview:
            return
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.run=run
        p=self.text_panel
        cell=find_table_cell(self.session.pdf,self.page,run.rect)
        x0,y0,x1,y1=run.rect
        insertion_rect=cell or (x0,y0,x1+40,y1+run.size)
        request=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),
            self.page,run.id,"",run.rect,p.font_path,run.size,run.color,"left")
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage("正在刪除文字…")
        def done(pdf):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.session.apply_pdf(pdf)
            self.preview=None
            self.run=None
            self.insertion_rect=insertion_rect
            self.text_panel.font_path=str(default_font())
            self.text_panel.font_label.setText("替代字型：Noto Sans CJK TC（完整繁中文字元）")
            self.text_panel.set_insertion(insertion_rect,run.size,cell is not None)
            self.refresh_actions()
            self.request_render()
            self.canvas.begin_inline_text(insertion_rect,"",None,run.size,
                self.text_panel.alignment.currentData())
        self.jobs.submit(replace_text,(self.session.pdf,request),done,self.error)

    def apply_text_immediately(self,request,status,operation=replace_text):
        if self.busy:
            return
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(status)
        def done(pdf):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.session.apply_pdf(pdf)
            self.preview=None
            self.run=None
            self.insertion_rect=None
            self.text_panel.setEnabled(False)
            self.refresh_actions()
            self.request_render()
            self.queue_thumbnail(0,self.token,self.session.revision)
        self.jobs.submit(operation,(self.session.pdf,request),done,self.error)

    def preview_replacement(self,request,operation=replace_text):
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
        self.jobs.submit(operation,(self.session.pdf,request),done,self.error)

    def cancel_preview(self):
        self.preview=None
        self.refresh_actions()
        self.request_render()

    def apply_preview(self):
        if self.preview and self.preview[0]==self.session.revision:
            self.canvas.cancel_inline_editor()
            self.session.apply_pdf(self.preview[1])
            self.preview=None
            self.canvas.cancel_text_insertion()
            self.actions["add_text"].setChecked(False)
            self.insertion_rect=None
            self.run=None
            self.text_panel.setEnabled(False)
            self.refresh_actions()
            self.request_render()
            self.queue_thumbnail(0,self.token,self.session.revision)

    def history_step(self,redo):
        if not self.session or self.busy:
            return
        self.preview=None
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.clear_annotation_selection()
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.insertion_rect=None
        self.run=None
        self.annotation=None
        self.session.redo() if redo else self.session.undo()
        self.text_panel.setEnabled(False)
        self.sync_page_navigation()

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
        self.canvas.cancel_inline_editor()
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
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        if not self.confirm_leave():
            event.ignore()
            return
        self.closed=True
        self.close_comparison()
        self.jobs.close()
        if self.session:
            self.session.close()
            self.session=None
        event.accept()
