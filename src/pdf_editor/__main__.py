import sys
import multiprocessing


def _acceptance_report(query, match_count, comparison_summary,
        revision_before, revision_after, ocr_word_count=None):
    """建立安裝版私有驗收紀錄，並拒絕未通過的結果。"""
    if match_count <= 0:
        raise AssertionError("OCR 文字無法搜尋。")
    if revision_before != revision_after:
        raise AssertionError("頁面比較改變了 revision。")
    if not comparison_summary.startswith("相似度"):
        raise AssertionError("頁面比較沒有完成。")
    ocr_line = f"OCR 文字區段={ocr_word_count}\n" if ocr_word_count is not None else ""
    return (
        ocr_line +
        f"搜尋文字={query}\n"
        f"搜尋命中={match_count}\n"
        f"比較結果={comparison_summary}\n"
        f"revision={revision_before} -> {revision_after}\n"
    )


def _recognize_for_acceptance(scan_path, ocr_output):
    """在主程序呼叫封裝 OCR，供 frozen 安裝版驗收。"""
    from pdf_editor.ocr import ocr_pages
    from pdf_editor.ocr_assets import validate_ocr_assets

    result = ocr_pages(scan_path.read_bytes(), (0,), validate_ocr_assets())
    if result.word_count <= 0:
        raise AssertionError("OCR 沒有辨識到文字。")
    ocr_output.write_bytes(result.pdf)
    return result.word_count


def _save_acceptance_preview(window, dialog, target):
    """合併安裝版的搜尋主視窗與頁面比較視窗。"""
    from PySide6.QtGui import QColor, QImage, QPainter

    window_pixmap = window.grab()
    dialog_pixmap = dialog.grab()
    width = max(window_pixmap.width(), dialog_pixmap.width())
    preview = QImage(width, window_pixmap.height() + dialog_pixmap.height(),
                     QImage.Format.Format_RGB32)
    preview.fill(QColor("#20242a"))
    painter = QPainter(preview)
    painter.drawPixmap((width - window_pixmap.width()) // 2, 0, window_pixmap)
    painter.drawPixmap((width - dialog_pixmap.width()) // 2,
                       window_pixmap.height(), dialog_pixmap)
    painter.end()
    if not preview.save(str(target), "PNG"):
        raise AssertionError("無法儲存安裝版驗收預覽。")


def _finish_acceptance(window, app, exit_code):
    """關閉私有驗收視窗，不顯示未儲存詢問。"""
    if window.session is not None and window.session.dirty:
        window.session.saved_fingerprint = window.session.history.current[2]
    window.close()
    app.exit(exit_code)


def _schedule_subset_cache_prune():
    """啟動後清理過量的字型子集快取，避免長期累積佔用磁碟。"""
    from PySide6.QtCore import QTimer

    def prune():
        from pdf_editor.engine.fonts import prune_subset_cache
        from pdf_editor.logs import get_logger
        try:
            removed = prune_subset_cache()
        except Exception:
            get_logger().exception("清理字型子集快取失敗")
            return
        if removed:
            get_logger().info("已清理 %s 個字型子集快取檔案", removed)

    QTimer.singleShot(5000, prune)


def _install_exception_hook(app):
    """介面事件中未處理的例外寫入紀錄檔並提示使用者，避免程式默默失效。"""
    from pdf_editor.logs import get_logger, log_path

    def hook(kind, value, trace):
        get_logger().error("未處理的例外", exc_info=(kind, value, trace))
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(app.activeWindow(), "發生未預期的錯誤",
                f"{value}\n\n目前文件未被覆蓋，建議先另存新檔。\n紀錄檔：{log_path()}")
        except Exception:
            pass

    sys.excepthook = hook


def _install_qt_translations(app):
    """載入 Qt 內建繁體中文翻譯，讓預覽列印等標準視窗顯示中文。"""
    from pathlib import Path

    import PySide6
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    locale = QLocale(QLocale.Language.Chinese, QLocale.Country.Taiwan)
    # 封裝版的 Qt 路徑設定可能不同，找不到時改用 PySide6 套件內的翻譯資料夾。
    folders = (QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
               str(Path(PySide6.__file__).parent / "translations"))
    for folder in folders:
        translator = QTranslator(app)
        if translator.load(locale, "qtbase", "_", folder):
            app.installTranslator(translator)
            return translator
    return None


def main(argv=None):
    multiprocessing.freeze_support()
    args=list(sys.argv[1:] if argv is None else argv)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from pdf_editor.document.history import cleanup_stale
    from pdf_editor.ui.main_window import MainWindow
    app=QApplication(sys.argv)
    app.setOrganizationName("LocalPDFEditor")
    app.setApplicationName("墨頁 PDF")
    cleanup_stale()
    _schedule_subset_cache_prune()
    _install_exception_hook(app)
    _install_qt_translations(app)
    window=MainWindow()
    window.show()
    if args and args[0]=="--smoke-test":
        if len(args)!=3:
            window.close()
            return 2
        from pathlib import Path
        try:
            window.open_document(Path(args[1]))
        except Exception:
            window.close()
            return 2
        remaining={"ticks":600}
        def finish_smoke():
            if window.page_data is not None:
                ok=window.grab().save(args[2])
                window.close()
                app.exit(0 if ok else 2)
                return
            remaining["ticks"]-=1
            if remaining["ticks"]<=0:
                window.close()
                app.exit(2)
                return
            QTimer.singleShot(50,finish_smoke)
        QTimer.singleShot(50,finish_smoke)
    elif args and args[0]=="--ocr-comparison-smoke-test":
        if len(args)!=6:
            window.close()
            return 2
        from pathlib import Path

        scan_path=Path(args[1])
        comparison_path=Path(args[2])
        ocr_output=Path(args[3])
        screenshot=Path(args[4])
        report_output=Path(args[5])
        try:
            comparison_pdf=comparison_path.read_bytes()
            word_count=_recognize_for_acceptance(scan_path,ocr_output)
            window.open_document(ocr_output)
        except Exception:
            import traceback

            report_output.write_text(traceback.format_exc(),encoding="utf-8")
            window.close()
            return 2
        state={"phase":"open","ticks":2400,"revision_before":None,
               "word_count":word_count}

        def fail_acceptance():
            _finish_acceptance(window,app,2)

        def finish_acceptance():
            try:
                state["ticks"]-=1
                if state["ticks"]<=0:
                    fail_acceptance()
                    return
                if state["phase"]=="open" and window.page_data is not None:
                    window.perform_search("材料")
                    state["phase"]="search"
                elif (state["phase"]=="search" and not window.busy
                        and window.search_results):
                    state["revision_before"]=window.session.revision
                    window.open_comparison(comparison_pdf)
                    state["phase"]="comparison"
                elif state["phase"]=="comparison":
                    dialog=window.comparison_dialog
                    if (dialog is not None
                            and dialog.difference_image.pixmap() is not None
                            and dialog.summary.text().startswith("相似度")):
                        report=_acceptance_report(
                            "材料",len(window.search_results),dialog.summary.text(),
                            state["revision_before"],window.session.revision,
                            state["word_count"])
                        _save_acceptance_preview(window,dialog,screenshot)
                        report_output.write_text(report,encoding="utf-8")
                        _finish_acceptance(window,app,0)
                        return
            except Exception:
                fail_acceptance()
                return
            QTimer.singleShot(50,finish_acceptance)

        QTimer.singleShot(50,finish_acceptance)
    elif args and args[0].lower().endswith(".pdf"):
        # 檔案總管「開啟方式」或拖曳到捷徑時傳入路徑，視窗顯示後再開啟。
        QTimer.singleShot(0,lambda:window.open_path(args[0]))
    return app.exec()

if __name__=="__main__":
    raise SystemExit(main())
