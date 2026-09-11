from dataclasses import replace
from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QPixmap, QPen, QColor, QTransform, QPainter
from PySide6.QtWidgets import QGraphicsView,QGraphicsScene,QGraphicsPixmapItem,QGraphicsItem
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

class Canvas(QGraphicsView):
    run_selected=Signal(object)
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
        self.setMinimumWidth(400)

    def display(self,data,layers=()):
        self.scene().clear()
        self.highlight=None
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

    def mousePressEvent(self,event):
        scene_pos=self.mapToScene(event.pos())
        item=self.scene().itemAt(scene_pos,QTransform())
        if not isinstance(item,LayerItem):
            x,y=transform_point(inverse_transform(self.matrix),scene_pos.x(),scene_pos.y())
            for run in self.runs:
                x0,y0,x1,y1=run.rect
                if x0<=x<=x1 and y0<=y<=y1:
                    if self.highlight:
                        self.scene().removeItem(self.highlight)
                    r=transformed_rect(self.matrix,run.rect)
                    self.highlight=self.scene().addRect(r[0],r[1],r[2]-r[0],r[3]-r[1],
                        QPen(QColor("#246b55"),2))
                    self.highlight.setZValue(2)
                    self.run_selected.emit(run)
                    break
        super().mousePressEvent(event)
