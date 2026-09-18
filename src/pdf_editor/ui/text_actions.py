"""文字編輯與註解：選取、輸入、格式、標記與復原。"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QInputDialog
from pdf_editor.annotations import (mark_text,add_text_note,delete_annotation,
    set_highlight_color)
from pdf_editor.engine.fonts import default_font,embedded_font,checked_font,similar_font
from pdf_editor.engine.text import overlaps as text_overlaps
from pdf_editor.engine.text import page_cell_index
from pdf_editor.engine.text import replace_text, insert_text, find_table_cell
from pdf_editor.errors import EditorError
from pdf_editor.model import TextReplacement, TextInsertion
import hashlib
import pymupdf


class TextActionsMixin:
    def select_run(self,run,open_editor=True):
        if not self.session or not self.session.access.can_edit:
            return
        self.text_panel.cancel_pending_format()
        self.canvas.clear_conflicts()
        self.cancel_editing_modes(crop=False)
        self.canvas.clear_annotation_selection()
        self.annotation=None
        self.run=run
        self.insertion_rect=None
        self.panels.setCurrentWidget(self.text_panel)
        self.text_panel.set_run(run)
        self.text_panel.font_path=str(default_font())
        self.text_panel.font_label.setText("替代字型：Noto Sans CJK TC（完整繁中文字元）")
        if not run.editable:
            message="此文字無法安全修改，但仍可加入螢光標記或底線。"
            self.text_panel.info.setText(message)
            self.refresh_actions()
            self.statusBar().showMessage(message)
            return
        original=embedded_font(self.session.pdf,self.page,run,self.session.history.root)
        if original:
            self.text_panel.font_path=str(original)
            self.text_panel.font_label.setText("原字型："+run.font_name+"；缺字時會改用相近字型。")
        else:
            similar=similar_font(run.font_name,run.text)
            if similar:
                self.text_panel.font_path=similar[1]
                self.text_panel.font_label.setText(
                    f"原字型 {run.font_name} 無法直接使用，改用相近字型：{similar[0]}")
        cell=find_table_cell(self.session.pdf,self.page,run.rect,self.session.document_key)
        if cell:
            self.text_panel.set_rect(cell)
            self.text_panel.set_alignment(2)
        elif self.page_data and self.is_page_centered(run.rect):
            # 標題等置中文字：改字後維持以原中心點置中。
            self.text_panel.set_alignment(1)
        if not open_editor:
            self.canvas.highlight_run(run)
            self.text_panel.info.setText("已套用。右側可繼續調整格式；按 Enter、F2 或再點一次文字可修改內容。")
            self.refresh_actions()
            return
        self.text_panel.info.setText("請直接在頁面文字框輸入；Enter 或點到別處套用，Esc 取消。")
        self.canvas.begin_inline_text(self.text_panel.rect(),run.text,run,run.size,
            self.text_panel.alignment.currentData(),self.text_panel.font_path)
        self.refresh_actions()

    def is_page_centered(self,rect,tolerance=3.0):
        width=self.page_data["bounds"][2]
        center=(rect[0]+rect[2])/2
        return abs(center-width/2)<=tolerance and rect[2]-rect[0]<=width*0.9

    def commit_inline_text(self,payload):
        if not self.session or self.busy:
            return
        run,text,rect=payload
        p=self.text_panel
        p.text.setPlainText(text)
        target_rect=tuple(rect or p.rect())
        self.canvas.clear_conflicts()
        # 表格文字：提交時以儲存格重新解析，確保改字後仍位於表格正中央；
        # 若使用者已手動移動文字框（差異超過 2 點）則尊重其設定。
        in_cell=False
        if run is not None and p.alignment.currentData()=="center":
            cell=find_table_cell(self.session.pdf,self.page,run.rect,self.session.document_key)
            manual=tuple(p.rect())
            if cell and (not p.modified or abs(manual[0]-cell[0])+abs(manual[1]-cell[1])
                    +abs(manual[2]-cell[2])+abs(manual[3]-cell[3])<2.0):
                target_rect=cell
                in_cell=True
        elif run is None and self.insertion_rect is not None:
            in_cell=find_table_cell(self.session.pdf,self.page,target_rect,
                self.session.document_key)==tuple(target_rect)
        # 儲存格內縮小字級；一般文字自動加寬文字框。
        fit="shrink" if in_cell else "expand"
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
                similar=similar_font(run.font_name if run is not None else "",text)
                if similar:
                    font_path=similar[1]
                    p.font_label.setText(f"原字型缺少部分字元，已改用相近字型：{similar[0]}")
                else:
                    font_path=str(default_font())
                    p.font_label.setText("已自動改用內建中文字型，以完整顯示新文字。")
                p.font_path=font_path
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
                text,target_rect,font_path,p.size.value(),p.color,p.alignment.currentData(),
                p.bold.isChecked(),fit)
            self.apply_text_immediately(request,"正在新增文字…",insert_text,
                editor_state=(None,text,target_rect))
            return
        request=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
            run.id,text,target_rect,font_path,p.size.value(),p.color,p.alignment.currentData(),
            p.bold.isChecked(),fit)
        self.apply_text_immediately(request,"正在更新文字…",
            editor_state=(run,text,target_rect))

    def apply_text_format(self):
        if not self.session or self.busy or not self.run:
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

    def move_run(self,run):
        if not self.session or not run.editable or self.busy:
            return
        self.canvas.cancel_inline_editor()
        self.run=run
        self.text_panel.set_rect(run.rect)
        p=self.text_panel
        request=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
            run.id,p.text.toPlainText(),p.rect(),p.font_path,p.size.value(),p.color,
            p.alignment.currentData(), p.bold.isChecked(),"expand")
        self.apply_text_immediately(request,"正在移動文字…")

    def start_text_insertion(self,checked=True):
        if not checked:
            self.cancel_text_insertion()
            return
        if not self.session or self.busy:
            self.actions["add_text"].setChecked(False)
            return
        self.cancel_editing_modes(crop=False)
        self.canvas.clear_annotation_selection()
        self.clear_selection_state()
        self.canvas.start_text_insertion()
        # 取消其他模式時會一併取消本動作的勾選，這裡重新標示為進行中。
        self.actions["add_text"].setChecked(True)
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
        self.cancel_editing_modes(crop=False)
        self.canvas.clear_annotation_selection()
        self.clear_selection_state(disable_panel=True)
        self.canvas.start_note_insertion()
        # 取消其他模式時會一併取消本動作的勾選，這裡重新標示為進行中。
        self.actions["text_note"].setChecked(True)
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
        self.cancel_editing_modes(crop=False)
        self.clear_selection_state(disable_panel=True)
        self.actions["select_annotation"].setChecked(True)
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

    def _guide_annotation_selection(self,message):
        if not self.session or self.busy or not self.session.access.can_edit:
            return
        self.actions["select_annotation"].setChecked(True)
        self.start_annotation_selection(True)
        self.statusBar().showMessage(message)

    def _guide_markup_selection(self,label):
        if not self.session or self.busy or not self.session.access.can_edit:
            return
        self.cancel_editing_modes(crop=False)
        self.canvas.clear_annotation_selection()
        self.cancel_direct_crop()
        self.annotation=None
        self.run=None
        self.insertion_rect=None
        self.text_panel.setEnabled(False)
        self.canvas.clear_text_selection()
        self.canvas.setFocus()
        self.statusBar().showMessage(
            f"請先點選要加入{label}的文字；選取後請再次選擇{label}。")

    def delete_selected_annotation(self,item=None):
        if hasattr(item,"xref"):
            self.annotation=item
        if self.busy:
            return
        if not self.annotation:
            self._guide_annotation_selection("請點選要刪除的註解；選取後按 Delete。")
            return
        selected=self.annotation
        self.submit_annotation(delete_annotation,(self.page,selected.xref),"正在刪除註解…")

    def change_highlight_color(self,color):
        if self.busy:
            return
        if not self.annotation:
            self._guide_annotation_selection(
                "請先點選要變更顏色的螢光標記；選取後請再次選擇顏色。")
            return
        if self.annotation.kind!="Highlight":
            self.statusBar().showMessage("只有螢光標記可以變更顏色。")
            return
        self.submit_annotation(set_highlight_color,
            (self.page,self.annotation.xref,color),"正在變更螢光標記顏色…")

    def apply_selected_markup(self,kind):
        label="螢光標記" if kind=="highlight" else "底線"
        if self.busy:
            return
        if not self.run:
            self._guide_markup_selection(label)
            return
        run=self.run
        self.canvas.cancel_inline_editor()
        self.submit_annotation(mark_text,(self.page,run.rect,kind),f"正在加入{label}…")

    def submit_annotation(self,operation,args,status):
        if not self.session or self.busy:
            return
        page=self.page
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
            self.queue_thumbnails((page,))
        self.jobs.submit(operation,(self.session.pdf,*args),done,self.error)

    def begin_text_insertion(self,position):
        if not self.session or self.busy:
            return
        x,y=position
        self.actions["add_text"].setChecked(False)
        cell=find_table_cell(self.session.pdf,self.page,(x,y,x,y),self.session.document_key)
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
            self.text_panel.alignment.currentData(),self.text_panel.font_path)
        self.refresh_actions()

    def delete_run(self,run):
        if not self.session or not run.editable or self.busy:
            return
        self.cancel_editing_modes(crop=False)
        self.run=run
        p=self.text_panel
        cell=find_table_cell(self.session.pdf,self.page,run.rect,self.session.document_key)
        x0,y0,x1,y1=run.rect
        insertion_rect=cell or (x0,y0,x1+40,y1+run.size)
        request=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),
            self.page,run.id,"",run.rect,p.font_path,run.size,run.color,"left",False)
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
            self.run=None
            self.insertion_rect=insertion_rect
            self.text_panel.font_path=str(default_font())
            self.text_panel.font_label.setText("替代字型：Noto Sans CJK TC（完整繁中文字元）")
            self.text_panel.set_insertion(insertion_rect,run.size,cell is not None)
            self.refresh_actions()
            self.request_render()
            self.canvas.begin_inline_text(insertion_rect,"",None,run.size,
                self.text_panel.alignment.currentData(),self.text_panel.font_path)
        self.jobs.submit(replace_text,(self.session.pdf,request),done,self.error)

    RECOVERABLE_TEXT_ERRORS=("TEXT_OVERFLOW","OVERLAP","GEOMETRY","FONT_MISSING_GLYPH",
        "FONT_INVALID","TEXT_EMPTY","SIZE")

    def apply_text_immediately(self,request,status,operation=replace_text,editor_state=None):
        if self.busy:
            return
        revision=self.session.revision
        token=self.token
        previous_run,previous_insertion=self.run,self.insertion_rect
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(status)
        def done(pdf):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.session.apply_pdf(pdf)
            self.run=None
            self.insertion_rect=None
            self.text_panel.setEnabled(False)
            if request.text.strip():
                self._reselect_after_render=(request.page,tuple(request.rect),request.text)
            self.refresh_actions()
            self.request_render()
            self.queue_thumbnails((request.page,))
        def failed(error):
            code,message,_completed=error
            if (editor_state is None or code not in self.RECOVERABLE_TEXT_ERRORS or self.closed
                    or token!=self.token or revision!=self.session.revision):
                self.error(error)
                return
            self.busy=False
            self.run,self.insertion_rect=previous_run,previous_insertion
            self.refresh_actions()
            self.reopen_text_editor(editor_state,request,message,code)
        self.jobs.submit(operation,(self.session.pdf,request),done,failed)

    def reopen_text_editor(self,editor_state,request,message,code):
        """套用失敗時重新開啟輸入框並保留內容，直接在旁邊說明原因。"""
        run,text,rect=editor_state
        if self.page!=request.page or not self.page_data:
            self.error((code,message,()))
            return
        self.panels.setCurrentWidget(self.text_panel)
        self.canvas.begin_inline_text(rect,text,run,self.text_panel.size.value(),
            self.text_panel.alignment.currentData(),self.text_panel.font_path)
        self.canvas.show_inline_error(message)
        if code=="OVERLAP":
            own=run.id if run is not None else None
            conflicts=[item.rect for item in self.page_data.get("runs",())
                if item.id!=own and (text_overlaps(item.rect,request.rect)
                    or (run is not None and text_overlaps(item.rect,run.rect)))]
            self.canvas.show_conflicts(conflicts)
        self.statusBar().showMessage(message.split("\n")[0])

    def schedule_cell_analysis(self):
        """頁面顯示後稍候預先分析表格，點選文字時不必再等待。"""
        if not self.session or not self.session.access.can_edit:
            return
        pdf,page,key=self.session.pdf,self.page,self.session.document_key
        def analyse():
            if (self.session is not None and self.session.document_key==key
                    and self.page==page and not self.busy):
                try:
                    page_cell_index(pdf,page,key)
                except Exception:
                    pass
        QTimer.singleShot(200,analyse)

    def reselect_text_after_render(self,result):
        """套用後自動選回剛修改的文字，方便連續調整格式。"""
        pending,self._reselect_after_render=self._reselect_after_render,None
        if not pending or not self.session or not self.session.access.can_edit:
            return
        page,rect,text=pending
        if result.get("page")!=page:
            return
        target=pymupdf.Rect(rect)+(-3,-3,3,3)
        wanted=text.replace("\n","").replace(" ","")
        candidates=[run for run in result.get("runs",()) if run.editable
            and pymupdf.Rect(run.rect).intersects(target)
            and run.text.replace(" ","") and run.text.replace(" ","") in wanted]
        if not candidates:
            return
        best=max(candidates,key=lambda run:(pymupdf.Rect(run.rect)&target).get_area())
        self.select_run(best,open_editor=False)
