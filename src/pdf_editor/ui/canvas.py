from dataclasses import replace
from PySide6.QtCore import Qt, Signal, QPointF, QTimer
from PySide6.QtGui import QPixmap, QPen, QColor, QTransform, QPainter, QKeyEvent
from PySide6.QtWidgets import QGraphicsView,QGraphicsScene,QGraphicsPixmapItem,QGraphicsItem,QLabel,QLineEdit
from pdf_editor.engine.geometry import transformed_rect, transform_point, inverse_transform
from pdf_editor.engine.overlay import transformed_image

class LayerItem(QGraphicsPixmapItem):
    def __init__(self,pix,layer,canvas):
        super().__init__(pix)
        self.layer,self.canvas=layer,canvas
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.start=QPointF()

    def mousePressEvent(self,event):
        self.start=self.pos()
        self.canvas.clear_text_selection()
        self.canvas.layer_selected.emit(self.layer.id)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
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
    layer_selected=Signal(str)
    layer_moved=Signal(object)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#d9dfdb"))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.matrix=(1,0,0,1,0,0)
        self.runs=[]
        self.highlight=None
        self.selected_run=None
        self._drag_run=None
        self._drag_start_scene=QPointF()
        self._drag_original_rect=None
        self._text_insertion=False
        self._note_insertion=False
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

    def display(self,data,layers=()):
        self.scene().clear()
        self.highlight=None
        self.selected_run=None
        self._drag_run=None
        self.matrix=data["matrix"]
        self.runs=data["runs"]
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
        self._position_inline_editor()

    def _run_at(self, scene_pos):
        x,y=transform_point(inverse_transform(self.matrix),scene_pos.x(),scene_pos.y())
        for run in reversed(self.runs):
            x0,y0,x1,y1=run.rect
            if x0<=x<=x1 and y0<=y<=y1:
                return run
        return None

    def clear_text_selection(self):
        self.selected_run=None
        if self.highlight:
            self.scene().removeItem(self.highlight)
            self.highlight=None

    def start_text_insertion(self):
        self.clear_text_selection()
        self.cancel_note_insertion()
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
        self.cancel_text_insertion()
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

    def _place_insertion_hint(self):
        self.insertion_hint.adjustSize()
        x=max(12,(self.viewport().width()-self.insertion_hint.width())//2)
        self.insertion_hint.move(x,12)

    def begin_inline_text(self,rect,text,run=None,size=11):
        self.cancel_inline_editor()
        self.inline_run=run
        self.inline_rect=tuple(rect)
        editor=InlineTextEditor(text,self.viewport())
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
        width=max(100,bottom_right.x()-top_left.x()+12)
        height=max(30,bottom_right.y()-top_left.y()+8)
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
        item=self.scene().itemAt(scene_pos,QTransform())
        self.setFocus()
        if isinstance(item,LayerItem):
            self.clear_text_selection()
        if not isinstance(item,LayerItem):
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
        if event.key()==Qt.Key.Key_Escape and self._note_insertion:
            self.cancel_note_insertion(True)
            event.accept()
            return
        if event.key()==Qt.Key.Key_Escape and self._text_insertion:
            self.cancel_text_insertion(True)
            event.accept()
            return
        if event.key()==Qt.Key.Key_Delete and self.selected_run is not None:
            self.run_delete_requested.emit(self.selected_run)
            event.accept()
            return
        super().keyPressEvent(event)
