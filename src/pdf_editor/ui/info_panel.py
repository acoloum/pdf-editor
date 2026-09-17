"""未選取任何物件時顯示的文件資訊面板。"""
from pathlib import Path

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QLabel, QVBoxLayout, QWidget

POINTS_PER_MM = 72 / 25.4

# 常見紙張尺寸（公釐，直式），用於顯示易懂的名稱。
PAPER_SIZES = {
    "A3": (297, 420), "A4": (210, 297), "A5": (148, 210),
    "B4": (250, 353), "B5": (176, 250), "Letter": (215.9, 279.4), "Legal": (215.9, 355.6),
}


def paper_name(width_pt, height_pt):
    """依頁面尺寸回傳紙張名稱與方向；不符合常見尺寸時回傳空字串。"""
    width, height = width_pt / POINTS_PER_MM, height_pt / POINTS_PER_MM
    short, long = sorted((width, height))
    for name, (w, h) in PAPER_SIZES.items():
        if abs(short - w) <= 2 and abs(long - h) <= 2:
            return f"{name} {'橫式' if width > height else '直式'}"
    return ""


def format_size(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def describe_document(pdf, page_index, source=None, access=None, password_used=False):
    """整理面板要顯示的欄位；只讀取文件，不做任何修改。"""
    info = {}
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        info["頁數"] = f"{doc.page_count} 頁"
        metadata = doc.metadata or {}
        if 0 <= page_index < doc.page_count:
            page = doc[page_index]
            width, height = page.rect.width, page.rect.height
            name = paper_name(width, height)
            size = f"{width / POINTS_PER_MM:.0f} × {height / POINTS_PER_MM:.0f} mm"
            info["頁面尺寸"] = f"{name}（{size}）" if name else size
            info["頁面旋轉"] = f"{page.rotation}°"
            fonts = sorted({font[3].split("+")[-1] for font in page.get_fonts() if font[3]})
            info["本頁字型"] = "、".join(fonts[:6]) + ("…" if len(fonts) > 6 else "") if fonts else "無（可能為掃描影像）"
        version = metadata.get("format") or ""
        if version:
            info["PDF 版本"] = version
        for key, label in (("title", "標題"), ("author", "作者"), ("producer", "產生程式")):
            if metadata.get(key):
                info[label] = metadata[key]
    if source is not None:
        path = Path(source)
        info = {"檔名": path.name, "位置": str(path.parent), **info}
        try:
            info["檔案大小"] = format_size(path.stat().st_size)
        except OSError:
            pass
    if access is not None:
        info["編輯權限"] = "可編輯" if access.can_edit else (access.reason or "僅供閱讀")
    info["密碼保護"] = "有（另存時不保留）" if password_used else "無"
    return info


class DocumentInfoPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        heading = QLabel("文件資訊")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        self.empty = QLabel("尚未開啟文件。\n\n點選「開啟 PDF」，或直接把 PDF 拖進視窗。")
        self.empty.setObjectName("hint")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.form.setVerticalSpacing(10)
        layout.addLayout(self.form)
        self.values = {}
        tips = QLabel(
            "操作提示\n"
            "・點選頁面文字即可直接修改\n"
            "・拖曳圖章本體移動，拖曳四角等比例縮放\n"
            "・Ctrl + 滾輪縮放，捲到頁尾自動翻頁\n"
            "・右鍵可複製文字與使用頁面操作")
        tips.setObjectName("hint")
        tips.setWordWrap(True)
        layout.addSpacing(12)
        layout.addWidget(tips)
        layout.addStretch()

    def clear(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        self.values = {}
        self.empty.show()

    def set_info(self, info):
        self.clear()
        self.empty.hide()
        for label, value in info.items():
            field = QLabel(str(value))
            field.setWordWrap(True)
            field.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            name = QLabel(label)
            name.setObjectName("hint")
            self.form.addRow(name, field)
            self.values[label] = field
