"""頁面操作：順序、旋轉、裁切、插入、抽取、合併、拆分與縮圖排程。"""
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QListWidgetItem, QFileDialog, QInputDialog, QLineEdit
from pathlib import Path
from pdf_editor.engine.fonts import default_font
from pdf_editor.engine.inspection import unlock_pdf
from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.engine.render import thumbnail
from pdf_editor.errors import EditorError
from pdf_editor.logs import get_logger
from pdf_editor.ocr import ocr_pages
from pdf_editor.ocr_assets import validate_ocr_assets
from pdf_editor.pages import page_order_after_move, page_order_after_drop
from pdf_editor.ui.background_jobs import (edit_page_document, export_extract, export_merge,
    export_page_images, export_split)
from pdf_editor.ui.page_dialogs import (MergeDialog,SplitDialog,CropPagesDialog,
    PageDecorationDialog,HeaderFooterTemplatesDialog)
from pdf_editor.ui.style import glyph_icon, thumbnail_icon
import pymupdf


class PageActionsMixin:
    def queue_thumbnails(self,pages=None):
        """排入需要重畫的縮圖；pages 為 None 代表全部頁面。

        尚未完成的頁面會保留並與新的頁面合併，畫面上看得到的縮圖優先處理。
        """
        if self.closed or not self.session:
            return
        targets=range(self.page_count) if pages is None else pages
        if self._thumb_token!=self.token:
            self._thumb_queue=[]
            self._thumb_token=self.token
        merged=set(self._thumb_queue)|{page for page in targets if 0<=page<self.page_count}
        self._thumb_queue=self.prioritized_thumbnails(merged)
        self._pump_thumbnails()

    def visible_thumbnail_rows(self):
        viewport=self.thumbs.viewport().rect()
        rows=[self.thumbs.row(self.thumbs.itemAt(point)) for point in
            (viewport.topLeft(),viewport.center(),viewport.bottomLeft())
            if self.thumbs.itemAt(point) is not None]
        if not rows:
            return range(0)
        # 上下各多留一列，捲動時不會馬上看到空白縮圖。
        return range(max(0,min(rows)-1),min(self.page_count,max(rows)+2))

    def prioritized_thumbnails(self,pages):
        visible=set(self.visible_thumbnail_rows())
        return sorted((page for page in pages if 0<=page<self.page_count),
            key=lambda page:(page not in visible,abs(page-self.page),page))

    def reprioritize_thumbnails(self):
        if self._thumb_queue:
            self._thumb_queue=self.prioritized_thumbnails(self._thumb_queue)

    def _pump_thumbnails(self):
        if self._thumb_running or not self._thumb_queue or self.closed or not self.session:
            return
        index=self._thumb_queue.pop(0)
        token,revision=self.token,self.session.revision
        self._thumb_running=True
        def current():
            return (not self.closed and self.session is not None and token==self.token
                and revision==self.session.revision and index<self.page_count)
        def finish():
            self._thumb_running=False
            self._pump_thumbnails()
        def done(png):
            if current():
                pix=QPixmap()
                pix.loadFromData(png)
                item=self.thumbs.item(index)
                if item:
                    item.setIcon(thumbnail_icon(pix))
                    item.setToolTip("")
            elif (not self.closed and self.session is not None and token==self.token
                    and index<self.page_count):
                # 產生期間文件已變更，結果過期，重新排入。
                self._thumb_queue=self.prioritized_thumbnails(set(self._thumb_queue)|{index})
            finish()
        def failed(error):
            if current():
                item=self.thumbs.item(index)
                if item:
                    item.setIcon(glyph_icon("thumbnail_error",48))
                    item.setToolTip("縮圖產生失敗："+error[1])
                get_logger().warning("第 %s 頁縮圖產生失敗：%s",index+1,error[1])
                self.statusBar().showMessage(f"第 {index+1} 頁縮圖產生失敗，頁面內容仍可正常檢視。")
            finish()
        self.jobs.submit(thumbnail,(str(self.session.pdf_path),index),done,failed)

    def sync_page_navigation(self,selected=None,selected_pages=None):
        with pymupdf.open(stream=self.session.pdf,filetype="pdf") as doc:
            self.page_count=doc.page_count
        self.page=max(0,min(self.page if selected is None else selected,self.page_count-1))
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1,self.page_count)
        self.page_spin.setValue(self.page+1)
        self.page_spin.blockSignals(False)
        self.thumbs.blockSignals(True)
        if self.thumbs.count()!=self.page_count:
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
        self.queue_thumbnails()
        self.refresh_outline()

    def submit_page_operation(self,operation,pages,target,status,selected,selected_pages=None):
        content_operations={"crop","page_number","text_watermark","image_watermark",
            "header_footer"}
        allowed=(self.session.access.can_edit if self.session and operation in content_operations
            else self.session.access.can_reorganize if self.session else False)
        if not self.session or self.busy or not allowed:
            return
        self.cancel_editing_modes()
        self.clear_selection_state()
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
                self.queue_thumbnails(result.processed_pages)
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
        name,_=QFileDialog.getOpenFileName(self,"插入另一份 PDF",self.settings.last_directory(),"PDF (*.pdf)")
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
        self.cancel_editing_modes(crop=False)
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
