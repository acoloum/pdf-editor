from dataclasses import dataclass

Rect = tuple[float, float, float, float]
Color = tuple[float, float, float]

@dataclass(frozen=True)
class TextRun:
    id: str
    text: str
    rect: Rect
    font_name: str
    size: float
    editable: bool
    reason: str | None
    color: Color = (0, 0, 0)

@dataclass(frozen=True)
class TextReplacement:
    document_hash: str
    page: int
    run_id: str
    text: str
    rect: Rect
    font_path: str
    size: float
    color: Color

@dataclass(frozen=True)
class DocumentAccess:
    can_edit: bool
    can_reorganize: bool
    reason: str | None

@dataclass(frozen=True)
class Overlay:
    id: str
    page: int
    asset_path: str
    rect: Rect
    angle: float = 0

