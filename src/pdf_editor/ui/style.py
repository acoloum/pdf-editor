"""墨頁 PDF 深色科技風主題：色票、Qt 樣式表、調色盤與工具列圖示。"""
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap

# 色票集中管理，畫布與對話框共用同一組顏色。
COLORS = {
    "bg": "#0a0e17",
    "surface": "#0f1623",
    "surface_alt": "#141d2c",
    "elevated": "#1a2537",
    "input": "#0c131f",
    "border": "#223049",
    "border_strong": "#304263",
    "text": "#dbe5f3",
    "text_muted": "#7f8ea8",
    "text_disabled": "#4a5873",
    "accent": "#22d3ee",
    "accent_strong": "#06b6d4",
    "accent_blue": "#3b82f6",
    "accent_soft": "rgba(34, 211, 238, 0.14)",
    "accent_hover": "rgba(34, 211, 238, 0.22)",
    "canvas": "#070a11",
    "canvas_grid": "#16213a",
    "danger": "#f87171",
    "annotation": "#a78bfa",
    "search": "#fbbf24",
}

ICON_FONT = "Segoe MDL2 Assets"

# 工具列動作對應的 Segoe MDL2 Assets 字碼（Windows 10/11 內建字型）。
ICON_GLYPHS = {
    "open": "\ue8e5",
    "save": "\ue792",
    "undo": "\ue7a7",
    "redo": "\ue7a6",
    "add_text": "\ue8d2",
    "stamp": "\ueb9f",
    "convert_stamp": "\ue81e",
    "signature": "\uec87",
    "collection": "\ue734",
    "ocr": "\uee6f",
    "compare": "\ue8a1",
    "merge": "\ue8c8",
    "split": "\ue8c6",
    "page_marks": "\ue8ec",
    "header_footer": "\uea37",
    "page_menu": "\ue7c3",
    "markup_menu": "\ue932",
    "search": "\ue721",
    "search_previous": "\ue76b",
    "search_next": "\ue76c",
    "zoom_out": "\ue738",
    "zoom_in": "\ue710",
    "print": "\ue749",
    "previous_page": "\ue70e",
    "next_page": "\ue70d",
    "first_page": "\ue892",
    "last_page": "\ue893",
}

C = COLORS


def _arrow_image(direction, color):
    """產生樣式表使用的小箭頭 PNG；Qt 樣式表無法直接繪製三角形。"""
    from PIL import Image, ImageDraw
    folder = Path(tempfile.gettempdir()) / "pdf_editor_theme"
    path = folder / f"arrow-{direction}-{color.lstrip('#')}.png"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        # 以 4 倍尺寸繪製再縮小，取得平滑邊緣。
        size, scale = (20, 14), 4
        image = Image.new("RGBA", (size[0] * scale, size[1] * scale), (0, 0, 0, 0))
        w, h = image.size
        points = ([(w * 0.2, h * 0.72), (w * 0.5, h * 0.28), (w * 0.8, h * 0.72)] if direction == "up"
            else [(w * 0.2, h * 0.28), (w * 0.5, h * 0.72), (w * 0.8, h * 0.28)])
        ImageDraw.Draw(image).polygon(points, fill=color)
        image.resize(size, Image.Resampling.LANCZOS).save(path)
    return path.as_posix()


try:
    ARROWS = {name: _arrow_image(direction, color) for name, direction, color in (
        ("up", "up", COLORS["accent"]), ("down", "down", COLORS["accent"]),
        ("up_disabled", "up", COLORS["text_disabled"]), ("down_disabled", "down", COLORS["text_disabled"]))}
except Exception:
    ARROWS = {}

STYLE = f"""
* {{ outline: 0; }}
QMainWindow, QDialog {{ background: {C['bg']}; }}
QWidget {{ font-family: "Microsoft JhengHei UI"; font-size: 10pt; color: {C['text']}; }}
QWidget:disabled {{ color: {C['text_disabled']}; }}

QToolBar {{ background: {C['surface']}; border: 0; border-bottom: 1px solid {C['border']};
    spacing: 4px; padding: 6px 10px; }}
QToolBar::separator {{ background: {C['border']}; width: 1px; margin: 6px 8px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px;
    padding: 6px 9px; color: {C['text']}; }}
QToolButton:hover {{ background: {C['accent_soft']}; border-color: {C['border_strong']}; }}
QToolButton:pressed {{ background: {C['accent_hover']}; }}
QToolButton:checked {{ background: {C['accent_hover']}; border-color: {C['accent']}; color: {C['accent']}; }}
QToolButton:disabled {{ color: {C['text_disabled']}; }}
QToolButton::menu-indicator {{ subcontrol-position: right center; width: 0; image: none; }}
QToolButton#qt_toolbar_ext_button {{ padding: 2px; }}
QToolBar#mainToolbar {{ spacing: 1px; padding: 5px 6px; }}
QToolBar#mainToolbar::separator {{ margin: 8px 4px; }}
QToolBar#mainToolbar QToolButton {{ padding: 4px 4px; font-size: 9pt; min-width: 48px; }}

QPushButton {{ background: {C['surface_alt']}; border: 1px solid {C['border_strong']}; border-radius: 6px;
    padding: 7px 14px; color: {C['text']}; }}
QPushButton:hover {{ background: {C['elevated']}; border-color: {C['accent']}; color: #ffffff; }}
QPushButton:pressed {{ background: {C['accent_soft']}; }}
QPushButton:disabled {{ background: {C['surface']}; border-color: {C['border']}; color: {C['text_disabled']}; }}
QPushButton#primary {{ border: 0; color: #041018; font-weight: 600;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {C['accent']}, stop:1 {C['accent_blue']}); }}
QPushButton#primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #67e8f9, stop:1 #60a5fa); }}
QPushButton#primary:disabled {{ background: {C['elevated']}; color: {C['text_disabled']}; }}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {C['input']}; border: 1px solid {C['border_strong']}; border-radius: 6px;
    padding: 5px 7px; color: {C['text']}; selection-background-color: {C['accent_strong']};
    selection-color: #041018; }}
QLineEdit:hover, QTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    border-color: #3f5680; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {C['accent']}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    background: {C['surface']}; border-color: {C['border']}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; width: 18px; border: 0; border-left: 1px solid {C['border']};
    background: transparent; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 6px; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 6px; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::down-button:hover {{ background: {C['accent_soft']}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{ARROWS.get('up', '')}"); width: 10px; height: 7px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{ARROWS.get('down', '')}"); width: 10px; height: 7px; }}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{ image: url("{ARROWS.get('up_disabled', '')}"); }}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{ image: url("{ARROWS.get('down_disabled', '')}"); }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: 0; }}
QComboBox::down-arrow {{ image: url("{ARROWS.get('down', '')}"); width: 10px; height: 7px; }}
QComboBox::down-arrow:disabled {{ image: url("{ARROWS.get('down_disabled', '')}"); }}
QComboBox QAbstractItemView {{ background: {C['elevated']}; border: 1px solid {C['border_strong']};
    selection-background-color: {C['accent_soft']}; selection-color: {C['accent']}; padding: 4px; }}

QMenu {{ background: {C['elevated']}; border: 1px solid {C['border_strong']}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 7px 28px 7px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {C['accent_soft']}; color: {C['accent']}; }}
QMenu::item:disabled {{ color: {C['text_disabled']}; }}
QMenu::separator {{ height: 1px; background: {C['border']}; margin: 5px 8px; }}

QListWidget {{ background: {C['surface']}; border: 0; border-right: 1px solid {C['border']}; padding: 8px; }}
QListWidget::item {{ padding: 8px; border-radius: 8px; border: 1px solid transparent; color: {C['text_muted']}; }}
QListWidget::item:hover {{ background: {C['surface_alt']}; border-color: {C['border']}; }}
QListWidget::item:selected {{ background: {C['accent_soft']}; border-color: {C['accent']}; color: {C['accent']}; }}

QScrollArea, QStackedWidget {{ background: {C['surface']}; border: 0; }}
QScrollArea {{ border-left: 1px solid {C['border']}; }}
QScrollArea > QWidget > QWidget {{ background: {C['surface']}; }}
QSplitter::handle {{ background: {C['bg']}; }}
QSplitter::handle:hover {{ background: {C['accent_strong']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {C['border_strong']}; border-radius: 3px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {C['accent_strong']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QLabel {{ background: transparent; }}
QLabel#heading {{ font-size: 15pt; font-weight: 600; padding: 6px 0 10px 0; color: #ffffff;
    border-bottom: 2px solid {C['accent']}; margin-bottom: 6px; }}
QLabel#hint {{ color: {C['text_muted']}; }}

QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px;
    border: 1px solid {C['border_strong']}; background: {C['input']}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {C['accent']}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {C['accent']}, stop:0.55 {C['accent']}, stop:0.6 {C['input']}, stop:1 {C['input']});
    border-color: {C['accent']}; }}

QGroupBox {{ border: 1px solid {C['border']}; border-radius: 8px; margin-top: 16px; padding: 14px 12px 12px 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {C['accent']}; }}

QTabWidget::pane {{ border: 1px solid {C['border']}; border-radius: 6px; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 8px 16px; color: {C['text_muted']};
    border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {C['accent']}; border-bottom-color: {C['accent']}; }}
QTabBar::tab:hover {{ color: {C['text']}; }}

QProgressBar {{ background: {C['input']}; border: 1px solid {C['border']}; border-radius: 5px;
    text-align: center; height: 14px; }}
QProgressBar::chunk {{ border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {C['accent']}, stop:1 {C['accent_blue']}); }}

QToolTip {{ background: {C['elevated']}; color: {C['text']}; border: 1px solid {C['accent']};
    border-radius: 4px; padding: 5px 8px; }}
QStatusBar {{ background: {C['surface']}; border-top: 1px solid {C['border']}; color: {C['text_muted']};
    padding: 3px 8px; }}
QStatusBar::item {{ border: 0; }}
QMessageBox, QInputDialog {{ background: {C['bg']}; }}
QToolButton[popupMode="1"] {{ padding-right: 16px; }}
QToolButton::menu-button {{ border: 0; border-left: 1px solid transparent; width: 14px;
    border-top-right-radius: 6px; border-bottom-right-radius: 6px; }}
QToolButton::menu-button:hover {{ background: {C['accent_soft']}; border-left-color: {C['border_strong']}; }}
QToolButton::menu-arrow {{ image: url("{ARROWS.get('down', '')}"); width: 8px; height: 6px; }}
QToolButton::menu-arrow:disabled {{ image: url("{ARROWS.get('down_disabled', '')}"); }}

QTabWidget#sideTabs::pane {{ border: 0; border-top: 1px solid {C['border']}; top: -1px; }}
QTabWidget#sideTabs QTabBar::tab {{ min-width: 56px; }}
QTreeWidget {{ background: {C['surface']}; border: 0; padding: 6px; }}
QTreeWidget::item {{ padding: 5px 4px; border-radius: 4px; }}
QTreeWidget::item:hover {{ background: {C['surface_alt']}; }}
QTreeWidget::item:selected {{ background: {C['accent_soft']}; color: {C['accent']}; }}
QTabWidget#sideTabs > QWidget {{ background: {C['surface']}; }}
QFrame#searchBar {{ background: {C['elevated']}; border: 1px solid {C['accent']}; border-radius: 10px; }}
QFrame#searchBar QLineEdit {{ background: {C['input']}; }}
QFrame#searchBar QToolButton {{ padding: 4px; }}

QStatusBar QLabel#statusField {{ color: {C['text_muted']}; padding: 0 10px; border-left: 1px solid {C['border']};
    font-family: "Consolas", "Microsoft JhengHei UI"; }}
QStatusBar QLabel#dirtyIndicator {{ color: {C['search']}; padding: 0 10px; font-weight: 600; }}
QStatusBar QWidget#statusGroup {{ border-left: 1px solid {C['border']}; }}
QStatusBar QToolButton {{ padding: 3px; border-radius: 4px; }}
QStatusBar QSpinBox, QStatusBar QComboBox {{ padding: 2px 6px; }}
"""


def build_palette():
    """建立深色調色盤，讓 Fusion 原生繪製的元件（箭頭、選單）與樣式表一致。"""
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: C["bg"],
        QPalette.ColorRole.WindowText: C["text"],
        QPalette.ColorRole.Base: C["input"],
        QPalette.ColorRole.AlternateBase: C["surface_alt"],
        QPalette.ColorRole.ToolTipBase: C["elevated"],
        QPalette.ColorRole.ToolTipText: C["text"],
        QPalette.ColorRole.PlaceholderText: C["text_muted"],
        QPalette.ColorRole.Text: C["text"],
        QPalette.ColorRole.Button: C["surface_alt"],
        QPalette.ColorRole.ButtonText: C["text"],
        QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Light: C["border_strong"],
        QPalette.ColorRole.Midlight: C["border"],
        QPalette.ColorRole.Mid: C["border"],
        QPalette.ColorRole.Dark: C["bg"],
        QPalette.ColorRole.Shadow: "#000000",
        QPalette.ColorRole.Highlight: C["accent_strong"],
        QPalette.ColorRole.HighlightedText: "#041018",
        QPalette.ColorRole.Link: C["accent"],
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(C["text_disabled"]))
    return palette


def apply_theme(app):
    """將深色主題套用到整個應用程式，涵蓋未指定父視窗的對話框。"""
    if app is None or app.property("pdf_editor_theme"):
        return
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(STYLE)
    app.setProperty("pdf_editor_theme", True)


def thumbnail_icon(pixmap):
    """建立縮圖圖示；選取狀態沿用原圖，避免頁面被選取色染色。"""
    icon = QIcon()
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Selected, QIcon.Mode.Active):
        icon.addPixmap(pixmap, mode)
    return icon


def glyph_icon(name, size=18):
    """以系統圖示字型繪製工具列圖示，分別產生一般與停用狀態。"""
    glyph = ICON_GLYPHS.get(name)
    if glyph is None:
        return QIcon()
    icon = QIcon()
    for mode, color in ((QIcon.Mode.Normal, C["accent"]), (QIcon.Mode.Active, "#67e8f9"),
            (QIcon.Mode.Selected, "#67e8f9"), (QIcon.Mode.Disabled, C["text_disabled"])):
        for ratio in (1, 2):
            pixmap = QPixmap(size * ratio, size * ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            font = QFont(ICON_FONT)
            font.setPixelSize(round(size * ratio * 0.8))
            painter.setFont(font)
            painter.setPen(QColor(color))
            painter.drawText(QRect(0, 0, size * ratio, size * ratio), Qt.AlignmentFlag.AlignCenter, glyph)
            painter.end()
            pixmap.setDevicePixelRatio(ratio)
            icon.addPixmap(pixmap, mode)
    return icon


def apply_dark_title_bar(widget):
    """在 Windows 10/11 啟用深色視窗標題列；其他平台或失敗時略過。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        value = ctypes.c_int(1)
        # DWMWA_USE_IMMERSIVE_DARK_MODE：Windows 11 與 20H1 以後為 20，較舊版本為 19。
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass
