"""拖曳物件時的對齊輔助線：比對移動中物件的左、中、右（上、中、下）邊與頁面及其他物件，
距離夠近就吸附並回傳要顯示的輔助線。座標一律使用畫布場景座標（螢幕像素）。"""
from dataclasses import dataclass

# 吸附距離（螢幕像素）。
SNAP_DISTANCE=6.0
# 吸附後視為對齊的誤差。
_ALIGNED=0.5


@dataclass(frozen=True)
class Target:
    """一條可對齊的位置：value 為座標，low/high 為該物件在另一軸的範圍。"""
    value: float
    low: float
    high: float
    page: bool = False


@dataclass(frozen=True)
class Guide:
    """要畫出的輔助線。axis 為 "x"（垂直線）或 "y"（水平線）；page 表示對齊頁面中線或邊緣。"""
    axis: str
    value: float
    start: float
    end: float
    page: bool = False


@dataclass(frozen=True)
class Targets:
    xs: tuple = ()
    ys: tuple = ()


def _features(low,high):
    return (low,(low+high)/2,high)


def alignment_targets(page_size,rects):
    """頁面（左緣、中線、右緣；上緣、中線、下緣）與各物件外框 (x0, y0, x1, y1) 的對齊位置。"""
    width,height=page_size
    xs=[Target(value,0.0,height,True) for value in _features(0.0,width)]
    ys=[Target(value,0.0,width,True) for value in _features(0.0,height)]
    for x0,y0,x1,y1 in rects:
        xs.extend(Target(value,y0,y1) for value in _features(x0,x1))
        ys.extend(Target(value,x0,x1) for value in _features(y0,y1))
    return Targets(tuple(xs),tuple(ys))


def _best_offset(features,targets,threshold):
    best=None
    for target in targets:
        for feature in features:
            offset=target.value-feature
            if abs(offset)<=threshold and (best is None or abs(offset)<abs(best)):
                best=offset
    return best


def _guides(axis,features,span,targets):
    """吸附後與移動物件某一邊對齊的位置；同一位置的多個物件合併成一條線。"""
    merged={}
    for target in targets:
        if not any(abs(target.value-feature)<=_ALIGNED for feature in features):
            continue
        key=round(target.value*2)/2
        start,end=(target.low,target.high) if target.page else (
            min(target.low,span[0]),max(target.high,span[1]))
        if key in merged:
            old=merged[key]
            merged[key]=Guide(axis,old.value,min(old.start,start),max(old.end,end),
                old.page or target.page)
        else:
            merged[key]=Guide(axis,target.value,start,end,target.page)
    return list(merged.values())


def snap(rect,targets,threshold=SNAP_DISTANCE):
    """rect 為移動中物件的外框；回傳吸附位移 (dx, dy) 與吸附後要顯示的輔助線。"""
    x0,y0,x1,y1=rect
    dx=_best_offset(_features(x0,x1),targets.xs,threshold) or 0.0
    dy=_best_offset(_features(y0,y1),targets.ys,threshold) or 0.0
    x0,x1,y0,y1=x0+dx,x1+dx,y0+dy,y1+dy
    guides=(_guides("x",_features(x0,x1),(y0,y1),targets.xs)
        +_guides("y",_features(y0,y1),(x0,x1),targets.ys))
    return (dx,dy),tuple(guides)
