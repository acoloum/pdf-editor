"""圖章與簽名：匯入、收藏、點選或轉換既有圖片、多頁連續蓋章與圖層調整。"""
import pymupdf
from PySide6.QtCore import QBuffer,QIODevice,QMimeData
from PySide6.QtGui import QCursor,QImage,QPixmap
from PySide6.QtWidgets import QApplication,QFileDialog
from dataclasses import replace
from pathlib import Path
from pdf_editor.assets import AssetStore
from pdf_editor.engine.overlay import flatten_overlays,transformed_image
from pdf_editor.layer_clipboard import MIME_TYPE,ClipboardLayer,decode_layer,encode_layer,paste_rect
from pdf_editor.legacy_overlay_conversion import (
    convert_image_at,
    convert_legacy_image,
    find_convertible_images,
)
from pdf_editor.model import Overlay
from pdf_editor.ui.legacy_stamp_dialog import LegacyStampDialog
from pdf_editor.ui.signature_dialog import SignatureDialog
from pdf_editor.ui.stamp_pages_dialog import StampPagesDialog
import uuid


class StampActionsMixin:
    def add_stamp(self):
        name,_=QFileDialog.getOpenFileName(self,"匯入圖章或簽名","","PNG (*.png)")
        if name:
            self.import_layer(Path(name),True)

    def edit_image_at(self,image):
        """直接點選頁面上的圖片：在背景轉成可拖曳、縮放的圖層。"""
        if not self.session or self.busy or not self.session.access.can_edit:
            return
        if self.comparison_dialog is not None:
            # 轉換會改變文件版本，使開啟中的頁面比較失效而被關閉；比較期間不允許編輯圖片。
            self.statusBar().showMessage("頁面比較開啟中，無法編輯圖片。")
            return
        token=self.token
        revision=self.session.revision
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage("正在準備編輯圖片…")

        def done(result):
            self._apply_clicked_image(result,token,revision)

        def failed(error):
            self._finish_clicked_image_failure(error,token,revision)

        self.jobs.submit(convert_image_at,(self.session.pdf,image,self.asset_root),done,failed)

    def _apply_clicked_image(self,result,token,revision):
        self.busy=False
        if not self._legacy_stamp_context_is_current(token,revision):
            self.refresh_actions()
            return
        try:
            base_pdf,layer=result
            pending=self.pending_conversion
            pending_ids=pending[1] if pending and pending[0]==revision else ()
            self.session.apply_state(base_pdf,self.session.overlays+(layer,))
            # 記下轉換後的版本；之後若沒有任何改動就離開，會自動撤銷轉換。
            # 必須先記錄再選取，選取同一圖層時才不會被當成離開。
            self.pending_conversion=(self.session.revision,pending_ids+(layer.id,))
            self.clear_search_results()
            self.select_layer(layer.id)
            self.refresh_actions()
            self.request_render("可拖曳圖片移動，或拖曳四角縮放；右側可勾選「去除白底」。")
            self.queue_thumbnails((layer.page,))
        except Exception as exc:
            # 不讓例外離開背景工作回呼，否則同一輪排隊的其他回呼會被略過。
            self.error((getattr(exc,"code","STAMP_CONVERSION"),str(exc),()))

    def _finish_clicked_image_failure(self,error,token,revision):
        self.busy=False
        self.refresh_actions()
        if self._legacy_stamp_context_is_current(token,revision):
            # 點圖片失敗只在狀態列說明，不跳出對話框打斷操作。
            self.statusBar().showMessage(error[1])

    def _pending_conversion_is_live(self):
        """點圖片轉換後是否仍未有任何改動（文件版本與轉換當時相同）。"""
        pending=self.pending_conversion
        return (pending is not None and self.session is not None
            and self.session.revision==pending[0])

    def _pending_conversion_can_revert(self):
        """目前是否能撤銷未改動的圖片轉換。"""
        # 大型文件的轉換前狀態可能已被歷程上限裁掉；無法全部撤銷時保留轉換。
        return (not self.busy and self._pending_conversion_is_live()
            and self.session.can_discard(len(self.pending_conversion[1])))

    def discard_pending_conversion(self,render=True):
        """點圖片轉換後若沒有任何改動，撤銷轉換且不留重做紀錄；回傳是否有撤銷。

        render=False 供呼叫端之後自行重繪（例如換頁），避免多送一次即將作廢的渲染。
        """
        if self.busy:
            return False
        can_revert=self._pending_conversion_can_revert()
        pending,self.pending_conversion=self.pending_conversion,None
        if not can_revert:
            return False
        layer_ids=pending[1]
        # 撤銷前先記下圖層所在頁面，撤銷後才能更新這些頁面的縮圖。
        pages={o.page for o in self.session.overlays if o.id in layer_ids}
        for _ in layer_ids:
            self.session.discard_last()
        if self.layer_id in layer_ids:
            self.layer_id=None
            self.panels.setCurrentWidget(self.text_panel)
        self.clear_search_results()
        self.refresh_actions()
        if render:
            self.request_render_after_release()
        self.queue_thumbnails(tuple(sorted(pages)))
        return True

    def convert_legacy_stamp(self):
        """掃描文件，讓使用者明確選取後將既有圖章抽離為工作層。"""
        if not self.session or self.busy or not self.session.access.can_edit:
            return
        # 先撤銷尚未改動的點圖片轉換，避免掃描清單時殘留多餘的轉換結果。
        self.discard_pending_conversion()
        token = self.token
        revision = self.session.revision
        pdf = self.session.pdf
        self.busy = True
        self.refresh_actions()
        self.statusBar().showMessage("正在掃描可單獨編輯的圖片…")

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
                "沒有可單獨編輯的圖片；Logo、整頁掃描與重複使用的影像不會列出。"
            )
            return

        dialog = LegacyStampDialog(candidates, self)
        if not dialog.exec() or dialog.selected_candidate is None:
            self._restore_after_legacy_stamp_job()
            self.statusBar().showMessage("已取消編輯既有圖片。")
            return

        candidate = dialog.selected_candidate
        self.statusBar().showMessage("正在轉換選取的圖片…")

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
            self.request_render("已轉為可編輯圖片，可拖曳移動或縮放。")
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
            layer=Overlay(uuid.uuid4().hex,self.page,str(copied),
                self.new_stamp_rect(copied,ratio),0)
            self.session.set_overlays(self.session.overlays+(layer,))
            self.layer_id=layer.id
            self.select_layer(layer.id)
            self.refresh_actions()
            reused=self.settings.stamp_geometry(copied) is not None
            self.request_render("已新增圖章，沿用上次調整的大小；可用「蓋到多個頁面…」一次蓋到其他頁。"
                if reused else "已新增圖章；調整好大小後可用「蓋到多個頁面…」一次蓋到其他頁。")
            self.queue_thumbnails((layer.page,))
        except Exception as exc:
            self.error((getattr(exc,"code","IMAGE"),str(exc),()))

    def new_stamp_rect(self,asset,ratio):
        """新圖章的位置與大小：沿用上次調整好的尺寸，沒有紀錄時用預設值。"""
        page_width,page_height=self.page_data["bounds"][2:]
        remembered=self.settings.stamp_geometry(asset)
        if remembered:
            x,y,width,height=remembered
            # 記住的尺寸以寬度為準，高度改依這張圖的比例，避免換圖後變形。
            height=width*ratio
        else:
            x=y=40.0
            width=min(150,page_width*0.4)
            height=min(width*ratio,page_height*0.4)
            width=height/ratio
        width=min(width,page_width-4)
        height=min(height,page_height-4)
        x=max(0,min(x,page_width-width))
        y=max(0,min(y,page_height-height))
        return (x,y,x+width,y+height)

    def remember_stamp_geometry(self,layer):
        self.settings.set_stamp_geometry(layer.asset_path,layer.rect)

    def stamp_to_pages(self):
        """把目前選取的圖章，以相同大小、角度與位置蓋到多個頁面。"""
        layer=next((o for o in self.session.overlays if o.id==self.layer_id),None) if self.session else None
        if layer is None or self.busy or not self.session.access.can_edit:
            return
        dialog=StampPagesDialog(self.page_count,self.page,self.selected_thumbnail_indices(),self)
        if not dialog.exec():
            return
        existing={(o.page,o.asset_path,tuple(round(v,1) for v in o.rect))
            for o in self.session.overlays}
        pages_with_asset={o.page for o in self.session.overlays if o.asset_path==layer.asset_path}
        added,outside,duplicated=[],[],[]
        rect=pymupdf.Rect(layer.rect)
        with pymupdf.open(stream=self.session.pdf,filetype="pdf") as doc:
            for page in dialog.pages():
                if not 0<=page<doc.page_count or page==layer.page:
                    continue
                if dialog.skip_existing.isChecked() and page in pages_with_asset:
                    duplicated.append(page+1)
                    continue
                if (page,layer.asset_path,tuple(round(v,1) for v in layer.rect)) in existing:
                    duplicated.append(page+1)
                    continue
                bounds=pymupdf.Rect(0,0,doc[page].cropbox.width,doc[page].cropbox.height)
                if not bounds.contains(rect):
                    outside.append(page+1)
                    continue
                added.append(replace(layer,id=uuid.uuid4().hex,page=page))
        if not added:
            self.statusBar().showMessage("沒有可蓋章的頁面："
                +self.describe_stamp_skips(outside,duplicated))
            return
        self.session.set_overlays(self.session.overlays+tuple(added))
        self.remember_stamp_geometry(layer)
        self.refresh_actions()
        skips=self.describe_stamp_skips(outside,duplicated)
        message=f"已在 {len(added)} 頁蓋上相同圖章。"+("　"+skips if skips else "")
        self.request_render(message)
        self.queue_thumbnails(tuple(item.page for item in added))

    @staticmethod
    def describe_stamp_skips(outside,duplicated):
        parts=[]
        if duplicated:
            parts.append(f"已有相同圖章 {len(duplicated)} 頁")
        if outside:
            listed="、".join(str(page) for page in outside[:5])
            more="…" if len(outside)>5 else ""
            parts.append(f"超出頁面範圍 {len(outside)} 頁（第 {listed}{more} 頁）")
        return ("略過：" + "；".join(parts)) if parts else ""

    def selected_layer(self):
        """目前選取的圖層；沒有時回傳 None。"""
        if not self.session:
            return None
        return next((o for o in self.session.overlays if o.id==self.layer_id),None)

    def copy_selection(self):
        """Ctrl+C：最後選取的是圖層就複製圖片，否則照舊複製文字。"""
        layer=self.selected_layer() if self.last_selection=="layer" else None
        if layer is not None:
            self.copy_layer(layer)
            return
        self.copy_selected_text()

    def copy_layer(self,layer):
        """同時放入墨頁專用格式與畫面外觀的 PNG，墨頁與其他程式都能貼上。"""
        try:
            data=encode_layer(layer)
            png,_rect=transformed_image(layer)
        except Exception:
            self.statusBar().showMessage("無法複製圖片，圖檔可能已被移除。")
            return
        mime=QMimeData()
        mime.setData(MIME_TYPE,data)
        mime.setData("image/png",png)
        mime.setImageData(QImage.fromData(png,"PNG"))
        QApplication.clipboard().setMimeData(mime)
        self.statusBar().showMessage("已複製圖片；按 Ctrl+V 貼在滑鼠位置，也可以貼到其他程式。")

    def clipboard_layer(self):
        """讀取剪貼簿：優先墨頁專用格式，其次一般圖片；沒有圖片時回傳 None。"""
        mime=QApplication.clipboard().mimeData()
        if mime is None:
            return None
        if mime.hasFormat(MIME_TYPE):
            item=decode_layer(bytes(mime.data(MIME_TYPE)))
            if item is not None:
                return item
        if not mime.hasImage():
            return None
        data=mime.imageData()
        image=data.toImage() if isinstance(data,QPixmap) else QImage(data)
        if image.isNull():
            return None
        buffer=QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer,"PNG")
        return ClipboardLayer(bytes(buffer.data()))

    def can_paste_image(self):
        mime=QApplication.clipboard().mimeData()
        return mime is not None and (mime.hasFormat(MIME_TYPE) or mime.hasImage())

    def paste_image(self):
        """Ctrl+V：以滑鼠位置為中心貼上；游標不在頁面上時貼在可見區域中央。"""
        if not self.session:
            return
        point=self.canvas.page_point_at(self.canvas.viewport().mapFromGlobal(QCursor.pos()))
        self.paste_layer_at(point if point is not None else self.visible_page_center())

    def paste_layer_at(self,point):
        """在目前頁面以 point 為中心貼上剪貼簿圖片，建立可復原的新圖層。"""
        if not self.session or point is None or self.page_data is None:
            return
        if self.busy or not self.session.access.can_edit:
            self.statusBar().showMessage("目前無法貼上圖片。")
            return
        if self.comparison_dialog is not None:
            self.statusBar().showMessage("頁面比較開啟中，無法貼上圖片。")
            return
        item=self.clipboard_layer()
        if item is None:
            self.statusBar().showMessage("剪貼簿沒有可貼上的圖片。")
            return
        try:
            asset=AssetStore(self.session.history.root/"assets").import_png_bytes(item.png)
            if item.width is None:
                # 外部圖片沿用「蓋章」的大小規則（上次調整的寬度，或預設寬度），比例依原圖。
                from PIL import Image
                with Image.open(asset) as image:
                    ratio=image.height/image.width
                x0,y0,x1,y1=self.new_stamp_rect(asset,ratio)
                width,height=x1-x0,y1-y0
            else:
                width,height=item.width,item.height
            rect=paste_rect(point,width,height,self.page_data["bounds"][2:],item.angle)
            layer=Overlay(uuid.uuid4().hex,self.page,str(asset),rect,item.angle,item.remove_white)
            # 先驗證能正常輸出，失敗時不寫入歷程。
            flatten_overlays(self.session.pdf,(layer,))
        except Exception as exc:
            self.statusBar().showMessage("無法貼上圖片："+str(exc))
            return
        self.session.set_overlays(self.session.overlays+(layer,))
        self.select_layer(layer.id)
        self.refresh_actions()
        self.request_render("已貼上圖片，可拖曳移動或縮放。")
        self.queue_thumbnails((layer.page,))

    def select_layer(self,id):
        self.last_selection="layer"
        pending=self.pending_conversion
        if pending and id not in pending[1]:
            self.discard_pending_conversion()
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
            self.remember_stamp_geometry(layer)
            self.select_layer(layer.id)
            self.refresh_actions()
            # 移動、縮放、旋轉與去除白底都經過這裡，讓該頁縮圖跟著更新。
            self.queue_thumbnails((layer.page,))
        except Exception as exc:
            self.error((getattr(exc,"code","GEOMETRY"),str(exc),()))
        self.request_render()

    def update_layer(self):
        layer=next((o for o in self.session.overlays if o.id==self.layer_id),None)
        if layer:
            x,y,w,h,angle=(s.value() for s in self.overlay_panel.fields)
            self.move_layer(replace(layer,rect=(x,y,x+w,y+h),angle=angle))

    def set_layer_remove_white(self,checked):
        """切換選取圖層的去除白底；與移動、縮放相同，會留下可復原的紀錄。"""
        layer=next((o for o in self.session.overlays if o.id==self.layer_id),None) if self.session else None
        if layer is None or layer.remove_white==checked:
            return
        self.move_layer(replace(layer,remove_white=checked))
        # 驗證失敗時沒有寫入，勾選框要回到實際狀態。
        current=next((o for o in self.session.overlays if o.id==layer.id),None)
        if current:
            self.overlay_panel.set_layer(current)

    def delete_layer(self):
        pages={o.page for o in self.session.overlays if o.id==self.layer_id}
        self.session.set_overlays(tuple(o for o in self.session.overlays if o.id!=self.layer_id))
        self.panels.setCurrentWidget(self.text_panel)
        self.refresh_actions()
        self.request_render()
        self.queue_thumbnails(tuple(pages))
