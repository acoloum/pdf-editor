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
    alignment: str = "left"

@dataclass(frozen=True)
class TextInsertion:
    document_hash: str
    page: int
    text: str
    rect: Rect
    font_path: str
    size: float
    color: Color
    alignment: str = "left"

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


@dataclass(frozen=True)
class LegacyImageCandidate:
    """可安全抽離為可編輯圖章的既有 PDF 影像。"""
    xref: int
    page: int
    rect: Rect
    png: bytes
    width: int
    height: int

@dataclass(frozen=True)
class AnnotationInfo:
    xref: int
    kind: str
    rect: Rect
    color: Color | None = None
    content: str = ""


@dataclass(frozen=True)
class SearchMatch:
    page: int
    rect: Rect
