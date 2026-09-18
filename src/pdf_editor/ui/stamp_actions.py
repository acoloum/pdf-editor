"""圖章與簽名：匯入、收藏、轉換既有圖章與圖層調整。"""
from PySide6.QtWidgets import QFileDialog
from dataclasses import replace
from pathlib import Path
from pdf_editor.assets import AssetStore
from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.legacy_overlay_conversion import (
    convert_legacy_image,
    find_convertible_images,
)
from pdf_editor.model import Overlay
from pdf_editor.ui.legacy_stamp_dialog import LegacyStampDialog
from pdf_editor.ui.signature_dialog import SignatureDialog
import uuid


class StampActionsMixin:
    def add_stamp(self):
        name,_=QFileDialog.getOpenFileName(self,"匯入圖章或簽名","","PNG (*.png)")
        if name:
            self.import_layer(Path(name),True)

    def convert_legacy_stamp(self):
        """掃描文件，讓使用者明確選取後將既有圖章抽離為工作層。"""
        if not self.session or self.busy or not self.session.access.can_edit:
            return
        token = self.token
        revision = self.session.revision
        pdf = self.session.pdf
        self.busy = True
        self.refresh_actions()
        self.statusBar().showMessage("正在掃描可安全轉換的既有圖章…")

        def done(candidates):
            self._show_legacy_stamp_candidates(candidates, pdf, token, revision)

        def failed(error):
            self._finish_legacy_stamp_failure(error, token, revision)

        self.jobs.submit(find_convertible_images, (pdf,), done, failed)

    def _show_legacy_stamp_candidates(self, candidates, pdf, token, revision):
        if not self._legacy_stamp_context_is_current(token, revision):
            self._restore_after_legacy_stamp_job()
            return
        if not candidates:
            self._restore_after_legacy_stamp_job()
            self.statusBar().showMessage(
                "沒有可安全轉換的既有圖章；Logo、整頁掃描與重複影像不會列出。"
            )
            return

        dialog = LegacyStampDialog(candidates, self)
        if not dialog.exec() or dialog.selected_candidate is None:
            self._restore_after_legacy_stamp_job()
            self.statusBar().showMessage("已取消轉換既有圖章。")
            return

        candidate = dialog.selected_candidate
        self.statusBar().showMessage("正在轉換選取的既有圖章…")

        def done(result):
            self._apply_converted_legacy_stamp(result, token, revision)

        def failed(error):
            self._finish_legacy_stamp_failure(error, token, revision)

        self.jobs.submit(
            convert_legacy_image,
            (pdf, candidate, self.asset_root),
            done,
            failed,
        )

    def _apply_converted_legacy_stamp(self, result, token, revision):
        self.busy = False
        if not self._legacy_stamp_context_is_current(token, revision):
            self.refresh_actions()
            return
        try:
            base_pdf, layer = result
            self.session.apply_state(base_pdf, self.session.overlays + (layer,))
            self.clear_search_results()
            self.page_data = None
            self.select_layer(layer.id)
            self.refresh_actions()
            self.request_render("已轉換為可編輯圖章。")
            self.queue_thumbnails((layer.page,))
        except Exception as exc:
            self.error((getattr(exc, "code", "STAMP_CONVERSION"), str(exc), ()))

    def _finish_legacy_stamp_failure(self, error, token, revision):
        self.busy = False
        if not self._legacy_stamp_context_is_current(token, revision):
            self.refresh_actions()
            return
        self.error(error)

    def _restore_after_legacy_stamp_job(self):
        self.busy = False
        self.refresh_actions()

    def _legacy_stamp_context_is_current(self, token, revision):
        return (not self.closed and self.session is not None
            and token == self.token and revision == self.session.revision)

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
        if not self.session:
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
