import sys
import multiprocessing

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
    elif args and args[0].lower().endswith(".pdf"):
        from pathlib import Path
        try:
            window.open_document(Path(args[0]))
        except Exception as exc:
            window.error((getattr(exc,"code","OPEN"),str(exc),()))
    return app.exec()

if __name__=="__main__":
    raise SystemExit(main())
