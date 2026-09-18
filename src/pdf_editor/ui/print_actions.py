"""預覽列印與送印。"""
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QToolBar, QApplication, QProgressDialog
from pdf_editor.engine.overlay import flatten_overlays
from pdf_editor.errors import EditorError
from pdf_editor.ui.printing import print_pages
from pdf_editor.ui.style import themed_glyph_icon


class PrintActionsMixin:
    def print_document(self):
        """先開啟程式內的預覽列印；Windows 列印對話框無法替桌面程式顯示預覽。"""
        if not self.session or self.busy:
            return
        from PySide6.QtPrintSupport import QPrinter,QPrintPreviewDialog
        printer=QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(self.session.source.stem)
        preview=QPrintPreviewDialog(printer,self)
        preview.setWindowTitle(f"預覽列印 — {self.session.source.name}")
        preview.resize(max(900,int(self.width()*0.8)),max(700,int(self.height()*0.9)))
        self.style_print_preview(preview)
        result={"printed":None}
        def paint(target):
            result["printed"]=self.print_to(target,self.print_page_indices(target),announce=False)
        preview.paintRequested.connect(paint)
        if preview.exec()==QPrintPreviewDialog.DialogCode.Accepted and result["printed"] is not None:
            self.statusBar().showMessage(f"已送出 {result['printed']} 頁到印表機。")
        else:
            self.statusBar().showMessage("已關閉預覽列印。")

    def style_print_preview(self,preview):
        """預覽工具列的內建圖示在深色主題下看不清楚，改用主題圖示。"""
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtGui import QColor,QIcon,QTransform
        from PySide6.QtWidgets import QGraphicsView,QToolBar
        from pdf_editor.ui.style import COLORS
        # 以 Qt 原始按鈕名稱（含翻譯後名稱）對應圖示，不依賴按鈕排列順序。
        glyphs={"Fit width":"\ue740","Fit page":"\ue9a6","Zoom in":"\ue8a3","Zoom out":"\ue71f",
            "Portrait":"\ue7c3","Landscape":"\ue7c3","First page":"\ue892","Previous page":"\ue76b",
            "Next page":"\ue76c","Last page":"\ue893","Show single page":"\ue7c3",
            "Show facing pages":"\ue736","Show overview of all pages":"\ue80a",
            "Page setup":"\ue713","Print":"\ue749"}
        lookup={}
        for source,glyph in glyphs.items():
            lookup[source]=(source,glyph)
            lookup[QCoreApplication.translate("QPrintPreviewDialog",source)]=(source,glyph)
        for toolbar in preview.findChildren(QToolBar):
            toolbar.setIconSize(QSize(20,20))
            for action in toolbar.actions():
                match=lookup.get(action.text())
                if match is None:
                    continue
                source,glyph=match
                icon=themed_glyph_icon(glyph,20)
                if source=="Landscape":
                    # 橫印圖示以直印圖示旋轉 90 度表示。
                    icon=QIcon(icon.pixmap(QSize(40,40)).transformed(QTransform().rotate(90)))
                action.setIcon(icon)
        for view in preview.findChildren(QGraphicsView):
            view.setBackgroundBrush(QColor(COLORS["canvas"]))

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

    def print_to(self,printer,pages,announce=True):
        """合併工作層後逐頁列印，並顯示可取消的進度。"""
        try:
            pdf=flatten_overlays(self.session.pdf,self.session.overlays) if self.session.overlays else self.session.pdf
        except EditorError as exc:
            self.error((exc.code,str(exc),()))
            return 0
        progress=QProgressDialog("正在準備頁面…","取消",0,len(pages),self)
        progress.setWindowTitle("列印")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(400)
        def step(index,total):
            progress.setValue(index)
            progress.setLabelText(f"正在處理第 {index+1} / {total} 頁…")
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
        if announce:
            self.statusBar().showMessage(f"已送出 {printed} 頁到印表機。" if printed==len(pages)
                else f"列印已取消，已送出 {printed} 頁。")
        return printed
