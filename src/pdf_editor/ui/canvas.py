from dataclasses import replace
from PySide6.QtCore import Qt, Signal, QPointF, QTimer, QRectF
from PySide6.QtGui import QPixmap, QPen, QColor, QTransform, QPainter, QKeyEvent, QBrush
from PySide6.QtWidgets import (QGraphicsView,QGraphicsScene,QGraphicsPixmapItem,
    QGraphicsItem,QLabel,QLineEdit)
from pdf_editor.engine.geometry import transformed_rect, transform_point, inverse_transform
from pdf_editor.engine.overlay import transformed_image

class LayerItem(QGraphicsPixmapItem):
    def __init__(self,pix,layer,canvas):
        super().__init__(pix)
        self.layer,self.canvas=layer,canvas
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.start=QPointF()
        self.source_pixmap=QPixmap(pix)
        self.resize_corner=None
        self.resize_start=None
        self.resize_scale=1.0
        self.setAcceptHoverEvents(True)

    def _corner_at(self,point):
        rect=self.boundingRect()
        corners={"top_left":rect.topLeft(),"top_right":rect.topRight(),
            "bottom_left":rect.bottomLeft(),"bottom_right":rect.bottomRight()}
        for name,corner in corners.items():
            if abs(point.x()-corner.x())<=12 and abs(point.y()-corner.y())<=12:
                return name
        return None

    def _resized_rect(self,point):
        start=self.resize_start
        fixed={"top_left":start.bottomRight(),"top_right":start.bottomLeft(),
            "bottom_left":start.topRight(),"bottom_right":start.topLeft()}[self.resize_corner]
        raw_width=max(1,abs(point.x()-fixed.x()))
        raw_height=max(1,abs(point.y()-fixed.y()))
        ratio=start.width()/start.height()
        if raw_width/raw_height>=ratio:
            width,height=raw_width,raw_width/ratio
        else:
            width,height=raw_height*ratio,raw_height
        if min(width,height)<24:
            scale=24/min(width,height)
            width,height=width*scale,height*scale
        if self.resize_corner=="top_left":
            return QRectF(fixed.x()-width,fixed.y()-height,width,height)
        if self.resize_corner=="top_right":
            return QRectF(fixed.x(),fixed.y()-height,width,height)
        if self.resize_corner=="bottom_left":
            return QRectF(fixed.x()-width,fixed.y(),width,height)
        return QRectF(fixed.x(),fixed.y(),width,height)

    def paint(self,painter,option,widget=None):
        super().paint(painter,option,widget)
        if not self.isSelected():
            return
        painter.save()
        pen=QPen(QColor("#24765b"),2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.boundingRect())
        painter.setBrush(QBrush(QColor("white")))
        for corner in (self.boundingRect().topLeft(),self.boundingRect().topRight(),
                self.boundingRect().bottomLeft(),self.boundingRect().bottomRight()):
            painter.drawRect(QRectF(corner.x()-5,corner.y()-5,10,10))
        painter.restore()

    def hoverMoveEvent(self,event):
        corner=self._corner_at(event.pos()) if self.isSelected() else None
        if corner in ("top_left","bottom_right"):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif corner:
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self,event):
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self,event):
        self.start=self.pos()
        self.canvas.clear_text_selection()
        self.canvas.layer_selected.emit(self.layer.id)
        self.setSelected(True)
        corner=self._corner_at(event.pos())
        if corner:
            self.resize_corner=corner
            self.resize_start=self.sceneBoundingRect()
            self.resize_scale=1.0
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if not self.resize_corner:
            super().mouseMoveEvent(event)
            return
        rect=self._resized_rect(event.scenePos())
        self.resize_scale=rect.width()/self.resize_start.width()
        scaled=self.source_pixmap.scaled(max(1,round(rect.width())),max(1,round(rect.height())),
            Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled)
        self.setPos(rect.topLeft())
        event.accept()

    def mouseReleaseEvent(self,event):
        if self.resize_corner:
            rect=self.sceneBoundingRect()
            center=rect.center()
            cx,cy=transform_point(inverse_transform(self.canvas.matrix),center.x(),center.y())
            original=self.layer.rect
            width=(original[2]-original[0])*self.resize_scale
            height=(original[3]-original[1])*self.resize_scale
            resized=replace(self.layer,rect=(cx-width/2,cy-height/2,cx+width/2,cy+height/2))
            changed=abs(self.resize_scale-1)>0.001
            self.resize_corner=None
            self.resize_start=None
            if changed:
                self.canvas.layer_moved.emit(resized)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        delta=self.pos()-self.start
        inv=inverse_transform(self.canvas.matrix)
        x,y=transform_point(inv,delta.x(),delta.y())
        ox,oy=transform_point(inv,0,0)
        dx,dy=x-ox,y-oy
        if abs(dx)+abs(dy)>0.01:
            r=self.layer.rect
            self.canvas.layer_moved.emit(replace(self.layer,rect=(r[0]+dx,r[1]+dy,r[2]+dx,r[3]+dy)))

class InlineTextEditor(QLineEdit):
    commit_requested=Signal(str)
    cancel_requested=Signal()

    def __init__(self,text,parent=None):
        super().__init__(text,parent)
        self.finished=False

    def commit(self):
        if self.finished:
            return
        self.finished=True
        self.commit_requested.emit(self.text())

    def cancel(self):
        if self.finished:
            return
        self.finished=True
        self.cancel_requested.emit()

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):
            self.commit()
            event.accept()
            return
        if event.key()==Qt.Key.Key_Escape:
            self.cancel()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self,event):
        super().focusOutEvent(event)
        QTimer.singleShot(0,self.commit)

class Canvas(QGraphicsView):
    run_selected=Signal(object)
    run_moved=Signal(object)
    run_delete_requested=Signal(object)
    text_insertion_requested=Signal(object)
    text_insertion_cancelled=Signal()
    inline_text_committed=Signal(object)
    inline_text_cancelled=Signal()
    note_insertion_requested=Signal(object)
    note_insertion_cancelled=Signal()
    annotation_selected=Signal(object)
    annotation_delete_requested=Signal(object)
    layer_selected=Signal(str)
    layer_moved=Signal(object)
    crop_requested=Signal(object)
    crop_cancelled=Signal()

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#d9dfdb"))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.matrix=(1,0,0,1,0,0)
        self.runs=[]
        self.annotations=[]
        self.highlight=None
        self.annotation_highlight=None
        self.search_highlight=None
        self.selected_run=None
        self.selected_annotation=None
        self._drag_run=None
        self._drag_start_scene=QPointF()
        self._drag_original_rect=None
        self._text_insertion=False
        self._note_insertion=False
        self._annotation_selection=False
        self._crop_mode=False
        self._crop_rect=None
        self._crop_bounds=None
        self._crop_frame=None
        self._crop_handles=[]
        self._crop_drag_handle=None
        self._crop_drag_start=None
        self._crop_start_rect=None
        self.inline_editor=None
        self.inline_run=None
        self.inline_rect=None
        self.insertion_hint=QLabel("新增文字模式：請在頁面中點選位置（Esc 取消）",self.viewport())
        self.insertion_hint.setStyleSheet(
            "background:#1f5f4a;color:white;padding:10px 16px;border-radius:6px;font-weight:600;")
        self.insertion_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.insertion_hint.hide()
        self.setMinimumWidth(400)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def display(self,data,layers=(),selected_layer_id=None):
        self._clear_crop_items()
        self._crop_mode=False
        self.scene().clear()
        self.highlight=None
        self.annotation_highlight=None
        self.search_highlight=None
        self.selected_run=None
        self.selected_annotation=None
        self._drag_run=None
        self.matrix=data["matrix"]
        self.runs=data["runs"]
        self.annotations=data.get("annotations",())
        pix=QPixmap()
        pix.loadFromData(data["png"])
        pix.setDevicePixelRatio(data.get("pixel_ratio",1.0))
        self.scene().addPixmap(pix)
        width,height=data.get("display_size",(pix.width(),pix.height()))
        self.scene().setSceneRect(0,0,width,height)
        for layer in layers:
            if layer.page!=data["page"]:
                continue
            png,rect=transformed_image(layer)
            screen=transformed_rect(self.matrix,rect)
            lp=QPixmap()
            lp.loadFromData(png)
            lp=lp.transformed(QTransform().rotate(data["rotation"]),Qt.TransformationMode.SmoothTransformation)
            lp=lp.scaled(max(1,round(screen[2]-screen[0])),max(1,round(screen[3]-screen[1])),
                Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
            item=LayerItem(lp,layer,self)
            item.setPos(screen[0],screen[1])
            item.setZValue(5)
            self.scene().addItem(item)
            item.setSelected(layer.id==selected_layer_id)
        self._position_inline_editor()

    def _run_at(self, scene_pos):
        x,y=transform_point(inverse_transform(self.matrix),scene_pos.x(),scene_pos.y())
        for run in reversed(self.runs):
            x0,y0,x1,y1=run.rect
            if x0<=x<=x1 and y0<=y<=y1:
                return run
        return None

    def _annotation_at(self,scene_pos):
        x,y=transform_point(inverse_transform(self.matrix),scene_pos.x(),scene_pos.y())
        for item in reversed(self.annotations):
            x0,y0,x1,y1=item.rect
            if x0<=x<=x1 and y0<=y<=y1:
                return item
        return None

    def clear_text_selection(self):
        self.selected_run=None
        if self.highlight:
            self.scene().removeItem(self.highlight)
            self.highlight=None

    def clear_annotation_selection(self):
        self.selected_annotation=None
        if self.annotation_highlight:
            self.scene().removeItem(self.annotation_highlight)
            self.annotation_highlight=None

    def show_search_result(self,rect):
        self.clear_search_result()
        transformed=transformed_rect(self.matrix,rect)
        pen=QPen(QColor("#c58a00"),2)
        pen.setCosmetic(True)
        self.search_highlight=self.scene().addRect(transformed[0],transformed[1],
            transformed[2]-transformed[0],transformed[3]-transformed[1],pen,
            QBrush(QColor(255,210,35,105)))
        self.search_highlight.setZValue(3)

    def clear_search_result(self):
        if self.search_highlight:
            self.scene().removeItem(self.search_highlight)
            self.search_highlight=None

    def select_annotation(self,item):
        self.clear_annotation_selection()
        self.selected_annotation=item
        r=transformed_rect(self.matrix,item.rect)
        pen=QPen(QColor("#7b3fc6"),3,Qt.PenStyle.DashLine)
        self.annotation_highlight=self.scene().addRect(
            r[0],r[1],r[2]-r[0],r[3]-r[1],pen)
        self.annotation_highlight.setZValue(4)

    def start_annotation_selection(self):
        self.clear_text_selection()
        self.clear_annotation_selection()
        self.cancel_text_insertion()
        self.cancel_note_insertion()
        self.cancel_crop(True)
        self._annotation_selection=True
        self.insertion_hint.setText("選取註解模式：請點選螢光、底線或文字註解（Esc 取消）")
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus()
        self._place_insertion_hint()
        self.insertion_hint.show()
        self.insertion_hint.raise_()

    def cancel_annotation_selection(self):
        self._annotation_selection=False
        self.unsetCursor()
        self.insertion_hint.hide()

    def start_text_insertion(self):
        self.clear_text_selection()
        self.cancel_annotation_selection()
        self.cancel_note_insertion()
        self.cancel_crop(True)
        self._text_insertion=True
        self.insertion_hint.setText("新增文字模式：請在頁面中點選位置（Esc 取消）")
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus()
        self._place_insertion_hint()
        self.insertion_hint.show()
        self.insertion_hint.raise_()

    def cancel_text_insertion(self,notify=False):
        self._text_insertion=False
        self.unsetCursor()
        self.insertion_hint.hide()
        if notify:
            self.text_insertion_cancelled.emit()

    def start_note_insertion(self):
        self.clear_text_selection()
        self.cancel_annotation_selection()
        self.cancel_text_insertion()
        self.cancel_crop(True)
        self._note_insertion=True
        self.insertion_hint.setText("文字註解模式：請在頁面中點選位置（Esc 取消）")
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus()
        self._place_insertion_hint()
        self.insertion_hint.show()
        self.insertion_hint.raise_()

    def cancel_note_insertion(self,notify=False):
        self._note_insertion=False
        self.unsetCursor()
        self.insertion_hint.hide()
        if notify:
            self.note_insertion_cancelled.emit()

    def start_crop(self,page_rect):
        self.cancel_inline_editor()
        self.cancel_text_insertion()
        self.cancel_note_insertion()
        self.cancel_annotation_selection()
        self.clear_text_selection()
        self.clear_annotation_selection()
        self.cancel_crop()
        transformed=transformed_rect(self.matrix,page_rect)
        self._crop_bounds=QRectF(transformed[0],transformed[1],
            transformed[2]-transformed[0],transformed[3]-transformed[1]).normalized()
        self._crop_rect=QRectF(self._crop_bounds)
        self._crop_mode=True
        self._create_crop_items()
        self.insertion_hint.setText("裁切模式：拖曳綠框的邊線或角落，放開立即套用（Esc 取消）")
        self.setFocus()
        self._place_insertion_hint()
        self.insertion_hint.show()
        self.insertion_hint.raise_()

    def _create_crop_items(self):
        pen=QPen(QColor("#16a36f"),3,Qt.PenStyle.SolidLine)
        pen.setCosmetic(True)
        self._crop_frame=self.scene().addRect(self._crop_rect,pen,
            QBrush(QColor(22,163,111,24)))
        self._crop_frame.setZValue(20)
        self._crop_handles=[]
        for _ in range(8):
            handle=self.scene().addRect(QRectF(),QPen(QColor("#0f6d4c"),1),
                QBrush(QColor("white")))
            handle.setZValue(21)
            self._crop_handles.append(handle)
        self._update_crop_items()

    def _update_crop_items(self):
        if self._crop_frame is None:
            return
        self._crop_frame.setRect(self._crop_rect)
        rect=self._crop_rect
        points=(rect.topLeft(),QPointF(rect.center().x(),rect.top()),rect.topRight(),
            QPointF(rect.right(),rect.center().y()),rect.bottomRight(),
            QPointF(rect.center().x(),rect.bottom()),rect.bottomLeft(),
            QPointF(rect.left(),rect.center().y()))
        for item,point in zip(self._crop_handles,points):
            item.setRect(point.x()-5,point.y()-5,10,10)

    def _clear_crop_items(self):
        for item in ([self._crop_frame] if self._crop_frame is not None else [])+self._crop_handles:
            try:
                if item.scene() is self.scene():
                    self.scene().removeItem(item)
            except RuntimeError:
                pass
        self._crop_frame=None
        self._crop_handles=[]

    def cancel_crop(self,notify=False):
        was_active=self._crop_mode
        self._crop_mode=False
        self._crop_drag_handle=None
        self._crop_drag_start=None
        self._crop_start_rect=None
        self._clear_crop_items()
        if was_active:
            self.insertion_hint.hide()
            self.unsetCursor()
            if notify:
                self.crop_cancelled.emit()

    def _crop_handle_at(self,point):
        rect=self._crop_rect
        tolerance=12
        near_left=abs(point.x()-rect.left())<=tolerance
        near_right=abs(point.x()-rect.right())<=tolerance
        near_top=abs(point.y()-rect.top())<=tolerance
        near_bottom=abs(point.y()-rect.bottom())<=tolerance
        within_x=rect.left()-tolerance<=point.x()<=rect.right()+tolerance
        within_y=rect.top()-tolerance<=point.y()<=rect.bottom()+tolerance
        if near_left and near_top:
            return "top_left"
        if near_right and near_top:
            return "top_right"
        if near_left and near_bottom:
            return "bottom_left"
        if near_right and near_bottom:
            return "bottom_right"
        if near_left and within_y:
            return "left"
        if near_right and within_y:
            return "right"
        if near_top and within_x:
            return "top"
        if near_bottom and within_x:
            return "bottom"
        if rect.contains(point):
            return "move"
        return None

    def _drag_crop(self,point):
        start=QRectF(self._crop_start_rect)
        bounds=self._crop_bounds
        delta=point-self._crop_drag_start
        handle=self._crop_drag_handle
        minimum=10
        if handle=="move":
            width,height=start.width(),start.height()
            left=max(bounds.left(),min(start.left()+delta.x(),bounds.right()-width))
            top=max(bounds.top(),min(start.top()+delta.y(),bounds.bottom()-height))
            self._crop_rect=QRectF(left,top,width,height)
        else:
            left,right,top,bottom=start.left(),start.right(),start.top(),start.bottom()
            if "left" in handle:
                left=max(bounds.left(),min(point.x(),right-minimum))
            if "right" in handle:
                right=min(bounds.right(),max(point.x(),left+minimum))
            if "top" in handle:
                top=max(bounds.top(),min(point.y(),bottom-minimum))
            if "bottom" in handle:
                bottom=min(bounds.bottom(),max(point.y(),top+minimum))
            self._crop_rect=QRectF(QPointF(left,top),QPointF(right,bottom))
        self._update_crop_items()

    def _crop_page_rect(self):
        inv=inverse_transform(self.matrix)
        rect=self._crop_rect
        points=(rect.topLeft(),rect.topRight(),rect.bottomLeft(),rect.bottomRight())
        mapped=[transform_point(inv,point.x(),point.y()) for point in points]
        xs=[point[0] for point in mapped]
        ys=[point[1] for point in mapped]
        return (min(xs),min(ys),max(xs),max(ys))

    def _place_insertion_hint(self):
        self.insertion_hint.adjustSize()
        x=max(12,(self.viewport().width()-self.insertion_hint.width())//2)
        self.insertion_hint.move(x,12)

    def begin_inline_text(self,rect,text,run=None,size=11,alignment="left"):
        self.cancel_inline_editor()
        self.inline_run=run
        self.inline_rect=tuple(rect)
        editor=InlineTextEditor(text,self.viewport())
        horizontal=(Qt.AlignmentFlag.AlignHCenter if alignment in ("hcenter","center")
            else Qt.AlignmentFlag.AlignLeft)
        editor.setAlignment(horizontal | Qt.AlignmentFlag.AlignVCenter)
        editor.setPlaceholderText("直接輸入文字")
        editor.setStyleSheet(
            "background:rgba(255,255,255,235);color:#17231f;border:2px solid #24765b;"
            "border-radius:3px;padding:2px 5px;")
        font=editor.font()
        font.setPointSizeF(max(6,float(size)))
        editor.setFont(font)
        editor.commit_requested.connect(self._commit_inline_text)
        editor.cancel_requested.connect(self._cancel_inline_text)
        self.inline_editor=editor
        self._position_inline_editor()
        editor.show()
        editor.raise_()
        QTimer.singleShot(0,lambda:self._focus_inline_editor(editor,text))

    def _focus_inline_editor(self,editor,text):
        if self.inline_editor is not editor:
            return
        editor.setFocus()
        if text:
            editor.selectAll()

    def _position_inline_editor(self):
        if self.inline_editor is None or self.inline_rect is None:
            return
        x0,y0,x1,y1=transformed_rect(self.matrix,self.inline_rect)
        top_left=self.mapFromScene(QPointF(x0,y0))
        bottom_right=self.mapFromScene(QPointF(x1,y1))
        if self.inline_run is None:
            width=max(100,bottom_right.x()-top_left.x()+12)
            height=max(30,bottom_right.y()-top_left.y()+8)
        else:
            # 編輯既有文字時貼齊原文字框，避免窄表格儲存格在套用後產生視覺跳位。
            width=max(1,bottom_right.x()-top_left.x())
            height=max(1,bottom_right.y()-top_left.y())
        width=min(width,max(100,self.viewport().width()-12))
        left=max(6,min(top_left.x(),self.viewport().width()-width-6))
        top=max(6,min(top_left.y(),self.viewport().height()-height-6))
        self.inline_editor.setGeometry(left,top,width,height)

    def _finish_inline_editor(self):
        editor=self.inline_editor
        self.inline_editor=None
        self.inline_run=None
        self.inline_rect=None
        if editor is not None:
            editor.finished=True
            editor.hide()
            editor.deleteLater()

    def _commit_inline_text(self,text):
        payload=(self.inline_run,text,self.inline_rect)
        self._finish_inline_editor()
        self.inline_text_committed.emit(payload)

    def _cancel_inline_text(self):
        self._finish_inline_editor()
        self.inline_text_cancelled.emit()

    def cancel_inline_editor(self):
        self._finish_inline_editor()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self._place_insertion_hint()
        self._position_inline_editor()

    def scrollContentsBy(self,dx,dy):
        super().scrollContentsBy(dx,dy)
        self._position_inline_editor()

    def _show_highlight(self, rect):
        if self.highlight:
            self.scene().removeItem(self.highlight)
        r=transformed_rect(self.matrix,rect)
        self.highlight=self.scene().addRect(r[0],r[1],r[2]-r[0],r[3]-r[1],
            QPen(QColor("#246b55"),2))
        self.highlight.setZValue(2)

    def mousePressEvent(self,event):
        if self.inline_editor is not None:
            self.inline_editor.commit()
            event.accept()
            return
        scene_pos=self.mapToScene(event.position().toPoint())
        if self._crop_mode and event.button()==Qt.MouseButton.LeftButton:
            handle=self._crop_handle_at(scene_pos)
            if handle:
                self._crop_drag_handle=handle
                self._crop_drag_start=scene_pos
                self._crop_start_rect=QRectF(self._crop_rect)
                if handle in ("top_left","bottom_right"):
                    self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                elif handle in ("top_right","bottom_left"):
                    self.setCursor(Qt.CursorShape.SizeBDiagCursor)
                elif handle in ("left","right"):
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif handle in ("top","bottom"):
                    self.setCursor(Qt.CursorShape.SizeVerCursor)
                else:
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                event.accept()
                return
            event.accept()
            return
        item=self.scene().itemAt(scene_pos,QTransform())
        self.setFocus()
        if isinstance(item,LayerItem):
            self.clear_text_selection()
        if not isinstance(item,LayerItem):
            if self._annotation_selection:
                annotation=self._annotation_at(scene_pos)
                if annotation:
                    self.cancel_annotation_selection()
                    self.clear_text_selection()
                    self.select_annotation(annotation)
                    self.annotation_selected.emit(annotation)
                event.accept()
                return
            if self._note_insertion:
                x,y=transform_point(inverse_transform(self.matrix),scene_pos.x(),scene_pos.y())
                self.cancel_note_insertion()
                self.note_insertion_requested.emit((x,y))
                event.accept()
                return
            if self._text_insertion:
                x,y=transform_point(inverse_transform(self.matrix),scene_pos.x(),scene_pos.y())
                self.cancel_text_insertion()
                self.text_insertion_requested.emit((x,y))
                event.accept()
                return
            self.clear_annotation_selection()
            run=self._run_at(scene_pos)
            if run:
                self.selected_run=run
                self._drag_run=run
                self._drag_start_scene=scene_pos
                self._drag_original_rect=run.rect
                self._show_highlight(run.rect)
                self.run_selected.emit(run)
                event.accept()
                return
            self.clear_text_selection()
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self._crop_mode and self._crop_drag_handle:
            self._drag_crop(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        if self._drag_run:
            scene_pos=self.mapToScene(event.position().toPoint())
            delta=scene_pos-self._drag_start_scene
            inv=inverse_transform(self.matrix)
            x,y=transform_point(inv,delta.x(),delta.y())
            ox,oy=transform_point(inv,0,0)
            dx,dy=x-ox,y-oy
            r=self._drag_original_rect
            self._show_highlight((r[0]+dx,r[1]+dy,r[2]+dx,r[3]+dy))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if self._crop_mode and self._crop_drag_handle:
            changed=self._crop_rect!=self._crop_start_rect
            self._crop_drag_handle=None
            self._crop_drag_start=None
            self._crop_start_rect=None
            if changed:
                result=self._crop_page_rect()
                self.cancel_crop()
                self.crop_requested.emit(result)
            event.accept()
            return
        if self._drag_run:
            scene_pos=self.mapToScene(event.position().toPoint())
            delta=scene_pos-self._drag_start_scene
            inv=inverse_transform(self.matrix)
            x,y=transform_point(inv,delta.x(),delta.y())
            ox,oy=transform_point(inv,0,0)
            dx,dy=x-ox,y-oy
            r=self._drag_original_rect
            moved=replace(self._drag_run,rect=(r[0]+dx,r[1]+dy,r[2]+dx,r[3]+dy))
            self._drag_run=None
            if abs(dx)+abs(dy)>0.01:
                self.selected_run=moved
                self.run_moved.emit(moved)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def keyPressEvent(self,event: QKeyEvent):
        if event.key()==Qt.Key.Key_Escape and self._crop_mode:
            self.cancel_crop(True)
            event.accept()
            return
        if event.key()==Qt.Key.Key_Escape and self._annotation_selection:
            self.cancel_annotation_selection()
            event.accept()
            return
        if event.key()==Qt.Key.Key_Escape and self._note_insertion:
            self.cancel_note_insertion(True)
            event.accept()
            return
        if event.key()==Qt.Key.Key_Escape and self._text_insertion:
            self.cancel_text_insertion(True)
            event.accept()
            return
        if event.key()==Qt.Key.Key_Delete and self.selected_annotation is not None:
            self.annotation_delete_requested.emit(self.selected_annotation)
            event.accept()
            return
        if event.key()==Qt.Key.Key_Delete and self.selected_run is not None:
            self.run_delete_requested.emit(self.selected_run)
            event.accept()
            return
        super().keyPressEvent(event)
