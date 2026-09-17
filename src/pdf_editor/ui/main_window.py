from pathlib import Path
from dataclasses import replace
import hashlib
import uuid
import pymupdf
from PySide6.QtCore import Qt,QStandardPaths,QSize,Signal,QPointF
from PySide6.QtGui import QAction,QKeySequence,QIcon,QPixmap
from PySide6.QtWidgets import QMainWindow,QWidget,QVBoxLayout,QLabel,QSplitter,QListWidget,QListWidgetItem,QToolBar,QFileDialog,QMessageBox,QInputDialog,QLineEdit,QStackedWidget,QComboBox,QSpinBox,QScrollArea,QListView,QMenu,QToolButton,QAbstractItemView,QApplication,QHBoxLayout,QTabWidget,QTreeWidget,QTreeWidgetItem,QProgressDialog
from pdf_editor.document.session import DocumentSession
from pdf_editor.document.save import write_pdf,publish_batch
from pdf_editor.engine.render import render_page,thumbnail
from pdf_editor.engine.geometry import transform_point,inverse_transform
from pdf_editor.engine.inspection import unlock_pdf
from pdf_editor.engine.text import replace_text, insert_text, find_table_cell
from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.engine.fonts import default_font,embedded_font,checked_font
from pdf_editor.model import TextReplacement,TextInsertion,Overlay
from pdf_editor.errors import EditorError
from pdf_editor.workers import Jobs
from pdf_editor.assets import AssetStore
from pdf_editor.templates import HeaderFooterTemplateStore
from pdf_editor.search import find_text
from pdf_editor.ocr import ocr_pages
from pdf_editor.comparison import compare_pages
from pdf_editor.legacy_overlay_conversion import (
    convert_legacy_image,
    find_convertible_images,
)
from pdf_editor.persistent_overlays import embed_workspace
from pdf_editor.ocr_assets import validate_ocr_assets
from pdf_editor.page_images import export_pages_as_png
from pdf_editor.pages import (merge_pages,split_pages,move_pages,move_pages_to,rotate_pages,
    delete_pages,duplicate_pages,page_order_after_move,page_order_after_drop,insert_pages,
    insert_blank_page,extract_pages)
from pdf_editor.page_decorations import (crop_pages,add_page_numbers,
    add_text_watermark,add_image_watermark,add_header_footer)
from pdf_editor.annotations import (mark_text,add_text_note,delete_annotation,
    set_highlight_color)
from pdf_editor.ui.canvas import Canvas
from pdf_editor.ui.text_panel import TextPanel
from pdf_editor.ui.overlay_panel import OverlayPanel
from pdf_editor.ui.signature_dialog import SignatureDialog
from pdf_editor.ui.comparison_dialog import ComparisonDialog
from pdf_editor.ui.legacy_stamp_dialog import LegacyStampDialog
from pdf_editor.ui.page_dialogs import (MergeDialog,SplitDialog,CropPagesDialog,
    PageDecorationDialog,HeaderFooterTemplatesDialog)
from pdf_editor.ui.style import STYLE,apply_theme,apply_dark_title_bar,glyph_icon,thumbnail_icon
from pdf_editor.ui.settings import AppSettings
from pdf_editor.ui.info_panel import DocumentInfoPanel,describe_document
from pdf_editor.ui.search_bar import SearchBar
from pdf_editor.ui.printing import print_pages
from pdf_editor.outline import read_outline,page_text
from pdf_editor.logs import get_logger,log_path

ZOOM_LEVELS=(0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.5,3.0,4.0,5.0)

# 依錯誤代碼顯示較明確的視窗標題；未列出的沿用「無法完成操作」。
ERROR_TITLES={
    "OPEN":"無法開啟文件",
    "PASSWORD":"密碼錯誤",
    "PRINT":"列印失敗",
    "WORKER":"背景工作中斷",
    "ERROR":"發生未預期的錯誤",
}
UNEXPECTED_ERRORS=("ERROR","WORKER")

def export_document(pdf,layers,target,source,overwrite):
    data=embed_workspace(pdf,layers) if layers else pdf
    return str(write_pdf(data,Path(target),overwrite,(Path(source),)))

def export_merge(sources,order,target,paths):
    return str(write_pdf(merge_pages(sources,order),Path(target),False,tuple(Path(p) for p in paths)))

def export_split(pdf,layers,groups,folder,source):
    data=flatten_overlays(pdf,layers) if layers else pdf
    documents=split_pages(data,groups)
    targets=tuple(Path(folder)/f"拆分_{i+1:03}.pdf" for i in range(len(groups)))
    return tuple(str(p) for p in publish_batch(documents,targets,(Path(source),)))


def export_extract(pdf,layers,pages,target,source):
    data=flatten_overlays(pdf,layers) if layers else pdf
    extracted=extract_pages(data,pages)
    return str(write_pdf(extracted,Path(target),False,(Path(source),)))


def export_page_images(pdf,layers,pages,folder,stem,dpi):
    data=flatten_overlays(pdf,layers) if layers else pdf
    return tuple(str(path) for path in export_pages_as_png(
        data,pages,Path(folder),stem,dpi))

def edit_page_document(pdf,layers,operation,pages,target=None):
    data=flatten_overlays(pdf,layers) if layers else pdf
    if operation=="move":
        return move_pages(data,pages,target)
    if operation=="move_to":
        return move_pages_to(data,pages,target)
    if operation=="rotate":
        return rotate_pages(data,pages,target)
    if operation=="delete":
        return delete_pages(data,pages)
    if operation=="duplicate":
        return duplicate_pages(data,pages)
    if operation=="blank":
        return insert_blank_page(data,pages[0])
    if operation=="insert":
        return insert_pages(data,target,pages[0])
    if operation=="crop":
        return crop_pages(data,pages,target)
    if operation=="page_number":
        return add_page_numbers(data,pages,target["start"],target["prefix"],
            target["suffix"],target["position"],target["font_size"],target["font_path"])
    if operation=="text_watermark":
        return add_text_watermark(data,pages,target["text"],target["font_size"],
            target["opacity"],target["angle"],target["font_path"])
    if operation=="image_watermark":
        return add_image_watermark(data,pages,target["image_path"],
            target["width_percent"],target["opacity"],target["angle"])
    if operation=="header_footer":
        return add_header_footer(data,pages,target["text"],target["position"],
            target["font_size"],target["font_path"])
    raise EditorError("PAGE_OPERATION","頁面操作無效。")


class ThumbnailList(QListWidget):
    pages_dropped=Signal(tuple,int)
    files_dropped=Signal(list)

    def dragEnterEvent(self,event):
        if event.source() is not self and Canvas.dropped_pdf_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self,event):
        if event.source() is not self and Canvas.dropped_pdf_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self,event):
        paths=Canvas.dropped_pdf_paths(event.mimeData()) if event.source() is not self else []
        if paths:
            # 從檔案總管拖入 PDF 時改為開啟文件，不影響頁面排序拖曳。
            event.acceptProposedAction()
            self.files_dropped.emit(paths)
            return
        if event.source() is not self:
            super().dropEvent(event)
            return
        pages=tuple(sorted(self.row(item) for item in self.selectedItems()))
        if not pages:
            event.ignore()
            return
        point=event.position().toPoint()
        index=self.indexAt(point)
        destination=self.count()
        if index.isValid():
            rect=self.visualRect(index)
            destination=index.row()+int(point.y()>=rect.center().y())
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.pages_dropped.emit(pages,destination)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("墨頁 PDF")
        self.resize(1320,850)
        apply_theme(QApplication.instance())
        self.setStyleSheet(STYLE)
        self.session=None
        self.page_data=None
        self.page_count=0
        self.page=0
        self.scale=1.25
        self.preview=None
        self.run=None
        self.insertion_rect=None
        self.layer_id=None
        self.annotation=None
        self.crop_pages=()
        self.search_results=()
        self.search_index=-1
        self.search_revision=None
        self.token=0
        self.busy=False
        self.render_serial=0
        self.comparison_serial=0
        self.comparison_dialog=None
        self.comparison_pdf=None
        self.comparison_base_pdf=None
        self.comparison_base_page=None
        self.comparison_revision=None
        self.comparison_token=None
        self.closed=False
        self.settings=AppSettings()
        self._scroll_after_render=None
        self._fit_after_render=None
        self.jobs=Jobs(self)
        self._thumb_queue=[]
        self._thumb_token=None
        self._thumb_running=False
        self.asset_root=Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))/"assets"
        self.assets=AssetStore(self.asset_root)
        self.header_footer_templates=HeaderFooterTemplateStore(
            self.asset_root.parent/"header-footer-templates.json")
        toolbar=QToolBar("文件工具",self)
        toolbar.setMovable(False)
        toolbar.setObjectName("mainToolbar")
        toolbar.setIconSize(QSize(20,20))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.addToolBar(toolbar)
        self.toolbar=toolbar
        self.actions={}
        for name,label,handler,key in [
            ("open","開啟 PDF",self.choose_open,"Ctrl+O"),
            ("save","另存新檔",self.save,"Ctrl+S"),
            ("undo","復原",lambda:self.history_step(False),"Ctrl+Z"),
            ("redo","重做",lambda:self.history_step(True),"Ctrl+Y"),
            ("add_text","新增文字",self.start_text_insertion,"Ctrl+T"),
            ("stamp","蓋章",self.add_stamp,None),
            ("convert_stamp","轉換既有圖章",self.convert_legacy_stamp,None),
            ("signature","手寫簽名",self.add_signature,None),
            ("collection","常用圖章",self.add_collection,None),
            ("ocr","OCR 文字辨識",self.run_ocr,None),
            ("compare","頁面比較",self.choose_comparison_pdf,None),
            ("merge","合併",self.merge,None),
            ("split","拆分",self.split,None),
            ("page_marks","頁碼／浮水印",self.show_page_decoration_dialog,None),
            ("header_footer","頁首頁尾範本",self.show_header_footer_dialog,None)]:
            action=QAction(glyph_icon(name),label,self)
            action.triggered.connect(handler)
            if key:
                action.setShortcut(QKeySequence(key))
            if name=="add_text":
                action.setCheckable(True)
            self.actions[name]=action
        # 另存新檔同時支援 Ctrl+S 與 Ctrl+Shift+S，避免習慣按 Ctrl+S 時沒有反應。
        self.actions["save"].setShortcuts([QKeySequence("Ctrl+S"),QKeySequence("Ctrl+Shift+S")])
        self.recent_menu=QMenu("最近開啟的檔案",self)
        self.recent_menu.aboutToShow.connect(self.rebuild_recent_menu)
        self.actions["open"].setMenu(self.recent_menu)
        page_menu=QMenu(self)
        for name,label,handler,key in [
            ("page_up","選取頁面上移",lambda:self.move_current_page(-1),"Alt+Up"),
            ("page_down","選取頁面下移",lambda:self.move_current_page(1),"Alt+Down"),
            ("rotate_left","選取頁面向左旋轉",lambda:self.rotate_current_page(-90),"Ctrl+Shift+Left"),
            ("rotate_right","選取頁面向右旋轉",lambda:self.rotate_current_page(90),"Ctrl+Shift+Right"),
            ("duplicate_page","複製選取頁面",self.duplicate_selected_pages,"Ctrl+D"),
            ("blank_page","新增空白頁",self.add_blank_page,None),
            ("insert_pdf","插入另一份 PDF（全部頁面）…",self.insert_pdf_pages,None),
            ("extract_pages","抽取選取頁面另存…",self.extract_selected_pages,None),
            ("export_png","匯出選取頁面為 PNG…",self.export_selected_pages_png,None),
            ("direct_crop","直接拖曳裁切框",self.toggle_direct_crop,None),
            ("crop_page","精確輸入裁切邊距…",self.show_crop_dialog,None),
            ("delete_page","刪除選取頁面",self.delete_current_page,"Ctrl+Delete")]:
            action=QAction(label,self)
            action.triggered.connect(handler)
            action.setShortcut(QKeySequence(key))
            if name=="direct_crop":
                action.setCheckable(True)
            page_menu.addAction(action)
            if name in ("rotate_right","blank_page","export_png","crop_page"):
                page_menu.addSeparator()
            self.actions[name]=action
        self.page_menu=page_menu
        markup_menu=QMenu(self)
        for name,label,handler in [
            ("highlight","螢光標記",lambda:self.apply_selected_markup("highlight")),
            ("underline","加底線",lambda:self.apply_selected_markup("underline")),
            ("text_note","文字註解",self.start_text_note),
            ("select_annotation","選取註解",self.start_annotation_selection),
            ("highlight_yellow","螢光色：黃色",lambda:self.change_highlight_color((1.0,0.84,0.18))),
            ("highlight_green","螢光色：綠色",lambda:self.change_highlight_color((0.30,0.78,0.48))),
            ("highlight_pink","螢光色：粉紅色",lambda:self.change_highlight_color((1.0,0.45,0.66))),
            ("highlight_blue","螢光色：藍色",lambda:self.change_highlight_color((0.32,0.67,1.0))),
            ("delete_annotation","刪除選取註解",self.delete_selected_annotation)]:
            action=QAction(label,self)
            action.triggered.connect(handler)
            if name in ("text_note","select_annotation"):
                action.setCheckable(True)
            markup_menu.addAction(action)
            if name in ("underline","select_annotation","highlight_blue"):
                markup_menu.addSeparator()
            self.actions[name]=action
        stamp_menu=QMenu(self)
        for name in ("stamp","collection","convert_stamp"):
            stamp_menu.addAction(self.actions[name])
        for name,label,handler,shortcuts,tip in [
            ("zoom_out","縮小",lambda:self.zoom_by(-1),("Ctrl+-",),"縮小頁面（Ctrl+- 或 Ctrl+滾輪）"),
            ("zoom_in","放大",lambda:self.zoom_by(1),("Ctrl++","Ctrl+="),"放大頁面（Ctrl++ 或 Ctrl+滾輪）"),
            ("previous_page","上一頁",lambda:self.step_page(-1),("PgUp",),"上一頁（PageUp）"),
            ("next_page","下一頁",lambda:self.step_page(1),("PgDown",),"下一頁（PageDown）"),
            ("first_page","第一頁",lambda:self.goto_page_with_scroll(0,"top"),("Ctrl+Home",),"第一頁（Ctrl+Home）"),
            ("last_page","最後一頁",lambda:self.goto_page_with_scroll(self.page_count-1,"top"),
                ("Ctrl+End",),"最後一頁（Ctrl+End）"),
            ("search","搜尋",self.focus_search,("Ctrl+F",),"搜尋全文（Ctrl+F）"),
            ("search_previous","上一筆",lambda:self.next_search_result(-1),("Shift+F3",),"上一筆結果（Shift+F3）"),
            ("search_next","下一筆",self.next_search_result,("F3",),"下一筆結果（F3）"),
            ("print","列印",self.print_document,("Ctrl+P",),"列印文件（Ctrl+P），圖章與簽名會一併印出"),
            ("copy_text","複製文字",self.copy_selected_text,("Ctrl+C",),"複製選取的文字（Ctrl+C）"),
            ("copy_page_text","複製本頁全部文字",self.copy_page_text,("Ctrl+Shift+C",),
                "複製目前頁面的全部文字（Ctrl+Shift+C）"),
            ("fit_page","適合頁面",lambda:self.zoom.setCurrentText("適合頁面"),(),"整頁顯示"),
            ("fit_width","適合寬度",lambda:self.zoom.setCurrentText("適合寬度"),(),"頁面寬度填滿畫布")]:
            action=QAction(glyph_icon(name),label,self)
            action.triggered.connect(handler)
            action.setShortcuts([QKeySequence(key) for key in shortcuts])
            action.setToolTip(tip)
            self.actions[name]=action
        # 工具列依用途分組：檔案｜復原｜編輯與圖章｜頁面｜工具。
        self.stamp_menu_button=QToolButton()
        self.stamp_menu_button.setDefaultAction(self.actions["stamp"])
        self.stamp_menu_button.setMenu(stamp_menu)
        self.stamp_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.page_menu_button=QToolButton()
        self.page_menu_button.setText("頁面操作")
        self.page_menu_button.setIcon(glyph_icon("page_menu"))
        self.page_menu_button.setMenu(page_menu)
        self.page_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.markup_menu_button=QToolButton()
        self.markup_menu_button.setText("標記註解")
        self.markup_menu_button.setIcon(glyph_icon("markup_menu"))
        self.markup_menu_button.setMenu(markup_menu)
        self.markup_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        for button in (self.stamp_menu_button,self.page_menu_button,self.markup_menu_button):
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        layout=[("open","save","print"),("undo","redo"),
            ("add_text",self.markup_menu_button,self.stamp_menu_button,"signature"),
            (self.page_menu_button,"merge","split","page_marks","header_footer"),
            ("ocr","compare","search")]
        for group_index,group in enumerate(layout):
            if group_index:
                toolbar.addSeparator()
            for item in group:
                if isinstance(item,str):
                    toolbar.addAction(self.actions[item])
                else:
                    toolbar.addWidget(item)
        toolbar.widgetForAction(self.actions["open"]).setPopupMode(
            QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        tips={
            "open":"開啟 PDF（Ctrl+O）；點右側箭頭可選最近開啟的檔案",
            "save":"另存新檔（Ctrl+S）；原始檔案不會被覆蓋",
            "undo":"復原上一步（Ctrl+Z）",
            "redo":"重做（Ctrl+Y）",
            "add_text":"在頁面上點選位置新增文字（Ctrl+T）",
            "stamp":"匯入 PNG 圖章；點右側箭頭可使用常用圖章或轉換既有圖章",
            "collection":"從收藏中選擇常用圖章或簽名",
            "convert_stamp":"將文件中原有的圖章影像轉為可移動、縮放的圖層",
            "signature":"手寫簽名後放到頁面上",
            "ocr":"辨識縮圖列中選取頁面的文字，讓掃描檔可搜尋與複製",
            "compare":"與另一份 PDF 逐頁比較差異",
            "merge":"合併多份 PDF",
            "split":"將文件拆分為多個檔案",
            "page_marks":"加入頁碼或文字／圖片浮水印",
            "header_footer":"套用頁首頁尾範本",
            "print":"列印（Ctrl+P），圖章與簽名會一併印出",
        }
        for name,tip in tips.items():
            self.actions[name].setToolTip(tip)
        self.page_menu_button.setToolTip("移動、旋轉、複製、裁切、抽取或刪除選取頁面")
        self.markup_menu_button.setToolTip("螢光標記、底線與文字註解")
        self.page_spin=QSpinBox()
        self.page_spin.setRange(1,1)
        self.page_spin.setToolTip("輸入頁碼後按 Enter 跳頁")
        self.page_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.page_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_spin.setMinimumWidth(56)
        self.page_spin.valueChanged.connect(lambda n:self.goto_page(n-1))
        self.page_total=QLabel("/ 0")
        self.zoom=QComboBox()
        self.zoom.addItems(["適合頁面","適合寬度"]+
            [f"{round(level*100)}%" for level in ZOOM_LEVELS])
        self.zoom.setCurrentText("125%")
        self.zoom.setToolTip("縮放比例")
        self.zoom.currentTextChanged.connect(self.change_zoom)
        self.build_status_bar()
        splitter=QSplitter()
        self.thumbs=ThumbnailList()
        self.thumbs.setIconSize(QSize(110,140))
        self.thumbs.setViewMode(QListView.ViewMode.IconMode)
        self.thumbs.setFlow(QListView.Flow.TopToBottom)
        self.thumbs.setWrapping(False)
        self.thumbs.setWordWrap(True)
        self.thumbs.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.thumbs.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.thumbs.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.thumbs.setGridSize(QSize(145,165))
        self.thumbs.setMinimumWidth(160)
        self.thumbs.setMaximumWidth(220)
        self.thumbs.currentRowChanged.connect(self.goto_page)
        self.thumbs.itemSelectionChanged.connect(self.thumbnail_selection_changed)
        self.thumbs.pages_dropped.connect(self.move_selected_pages_to)
        self.thumbs.files_dropped.connect(self.open_dropped_files)
        self.thumbs.verticalScrollBar().valueChanged.connect(lambda _value:self.reprioritize_thumbnails())
        self.thumbs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(self.show_thumbnail_menu)
        self.outline_tree=QTreeWidget()
        self.outline_tree.setHeaderHidden(True)
        self.outline_tree.setObjectName("outlineTree")
        self.outline_tree.itemActivated.connect(self.open_outline_item)
        self.outline_tree.itemClicked.connect(self.open_outline_item)
        self.outline_empty=QLabel("此文件沒有書籤。")
        self.outline_empty.setObjectName("hint")
        self.outline_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outline_page=QWidget()
        outline_layout=QVBoxLayout(outline_page)
        outline_layout.setContentsMargins(0,0,0,0)
        outline_layout.addWidget(self.outline_tree)
        outline_layout.addWidget(self.outline_empty)
        self.side_tabs=QTabWidget()
        self.side_tabs.setObjectName("sideTabs")
        self.side_tabs.addTab(self.thumbs,"頁面")
        self.side_tabs.addTab(outline_page,"書籤")
        self.side_tabs.setMinimumWidth(170)
        self.side_tabs.setMaximumWidth(260)
        splitter.addWidget(self.side_tabs)
        self.canvas=Canvas()
        self.canvas.run_selected.connect(self.select_run)
        self.canvas.run_moved.connect(self.move_run)
        self.canvas.run_delete_requested.connect(self.delete_run)
        self.canvas.text_insertion_requested.connect(self.begin_text_insertion)
        self.canvas.text_insertion_cancelled.connect(self.cancel_text_insertion)
        self.canvas.inline_text_committed.connect(self.commit_inline_text)
        self.canvas.inline_text_cancelled.connect(self.cancel_inline_text)
        self.canvas.note_insertion_requested.connect(self.begin_text_note)
        self.canvas.note_insertion_cancelled.connect(self.cancel_text_note)
        self.canvas.annotation_selected.connect(self.select_annotation)
        self.canvas.annotation_delete_requested.connect(self.delete_selected_annotation)
        self.canvas.layer_selected.connect(self.select_layer)
        self.canvas.layer_moved.connect(self.move_layer)
        self.canvas.crop_requested.connect(self.apply_direct_crop)
        self.canvas.crop_cancelled.connect(self.cancel_direct_crop)
        self.canvas.zoom_step_requested.connect(self.zoom_by)
        self.canvas.page_step_requested.connect(self.step_page)
        self.canvas.files_dropped.connect(self.open_dropped_files)
        self.canvas.pointer_moved.connect(self.show_pointer_position)
        self.canvas.context_menu_requested.connect(self.show_canvas_menu)
        self.search_bar=SearchBar(self.canvas,self.actions["search_previous"],self.actions["search_next"])
        self.search_input=self.search_bar.input
        self.search_count=self.search_bar.count
        self.search_input.returnPressed.connect(lambda:self.perform_search())
        self.search_bar.closed.connect(self.close_search_bar)
        splitter.addWidget(self.canvas)
        self.panels=QStackedWidget()
        self.info_panel=DocumentInfoPanel()
        self.panels.addWidget(self.info_panel)
        self.text_panel=TextPanel()
        self.text_panel.preview_requested.connect(self.preview_from_panel)
        self.text_panel.apply_requested.connect(self.apply_preview)
        self.text_panel.cancel_requested.connect(self.cancel_preview)
        self.text_panel.format_requested.connect(self.apply_text_format)
        self.overlay_panel=OverlayPanel()
        self.overlay_panel.update_requested.connect(self.update_layer)
        self.overlay_panel.delete_requested.connect(self.delete_layer)
        self.panels.addWidget(self.text_panel)
        self.panels.addWidget(self.overlay_panel)
        self.panels.setMinimumWidth(280)
        self.panels.setMaximumWidth(350)
        panel_scroll=QScrollArea()
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setWidget(self.panels)
        panel_scroll.setMinimumWidth(295)
        panel_scroll.setMaximumWidth(370)
        splitter.addWidget(panel_scroll)
        splitter.setSizes([170,820,310])
        self.setCentralWidget(splitter)
        self.splitter=splitter
        # 快捷鍵掛在主視窗上，收進選單的動作也能以鍵盤觸發。
        for action in self.actions.values():
            if action.shortcuts():
                self.addAction(action)
        self.setAcceptDrops(True)
        self.restore_window_state()
        self.statusBar().showMessage("開啟 PDF 開始編輯；也可以直接把 PDF 拖進視窗。文件全程在本機處理。")
        self.refresh_actions()

    def build_status_bar(self):
        """狀態列右側：游標座標、頁面尺寸、儲存狀態、翻頁與縮放。"""
        bar=self.statusBar()
        self.pointer_label=QLabel("")
        self.pointer_label.setObjectName("statusField")
        self.pointer_label.setMinimumWidth(150)
        self.pointer_label.setToolTip("游標所在的 PDF 座標（單位：點，72 點 = 1 英吋）")
        self.page_size_label=QLabel("")
        self.page_size_label.setObjectName("statusField")
        self.dirty_label=QLabel("")
        self.dirty_label.setObjectName("dirtyIndicator")
        for widget in (self.pointer_label,self.page_size_label,self.dirty_label):
            bar.addPermanentWidget(widget)
        navigation=QWidget()
        navigation.setObjectName("statusGroup")
        row=QHBoxLayout(navigation)
        row.setContentsMargins(6,0,6,0)
        row.setSpacing(2)
        self.status_buttons={}
        def button(name):
            widget=QToolButton()
            widget.setDefaultAction(self.actions[name])
            widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            widget.setAutoRaise(True)
            self.status_buttons[name]=widget
            return widget
        row.addWidget(button("first_page"))
        row.addWidget(button("previous_page"))
        row.addWidget(self.page_spin)
        row.addWidget(self.page_total)
        row.addWidget(button("next_page"))
        row.addWidget(button("last_page"))
        row.addSpacing(10)
        row.addWidget(button("zoom_out"))
        row.addWidget(self.zoom)
        row.addWidget(button("zoom_in"))
        bar.addPermanentWidget(navigation)

    def show_pointer_position(self,point):
        if point is None or not self.session:
            self.pointer_label.setText("")
            return
        self.pointer_label.setText(f"X {point[0]:.1f}  Y {point[1]:.1f} pt")

    def update_status_fields(self):
        """同步狀態列的頁數、頁面尺寸與未儲存標示。"""
        active=self.session is not None
        self.page_total.setText(f"/ {self.page_count}" if active else "/ 0")
        if active and self.page_data:
            width,height=self.page_data["bounds"][2:]
            self.page_size_label.setText(f"{width/72*25.4:.0f} × {height/72*25.4:.0f} mm")
        elif not active:
            self.page_size_label.setText("")
        if active and self.session.dirty:
            self.dirty_label.setText("● 未儲存")
            self.dirty_label.setToolTip("有尚未另存的變更（Ctrl+S 另存新檔）")
        else:
            self.dirty_label.setText("")
            self.dirty_label.setToolTip("")

    def update_side_panel(self):
        """沒有選取文字或圖章時，右側改顯示文件資訊。"""
        current=self.panels.currentWidget()
        editing_text=self.run is not None or self.insertion_rect is not None or self.preview is not None
        layer_selected=self.session is not None and any(
            layer.id==self.layer_id for layer in self.session.overlays)
        if current is self.text_panel and editing_text:
            return
        if current is self.overlay_panel and layer_selected:
            return
        if current is not self.info_panel:
            self.panels.setCurrentWidget(self.info_panel)
        self.refresh_document_info()

    def refresh_document_info(self):
        if self.panels.currentWidget() is not self.info_panel:
            return
        if not self.session:
            self.info_panel.clear()
            return
        key=(self.token,self.session.revision,self.page)
        if getattr(self,"_info_key",None)==key:
            return
        self._info_key=key
        try:
            self.info_panel.set_info(describe_document(self.session.pdf,self.page,
                self.session.source,self.session.access,self.session.password_used))
        except Exception:
            self.info_panel.clear()

    def restore_window_state(self):
        """還原上次的視窗大小、面板寬度與縮放模式。"""
        geometry=self.settings.bytes_value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        splitter=self.settings.bytes_value("window/splitter")
        if splitter is not None:
            self.splitter.restoreState(splitter)
        zoom=str(self.settings.value("view/zoom","") or "")
        if zoom and self.zoom.findText(zoom)>=0:
            self.zoom.blockSignals(True)
            self.zoom.setCurrentText(zoom)
            self.zoom.blockSignals(False)
            if zoom.endswith("%"):
                self.scale=float(zoom.rstrip("%"))/100

    def save_window_state(self):
        self.settings.set_value("window/geometry",self.saveGeometry())
        self.settings.set_value("window/splitter",self.splitter.saveState())
        self.settings.set_value("view/zoom",self.zoom.currentText())
        self.settings.sync()

    def rebuild_recent_menu(self):
        self.recent_menu.clear()
        files=self.settings.recent_files()
        if not files:
            empty=self.recent_menu.addAction("（沒有最近開啟的檔案）")
            empty.setEnabled(False)
            return
        for index,path in enumerate(files):
            label=f"&{index+1}  {Path(path).name}" if index<9 else Path(path).name
            action=self.recent_menu.addAction(label)
            action.setToolTip(path)
            action.setStatusTip(path)
            action.triggered.connect(lambda _checked=False,target=path:self.open_path(target))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("清除清單",self.settings.clear_recent_files)

    def open_path(self,path):
        """由最近檔案、拖放或命令列開啟指定 PDF。"""
        if self.busy:
            return False
        path=Path(path)
        if not path.is_file():
            self.settings.remove_recent_file(path)
            QMessageBox.warning(self,"找不到檔案",f"檔案已不存在或無法存取：\n{path}")
            return False
        if not self.confirm_leave():
            return False
        try:
            self.open_document(path)
        except Exception as exc:
            self.error((getattr(exc,"code","OPEN"),str(exc),()))
            return False
        return True

    def open_dropped_files(self,paths):
        if not paths:
            return
        if len(paths)>1:
            self.statusBar().showMessage("一次只能開啟一份 PDF，已開啟第一個檔案。")
        self.open_path(paths[0])

    def dragEnterEvent(self,event):
        if Canvas.dropped_pdf_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self,event):
        paths=Canvas.dropped_pdf_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.open_dropped_files(paths)
            return
        super().dropEvent(event)

    def step_page(self,offset):
        if not self.session or self.busy:
            return
        target=self.page+offset
        if not 0<=target<self.page_count:
            return
        self.goto_page_with_scroll(target,"top" if offset>0 else "bottom")

    def goto_page_with_scroll(self,page,position):
        if not self.session or not 0<=page<self.page_count or page==self.page:
            return
        self._scroll_after_render=position
        self.goto_page(page)

    def refresh_actions(self):
        active=self.session is not None
        edit=active and self.session.access.can_edit and not self.busy
        for name in ("save","add_text","stamp","convert_stamp","signature","collection",
                "page_marks","header_footer"):
            self.actions[name].setEnabled(edit)
        self.actions["undo"].setEnabled(edit and self.session.can_undo)
        self.actions["redo"].setEnabled(edit and self.session.can_redo)
        self.actions["split"].setEnabled(active and self.session.access.can_reorganize and not self.busy)
        self.actions["open"].setEnabled(not self.busy)
        self.actions["merge"].setEnabled(not self.busy)
        manage=active and self.session.access.can_reorganize and not self.busy
        selected=set(self.selected_page_indices()) if active else set()
        ocr_selected=self.selected_thumbnail_indices() if active else ()
        can_up=any(page>0 and page-1 not in selected for page in selected)
        can_down=any(page<self.page_count-1 and page+1 not in selected for page in selected)
        self.actions["page_up"].setEnabled(manage and can_up)
        self.actions["page_down"].setEnabled(manage and can_down)
        self.actions["rotate_left"].setEnabled(manage and bool(selected))
        self.actions["rotate_right"].setEnabled(manage and bool(selected))
        self.actions["duplicate_page"].setEnabled(manage and bool(selected))
        self.actions["blank_page"].setEnabled(manage and bool(selected))
        self.actions["insert_pdf"].setEnabled(manage and bool(selected))
        self.actions["extract_pages"].setEnabled(manage and bool(selected))
        self.actions["export_png"].setEnabled(manage and bool(selected))
        self.actions["crop_page"].setEnabled(edit and bool(selected))
        self.actions["direct_crop"].setEnabled(edit and bool(selected))
        self.actions["delete_page"].setEnabled(manage and bool(selected) and len(selected)<self.page_count)
        self.actions["ocr"].setEnabled(edit and bool(ocr_selected))
        self.actions["compare"].setEnabled(active and not self.busy)
        self.page_menu_button.setEnabled(manage or edit)
        for name in ("highlight","underline","text_note","select_annotation",
                "highlight_yellow","highlight_green","highlight_pink",
                "highlight_blue","delete_annotation"):
            self.actions[name].setEnabled(edit)
        self.markup_menu_button.setEnabled(edit)
        self.actions["search"].setEnabled(active and not self.busy)
        valid_search=active and not self.busy and bool(self.search_results) and self.search_revision==self.session.revision
        self.actions["search_previous"].setEnabled(valid_search)
        self.actions["search_next"].setEnabled(valid_search)
        self.actions["zoom_out"].setEnabled(active and self.scale>ZOOM_LEVELS[0])
        self.actions["zoom_in"].setEnabled(active and self.scale<ZOOM_LEVELS[-1])
        self.search_input.setEnabled(active and not self.busy)
        self.thumbs.setEnabled(active and not self.busy)
        self.text_panel.apply_button.setEnabled(edit and self.preview is not None)
        self.canvas.setEnabled(not self.busy)
        self.overlay_panel.setEnabled(edit)
        self.text_panel.setEnabled(edit and ((self.run is not None and self.run.editable) or
            self.insertion_rect is not None))
        self.actions["previous_page"].setEnabled(active and not self.busy and self.page>0)
        self.actions["first_page"].setEnabled(active and not self.busy and self.page>0)
        self.actions["next_page"].setEnabled(active and not self.busy and self.page<self.page_count-1)
        self.actions["last_page"].setEnabled(active and not self.busy and self.page<self.page_count-1)
        self.page_spin.setEnabled(active and not self.busy)
        self.zoom.setEnabled(active)
        self.actions["print"].setEnabled(active and not self.busy)
        self.actions["copy_page_text"].setEnabled(active)
        self.actions["copy_text"].setEnabled(active)
        self.actions["fit_page"].setEnabled(active)
        self.actions["fit_width"].setEnabled(active)
        self.update_status_fields()
        self.update_side_panel()

    def error(self,error):
        self.busy=False
        self.refresh_actions()
        code,message,completed=error
        get_logger().warning("操作失敗：%s %s 已完成=%s",code,message,list(completed))
        if completed:
            message+="\n已完成：\n"+"\n".join(completed)
        if code in UNEXPECTED_ERRORS:
            message+=f"\n\n若問題持續發生，請提供紀錄檔：\n{log_path()}"
        QMessageBox.warning(self,ERROR_TITLES.get(code,"無法完成操作"),message)
        self.statusBar().showMessage(message.split("\n")[0])

    def confirm_leave(self):
        if self.busy:
            return False
        if self.session and self.session.dirty:
            result=QMessageBox.question(self,"尚未儲存","是否儲存目前變更？",
                QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel)
            if result==QMessageBox.StandardButton.Save:
                self.save()
                return False
            return result==QMessageBox.StandardButton.Discard
        return True

    def choose_open(self):
        if not self.confirm_leave():
            return
        name,_=QFileDialog.getOpenFileName(self,"開啟 PDF",self.settings.last_directory(),"PDF (*.pdf)")
        if name:
            try:
                self.open_document(Path(name))
            except Exception as exc:
                self.error((getattr(exc,"code","OPEN"),str(exc),()))

    def open_document(self,path):
        try:
            session=DocumentSession.open(Path(path))
        except EditorError as exc:
            if exc.code!="PASSWORD":
                raise
            password,ok=QInputDialog.getText(self,"PDF 密碼","密碼：",QLineEdit.EchoMode.Password)
            if not ok:
                return
            session=DocumentSession.open(Path(path),password)
        self.close_comparison()
        if self.session:
            self.session.close()
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.cancel_direct_crop()
        self.clear_search_results(True)
        self.session=session
        self.token+=1
        self.preview=None
        self.run=None
        self.annotation=None
        self.insertion_rect=None
        self.page_data=None
        self.page=0
        self.text_panel.setEnabled(False)
        with pymupdf.open(stream=session.pdf) as doc:
            self.page_count=len(doc)
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1,self.page_count)
        self.page_spin.setValue(1)
        self.page_spin.blockSignals(False)
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        for i in range(self.page_count):
            self.thumbs.addItem(QListWidgetItem(f"第 {i+1} 頁"))
        self.thumbs.setCurrentRow(0)
        self.thumbs.blockSignals(False)
        self.setWindowTitle(f"{session.source.name} — 墨頁 PDF")
        self.settings.add_recent_file(session.source)
        self.settings.set_last_directory(session.source)
        self._scroll_after_render="top"
        if self.zoom.currentText() in ("適合頁面","適合寬度"):
            self._fit_after_render="page" if self.zoom.currentText()=="適合頁面" else "width"
        self.refresh_actions()
        self.request_render()
        self.queue_thumbnails()
        self.refresh_outline()
        if session.open_notice:
            QMessageBox.warning(self, "圖章工作層", session.open_notice)
            self.statusBar().showMessage(session.open_notice)

    def choose_comparison_pdf(self):
        if not self.session or self.busy:
            return
        name,_=QFileDialog.getOpenFileName(self,"選擇比較 PDF",self.settings.last_directory(),"PDF (*.pdf)")
        if not name:
            return
        try:
            raw=Path(name).read_bytes()
            try:
                comparison_pdf,_=unlock_pdf(raw)
            except EditorError as exc:
                if exc.code!="PASSWORD":
                    raise
                password,ok=QInputDialog.getText(self,"PDF 密碼",Path(name).name,
                    QLineEdit.EchoMode.Password)
                if not ok:
                    return
                comparison_pdf,_=unlock_pdf(raw,password)
            self.open_comparison(comparison_pdf)
        except Exception as exc:
            self.error((getattr(exc,"code","OPEN"),str(exc),()))

    def open_comparison(self,comparison_pdf):
        if not self.session or self.busy:
            return
        base_pdf=flatten_overlays(self.session.pdf,self.session.overlays)
        with pymupdf.open(stream=comparison_pdf,filetype="pdf") as document:
            page_count=document.page_count
        self.close_comparison()
        dialog=ComparisonDialog(page_count,self.page,self)
        self.comparison_dialog=dialog
        self.comparison_pdf=comparison_pdf
        self.comparison_base_pdf=base_pdf
        self.comparison_base_page=self.page
        self.comparison_revision=self.session.revision
        self.comparison_token=self.token
        dialog.page_requested.connect(self.request_comparison_page)
        dialog.finished.connect(
            lambda _result,current=dialog:self.clear_comparison(current))
        dialog.destroyed.connect(
            lambda _object=None,current=dialog:self.clear_comparison(current))
        dialog.show()
        self.request_comparison_page(0)

    def request_comparison_page(self,page):
        dialog=self.comparison_dialog
        if dialog is None:
            return
        if not self.comparison_context_is_current(dialog):
            self.close_comparison()
            return
        if not 0<=page<dialog.comparison_page_count:
            return
        self.comparison_serial+=1
        serial=self.comparison_serial
        token=self.token
        revision=self.session.revision
        dialog.set_busy(True)

        def current_request():
            if (dialog is not self.comparison_dialog
                    or serial!=self.comparison_serial):
                return False
            if (not self.comparison_context_is_current(dialog)
                    or token!=self.token or revision!=self.session.revision):
                self.close_comparison()
                return False
            return True

        def done(result):
            if current_request():
                dialog.set_result(result)

        def failed(error):
            if current_request():
                self.show_comparison_error(dialog,error)

        self.jobs.submit(compare_pages,(self.comparison_base_pdf,self.comparison_base_page,
            self.comparison_pdf,page),done,failed)

    def show_comparison_error(self,dialog,error):
        _code,message,completed=error
        if completed:
            message+="\n已完成：\n"+"\n".join(completed)
        dialog.set_busy(False)
        dialog.summary.setText("比較失敗："+message.split("\n")[0])
        QMessageBox.warning(self,"無法比較頁面",message)
        self.statusBar().showMessage(message.split("\n")[0])

    def comparison_context_is_current(self,dialog):
        return (not self.closed and self.session is not None
            and dialog is self.comparison_dialog
            and self.comparison_pdf is not None
            and self.comparison_base_pdf is not None
            and self.comparison_base_page is not None
            and self.comparison_revision==self.session.revision
            and self.comparison_token==self.token)

    def clear_comparison(self,dialog):
        if dialog is not self.comparison_dialog:
            return
        self.comparison_serial+=1
        self.comparison_dialog=None
        self.comparison_pdf=None
        self.comparison_base_pdf=None
        self.comparison_base_page=None
        self.comparison_revision=None
        self.comparison_token=None

    def close_comparison(self):
        dialog=self.comparison_dialog
        if dialog is None:
            return
        self.clear_comparison(dialog)
        dialog.close()

    def queue_thumbnails(self,pages=None):
        """排入需要重畫的縮圖；pages 為 None 代表全部頁面。

        尚未完成的頁面會保留並與新的頁面合併，畫面上看得到的縮圖優先處理。
        """
        if self.closed or not self.session:
            return
        targets=range(self.page_count) if pages is None else pages
        if self._thumb_token!=self.token:
            self._thumb_queue=[]
            self._thumb_token=self.token
        merged=set(self._thumb_queue)|{page for page in targets if 0<=page<self.page_count}
        self._thumb_queue=self.prioritized_thumbnails(merged)
        self._pump_thumbnails()

    def visible_thumbnail_rows(self):
        viewport=self.thumbs.viewport().rect()
        rows=[self.thumbs.row(self.thumbs.itemAt(point)) for point in
            (viewport.topLeft(),viewport.center(),viewport.bottomLeft())
            if self.thumbs.itemAt(point) is not None]
        if not rows:
            return range(0)
        # 上下各多留一列，捲動時不會馬上看到空白縮圖。
        return range(max(0,min(rows)-1),min(self.page_count,max(rows)+2))

    def prioritized_thumbnails(self,pages):
        visible=set(self.visible_thumbnail_rows())
        return sorted((page for page in pages if 0<=page<self.page_count),
            key=lambda page:(page not in visible,abs(page-self.page),page))

    def reprioritize_thumbnails(self):
        if self._thumb_queue:
            self._thumb_queue=self.prioritized_thumbnails(self._thumb_queue)

    def _pump_thumbnails(self):
        if self._thumb_running or not self._thumb_queue or self.closed or not self.session:
            return
        index=self._thumb_queue.pop(0)
        token,revision=self.token,self.session.revision
        self._thumb_running=True
        def current():
            return (not self.closed and self.session is not None and token==self.token
                and revision==self.session.revision and index<self.page_count)
        def finish():
            self._thumb_running=False
            self._pump_thumbnails()
        def done(png):
            if current():
                pix=QPixmap()
                pix.loadFromData(png)
                item=self.thumbs.item(index)
                if item:
                    item.setIcon(thumbnail_icon(pix))
                    item.setToolTip("")
            elif (not self.closed and self.session is not None and token==self.token
                    and index<self.page_count):
                # 產生期間文件已變更，結果過期，重新排入。
                self._thumb_queue=self.prioritized_thumbnails(set(self._thumb_queue)|{index})
            finish()
        def failed(error):
            if current():
                item=self.thumbs.item(index)
                if item:
                    item.setIcon(glyph_icon("thumbnail_error",48))
                    item.setToolTip("縮圖產生失敗："+error[1])
                get_logger().warning("第 %s 頁縮圖產生失敗：%s",index+1,error[1])
                self.statusBar().showMessage(f"第 {index+1} 頁縮圖產生失敗，頁面內容仍可正常檢視。")
            finish()
        self.jobs.submit(thumbnail,(self.session.pdf,index),done,failed)

    def goto_page(self,page):
        if not self.session or not 0<=page<self.page_count or page==self.page:
            return
        self.page=page
        if self.thumbs.currentRow()!=page:
            self.thumbs.blockSignals(True)
            self.thumbs.setCurrentRow(page)
            self.thumbs.blockSignals(False)
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.cancel_direct_crop()
        self.run=None
        self.annotation=None
        self.insertion_rect=None
        self.text_panel.setEnabled(False)
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(page+1)
        self.page_spin.blockSignals(False)
        self.refresh_actions()
        self.request_render()

    def refresh_outline(self):
        self.outline_tree.clear()
        entries=[]
        if self.session:
            try:
                entries=read_outline(self.session.pdf)
            except Exception:
                entries=[]
        def add(parent,entry):
            item=QTreeWidgetItem([entry.title])
            item.setData(0,Qt.ItemDataRole.UserRole,entry.page)
            if entry.page>=0:
                item.setToolTip(0,f"{entry.title}（第 {entry.page+1} 頁）")
            else:
                item.setDisabled(True)
            (parent.addChild(item) if parent is not None else self.outline_tree.addTopLevelItem(item))
            for child in entry.children:
                add(item,child)
        for entry in entries:
            add(None,entry)
        self.outline_tree.expandToDepth(0)
        self.outline_tree.setVisible(bool(entries))
        self.outline_empty.setVisible(not entries)

    def open_outline_item(self,item,_column=0):
        page=item.data(0,Qt.ItemDataRole.UserRole)
        if isinstance(page,int) and page>=0:
            self.goto_page_with_scroll(page,"top")

    def show_thumbnail_menu(self,position):
        if not self.session:
            return
        item=self.thumbs.itemAt(position)
        if item is not None and not item.isSelected():
            self.thumbs.clearSelection()
            item.setSelected(True)
            self.thumbs.setCurrentItem(item)
        self.page_menu.exec(self.thumbs.viewport().mapToGlobal(position))

    def build_canvas_menu(self,point,run):
        menu=QMenu(self)
        if run is not None and run.text.strip():
            preview=run.text.strip()
            preview=preview if len(preview)<=16 else preview[:16]+"…"
            action=menu.addAction(f"複製「{preview}」")
            action.triggered.connect(lambda _checked=False,text=run.text:self.copy_to_clipboard(text))
        menu.addAction(self.actions["copy_page_text"])
        edit=self.session is not None and self.session.access.can_edit and not self.busy and not self.preview
        if point is not None and edit:
            menu.addSeparator()
            add_text=menu.addAction(glyph_icon("add_text"),"在此新增文字")
            add_text.triggered.connect(lambda _checked=False,position=point:self.begin_text_insertion(position))
            note=menu.addAction("在此新增文字註解")
            note.triggered.connect(lambda _checked=False,position=point:self.begin_text_note(position))
        menu.addSeparator()
        pages=menu.addMenu("頁面操作")
        pages.addActions(self.page_menu.actions())
        pages.setEnabled(self.page_menu_button.isEnabled())
        menu.addSeparator()
        for name in ("zoom_in","zoom_out","fit_page","fit_width"):
            menu.addAction(self.actions[name])
        menu.addSeparator()
        menu.addAction(self.actions["print"])
        return menu

    def show_canvas_menu(self,global_position,point,run):
        if not self.session:
            return
        self.build_canvas_menu(point,run).exec(global_position)

    def copy_to_clipboard(self,text):
        QApplication.clipboard().setText(text)
        count=len(text)
        self.statusBar().showMessage(f"已複製 {count} 個字元到剪貼簿。")

    def copy_selected_text(self):
        if not self.session:
            return
        run=self.canvas.selected_run or self.run
        if run is None or not run.text:
            self.statusBar().showMessage("請先點選要複製的文字，或按右鍵選擇「複製本頁全部文字」。")
            return
        self.copy_to_clipboard(run.text)

    def copy_page_text(self):
        if not self.session:
            return
        text=page_text(self.session.pdf,self.page)
        if not text:
            self.statusBar().showMessage("本頁沒有可複製的文字；掃描頁面請先執行 OCR 文字辨識。")
            return
        self.copy_to_clipboard(text)

    def print_document(self):
        if not self.session or self.busy:
            return
        from PySide6.QtPrintSupport import QPrinter,QPrintDialog
        printer=QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(self.session.source.stem)
        dialog=QPrintDialog(printer,self)
        dialog.setWindowTitle("列印")
        dialog.setMinMax(1,self.page_count)
        dialog.setOption(QPrintDialog.PrintDialogOption.PrintPageRange,True)
        dialog.setOption(QPrintDialog.PrintDialogOption.PrintCurrentPage,True)
        if dialog.exec()!=QPrintDialog.DialogCode.Accepted:
            return
        pages=self.print_page_indices(printer)
        self.print_to(printer,pages)

    def print_page_indices(self,printer):
        from PySide6.QtPrintSupport import QPrinter
        mode=printer.printRange()
        if mode==QPrinter.PrintRange.CurrentPage:
            return [self.page]
        if mode==QPrinter.PrintRange.PageRange:
            first=max(1,printer.fromPage())
            last=min(self.page_count,printer.toPage() or self.page_count)
            return list(range(first-1,last))
        return list(range(self.page_count))

    def print_to(self,printer,pages):
        """合併工作層後逐頁列印，並顯示可取消的進度。"""
        try:
            pdf=flatten_overlays(self.session.pdf,self.session.overlays) if self.session.overlays else self.session.pdf
        except EditorError as exc:
            self.error((exc.code,str(exc),()))
            return 0
        progress=QProgressDialog("正在準備列印…","取消",0,len(pages),self)
        progress.setWindowTitle("列印")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(400)
        def step(index,total):
            progress.setValue(index)
            progress.setLabelText(f"正在列印第 {index+1} / {total} 頁…")
            QApplication.processEvents()
            return not progress.wasCanceled()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            printed=print_pages(printer,pdf,pages,step)
        except Exception as exc:
            self.error(("PRINT",f"列印失敗：{exc}",()))
            return 0
        finally:
            QApplication.restoreOverrideCursor()
            progress.setValue(len(pages))
            progress.close()
        self.statusBar().showMessage(f"已送出 {printed} 頁到印表機。" if printed==len(pages)
            else f"列印已取消，已送出 {printed} 頁。")
        return printed

    def sync_page_navigation(self,selected=None,selected_pages=None):
        with pymupdf.open(stream=self.session.pdf,filetype="pdf") as doc:
            self.page_count=doc.page_count
        self.page=max(0,min(self.page if selected is None else selected,self.page_count-1))
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1,self.page_count)
        self.page_spin.setValue(self.page+1)
        self.page_spin.blockSignals(False)
        self.thumbs.blockSignals(True)
        if self.thumbs.count()!=self.page_count:
            self.thumbs.clear()
            for index in range(self.page_count):
                self.thumbs.addItem(QListWidgetItem(f"第 {index+1} 頁"))
        self.thumbs.setCurrentRow(self.page)
        if selected_pages:
            self.thumbs.clearSelection()
            for page in selected_pages:
                if 0<=page<self.page_count:
                    self.thumbs.item(page).setSelected(True)
        self.thumbs.blockSignals(False)
        self.thumbnail_selection_changed()
        self.page_data=None
        self.refresh_actions()
        self.request_render()
        self.queue_thumbnails()
        self.refresh_outline()

    def submit_page_operation(self,operation,pages,target,status,selected,selected_pages=None):
        content_operations={"crop","page_number","text_watermark","image_watermark",
            "header_footer"}
        allowed=(self.session.access.can_edit if self.session and operation in content_operations
            else self.session.access.can_reorganize if self.session else False)
        if not self.session or self.busy or not allowed:
            return
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.cancel_direct_crop()
        self.preview=None
        self.run=None
        self.annotation=None
        self.insertion_rect=None
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(status)
        def done(pdf):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.session.apply_state(pdf,())
            self.text_panel.setEnabled(False)
            self.sync_page_navigation(selected,selected_pages)
        self.jobs.submit(edit_page_document,
            (self.session.pdf,self.session.overlays,operation,pages,target),done,self.error)

    def selected_thumbnail_indices(self):
        return tuple(sorted(self.thumbs.row(item) for item in self.thumbs.selectedItems()))

    def selected_page_indices(self):
        selected=self.selected_thumbnail_indices()
        if selected:
            return selected
        return (self.page,) if self.session and 0<=self.page<self.page_count else ()

    def run_ocr(self):
        pages=self.selected_thumbnail_indices()
        if not pages:
            return
        self.apply_ocr_to_pages(pages)

    def apply_ocr_to_pages(self,pages):
        pages=tuple(pages)
        if (not self.session or self.busy or not self.session.access.can_edit or not pages):
            return
        try:
            pdf=flatten_overlays(self.session.pdf,self.session.overlays) if self.session.overlays else self.session.pdf
            tessdata=validate_ocr_assets()
        except EditorError as exc:
            self.error((exc.code,str(exc),()))
            return
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(f"正在辨識 {len(pages)} 頁…")
        def done(result):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            if result.processed_pages:
                self.session.apply_state(result.pdf,())
                self.clear_search_results()
                self.page_data=None
                summary=(f"OCR 完成：辨識 {len(result.processed_pages)} 頁，跳過 "
                    f"{len(result.skipped_pages)} 頁，共 {result.word_count} 個文字區段。")
                self.request_render(summary)
                self.queue_thumbnails(result.processed_pages)
            else:
                self.refresh_actions()
                self.statusBar().showMessage(
                    f"OCR 完成：全部 {len(result.skipped_pages)} 頁已有文字，未建立變更。")
        self.jobs.submit(ocr_pages,(pdf,pages,tessdata),done,self.error)

    def thumbnail_selection_changed(self):
        selected=self.selected_page_indices()
        self.thumbs.setDragEnabled(bool(selected))
        self.refresh_actions()
        if len(selected)>1 and not self.busy:
            self.statusBar().showMessage(f"已選取 {len(selected)} 頁，可整組拖曳或使用頁面操作。")

    def move_current_page(self,offset):
        pages=self.selected_page_indices()
        if not pages:
            return
        order,moved=page_order_after_move(self.page_count,pages,offset)
        if order==tuple(range(self.page_count)):
            return
        current=order.index(self.page)
        direction="上移" if offset<0 else "下移"
        self.submit_page_operation("move",pages,offset,
            f"正在{direction} {len(pages)} 頁…",current,moved)

    def move_selected_pages_to(self,pages,destination):
        if not pages:
            return
        order,moved=page_order_after_drop(self.page_count,pages,destination)
        if order==tuple(range(self.page_count)):
            return
        current=order.index(self.page)
        self.submit_page_operation("move_to",pages,destination,
            f"正在拖曳移動 {len(pages)} 頁…",current,moved)

    def rotate_current_page(self,degrees):
        if degrees not in (-90,90):
            return
        direction="逆時針" if degrees<0 else "順時針"
        pages=self.selected_page_indices()
        self.submit_page_operation("rotate",pages,degrees,
            f"正在將 {len(pages)} 頁{direction}旋轉…",self.page,pages)

    def delete_current_page(self):
        pages=self.selected_page_indices()
        if not pages or len(pages)>=self.page_count:
            return
        remaining=[page for page in range(self.page_count) if page not in set(pages)]
        if self.page in remaining:
            selected=remaining.index(self.page)
        else:
            selected=min(sum(page<self.page for page in remaining),len(remaining)-1)
        self.submit_page_operation("delete",pages,None,
            f"正在刪除 {len(pages)} 頁…",selected,(selected,))

    def duplicate_selected_pages(self):
        pages=self.selected_page_indices()
        if not pages:
            return
        first=pages[-1]+1
        copies=tuple(range(first,first+len(pages)))
        current=first+pages.index(self.page) if self.page in pages else first
        self.submit_page_operation("duplicate",pages,None,
            f"正在複製 {len(pages)} 頁…",current,copies)

    def add_blank_page(self):
        pages=self.selected_page_indices()
        if not pages:
            return
        after=pages[-1]
        inserted=after+1
        self.submit_page_operation("blank",(after,),None,
            "正在新增空白頁…",inserted,(inserted,))

    def insert_pdf_pages(self):
        if not self.session or self.busy:
            return
        name,_=QFileDialog.getOpenFileName(self,"插入另一份 PDF",self.settings.last_directory(),"PDF (*.pdf)")
        if not name:
            return
        try:
            raw=Path(name).read_bytes()
            try:
                data,access=unlock_pdf(raw)
            except EditorError as exc:
                if exc.code!="PASSWORD":
                    raise
                password,ok=QInputDialog.getText(self,"PDF 密碼",Path(name).name,
                    QLineEdit.EchoMode.Password)
                if not ok:
                    return
                data,access=unlock_pdf(raw,password)
            if not access.can_reorganize:
                raise EditorError("READ_ONLY",access.reason)
            self.apply_insert_pages(data)
        except Exception as exc:
            self.error((getattr(exc,"code","OPEN"),str(exc),()))

    def apply_insert_pages(self,source_pdf):
        pages=self.selected_page_indices()
        if not pages:
            return
        with pymupdf.open(stream=source_pdf,filetype="pdf") as source:
            count=source.page_count
        after=pages[-1]
        inserted=tuple(range(after+1,after+1+count))
        self.submit_page_operation("insert",(after,),source_pdf,
            f"正在插入 {count} 頁…",after+1,inserted)

    def extract_selected_pages(self):
        if not self.session or self.busy:
            return
        pages=self.selected_page_indices()
        if not pages:
            return
        suggested=self.session.source.with_name(self.session.source.stem+"-抽取.pdf")
        name,_=QFileDialog.getSaveFileName(self,"抽取選取頁面",str(suggested),"PDF (*.pdf)")
        if not name:
            return
        target=Path(name)
        if target.suffix.lower()!=".pdf":
            target=target.with_suffix(".pdf")
        self.extract_selected_to(target,pages)

    def extract_selected_to(self,target,pages=None):
        pages=tuple(pages) if pages is not None else self.selected_page_indices()
        if not self.session or self.busy or not pages:
            return
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(f"正在抽取 {len(pages)} 頁…")
        def done(path):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.refresh_actions()
            self.statusBar().showMessage("已抽取頁面："+path)
        self.jobs.submit(export_extract,(self.session.pdf,self.session.overlays,pages,
            str(target),str(self.session.source)),done,self.error)

    def export_selected_pages_png(self):
        if not self.session or self.busy:
            return
        folder=QFileDialog.getExistingDirectory(self,"選擇 PNG 輸出資料夾",
            str(self.session.source.parent))
        if not folder:
            return
        dpi,ok=QInputDialog.getInt(self,"PNG 解析度","DPI：",150,72,600,1)
        if ok:
            self.export_selected_png_to(Path(folder),dpi)

    def export_selected_png_to(self,folder,dpi,pages=None):
        pages=tuple(pages) if pages is not None else self.selected_page_indices()
        if not self.session or self.busy or not pages:
            return
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(f"正在將 {len(pages)} 頁輸出為 PNG…")
        def done(paths):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.refresh_actions()
            self.statusBar().showMessage(f"已輸出 {len(paths)} 張 PNG 至：{folder}")
        self.jobs.submit(export_page_images,(self.session.pdf,self.session.overlays,pages,
            str(folder),self.session.source.stem,dpi),done,self.error)

    def show_crop_dialog(self):
        pages=self.selected_page_indices()
        if not pages:
            return
        dialog=CropPagesDialog(len(pages),self)
        if dialog.exec():
            self.apply_page_crop(dialog.margins())

    def apply_page_crop(self,margins,pages=None):
        pages=tuple(pages) if pages is not None else self.selected_page_indices()
        if not pages:
            return
        self.submit_page_operation("crop",pages,margins,
            f"正在裁切 {len(pages)} 頁…",self.page,pages)

    def toggle_direct_crop(self,checked):
        if checked:
            self.start_direct_crop()
        else:
            self.canvas.cancel_crop()
            self.cancel_direct_crop()

    def start_direct_crop(self):
        if not self.page_data or not self.session or self.busy:
            self.actions["direct_crop"].setChecked(False)
            return
        pages=self.selected_page_indices()
        if not pages:
            self.actions["direct_crop"].setChecked(False)
            return
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.crop_pages=pages
        self.actions["direct_crop"].setChecked(True)
        self.canvas.start_crop(self.page_data["bounds"])
        self.statusBar().showMessage(
            f"拖曳綠色裁切框的邊線或四角；放開後套用到 {len(pages)} 個選取頁面。")

    def cancel_direct_crop(self):
        if self.canvas._crop_mode:
            self.canvas.cancel_crop()
        self.crop_pages=()
        self.actions["direct_crop"].setChecked(False)

    def apply_direct_crop(self,rect):
        pages=self.crop_pages
        if not pages or not self.page_data:
            self.cancel_direct_crop()
            return
        x0,y0,x1,y1=(float(value) for value in rect)
        bx0,by0,bx1,by1=self.page_data["bounds"]
        margins=(max(0,x0-bx0),max(0,y0-by0),max(0,bx1-x1),max(0,by1-y1))
        self.cancel_direct_crop()
        self.apply_page_crop(margins,pages)

    def show_page_decoration_dialog(self):
        pages=self.selected_page_indices()
        if not pages:
            return
        dialog=PageDecorationDialog(len(pages),self)
        if dialog.exec():
            operation,options=dialog.selection()
            self.apply_page_decoration(operation,options)

    def apply_page_decoration(self,operation,options):
        pages=self.selected_page_indices()
        if not pages or operation not in ("page_number","text_watermark","image_watermark"):
            return
        target=dict(options)
        if operation in ("page_number","text_watermark"):
            target["font_path"]=str(default_font())
        labels={"page_number":"頁碼","text_watermark":"文字浮水印",
            "image_watermark":"圖片浮水印"}
        self.submit_page_operation(operation,pages,target,
            f"正在加入{labels[operation]}至 {len(pages)} 頁…",self.page,pages)

    def show_header_footer_dialog(self):
        pages=self.selected_page_indices()
        if not pages:
            return
        dialog=HeaderFooterTemplatesDialog(self.header_footer_templates,len(pages),self)
        if dialog.exec():
            self.apply_header_footer(dialog.selection())

    def apply_header_footer(self,options):
        pages=self.selected_page_indices()
        if not pages:
            return
        target=dict(options)
        target["font_path"]=str(default_font())
        self.submit_page_operation("header_footer",pages,target,
            f"正在加入頁首頁尾至 {len(pages)} 頁…",self.page,pages)

    def change_zoom(self,text):
        if text=="適合頁面":
            self.fit_zoom("page")
            return
        if text=="適合寬度":
            self.fit_zoom("width")
            return
        view_center=self.visible_page_center()
        self.scale=float(text.rstrip("%"))/100
        self.request_render(view_center=view_center)

    def visible_page_center(self):
        if not self.page_data:
            return None
        center=self.canvas.mapToScene(self.canvas.viewport().rect().center())
        return transform_point(inverse_transform(self.canvas.matrix),center.x(),center.y())

    def zoom_by(self,direction):
        if not self.session or direction==0:
            return
        view_center=self.visible_page_center()
        if direction>0:
            target=next((level for level in ZOOM_LEVELS if level>self.scale+1e-9),
                ZOOM_LEVELS[-1])
        else:
            target=next((level for level in reversed(ZOOM_LEVELS)
                if level<self.scale-1e-9),ZOOM_LEVELS[0])
        self.scale=target
        self.zoom.blockSignals(True)
        self.zoom.setCurrentText(f"{round(target*100)}%")
        self.zoom.blockSignals(False)
        self.refresh_actions()
        self.request_render(view_center=view_center)

    def fit_zoom(self,mode):
        if not self.page_data or mode not in ("page","width"):
            return
        base_width=self.page_data["display_size"][0]/self.scale
        base_height=self.page_data["display_size"][1]/self.scale
        available_width=max(100,self.canvas.viewport().width()-32)
        available_height=max(100,self.canvas.viewport().height()-32)
        scale=available_width/base_width
        if mode=="page":
            scale=min(scale,available_height/base_height)
        self.scale=max(0.1,min(5.0,scale))
        label="適合頁面" if mode=="page" else "適合寬度"
        self.zoom.blockSignals(True)
        self.zoom.setCurrentText(label)
        self.zoom.blockSignals(False)
        self.request_render()

    def focus_search(self):
        if not self.session:
            return
        self.search_bar.open_bar()

    def close_search_bar(self):
        self.canvas.clear_search_result()
        self.canvas.setFocus()

    def clear_search_results(self,clear_query=False):
        self.search_results=()
        self.search_index=-1
        self.search_revision=None
        self.search_count.setText("0 / 0")
        self.canvas.clear_search_result()
        if clear_query:
            self.search_input.clear()

    def perform_search(self,query=None):
        if not self.session or self.busy:
            return
        text=(self.search_input.text() if query is None else str(query)).strip()
        if not text:
            self.clear_search_results()
            self.statusBar().showMessage("請輸入要搜尋的文字。")
            return
        self.search_input.setText(text)
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage("正在搜尋整份文件…")
        def done(matches):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.search_results=matches
            self.search_index=0 if matches else -1
            self.search_revision=revision
            self.refresh_actions()
            if matches:
                self.show_search_result()
            else:
                self.search_count.setText("0 / 0")
                self.canvas.clear_search_result()
                self.statusBar().showMessage(f"找不到「{text}」。")
        self.jobs.submit(find_text,(self.session.pdf,text),done,self.error)

    def next_search_result(self,step=1):
        if (not self.session or not self.search_results or
                self.search_revision!=self.session.revision):
            self.clear_search_results()
            self.refresh_actions()
            return
        self.search_index=(self.search_index+step)%len(self.search_results)
        self.show_search_result()

    def show_search_result(self):
        if not self.search_results or not 0<=self.search_index<len(self.search_results):
            return
        match=self.search_results[self.search_index]
        self.search_count.setText(f"{self.search_index+1} / {len(self.search_results)}")
        if self.page!=match.page:
            self.goto_page(match.page)
        elif self.page_data and self.page_data["page"]==match.page:
            self.canvas.show_search_result(match.rect)
        self.statusBar().showMessage(
            f"搜尋結果 {self.search_index+1} / {len(self.search_results)}，第 {match.page+1} 頁")

    def request_render(self,completion_status=None,view_center=None):
        if not self.session:
            return
        self.render_serial+=1
        serial,token=self.render_serial,self.token
        self.statusBar().showMessage("正在更新頁面…")
        data=self.preview[1] if self.preview else self.session.pdf
        layers=self.session.overlays
        def done(result):
            if self.closed or token!=self.token or serial!=self.render_serial:
                return
            self.page_data=result
            try:
                self.canvas.display(result,layers,self.layer_id)
                if self._fit_after_render:
                    mode,self._fit_after_render=self._fit_after_render,None
                    self.fit_zoom(mode)
                    return
                if self._scroll_after_render:
                    position,self._scroll_after_render=self._scroll_after_render,None
                    bar=self.canvas.verticalScrollBar()
                    bar.setValue(bar.minimum() if position=="top" else bar.maximum())
                if view_center is not None:
                    x,y=transform_point(result["matrix"],*view_center)
                    self.canvas.centerOn(QPointF(x,y))
                if (self.search_results and self.search_revision==self.session.revision and
                        self.search_results[self.search_index].page==self.page):
                    self.canvas.show_search_result(self.search_results[self.search_index].rect)
                if self.annotation is not None:
                    selected=next((item for item in result.get("annotations",())
                        if item.xref==self.annotation.xref),None)
                    self.annotation=selected
                    if selected is not None:
                        self.canvas.select_annotation(selected)
            except EditorError as exc:
                self.error((exc.code,str(exc),()))
            self.refresh_actions()
            if completion_status is not None:
                self.statusBar().showMessage(completion_status)
            else:
                status=self.session.access.reason or ("預覽中，尚未套用" if self.preview else
                    "有未儲存變更" if self.session.dirty else "可編輯")
                self.statusBar().showMessage(f"第 {self.page+1} / {self.page_count} 頁  ·  {status}")
            self.update_status_fields()
        pixel_ratio=float(self.canvas.devicePixelRatioF())
        self.jobs.submit(render_page,(data,self.page,self.scale,pixel_ratio),done,self.error)

    def select_run(self,run):
        if not self.session or not self.session.access.can_edit or self.preview:
            return
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.clear_annotation_selection()
        self.annotation=None
        self.run=run
        self.insertion_rect=None
        self.panels.setCurrentWidget(self.text_panel)
        self.text_panel.set_run(run)
        self.text_panel.font_path=str(default_font())
        self.text_panel.font_label.setText("替代字型：Noto Sans CJK TC（完整繁中文字元）")
        if not run.editable:
            message="此文字無法安全修改，但仍可加入螢光標記或底線。"
            self.text_panel.info.setText(message)
            self.refresh_actions()
            self.statusBar().showMessage(message)
            return
        original=embedded_font(self.session.pdf,self.page,run,self.session.history.root)
        if original:
            self.text_panel.font_path=str(original)
            self.text_panel.font_label.setText("原字型："+run.font_name+"；缺字時請使用內建中文字型。")
        cell=find_table_cell(self.session.pdf,self.page,run.rect)
        if cell:
            self.text_panel.set_rect(cell)
            self.text_panel.set_alignment(2)
        self.text_panel.info.setText("請直接在頁面文字框輸入；Enter 或點到別處套用，Esc 取消。")
        self.canvas.begin_inline_text(self.text_panel.rect(),run.text,run,run.size,
            self.text_panel.alignment.currentData())
        self.refresh_actions()

    def commit_inline_text(self,payload):
        if not self.session or self.busy:
            return
        run,text,rect=payload
        p=self.text_panel
        p.text.setPlainText(text)
        target_rect=tuple(rect or p.rect())
        # 表格文字：提交時以儲存格重新解析，確保改字後仍位於表格正中央；
        # 若使用者已手動移動文字框（差異超過 2 點）則尊重其設定。
        if run is not None and p.alignment.currentData()=="center":
            cell=find_table_cell(self.session.pdf,self.page,run.rect)
            manual=tuple(p.rect())
            if cell and (not p.modified or abs(manual[0]-cell[0])+abs(manual[1]-cell[1])
                    +abs(manual[2]-cell[2])+abs(manual[3]-cell[3])<2.0):
                target_rect=cell
        if run is not None and text==run.text and not p.modified:
            self.refresh_actions()
            self.statusBar().showMessage("文字維持不變，可繼續使用標記或格式工具。")
            return
        font_path=p.font_path
        if text:
            try:
                checked_font(font_path,text)
            except EditorError as exc:
                if exc.code!="FONT_MISSING_GLYPH":
                    self.error((exc.code,str(exc),()))
                    return
                font_path=str(default_font())
                p.font_path=font_path
                p.font_label.setText("已自動改用內建中文字型，以完整顯示新文字。")
        if run is None:
            if not text:
                self.run=None
                self.insertion_rect=None
                self.text_panel.setEnabled(False)
                self.refresh_actions()
                self.request_render()
                self.statusBar().showMessage("已取消新增文字。")
                return
            request=TextInsertion(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
                text,target_rect,font_path,p.size.value(),p.color,p.alignment.currentData(),
                p.bold.isChecked())
            self.apply_text_immediately(request,"正在新增文字…",insert_text)
            return
        request=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
            run.id,text,target_rect,font_path,p.size.value(),p.color,p.alignment.currentData(),
            p.bold.isChecked())
        self.apply_text_immediately(request,"正在更新文字…")

    def apply_text_format(self):
        if not self.session or self.busy or self.preview or not self.run:
            return
        if self.canvas.inline_editor is not None:
            self.canvas.inline_editor.commit()
            return
        self.commit_inline_text((self.run,self.text_panel.text.toPlainText(),self.text_panel.rect()))

    def cancel_inline_text(self):
        self.run=None
        self.insertion_rect=None
        self.text_panel.setEnabled(False)
        self.refresh_actions()
        self.request_render()
        self.statusBar().showMessage("已取消文字編輯。")

    def preview_from_panel(self):
        if not self.session or self.busy:
            return
        p=self.text_panel
        if self.insertion_rect is not None:
            req=TextInsertion(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
                p.text.toPlainText(),p.rect(),p.font_path,p.size.value(),p.color,
                p.alignment.currentData(), p.bold.isChecked())
            self.preview_replacement(req,insert_text)
            return
        if not self.run:
            return
        req=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),
            self.page,self.run.id,p.text.toPlainText(),p.rect(),p.font_path,p.size.value(),
            p.color, p.alignment.currentData(), p.bold.isChecked())
        self.preview_replacement(req)

    def move_run(self,run):
        if not self.session or not run.editable or self.busy or self.preview:
            return
        self.canvas.cancel_inline_editor()
        self.run=run
        x0,y0,x1,y1=run.rect
        self.text_panel.set_rect((x0,y0,x1+10,y1+run.size*0.5))
        p=self.text_panel
        request=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),self.page,
            run.id,p.text.toPlainText(),p.rect(),p.font_path,p.size.value(),p.color,
            p.alignment.currentData(), p.bold.isChecked())
        self.apply_text_immediately(request,"正在移動文字…")

    def start_text_insertion(self,checked=True):
        if not checked:
            self.cancel_text_insertion()
            return
        if self.preview:
            self.actions["add_text"].setChecked(False)
            message="請先按「套用預覽」或「取消預覽」，再新增文字。"
            QMessageBox.information(self,"尚有文字預覽",message)
            self.statusBar().showMessage(message)
            return
        if not self.session or self.busy:
            self.actions["add_text"].setChecked(False)
            return
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.clear_annotation_selection()
        self.annotation=None
        self.run=None
        self.insertion_rect=None
        self.canvas.start_text_insertion()
        self.text_panel.setEnabled(False)
        self.statusBar().showMessage("請在頁面空白處或空白儲存格中點一下。")

    def cancel_text_insertion(self):
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.statusBar().showMessage("已取消新增文字。")

    def start_text_note(self,checked=True):
        if not checked:
            self.cancel_text_note()
            return
        if not self.session or self.busy or not self.session.access.can_edit:
            self.actions["text_note"].setChecked(False)
            return
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.clear_annotation_selection()
        self.annotation=None
        self.run=None
        self.insertion_rect=None
        self.text_panel.setEnabled(False)
        self.canvas.start_note_insertion()
        self.refresh_actions()
        self.statusBar().showMessage("請在頁面上點選文字註解的位置。")

    def cancel_text_note(self):
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.statusBar().showMessage("已取消文字註解。")

    def begin_text_note(self,position):
        self.actions["text_note"].setChecked(False)
        text,ok=QInputDialog.getMultiLineText(self,"新增文字註解","註解內容：")
        if not ok or not text.strip():
            self.statusBar().showMessage("已取消文字註解。")
            return
        self.submit_annotation(add_text_note,(self.page,position,text),"正在新增文字註解…")

    def start_annotation_selection(self,checked=True):
        if not checked:
            self.canvas.cancel_annotation_selection()
            self.statusBar().showMessage("已取消選取註解。")
            return
        if not self.session or self.busy or not self.session.access.can_edit:
            self.actions["select_annotation"].setChecked(False)
            return
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.annotation=None
        self.run=None
        self.insertion_rect=None
        self.text_panel.setEnabled(False)
        self.canvas.start_annotation_selection()
        self.refresh_actions()
        self.statusBar().showMessage("請直接點選頁面上的螢光、底線或文字註解。")

    def select_annotation(self,item):
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.cancel_inline_editor()
        self.canvas.clear_text_selection()
        self.canvas.select_annotation(item)
        self.annotation=item
        self.run=None
        self.insertion_rect=None
        self.text_panel.setEnabled(False)
        self.refresh_actions()
        names={"Highlight":"螢光標記","Underline":"底線","Text":"文字註解"}
        self.statusBar().showMessage(
            f"已選取{names.get(item.kind,'註解')}；按 Delete 可直接刪除。")

    def _guide_annotation_selection(self,message):
        if not self.session or self.busy or not self.session.access.can_edit:
            return
        self.actions["select_annotation"].setChecked(True)
        self.start_annotation_selection(True)
        self.statusBar().showMessage(message)

    def _guide_markup_selection(self,label):
        if not self.session or self.busy or not self.session.access.can_edit:
            return
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.clear_annotation_selection()
        self.cancel_direct_crop()
        self.annotation=None
        self.run=None
        self.insertion_rect=None
        self.text_panel.setEnabled(False)
        self.canvas.clear_text_selection()
        self.canvas.setFocus()
        self.statusBar().showMessage(
            f"請先點選要加入{label}的文字；選取後請再次選擇{label}。")

    def delete_selected_annotation(self,item=None):
        if hasattr(item,"xref"):
            self.annotation=item
        if self.busy:
            return
        if not self.annotation:
            self._guide_annotation_selection("請點選要刪除的註解；選取後按 Delete。")
            return
        selected=self.annotation
        self.submit_annotation(delete_annotation,(self.page,selected.xref),"正在刪除註解…")

    def change_highlight_color(self,color):
        if self.busy:
            return
        if not self.annotation:
            self._guide_annotation_selection(
                "請先點選要變更顏色的螢光標記；選取後請再次選擇顏色。")
            return
        if self.annotation.kind!="Highlight":
            self.statusBar().showMessage("只有螢光標記可以變更顏色。")
            return
        self.submit_annotation(set_highlight_color,
            (self.page,self.annotation.xref,color),"正在變更螢光標記顏色…")

    def apply_selected_markup(self,kind):
        label="螢光標記" if kind=="highlight" else "底線"
        if self.busy:
            return
        if not self.run:
            self._guide_markup_selection(label)
            return
        run=self.run
        self.canvas.cancel_inline_editor()
        self.submit_annotation(mark_text,(self.page,run.rect,kind),f"正在加入{label}…")

    def submit_annotation(self,operation,args,status):
        if not self.session or self.busy:
            return
        page=self.page
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(status)
        def done(pdf):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.session.apply_pdf(pdf)
            self.run=None
            self.annotation=None
            self.canvas.clear_text_selection()
            self.canvas.clear_annotation_selection()
            self.canvas.cancel_annotation_selection()
            self.actions["select_annotation"].setChecked(False)
            self.text_panel.setEnabled(False)
            self.page_data=None
            self.refresh_actions()
            self.request_render()
            self.queue_thumbnails((page,))
        self.jobs.submit(operation,(self.session.pdf,*args),done,self.error)

    def begin_text_insertion(self,position):
        if not self.session or self.busy or self.preview:
            return
        x,y=position
        self.actions["add_text"].setChecked(False)
        cell=find_table_cell(self.session.pdf,self.page,(x,y,x,y))
        if cell:
            rect=cell
        else:
            width,height=self.page_data["bounds"][2:]
            x=max(0,min(x,width-160))
            y=max(0,min(y,height-40))
            rect=(x,y,min(width,x+160),min(height,y+40))
        self.run=None
        self.insertion_rect=rect
        self.panels.setCurrentWidget(self.text_panel)
        self.text_panel.font_path=str(default_font())
        self.text_panel.font_label.setText("替代字型：Noto Sans CJK TC（完整繁中文字元）")
        self.text_panel.set_insertion(rect,centered=cell is not None)
        self.canvas.begin_inline_text(rect,"",None,self.text_panel.size.value(),
            self.text_panel.alignment.currentData())
        self.refresh_actions()

    def delete_run(self,run):
        if not self.session or not run.editable or self.busy or self.preview:
            return
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.run=run
        p=self.text_panel
        cell=find_table_cell(self.session.pdf,self.page,run.rect)
        x0,y0,x1,y1=run.rect
        insertion_rect=cell or (x0,y0,x1+40,y1+run.size)
        request=TextReplacement(hashlib.sha256(self.session.pdf).hexdigest(),
            self.page,run.id,"",run.rect,p.font_path,run.size,run.color,"left",False)
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage("正在刪除文字…")
        def done(pdf):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.session.apply_pdf(pdf)
            self.preview=None
            self.run=None
            self.insertion_rect=insertion_rect
            self.text_panel.font_path=str(default_font())
            self.text_panel.font_label.setText("替代字型：Noto Sans CJK TC（完整繁中文字元）")
            self.text_panel.set_insertion(insertion_rect,run.size,cell is not None)
            self.refresh_actions()
            self.request_render()
            self.canvas.begin_inline_text(insertion_rect,"",None,run.size,
                self.text_panel.alignment.currentData())
        self.jobs.submit(replace_text,(self.session.pdf,request),done,self.error)

    def apply_text_immediately(self,request,status,operation=replace_text):
        if self.busy:
            return
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(status)
        def done(pdf):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.session.apply_pdf(pdf)
            self.preview=None
            self.run=None
            self.insertion_rect=None
            self.text_panel.setEnabled(False)
            self.refresh_actions()
            self.request_render()
            self.queue_thumbnails((request.page,))
        self.jobs.submit(operation,(self.session.pdf,request),done,self.error)

    def preview_replacement(self,request,operation=replace_text):
        if self.busy:
            return
        revision=self.session.revision
        token=self.token
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage("正在建立文字預覽…")
        def done(pdf):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                return
            self.preview=(revision,pdf)
            self.refresh_actions()
            self.request_render()
        self.jobs.submit(operation,(self.session.pdf,request),done,self.error)

    def cancel_preview(self):
        self.preview=None
        self.refresh_actions()
        self.request_render()

    def apply_preview(self):
        if self.preview and self.preview[0]==self.session.revision:
            self.canvas.cancel_inline_editor()
            self.session.apply_pdf(self.preview[1])
            self.preview=None
            self.canvas.cancel_text_insertion()
            self.actions["add_text"].setChecked(False)
            self.insertion_rect=None
            self.run=None
            self.text_panel.setEnabled(False)
            self.refresh_actions()
            self.request_render()
            self.queue_thumbnails((self.page,))

    def history_step(self,redo):
        if not self.session or self.busy:
            return
        self.preview=None
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.clear_annotation_selection()
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        self.insertion_rect=None
        self.run=None
        self.annotation=None
        self.session.redo() if redo else self.session.undo()
        self.text_panel.setEnabled(False)
        self.sync_page_navigation()

    def save(self):
        if not self.session or self.busy:
            return
        if self.preview:
            QMessageBox.information(self,"尚有預覽","請先套用或取消文字預覽，再另存新檔。")
            return
        if self.session.password_used:
            if QMessageBox.question(self,"輸出保護","另存的文件不保留密碼保護，是否繼續？")!=QMessageBox.StandardButton.Yes:
                return
        name,_=QFileDialog.getSaveFileName(self,"另存新檔",
            str(self.session.source.with_name(self.session.source.stem+"-已編輯.pdf")),"PDF (*.pdf)")
        if not name:
            return
        target=Path(name)
        if target.suffix.lower()!=".pdf":
            target=target.with_suffix(".pdf")
        revision=self.session.revision
        fingerprint=self.session.history.current[2]
        self.busy=True
        self.refresh_actions()
        def done(path):
            self.busy=False
            if self.session.revision==revision:
                self.session.saved_fingerprint=fingerprint
            self.refresh_actions()
            self.statusBar().showMessage("已儲存："+path)
        self.jobs.submit(export_document,(self.session.pdf,self.session.overlays,str(target),
            str(self.session.source),target.exists()),done,self.error)

    def add_stamp(self):
        name,_=QFileDialog.getOpenFileName(self,"匯入圖章或簽名","","PNG (*.png)")
        if name:
            self.import_layer(Path(name),True)

    def convert_legacy_stamp(self):
        """掃描文件，讓使用者明確選取後將既有圖章抽離為工作層。"""
        if (not self.session or self.busy or not self.session.access.can_edit
                or self.preview):
            return
        token = self.token
        revision = self.session.revision
        pdf = self.session.pdf
        self.busy = True
        self.refresh_actions()
        self.statusBar().showMessage("正在掃描可安全轉換的既有圖章…")

        def done(candidates):
            self._show_legacy_stamp_candidates(candidates, pdf, token, revision)

        def failed(error):
            self._finish_legacy_stamp_failure(error, token, revision)

        self.jobs.submit(find_convertible_images, (pdf,), done, failed)

    def _show_legacy_stamp_candidates(self, candidates, pdf, token, revision):
        if not self._legacy_stamp_context_is_current(token, revision):
            self._restore_after_legacy_stamp_job()
            return
        if not candidates:
            self._restore_after_legacy_stamp_job()
            self.statusBar().showMessage(
                "沒有可安全轉換的既有圖章；Logo、整頁掃描與重複影像不會列出。"
            )
            return

        dialog = LegacyStampDialog(candidates, self)
        if not dialog.exec() or dialog.selected_candidate is None:
            self._restore_after_legacy_stamp_job()
            self.statusBar().showMessage("已取消轉換既有圖章。")
            return

        candidate = dialog.selected_candidate
        self.statusBar().showMessage("正在轉換選取的既有圖章…")

        def done(result):
            self._apply_converted_legacy_stamp(result, token, revision)

        def failed(error):
            self._finish_legacy_stamp_failure(error, token, revision)

        self.jobs.submit(
            convert_legacy_image,
            (pdf, candidate, self.asset_root),
            done,
            failed,
        )

    def _apply_converted_legacy_stamp(self, result, token, revision):
        self.busy = False
        if not self._legacy_stamp_context_is_current(token, revision):
            self.refresh_actions()
            return
        try:
            base_pdf, layer = result
            self.session.apply_state(base_pdf, self.session.overlays + (layer,))
            self.clear_search_results()
            self.page_data = None
            self.select_layer(layer.id)
            self.refresh_actions()
            self.request_render("已轉換為可編輯圖章。")
            self.queue_thumbnails((layer.page,))
        except Exception as exc:
            self.error((getattr(exc, "code", "STAMP_CONVERSION"), str(exc), ()))

    def _finish_legacy_stamp_failure(self, error, token, revision):
        self.busy = False
        if not self._legacy_stamp_context_is_current(token, revision):
            self.refresh_actions()
            return
        self.error(error)

    def _restore_after_legacy_stamp_job(self):
        self.busy = False
        self.refresh_actions()

    def _legacy_stamp_context_is_current(self, token, revision):
        return (not self.closed and self.session is not None
            and token == self.token and revision == self.session.revision)

    def add_collection(self):
        name,_=QFileDialog.getOpenFileName(self,"選擇常用圖章",str(self.asset_root),"PNG (*.png)")
        if name:
            self.import_layer(Path(name),True)

    def add_signature(self):
        dialog=SignatureDialog(self)
        if dialog.exec():
            path=self.session.history.root/"signature.png"
            path.write_bytes(dialog.png_bytes())
            self.import_layer(path,dialog.collect.isChecked())

    def import_layer(self,path,persistent):
        if not self.session or self.preview:
            return
        try:
            store=self.assets if persistent else AssetStore(self.session.history.root/"assets")
            copied=store.import_png(path,persistent)
            from PIL import Image
            with Image.open(copied) as image:
                ratio=image.height/image.width
            width=min(150,self.page_data["bounds"][2]*0.4)
            height=min(width*ratio,self.page_data["bounds"][3]*0.4)
            layer=Overlay(uuid.uuid4().hex,self.page,str(copied),(40,40,40+width,40+height),0)
            self.session.set_overlays(self.session.overlays+(layer,))
            self.layer_id=layer.id
            self.select_layer(layer.id)
            self.refresh_actions()
            self.request_render()
        except Exception as exc:
            self.error((getattr(exc,"code","IMAGE"),str(exc),()))

    def select_layer(self,id):
        self.canvas.cancel_inline_editor()
        self.layer_id=id
        layer=next((o for o in self.session.overlays if o.id==id),None)
        if layer:
            self.panels.setCurrentWidget(self.overlay_panel)
            self.overlay_panel.set_layer(layer)

    def move_layer(self,layer):
        try:
            # 先驗證新位置，失敗時還原畫布而不寫入歷史。
            flatten_overlays(self.session.pdf,(layer,))
            self.session.set_overlays(tuple(layer if o.id==layer.id else o for o in self.session.overlays))
            self.select_layer(layer.id)
            self.refresh_actions()
        except Exception as exc:
            self.error((getattr(exc,"code","GEOMETRY"),str(exc),()))
        self.request_render()

    def update_layer(self):
        layer=next((o for o in self.session.overlays if o.id==self.layer_id),None)
        if layer:
            x,y,w,h,angle=(s.value() for s in self.overlay_panel.fields)
            self.move_layer(replace(layer,rect=(x,y,x+w,y+h),angle=angle))

    def delete_layer(self):
        self.session.set_overlays(tuple(o for o in self.session.overlays if o.id!=self.layer_id))
        self.panels.setCurrentWidget(self.text_panel)
        self.refresh_actions()
        self.request_render()

    def merge(self):
        dialog=MergeDialog(self)
        if not dialog.exec():
            return
        name,_=QFileDialog.getSaveFileName(self,"合併輸出","合併.pdf","PDF (*.pdf)")
        if name:
            self.busy=True
            self.refresh_actions()
            self.jobs.submit(export_merge,(tuple(dialog.sources),dialog.order(),name,
                tuple(str(p) for p in dialog.paths)),self.export_done,self.error)

    def split(self):
        if self.preview:
            QMessageBox.information(self,"尚有預覽","請先套用或取消文字預覽。")
            return
        dialog=SplitDialog(self.page_count,self)
        if dialog.exec():
            folder=QFileDialog.getExistingDirectory(self,"選擇輸出資料夾")
            if folder:
                self.busy=True
                self.refresh_actions()
                self.jobs.submit(export_split,(self.session.pdf,self.session.overlays,
                    dialog.selections(),folder,str(self.session.source)),self.export_done,self.error)

    def export_done(self,result):
        self.busy=False
        self.refresh_actions()
        self.statusBar().showMessage("輸出完成："+(result if isinstance(result,str) else f"{len(result)} 份 PDF"))

    def showEvent(self,event):
        super().showEvent(event)
        apply_dark_title_bar(self)

    def closeEvent(self,event):
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        if not self.confirm_leave():
            event.ignore()
            return
        self.closed=True
        self.save_window_state()
        self.close_comparison()
        self.jobs.close()
        if self.session:
            self.session.close()
            self.session=None
        event.accept()
