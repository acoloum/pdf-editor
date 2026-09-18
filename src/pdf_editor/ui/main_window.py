from pathlib import Path
import pymupdf
from PySide6.QtCore import Qt,QStandardPaths,QSize,Signal,QPointF,QTimer,QUrl
from PySide6.QtGui import QAction, QKeySequence, QDesktopServices
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QLabel,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QToolBar,
    QFileDialog,
    QMessageBox,
    QInputDialog,
    QLineEdit,
    QStackedWidget,
    QComboBox,
    QSpinBox,
    QScrollArea,
    QListView,
    QMenu,
    QToolButton,
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QCheckBox)
from pdf_editor.document.session import DocumentSession
from pdf_editor.engine.render import render_page
from pdf_editor.engine.geometry import transform_point,inverse_transform
from pdf_editor.engine.inspection import unlock_pdf
from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.errors import EditorError
from pdf_editor.workers import Jobs
from pdf_editor.assets import AssetStore
from pdf_editor.templates import HeaderFooterTemplateStore
from pdf_editor.search import find_text
from pdf_editor.comparison import compare_pages
from pdf_editor.ui.canvas import Canvas
from pdf_editor.ui.text_panel import TextPanel
from pdf_editor.ui.overlay_panel import OverlayPanel
from pdf_editor.ui.comparison_dialog import ComparisonDialog
from pdf_editor.ui.style import STYLE, apply_theme, apply_dark_title_bar, app_icon, glyph_icon
from pdf_editor.document.save import backup_folder
from pdf_editor.ui.settings import AppSettings
from pdf_editor.ui.background_jobs import export_document,overwrite_document
from pdf_editor.ui.page_actions import PageActionsMixin
from pdf_editor.ui.text_actions import TextActionsMixin
from pdf_editor.ui.stamp_actions import StampActionsMixin
from pdf_editor.ui.print_actions import PrintActionsMixin
from pdf_editor.ui.info_panel import DocumentInfoPanel,describe_document
from pdf_editor.ui.search_bar import SearchBar
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

class MainWindow(PageActionsMixin,TextActionsMixin,StampActionsMixin,PrintActionsMixin,QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("墨頁 PDF")
        self.resize(1320,850)
        apply_theme(QApplication.instance())
        self.setWindowIcon(app_icon())
        self.setStyleSheet(STYLE)
        self.session=None
        self.page_data=None
        self.page_count=0
        self.page=0
        self.scale=1.25
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
        self._reselect_after_render=None
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
        # 圖示改為視窗顯示後再產生（首次約需 0.2 秒），縮短啟動到看見視窗的時間。
        self._pending_icons=[]
        for name,label,handler,key in [
            ("open","開啟 PDF",self.choose_open,"Ctrl+O"),
            ("save","儲存",self.save_over_original,"Ctrl+S"),
            ("save_as","另存新檔",self.save,"Ctrl+Shift+S"),
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
            action=QAction(label,self)
            self._pending_icons.append((action,name))
            action.triggered.connect(handler)
            if key:
                action.setShortcut(QKeySequence(key))
            if name=="add_text":
                action.setCheckable(True)
            self.actions[name]=action
        self.save_menu=QMenu("儲存選項",self)
        self.save_menu.addAction(self.actions["save_as"])
        self.save_menu.addSeparator()
        self.save_menu.addAction("開啟原檔備份資料夾",self.open_backup_folder)
        self.actions["save"].setMenu(self.save_menu)
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
            ("print","列印",self.print_document,("Ctrl+P",),"預覽並列印文件（Ctrl+P），圖章與簽名會一併印出"),
            ("copy_text","複製文字",self.copy_selected_text,("Ctrl+C",),"複製選取的文字（Ctrl+C）"),
            ("copy_page_text","複製本頁全部文字",self.copy_page_text,("Ctrl+Shift+C",),
                "複製目前頁面的全部文字（Ctrl+Shift+C）"),
            ("fit_page","適合頁面",lambda:self.zoom.setCurrentText("適合頁面"),(),"整頁顯示"),
            ("fit_width","適合寬度",lambda:self.zoom.setCurrentText("適合寬度"),(),"頁面寬度填滿畫布")]:
            action=QAction(label,self)
            self._pending_icons.append((action,name))
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
        self._pending_icons.append((self.page_menu_button,"page_menu"))
        self.page_menu_button.setMenu(page_menu)
        self.page_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.markup_menu_button=QToolButton()
        self.markup_menu_button.setText("標記註解")
        self._pending_icons.append((self.markup_menu_button,"markup_menu"))
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
        for name in ("open","save"):
            toolbar.widgetForAction(self.actions[name]).setPopupMode(
                QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        tips={
            "open":"開啟 PDF（Ctrl+O）；點右側箭頭可選最近開啟的檔案",
            "save":"儲存（Ctrl+S）；直接覆蓋原檔，覆蓋前會自動保留原檔備份",
            "save_as":"另存新檔（Ctrl+Shift+S）；原始檔案不會被覆蓋",
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
            "print":"預覽並列印（Ctrl+P），圖章與簽名會一併印出",
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
        QTimer.singleShot(0,self.load_action_icons)
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
        editing_text=self.run is not None or self.insertion_rect is not None
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

    def load_action_icons(self):
        """視窗顯示後才產生工具列圖示，避免拖慢啟動。"""
        pending,self._pending_icons=self._pending_icons,[]
        for target,name in pending:
            target.setIcon(glyph_icon(name))

    def cancel_editing_modes(self,crop=True):
        """取消頁面上的輸入框與所有進行中的模式（新增文字、註解、選取註解、裁切）。"""
        self.canvas.cancel_inline_editor()
        self.canvas.cancel_note_insertion()
        self.actions["text_note"].setChecked(False)
        self.canvas.cancel_annotation_selection()
        self.actions["select_annotation"].setChecked(False)
        self.canvas.cancel_text_insertion()
        self.actions["add_text"].setChecked(False)
        if crop:
            self.cancel_direct_crop()

    def clear_selection_state(self,disable_panel=False):
        """清除目前選取的文字、註解與新增位置。"""
        self.run=None
        self.annotation=None
        self.insertion_rect=None
        if disable_panel:
            self.text_panel.setEnabled(False)

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
        for name in ("save","save_as","add_text","stamp","convert_stamp","signature","collection",
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
                self.save_over_original()
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
        self.cancel_editing_modes()
        self.clear_search_results(True)
        self.session=session
        self.token+=1
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






    def goto_page(self,page):
        if not self.session or not 0<=page<self.page_count or page==self.page:
            return
        self.page=page
        if self.thumbs.currentRow()!=page:
            self.thumbs.blockSignals(True)
            self.thumbs.setCurrentRow(page)
            self.thumbs.blockSignals(False)
        self.cancel_editing_modes()
        self.clear_selection_state(disable_panel=True)
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
        edit=self.session is not None and self.session.access.can_edit and not self.busy
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
        data=str(self.session.pdf_path)
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
                if self._reselect_after_render:
                    self.reselect_text_after_render(result)
                self.schedule_cell_analysis()
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
                status=self.session.access.reason or ("有未儲存變更" if self.session.dirty else "可編輯")
                self.statusBar().showMessage(f"第 {self.page+1} / {self.page_count} 頁  ·  {status}")
            self.update_status_fields()
        pixel_ratio=float(self.canvas.devicePixelRatioF())
        self.jobs.submit(render_page,(data,self.page,self.scale,pixel_ratio),done,self.error)



























    def history_step(self,redo):
        if not self.session or self.busy:
            return
        self.cancel_editing_modes(crop=False)
        self.canvas.clear_annotation_selection()
        self.clear_selection_state()
        self.session.redo() if redo else self.session.undo()
        self.text_panel.setEnabled(False)
        self.sync_page_navigation()

    def save_over_original(self):
        """直接覆蓋原檔；覆蓋前把原檔複製到備份資料夾。"""
        if not self.session or self.busy:
            return
        source=self.session.source
        if not source.exists():
            QMessageBox.information(self,"找不到原檔",
                f"原始檔案已不存在，請改用「另存新檔」：\n{source}")
            self.save()
            return
        if self.session.password_used and QMessageBox.question(self,"輸出保護",
                "覆蓋後的文件不保留密碼保護，是否繼續？")!=QMessageBox.StandardButton.Yes:
            return
        if not self.confirm_overwrite(source):
            return
        revision=self.session.revision
        fingerprint=self.session.history.current[2]
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage("正在儲存…")
        def done(result):
            path,backup=result
            self.busy=False
            if self.session.revision==revision:
                self.session.saved_fingerprint=fingerprint
            self.refresh_actions()
            self.statusBar().showMessage(f"已儲存：{path}"
                +(f"（原檔備份：{backup}）" if backup else "（未能建立原檔備份）"))
        self.jobs.submit(overwrite_document,(self.session.pdf,self.session.overlays,
            str(source),str(backup_folder())),done,self.error)

    def open_backup_folder(self):
        """在檔案總管開啟覆蓋原檔前保留的備份資料夾。"""
        folder=backup_folder()
        try:
            folder.mkdir(parents=True,exist_ok=True)
        except OSError as exc:
            self.error(("BACKUP",f"無法開啟備份資料夾：{exc}",()))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        self.statusBar().showMessage(f"備份資料夾：{folder}")

    def confirm_overwrite(self,source):
        """首次覆蓋原檔時說明行為，使用者可選擇不再詢問。"""
        if self.settings.value("save/skip_overwrite_confirm","") == "yes":
            return True
        box=QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("儲存並覆蓋原檔")
        box.setText(f"將直接覆蓋原檔：\n{source}")
        box.setInformativeText("覆蓋前會自動把原檔複製到備份資料夾。若要保留原檔，請改用「另存新檔」。")
        box.setStandardButtons(QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        remember=QCheckBox("以後不再詢問")
        box.setCheckBox(remember)
        if box.exec()!=QMessageBox.StandardButton.Save:
            return False
        if remember.isChecked():
            self.settings.set_value("save/skip_overwrite_confirm","yes")
            self.settings.sync()
        return True

    def save(self):
        if not self.session or self.busy:
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
