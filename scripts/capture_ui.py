from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from pdf_editor.ui.main_window import MainWindow
import sys

if __name__ == "__main__":
    app=QApplication([])
    app.setApplicationName("墨頁 PDF")
    window=MainWindow()
    window.show()
    window.open_document(Path(sys.argv[1]))
    output=Path(sys.argv[2])
    def capture():
        if window.page_data is None:
            QTimer.singleShot(100,capture)
            return
        window.select_run(next(r for r in window.page_data["runs"] if "品質" in r.text))
        window.grab().save(str(output))
        window.close()
        app.quit()
    QTimer.singleShot(300,capture)
    QTimer.singleShot(30000,app.quit)
    app.exec()

