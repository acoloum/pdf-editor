from dataclasses import replace
from PySide6.QtCore import Qt, Signal, QPointF, QTimer, QRectF
from PySide6.QtGui import (QPixmap,QPen,QColor,QTransform,QPainter,QKeyEvent,QBrush,
    QPainterPath)
from PySide6.QtWidgets import (QGraphicsView,QGraphicsScene,QGraphicsPixmapItem,
    QGraphicsItem,QLabel,QLineEdit)
from pdf_editor.engine.geometry import transformed_rect, transform_point, inverse_transform
from pdf_editor.engine.overlay import transformed_image
from pdf_editor.ui.style import COLORS

class LayerItem(QGraphicsPixmapItem):
    HANDLE_RADIUS=6

    def __init__(self,pix,layer,canvas,pixel_ratio=1.0):
        super().__init__(pix)
        self.layer,self.canvas=layer,canvas
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.start=QPointF()
        # 保留原始解析度影像，每次縮放都從原圖取樣，避免重複縮放造成模糊。
        self.source_pixmap=QPixmap(pix)
        self.pixel_ratio=max(1.0,float(pixel_ratio))
        self.resize_corner=None
        self.resize_start=None
        self.resize_scale=1.0
        self.setAcceptHoverEvents(True)

    def set_display_size(self,width,height):
        """依螢幕實體像素縮放圖章，於高 DPI 螢幕上維持清晰。"""
        ratio=self.pixel_ratio
        scaled=self.source_pixmap.scaled(max(1,round(width*ratio)),max(1,round(height*ratio)),
            Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        self.prepareGeometryChange()
        self.setPixmap(scaled)

    def _content_rect(self):
        return super().boundingRect()

    def boundingRect(self):
        return self._content_rect().adjusted(-self.HANDLE_RADIUS,-self.HANDLE_RADIUS,
            self.HANDLE_RADIUS,self.HANDLE_RADIUS)

    def shape(self):
        path=QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        content=self._content_rect()
        path.addRect(content)
        if self.isSelected():
            for corner in (content.topLeft(),content.topRight(),content.bottomLeft(),
                    content.bottomRight()):
                path.addRect(QRectF(corner.x()-self.HANDLE_RADIUS,
                    corner.y()-self.HANDLE_RADIUS,self.HANDLE_RADIUS*2,
                    self.HANDLE_RADIUS*2))
        return path

    def _corner_at(self,point):
        rect=self._content_rect()
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
        pen=QPen(QColor(COLORS["accent"]),2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self._content_rect())
        painter.setBrush(QBrush(QColor("white")))
        for corner in (self._content_rect().topLeft(),self._content_rect().topRight(),
                self._content_rect().bottomLeft(),self._content_rect().bottomRight()):
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
            self.resize_start=self.mapRectToScene(self._content_rect())
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
        self.set_display_size(rect.width(),rect.height())
        self.setPos(rect.topLeft())
        event.accept()

    def mouseReleaseEvent(self,event):
        if self.resize_corner:
            rect=self.mapRectToScene(self._content_rect())
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
        self.textEdited.connect(lambda _text:self.set_error(None))

    def set_error(self,message):
        """以紅框與提示顯示無法套用的原因，保留輸入內容讓使用者直接修正。"""
        self.setProperty("error",bool(message))
        self.style().unpolish(self)
        self.style().polish(self)
        label=getattr(self,"error_label",None)
        if label is not None:
            label.setVisible(bool(message))
            if message:
                label.setText(message)
                label.adjustSize()
                label.raise_()

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
    zoom_step_requested=Signal(int)
    page_step_requested=Signal(int)
    files_dropped=Signal(list)
    pointer_moved=Signal(object)
    context_menu_requested=Signal(object,object,object)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor(COLORS["canvas"]))
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
        self.inline_error=QLabel(self.viewport())
        self.inline_error.setObjectName("inlineError")
        self.inline_error.setWordWrap(True)
        self.inline_error.setMaximumWidth(360)
        self.inline_error.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.inline_error.hide()
        self.conflict_items=[]
        self.insertion_hint=QLabel("新增文字模式：請在頁面中點選位置（Esc 取消）",self.viewport())
        self.insertion_hint.setStyleSheet(
            "background:rgba(10,18,32,230);color:%s;padding:10px 18px;border-radius:8px;"
            "border:1px solid %s;font-weight:600;"%(COLORS["accent"],COLORS["accent"]))
        self.insertion_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.insertion_hint.hide()
        self.setMinimumWidth(400)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.viewport().setMouseTracking(True)
        self._wheel_page_delta=0

    def drawBackground(self,painter,rect):
        """繪製深色畫布與細點網格，營造工作區的科技感。"""
        super().drawBackground(painter,rect)
        spacing=28
        painter.save()
        pen=QPen(QColor(COLORS["canvas_grid"]),2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        left=int(rect.left())-int(rect.left())%spacing
        top=int(rect.top())-int(rect.top())%spacing
        points=[QPointF(x,y) for x in range(left,int(rect.right())+1,spacing)
            for y in range(top,int(rect.bottom())+1,spacing)]
        if points:
            painter.drawPoints(points)
        painter.restore()

    def display(self,data,layers=(),selected_layer_id=None):
        self.conflict_items=[]
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
        # 頁面外框加上淡青色光暈，讓白色頁面在深色畫布上有層次。
        for grow,alpha in ((6,18),(3,40),(1,90)):
            frame=self.scene().addRect(QRectF(-grow,-grow,width+grow*2,height+grow*2),
                QPen(QColor(34,211,238,alpha),1),QBrush(Qt.BrushStyle.NoBrush))
            frame.setZValue(-1)
        for layer in layers:
            if layer.page!=data["page"]:
                continue
            png,rect=transformed_image(layer)
            screen=transformed_rect(self.matrix,rect)
            source=QPixmap()
            source.loadFromData(png)
            source=source.transformed(QTransform().rotate(data["rotation"]),
                Qt.TransformationMode.SmoothTransformation)
            item=LayerItem(source,layer,self,data.get("pixel_ratio",1.0))
            item.set_display_size(screen[2]-screen[0],screen[3]-screen[1])
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
        pen=QPen(QColor(COLORS["search"]),2)
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
        pen=QPen(QColor(COLORS["annotation"]),3,Qt.PenStyle.DashLine)
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
        pen=QPen(QColor(COLORS["accent"]),2,Qt.PenStyle.SolidLine)
        pen.setCosmetic(True)
        self._crop_frame=self.scene().addRect(self._crop_rect,pen,
            QBrush(QColor(34,211,238,28)))
        self._crop_frame.setZValue(20)
        self._crop_handles=[]
        for _ in range(8):
            handle=self.scene().addRect(QRectF(),QPen(QColor(COLORS["accent"]),1),
                QBrush(QColor(COLORS["surface"])))
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

    _font_families={}

    @classmethod
    def font_family_for(cls,font_path):
        """載入字型檔並回傳字型家族名稱，讓輸入框外觀接近頁面上的字。"""
        if not font_path:
            return None
        if font_path not in cls._font_families:
            from PySide6.QtGui import QFontDatabase
            families=()
            try:
                font_id=QFontDatabase.addApplicationFont(str(font_path))
                if font_id>=0:
                    families=QFontDatabase.applicationFontFamilies(font_id)
            except Exception:
                families=()
            cls._font_families[font_path]=families[0] if families else None
        return cls._font_families[font_path]

    def view_scale(self):
        a,b=self.matrix[0],self.matrix[1]
        return max(0.1,(a*a+b*b)**0.5)

    def begin_inline_text(self,rect,text,run=None,size=11,alignment="left",font_path=None):
        self.cancel_inline_editor()
        self.inline_run=run
        self.inline_rect=tuple(rect)
        editor=InlineTextEditor(text,self.viewport())
        horizontal=(Qt.AlignmentFlag.AlignHCenter if alignment in ("hcenter","center")
            else Qt.AlignmentFlag.AlignLeft)
        editor.setAlignment(horizontal | Qt.AlignmentFlag.AlignVCenter)
        editor.setPlaceholderText("直接輸入文字")
        editor.setObjectName("inlineEditor")
        # PDF 點數依目前縮放換算成螢幕像素，輸入時的字大小與頁面上一致；
        # 字型須寫在樣式表內，否則會被全域樣式表的字級覆蓋。
        pixels=max(8,round(float(size)*self.view_scale()))
        family=self.font_family_for(font_path)
        families=(f'"{family}", ' if family else "")+'"Microsoft JhengHei UI"'
        editor.setStyleSheet(
            "#inlineEditor{background:rgba(255,255,255,240);color:#0b1220;border:2px solid %s;"
            "border-radius:3px;padding:2px 5px;font-size:%dpx;font-family:%s;}"
            "#inlineEditor[error=\"true\"]{border:2px solid %s;background:#fff5f5;}"
            %(COLORS["accent_strong"],pixels,families,COLORS["danger"]))
        editor.commit_requested.connect(self._commit_inline_text)
        editor.cancel_requested.connect(self._cancel_inline_text)
        editor.error_label=self.inline_error
        self.inline_error.hide()
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
            left=top_left.x()
            top=top_left.y()
        else:
            # 編輯框維持原文字中心，同時保留足以完整顯示輸入字型的高度。
            width=max(1,bottom_right.x()-top_left.x())
            height=max(self.inline_editor.sizeHint().height(),
                bottom_right.y()-top_left.y())
            left=(top_left.x()+bottom_right.x()-width)/2
            top=(top_left.y()+bottom_right.y()-height)/2
        width=min(width,max(100,self.viewport().width()-12))
        left=max(6,min(round(left),self.viewport().width()-width-6))
        top=max(6,min(round(top),self.viewport().height()-height-6))
        self.inline_editor.setGeometry(round(left),round(top),round(width),round(height))
        if self.inline_error.isVisible():
            below=round(top+height+4)
            if below+self.inline_error.height()>self.viewport().height():
                below=max(4,round(top-self.inline_error.height()-4))
            self.inline_error.move(max(4,min(round(left),self.viewport().width()-self.inline_error.width()-4)),below)

    def show_inline_error(self,message):
        if self.inline_editor is None:
            return
        self.inline_editor.set_error(message)
        self._position_inline_editor()

    def show_conflicts(self,rects):
        """以紅色虛線框標出與文字框衝突的既有文字。"""
        self.clear_conflicts()
        pen=QPen(QColor(COLORS["danger"]),2,Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        for rect in rects:
            r=transformed_rect(self.matrix,rect)
            item=self.scene().addRect(r[0],r[1],r[2]-r[0],r[3]-r[1],pen,QBrush(QColor(248,113,113,40)))
            item.setZValue(6)
            self.conflict_items.append(item)

    def clear_conflicts(self):
        for item in self.conflict_items:
            try:
                if item.scene() is self.scene():
                    self.scene().removeItem(item)
            except RuntimeError:
                pass
        self.conflict_items=[]

    def highlight_run(self,run):
        """選取文字但不開啟輸入框；按 Enter 或再點一次即可編輯。"""
        self.selected_run=run
        self._show_highlight(run.rect)

    def _finish_inline_editor(self):
        editor=self.inline_editor
        self.inline_editor=None
        self.inline_run=None
        self.inline_rect=None
        self.inline_error.hide()
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
            QPen(QColor(COLORS["accent"]),2))
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

    @staticmethod
    def dropped_pdf_paths(mime):
        """取出拖放資料中的本機 PDF 路徑。"""
        if not mime.hasUrls():
            return []
        return [url.toLocalFile() for url in mime.urls()
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf")]

    def dragEnterEvent(self,event):
        if self.dropped_pdf_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self,event):
        if self.dropped_pdf_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self,event):
        paths=self.dropped_pdf_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.files_dropped.emit(paths)
            return
        super().dropEvent(event)

    def wheelEvent(self,event):
        delta=event.angleDelta().y()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Ctrl + 滾輪：依滾動方向逐級縮放。
            if delta:
                self.zoom_step_requested.emit(1 if delta>0 else -1)
            event.accept()
            return
        bar=self.verticalScrollBar()
        at_bottom=bar.value()>=bar.maximum()
        at_top=bar.value()<=bar.minimum()
        if delta and ((delta<0 and at_bottom) or (delta>0 and at_top)):
            # 已捲到頁面邊緣時累積滾動量，滿一格才翻頁，避免誤觸。
            if (self._wheel_page_delta<0)!=(delta<0):
                self._wheel_page_delta=0
            self._wheel_page_delta+=delta
            if abs(self._wheel_page_delta)>=120:
                self._wheel_page_delta=0
                self.page_step_requested.emit(1 if delta<0 else -1)
            event.accept()
            return
        self._wheel_page_delta=0
        super().wheelEvent(event)

    def page_point_at(self,view_pos):
        """將視窗座標換算為 PDF 頁面座標；不在頁面內時回傳 None。"""
        rect=self.sceneRect()
        scene_pos=self.mapToScene(view_pos)
        if rect.isEmpty() or not rect.contains(scene_pos):
            return None
        return transform_point(inverse_transform(self.matrix),scene_pos.x(),scene_pos.y())

    def contextMenuEvent(self,event):
        """右鍵時回報螢幕位置、PDF 座標與游標下的文字。"""
        if self.inline_editor is not None:
            self.inline_editor.commit()
        point=self.page_point_at(event.pos())
        run=self._run_at(self.mapToScene(event.pos())) if point is not None else None
        self.context_menu_requested.emit(event.globalPos(),point,run)
        event.accept()

    def leaveEvent(self,event):
        self.pointer_moved.emit(None)
        super().leaveEvent(event)

    def mouseMoveEvent(self,event):
        self.pointer_moved.emit(self.page_point_at(event.position().toPoint()))
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
        if (event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_F2)
                and self.selected_run is not None and self.inline_editor is None):
            self.run_selected.emit(self.selected_run)
            event.accept()
            return
        if event.key()==Qt.Key.Key_Delete and self.selected_run is not None:
            self.run_delete_requested.emit(self.selected_run)
            event.accept()
            return
        super().keyPressEvent(event)
