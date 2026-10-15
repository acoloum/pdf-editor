from dataclasses import dataclass, replace
from PySide6.QtCore import Qt, Signal, QPointF, QTimer, QRectF, QEvent
from PySide6.QtGui import (QPixmap,QPen,QColor,QTransform,QPainter,QKeyEvent,QBrush,
    QPainterPath)
from PySide6.QtWidgets import (QGraphicsView,QGraphicsScene,QGraphicsPixmapItem,
    QGraphicsItem,QLabel,QTextEdit,QFrame,QApplication)
from PySide6.QtGui import QTextOption
from pdf_editor.engine.geometry import transformed_rect, transform_point, inverse_transform
from pdf_editor.engine.overlay import transformed_image
from pdf_editor.ui.style import COLORS

@dataclass(frozen=True)
class CanvasSelection:
    """以滑鼠圈選的物件：同一頁的圖層代碼、可修改的文字、註解與格線等向量圖形外框。"""
    page: int
    layers: tuple = ()
    runs: tuple = ()
    annotations: tuple = ()
    # 圈到但無法安全修改（例如直書）而略過的文字數。
    skipped_runs: int = 0
    drawings: tuple = ()

    @property
    def count(self):
        return len(self.layers)+len(self.runs)+len(self.annotations)+len(self.drawings)

    def shifted(self,dx,dy):
        """位移後的選取內容，供重新渲染後依新位置找回同一批物件。"""
        def moved_rect(r):
            return (r[0]+dx,r[1]+dy,r[2]+dx,r[3]+dy)
        def moved(item):
            return replace(item,rect=moved_rect(item.rect))
        return replace(self,runs=tuple(moved(run) for run in self.runs),
            annotations=tuple(moved(item) for item in self.annotations),
            drawings=tuple(moved_rect(rect) for rect in self.drawings))


def _contains(outer,inner):
    """inner 矩形是否完整落在 outer 內（頁面座標，容許 0.5 點誤差）。"""
    return (inner[0]>=outer[0]-0.5 and inner[1]>=outer[1]-0.5
        and inner[2]<=outer[2]+0.5 and inner[3]<=outer[3]+0.5)


def _same_place(first,second,tolerance=1.5):
    return abs(first[0]-second[0])<=tolerance and abs(first[1]-second[1])<=tolerance


def _same_rect(first,second,tolerance=0.5):
    return all(abs(a-b)<=tolerance for a,b in zip(first,second))


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

# QTextDocument 以 Unicode 段落與行分隔符號表示換行。
PARAGRAPH_SEPARATOR="\u2029"
LINE_SEPARATOR="\u2028"
NEWLINE=chr(10)

class InlineTextEditor(QTextEdit):
    """頁面上的輸入框：Enter 套用、Shift+Enter 換行、Esc 取消。"""
    commit_requested=Signal(str)
    cancel_requested=Signal()
    content_resized=Signal()

    def __init__(self,text,parent=None):
        super().__init__(parent)
        self.finished=False
        self._alignment=Qt.AlignmentFlag.AlignLeft
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.document().setDocumentMargin(1)
        self.setPlainText(text)
        self.textChanged.connect(self._text_changed)

    def _text_changed(self):
        if self.property("error"):
            self.set_error(None)
        self.content_resized.emit()

    def text(self):
        # toPlainText 會把不換行空格（U+00A0）換成一般空格，造成未修改的文字被誤判為變更；
        # 改讀原始內容，並把段落分隔符號轉回換行。
        raw=self.document().toRawText()
        return raw.replace(PARAGRAPH_SEPARATOR,NEWLINE).replace(LINE_SEPARATOR,NEWLINE)

    def setText(self,text):
        self.setPlainText(text)
        self.setAlignment(self._alignment)

    def setAlignment(self,alignment):
        # 對齊套用在整份內容，換行後的新段落也維持相同對齊。
        self._alignment=alignment
        option=QTextOption(alignment & Qt.AlignmentFlag.AlignHorizontal_Mask)
        option.setWrapMode(QTextOption.WrapMode.NoWrap)
        self.document().setDefaultTextOption(option)
        cursor=self.textCursor()
        cursor.select(cursor.SelectionType.Document)
        block=cursor.blockFormat()
        block.setAlignment(alignment & Qt.AlignmentFlag.AlignHorizontal_Mask)
        cursor.mergeBlockFormat(block)

    def alignment(self):
        return self._alignment

    def line_count(self):
        return max(1,self.document().blockCount())

    def sizeHint(self):
        hint=super().sizeHint()
        lines=self.line_count()
        height=self.fontMetrics().lineSpacing()*lines+self.contentsMargins().top()+self.contentsMargins().bottom()+8
        hint.setHeight(height)
        return hint

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
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.textCursor().insertText("\n")
                event.accept()
                return
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
    image_clicked=Signal(object)
    background_clicked=Signal()
    # 圈選完成（CanvasSelection）、整批拖曳放開（選取內容, (dx, dy)）與按 Delete 刪除整批。
    objects_selected=Signal(object)
    selection_moved=Signal(object,object)
    selection_delete_requested=Signal(object)
    # 每次在畫布上放開滑鼠後發出；主視窗用來補做拖曳期間延後的重繪。
    mouse_released=Signal()

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor(COLORS["canvas"]))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.matrix=(1,0,0,1,0,0)
        self.runs=[]
        self.annotations=[]
        self.editable_images=()
        # 可圈選的格線等向量圖形外框（頁面座標）。
        self.drawings=()
        self.image_hover=None
        self._hover_image=None
        self.highlight=None
        self.annotation_highlight=None
        self.search_highlight=None
        self.selected_run=None
        self.selected_annotation=None
        self._drag_run=None
        self._drag_start_scene=QPointF()
        self._drag_original_rect=None
        # 是否有從畫布開始、尚未放開的滑鼠按壓。
        self.pointer_pressed=False
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
        self._page=None
        # 圈選：按下的位置（場景與視窗座標）、是否已拖曳超過門檻、顯示中的虛線框。
        self._marquee_start=None
        self._marquee_press=None
        self._marquee_active=False
        self._marquee_item=None
        # 在圖片上按下時先記住，放開時沒有拖曳才當作點選圖片。
        self._pending_image=None
        # 目前的整批選取與其外框；拖曳整批時記錄起點與各圖層原位置。
        self.group=None
        self._group_items=[]
        self._group_drag_start=None
        self._group_origins={}
        self._group_hover=False

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
        # 整批選取保留到重建畫面後，再依新資料找回仍存在的物件。
        group=self.group
        self._reset_pointer_tracking()
        self.group=None
        self._group_items=[]
        self.scene().clear()
        self.highlight=None
        self.annotation_highlight=None
        self.search_highlight=None
        self.selected_run=None
        self.selected_annotation=None
        self._drag_run=None
        self.matrix=data["matrix"]
        self._page=data["page"]
        self.runs=data["runs"]
        self.annotations=data.get("annotations",())
        self.editable_images=data.get("images",())
        self.drawings=data.get("drawings",())
        # scene().clear() 已移除舊外框；只有提示曾設定游標時才還原，避免蓋掉拖曳中的游標。
        had_hover=self._hover_image is not None
        self.image_hover=None
        self._hover_image=None
        if had_hover:
            self.viewport().unsetCursor()
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
        if group is not None and group.page==data["page"]:
            self._restore_group(group,layers)
        self._position_inline_editor()

    def _reset_pointer_tracking(self):
        """重建畫面時放棄進行中的圈選與整批拖曳（相關圖形會隨場景一起清除）。"""
        self._marquee_start=None
        self._marquee_press=None
        self._marquee_active=False
        self._marquee_item=None
        self._pending_image=None
        self._group_drag_start=None
        self._group_origins={}
        if self._group_hover:
            self._group_hover=False
            self.viewport().unsetCursor()

    def _restore_group(self,group,layers):
        """以圖層代碼、文字內容與位置找回重新渲染後仍存在的選取物件。"""
        layer_ids=tuple(layer.id for layer in layers
            if layer.page==group.page and layer.id in group.layers)
        runs=tuple(run for old in group.runs for run in self.runs
            if run.editable and run.text==old.text and _same_place(run.rect,old.rect))
        annotations=tuple(item for old in group.annotations for item in self.annotations
            if item.kind==old.kind and _same_place(item.rect,old.rect))
        drawings=tuple(rect for old in group.drawings for rect in self.drawings
            if _same_rect(rect,old))
        restored=CanvasSelection(group.page,layer_ids,tuple(dict.fromkeys(runs)),
            tuple(dict.fromkeys(annotations)),drawings=tuple(dict.fromkeys(drawings)))
        if restored.count:
            self.set_group_selection(restored)

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

    def layer_at(self,view_pos):
        """視窗座標下最上層的圖層；沒有時回傳 None。"""
        for item in self.items(view_pos):
            if isinstance(item,LayerItem):
                return item.layer
        return None

    def _image_at(self,scene_pos):
        """游標下可直接點選編輯的圖片（以 PDF 座標判斷）。"""
        x,y=transform_point(inverse_transform(self.matrix),scene_pos.x(),scene_pos.y())
        for image in reversed(self.editable_images):
            x0,y0,x1,y1=image.rect
            if x0<=x<=x1 and y0<=y<=y1:
                return image
        return None

    def _image_hover_target(self,scene_pos):
        """只有在沒有圖層、文字或進行中模式時，才提示圖片可點選。"""
        if not self.editable_images:
            return None
        if (self._crop_mode or self._text_insertion or self._note_insertion
                or self._annotation_selection or self._drag_run
                or self._marquee_start is not None or self._group_drag_start is not None):
            return None
        if isinstance(self.scene().itemAt(scene_pos,QTransform()),LayerItem):
            return None
        if self._group_contains(scene_pos):
            # 整批選取的物件上顯示移動游標，不提示圖片。
            return None
        if self._run_at(scene_pos):
            return None
        return self._image_at(scene_pos)

    def _show_image_hover(self,image):
        if image!=self._hover_image:
            self.clear_image_hover()
            r=transformed_rect(self.matrix,image.rect)
            pen=QPen(QColor(COLORS["accent"]),2,Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self.image_hover=self.scene().addRect(r[0],r[1],r[2]-r[0],r[3]-r[1],pen)
            self.image_hover.setZValue(4)
            self.image_hover.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._hover_image=image
        # 每次移動都重設游標：從圖層移過來時，Qt 會把游標還原成圖層前的樣式。
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)

    def clear_image_hover(self):
        was_hovering=self._hover_image is not None
        if self.image_hover is not None:
            self.scene().removeItem(self.image_hover)
        self.image_hover=None
        self._hover_image=None
        if was_hovering:
            self.viewport().unsetCursor()

    def clear_text_selection(self):
        self.selected_run=None
        if self.highlight:
            self.scene().removeItem(self.highlight)
            self.highlight=None

    def cancel_text_drag(self):
        """取消進行中的文字拖曳並移除選取外框；頁面資料已過時（例如剛自動還原）時使用。"""
        self._drag_run=None
        self.clear_text_selection()

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
        self.clear_group_selection()
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
        self.clear_group_selection()
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
        self.clear_group_selection()
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
        self.clear_group_selection()
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

    def begin_inline_text(self,rect,text,run=None,size=11,alignment="left",font_path=None,
            bold=False):
        self.cancel_inline_editor()
        self.inline_run=run
        self.inline_rect=tuple(rect)
        editor=InlineTextEditor(text,self.viewport())
        horizontal=(Qt.AlignmentFlag.AlignHCenter if alignment in ("hcenter","center")
            else Qt.AlignmentFlag.AlignLeft)
        editor.setAlignment(horizontal | Qt.AlignmentFlag.AlignVCenter)
        editor.setPlaceholderText("直接輸入文字（Shift+Enter 換行）")
        editor.setObjectName("inlineEditor")
        # PDF 點數依目前縮放換算成螢幕像素，輸入時的字大小與頁面上一致；
        # 字型須寫在樣式表內，否則會被全域樣式表的字級覆蓋。
        pixels=max(8,round(float(size)*self.view_scale()))
        family=self.font_family_for(font_path)
        families=(f'"{family}", ' if family else "")+'"Microsoft JhengHei UI"'
        editor.setStyleSheet(
            "#inlineEditor{background:rgba(255,255,255,240);color:#0b1220;border:2px solid %s;"
            "border-radius:3px;padding:2px 5px;font-size:%dpx;font-family:%s;font-weight:%s;}"
            "#inlineEditor[error=\"true\"]{border:2px solid %s;background:#fff5f5;}"
            %(COLORS["accent_strong"],pixels,families,"bold" if bold else "normal",
                COLORS["danger"]))
        editor.commit_requested.connect(self._commit_inline_text)
        editor.cancel_requested.connect(self._cancel_inline_text)
        editor.content_resized.connect(self._position_inline_editor)
        editor.error_label=self.inline_error
        self.inline_error.hide()
        self.inline_editor=editor
        # 先套用樣式表（含字級）再計算尺寸，避免高度以預設字型估算。
        editor.ensurePolished()
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
            height=max(30,bottom_right.y()-top_left.y()+8,self.inline_editor.sizeHint().height())
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
            try:
                editor.content_resized.disconnect(self._position_inline_editor)
            except (RuntimeError,TypeError):
                pass
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

    def _page_rect(self,scene_rect):
        """場景矩形換算成頁面座標矩形 (x0, y0, x1, y1)。"""
        inv=inverse_transform(self.matrix)
        points=(scene_rect.topLeft(),scene_rect.topRight(),scene_rect.bottomLeft(),
            scene_rect.bottomRight())
        mapped=[transform_point(inv,point.x(),point.y()) for point in points]
        xs=[point[0] for point in mapped]
        ys=[point[1] for point in mapped]
        return (min(xs),min(ys),max(xs),max(ys))

    def _page_delta(self,delta):
        """場景中的位移換算成頁面座標的位移。"""
        inv=inverse_transform(self.matrix)
        x,y=transform_point(inv,delta.x(),delta.y())
        ox,oy=transform_point(inv,0,0)
        return x-ox,y-oy

    def _layer_items(self):
        return [item for item in self.scene().items() if isinstance(item,LayerItem)]

    def objects_in(self,scene_rect):
        """完整落在圈選範圍內的圖層、可修改文字、註解與格線等向量圖形。"""
        area=self._page_rect(scene_rect)
        layers=tuple(item.layer.id for item in reversed(self._layer_items())
            if scene_rect.contains(item.mapRectToScene(item._content_rect())))
        inside=[run for run in self.runs if _contains(area,run.rect)]
        runs=tuple(run for run in inside if run.editable)
        annotations=tuple(item for item in self.annotations if _contains(area,item.rect))
        drawings=tuple(rect for rect in self.drawings if _contains(area,rect))
        return CanvasSelection(self._page,layers,runs,annotations,len(inside)-len(runs),drawings)

    def set_group_selection(self,selection):
        """以虛線框標示整批選取的文字與註解；圖層顯示一般的選取外框。"""
        self.clear_group_selection()
        self.clear_text_selection()
        self.clear_annotation_selection()
        self.group=selection
        for item in self._layer_items():
            item.setSelected(item.layer.id in selection.layers)
        for rect,color in ([(run.rect,COLORS["accent"]) for run in selection.runs]
                +[(item.rect,COLORS["annotation"]) for item in selection.annotations]):
            r=transformed_rect(self.matrix,rect)
            pen=QPen(QColor(color),2,Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            fill=QColor(color)
            fill.setAlpha(36)
            frame=self.scene().addRect(r[0]-2,r[1]-2,r[2]-r[0]+4,r[3]-r[1]+4,pen,QBrush(fill))
            frame.setZValue(4)
            frame.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._group_items.append(frame)
        # 格線以青色覆蓋原線條標示；細線至少畫 2 像素寬，才看得出已選取。
        color=QColor(COLORS["accent"])
        color.setAlpha(200)
        for rect in selection.drawings:
            r=transformed_rect(self.matrix,rect)
            width,height=max(2.0,r[2]-r[0]),max(2.0,r[3]-r[1])
            cx,cy=(r[0]+r[2])/2,(r[1]+r[3])/2
            line=self.scene().addRect(cx-width/2,cy-height/2,width,height,
                QPen(Qt.PenStyle.NoPen),QBrush(color))
            line.setZValue(4)
            line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._group_items.append(line)

    def keep_group(self,selection):
        """下次重建畫面時改依 selection 找回整批選取（例如移動失敗、物件回到原位）。"""
        if self.group is not None:
            self.group=selection

    def clear_group_selection(self):
        if self.group is None and not self._group_items:
            return
        layer_ids=self.group.layers if self.group is not None else ()
        self.group=None
        for item in self._group_items:
            try:
                if item.scene() is self.scene():
                    self.scene().removeItem(item)
            except RuntimeError:
                pass
        self._group_items=[]
        self._group_drag_start=None
        self._group_origins={}
        for item in self._layer_items():
            if item.layer.id in layer_ids:
                item.setSelected(False)
        if self._group_hover:
            self._group_hover=False
            self.viewport().unsetCursor()

    def _group_contains(self,scene_pos):
        """游標是否落在整批選取的任一物件上。"""
        group=self.group
        if group is None:
            return False
        for item in self._layer_items():
            if item.layer.id in group.layers and item.mapRectToScene(
                    item._content_rect()).contains(scene_pos):
                return True
        x,y=transform_point(inverse_transform(self.matrix),scene_pos.x(),scene_pos.y())
        if any(r[0]<=x<=r[2] and r[1]<=y<=r[3]
                for r in [run.rect for run in group.runs]+[item.rect for item in group.annotations]):
            return True
        # 格線很細：容許游標偏離約 4 像素。
        slack=4/self.view_scale()
        return any(r[0]-slack<=x<=r[2]+slack and r[1]-slack<=y<=r[3]+slack
            for r in group.drawings)

    def _begin_marquee(self,scene_pos,view_pos,image=None):
        self._marquee_start=scene_pos
        self._marquee_press=view_pos
        self._marquee_active=False
        self._pending_image=image

    def _update_marquee(self,scene_pos,view_pos):
        if not self._marquee_active:
            moved=(view_pos-self._marquee_press).manhattanLength()
            if moved<QApplication.startDragDistance():
                return
            # 拖曳超過門檻才算圈選；在圖片上開始拖曳時改為圈選，不再編輯該圖片。
            self._marquee_active=True
            self._pending_image=None
            pen=QPen(QColor(COLORS["accent"]),1,Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            fill=QColor(COLORS["accent"])
            fill.setAlpha(40)
            self._marquee_item=self.scene().addRect(QRectF(),pen,QBrush(fill))
            self._marquee_item.setZValue(30)
            self._marquee_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._marquee_item.setRect(QRectF(self._marquee_start,scene_pos).normalized())

    def _finish_marquee(self,scene_pos):
        start,active,image=self._marquee_start,self._marquee_active,self._pending_image
        item=self._marquee_item
        self._marquee_start=None
        self._marquee_press=None
        self._marquee_active=False
        self._marquee_item=None
        self._pending_image=None
        if item is not None:
            try:
                if item.scene() is self.scene():
                    self.scene().removeItem(item)
            except RuntimeError:
                pass
        if not active:
            if image is not None:
                # 主視窗轉換期間會停用畫布，之後的放開處理不一定執行；這次按壓視為已結束。
                self.pointer_pressed=False
                self.image_clicked.emit(image)
            return
        selection=self.objects_in(QRectF(start,scene_pos).normalized())
        if not selection.count:
            if selection.skipped_runs:
                # 只圈到無法修改的文字：讓主視窗說明原因。
                self.objects_selected.emit(selection)
            return
        if selection.count==1 and selection.layers:
            # 只圈到一個圖層時與直接點選相同，右側顯示圖層設定。
            for layer_item in self._layer_items():
                layer_item.setSelected(layer_item.layer.id==selection.layers[0])
            self.layer_selected.emit(selection.layers[0])
            return
        self.set_group_selection(selection)
        self.objects_selected.emit(selection)

    def _start_group_drag(self,scene_pos):
        self._group_drag_start=scene_pos
        self._group_origins={item:item.pos() for item in self._layer_items()
            if item.layer.id in self.group.layers}

    def _drag_group(self,scene_pos):
        delta=scene_pos-self._group_drag_start
        for item,origin in self._group_origins.items():
            item.setPos(origin+delta)
        for item in self._group_items:
            item.setPos(delta)

    def _finish_group_drag(self,scene_pos):
        delta=scene_pos-self._group_drag_start
        self._group_drag_start=None
        self._group_origins={}
        dx,dy=self._page_delta(delta)
        if abs(dx)+abs(dy)<=0.01:
            return
        group=self.group
        # 先記下移動後的位置，重新渲染後才能在新位置找回同一批物件。
        self.group=group.shifted(dx,dy)
        self.selection_moved.emit(group,(dx,dy))

    def _show_highlight(self, rect):
        if self.highlight:
            self.scene().removeItem(self.highlight)
        r=transformed_rect(self.matrix,rect)
        self.highlight=self.scene().addRect(r[0],r[1],r[2]-r[0],r[3]-r[1],
            QPen(QColor(COLORS["accent"]),2))
        self.highlight.setZValue(2)

    def mousePressEvent(self,event):
        self.pointer_pressed=True
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
        modal=self._text_insertion or self._note_insertion or self._annotation_selection
        if self.group is not None and not modal and event.button()==Qt.MouseButton.LeftButton:
            if self._group_contains(scene_pos):
                # 按在整批選取的物件上：拖曳時整批一起移動。
                self.setFocus()
                self._start_group_drag(scene_pos)
                event.accept()
                return
            self.clear_group_selection()
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
            if event.button()==Qt.MouseButton.LeftButton:
                # 文字優先；沒點到文字才看圖片。在圖片上按下時延到放開才轉交圖片編輯，
                # 拖曳超過門檻則改為圈選。
                # 注意：接收訊號的槽函式不可同步重建場景（畫面是非同步重繪）。
                image=self._image_at(scene_pos)
                if image:
                    self.clear_image_hover()
                else:
                    # 右鍵（內容選單）與中鍵不算點空白處，不發訊號。
                    self.background_clicked.emit()
                self._begin_marquee(scene_pos,event.position(),image)
        super().mousePressEvent(event)
        if self._marquee_start is not None:
            # 空白處沒有圖形項目接收按壓；明確接受，後續的拖曳與放開才會送到畫布。
            event.accept()

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
        if self.image_hover is not None:
            self.clear_image_hover()
        super().leaveEvent(event)

    def mouseMoveEvent(self,event):
        self.pointer_moved.emit(self.page_point_at(event.position().toPoint()))
        if self._crop_mode and self._crop_drag_handle:
            self._drag_crop(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        if self._marquee_start is not None:
            self._update_marquee(self.mapToScene(event.position().toPoint()),event.position())
            event.accept()
            return
        if self._group_drag_start is not None:
            self._drag_group(self.mapToScene(event.position().toPoint()))
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
        # 按住任何按鍵（拖曳圖層、捲動頁面）時不提示圖片，避免外框閃現並蓋掉拖曳游標。
        image=(None if event.buttons()!=Qt.MouseButton.NoButton
            else self._image_hover_target(self.mapToScene(event.position().toPoint())))
        if image is None:
            # 先清除提示，Qt 處理圖層游標時才不會存下圖片提示的游標當作原始游標。
            self.clear_image_hover()
        super().mouseMoveEvent(event)
        if image is not None:
            self._show_image_hover(image)
        elif event.buttons()==Qt.MouseButton.NoButton and self.group is not None:
            self._update_group_hover(self.mapToScene(event.position().toPoint()))

    def _update_group_hover(self,scene_pos):
        """游標移到整批選取的物件上時顯示移動游標，提示可整批拖曳。"""
        hovering=self._group_contains(scene_pos)
        if hovering:
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        elif self._group_hover:
            self.viewport().unsetCursor()
        self._group_hover=hovering

    def changeEvent(self,event):
        super().changeEvent(event)
        if (event.type()==QEvent.Type.EnabledChange and not self.isEnabled()
                and self.pointer_pressed):
            # 按住期間被停用時收不到放開事件；視同放開，讓延後的重繪照常補做。
            self.pointer_pressed=False
            self._abort_pointer_actions()
            self.mouse_released.emit()

    def _abort_pointer_actions(self):
        """放棄進行中的圈選與整批拖曳：移除虛線框，整批選取的物件回到原位。"""
        item=self._marquee_item
        if item is not None:
            try:
                if item.scene() is self.scene():
                    self.scene().removeItem(item)
            except RuntimeError:
                pass
        for layer_item,origin in self._group_origins.items():
            layer_item.setPos(origin)
        for frame in self._group_items:
            frame.setPos(0,0)
        self._reset_pointer_tracking()

    def mouseReleaseEvent(self,event):
        self._handle_mouse_release(event)
        if event.buttons()==Qt.MouseButton.NoButton:
            self.pointer_pressed=False
        # 拖曳（含裁切與文字拖曳）結束後才通知，主視窗可在此補做延後的重繪。
        self.mouse_released.emit()

    def _handle_mouse_release(self,event):
        if self._marquee_start is not None:
            self._finish_marquee(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        if self._group_drag_start is not None:
            self._finish_group_drag(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
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

    def keyPressEvent(self,event: QKeyEvent):
        if self.group is not None and event.key()==Qt.Key.Key_Delete:
            self.selection_delete_requested.emit(self.group)
            event.accept()
            return
        if self.group is not None and event.key()==Qt.Key.Key_Escape:
            self.clear_group_selection()
            event.accept()
            return
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
