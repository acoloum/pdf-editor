"""圈選多個物件：整批刪除或移動圖層、文字、註解與表格格線，只留下一筆可復原的紀錄。"""
import hashlib
from dataclasses import replace

from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.model import SelectionEdit
from pdf_editor.selection_edit import edit_selection_with_images

# 這些錯誤只是位置或內容不適合，在狀態列說明即可，不跳出對話框。
RECOVERABLE_SELECTION_ERRORS=("OVERLAP","GEOMETRY","UNSUPPORTED_TEXT","STALE","TEXT_OVERFLOW",
    "FONT_MISSING_GLYPH","REDACTIONS","GRAPHICS","STAMP_CONVERSION")


def describe_selection(selection):
    """以使用者看得懂的方式列出選取內容，例如「3 段文字、格線等 12 個圖形」。"""
    parts=[]
    if selection.runs:
        parts.append(f"{len(selection.runs)} 段文字")
    if selection.layers or selection.images:
        parts.append(f"{len(selection.layers)+len(selection.images)} 個圖章或圖片")
    if selection.annotations:
        parts.append(f"{len(selection.annotations)} 個註解")
    if selection.drawings:
        parts.append(f"格線等 {len(selection.drawings)} 個圖形")
    return "、".join(parts)


class SelectionActionsMixin:
    def select_objects(self,selection):
        if not self.session:
            return
        if not selection.count:
            self.statusBar().showMessage(
                f"圈到的 {selection.skipped_runs} 段文字無法安全修改（例如直書），未選取。")
            return
        pending=self.pending_conversion
        if pending and not set(pending[1])&set(selection.layers):
            self.discard_pending_conversion()
        # 整批選取不屬於單一文字或圖層：Ctrl+C 不複製，右側改回文件資訊。
        self.last_selection=None
        self.cancel_editing_modes(crop=False)
        self.clear_selection_state(disable_panel=True)
        self.layer_id=None
        self.refresh_actions()
        message=f"已圈選{describe_selection(selection)}：拖曳可一起移動，按 Delete 刪除，Esc 取消選取。"
        if selection.skipped_runs:
            message+=f"（{selection.skipped_runs} 段文字無法修改，已略過）"
        self.statusBar().showMessage(message)

    def delete_objects(self,selection):
        self._edit_objects(selection,None)

    def move_objects(self,selection,offset):
        self._edit_objects(selection,tuple(offset))

    def _edit_objects(self,selection,offset):
        if not self.session or self.busy or selection.page!=self.page:
            return
        verb="刪除" if offset is None else "移動"
        if not self.session.access.can_edit:
            self.statusBar().showMessage(self.session.access.reason or "文件僅供閱讀，無法修改。")
            self.request_render()
            return
        layer_ids=set(selection.layers)
        overlays=self.session.overlays
        if offset is None:
            overlays=tuple(o for o in overlays if o.id not in layer_ids)
        else:
            dx,dy=offset
            moved={o.id:replace(o,rect=(o.rect[0]+dx,o.rect[1]+dy,o.rect[2]+dx,o.rect[3]+dy))
                for o in overlays if o.id in layer_ids}
            try:
                # 先驗證新位置，失敗時還原畫布而不寫入歷程。
                if moved:
                    flatten_overlays(self.session.pdf,tuple(moved.values()))
            except Exception as exc:
                self._selection_edit_failed((getattr(exc,"code","GEOMETRY"),str(exc),()),selection)
                return
            overlays=tuple(moved.get(o.id,o) for o in overlays)
        summary=describe_selection(selection)
        if not (selection.runs or selection.annotations or selection.drawings or selection.images):
            try:
                self.session.set_overlays(overlays)
            except Exception as exc:
                self._selection_edit_failed((getattr(exc,"code","GEOMETRY"),str(exc),()),selection)
                return
            self._finish_selection_edit(layer_ids,offset,f"已{verb}{summary}。")
            return
        pdf=self.session.pdf
        request=SelectionEdit(hashlib.sha256(pdf).hexdigest(),self.page,
            tuple(run.id for run in selection.runs),
            tuple(item.xref for item in selection.annotations),offset,
            tuple(tuple(rect) for rect in selection.drawings))
        revision,token=self.session.revision,self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(f"正在{verb}{summary}…")

        def done(result):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                self.refresh_actions()
                return
            edited_pdf,new_layers=result
            try:
                self.session.apply_state(edited_pdf,overlays+tuple(new_layers))
            except Exception as exc:
                self._selection_edit_failed((getattr(exc,"code","ERROR"),str(exc),()),selection)
                return
            if new_layers:
                # 頁面圖片已轉成圖層：重新渲染後改以圖層代碼保留整批選取。
                group=self.canvas.group
                if group is not None:
                    self.canvas.keep_group(replace(group,images=(),
                        layers=group.layers+tuple(layer.id for layer in new_layers)))
            self._finish_selection_edit(layer_ids,offset,f"已{verb}{summary}。")

        def failed(error):
            if self.closed or token!=self.token:
                return
            self._selection_edit_failed(error,selection)

        self.jobs.submit(edit_selection_with_images,
            (pdf,request,tuple(selection.images),str(self.asset_root)),done,failed)

    def _finish_selection_edit(self,layer_ids,offset,status):
        if offset is None:
            self.canvas.clear_group_selection()
            if self.layer_id in layer_ids:
                self.layer_id=None
        self.refresh_actions()
        self.request_render(status)
        self.queue_thumbnails((self.page,))

    def _selection_edit_failed(self,error,selection):
        """失敗時不寫入歷程，重新渲染讓畫布回到原位置，並在原位置保留整批選取。"""
        self.busy=False
        self.canvas.keep_group(selection)
        if error[0] in RECOVERABLE_SELECTION_ERRORS:
            self.refresh_actions()
            self.request_render(error[1])
        else:
            self.error(error)
            self.request_render()
