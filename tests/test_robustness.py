"""縮圖排程與錯誤處理測試。"""
from concurrent.futures import Future

from pdf_editor.logs import log_path
from pdf_editor.ui.main_window import MainWindow
import pdf_editor.ui.main_window as main_window


def _close(window):
    if window.session is not None:
        window.session.saved_fingerprint = window.session.history.current[2]
    window.close()


def _record_thumbnail_jobs(window, monkeypatch):
    """攔截縮圖工作，記錄排程順序而不真正產生縮圖。"""
    calls = []
    original = window.jobs.submit

    def submit(fn, args, success, failure):
        if fn is main_window.thumbnail:
            calls.append((args[1], success, failure))
            return
        original(fn, args, success, failure)

    monkeypatch.setattr(window.jobs, "submit", submit)
    return calls


def test_thumbnails_prioritize_current_page_and_run_one_at_a_time(qtbot, multi_page_path, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        calls = _record_thumbnail_jobs(window, monkeypatch)
        window.open_document(multi_page_path)
        assert [call[0] for call in calls] == [0]
        window.page = 2
        window._thumb_queue = window.prioritized_thumbnails(window._thumb_queue)
        calls[0][1](b"")
        # 同時只送出一個縮圖工作，完成後接著處理離目前頁最近的頁面。
        assert [call[0] for call in calls] == [0, 2]
    finally:
        _close(window)


def test_single_page_edit_only_refreshes_that_thumbnail(qtbot, multi_page_path, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        calls = _record_thumbnail_jobs(window, monkeypatch)
        window.open_document(multi_page_path)
        for _ in range(3):
            calls[-1][1](b"")
        assert [call[0] for call in calls] == [0, 1, 2]
        window.queue_thumbnails((1,))
        assert [call[0] for call in calls] == [0, 1, 2, 1]
        calls[-1][1](b"")
        assert len(calls) == 4
    finally:
        _close(window)


def test_stale_thumbnail_result_is_requeued(qtbot, multi_page_path, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        calls = _record_thumbnail_jobs(window, monkeypatch)
        window.open_document(multi_page_path)
        window.session.revision += 1
        calls[0][1](b"")
        assert calls[-1][0] == 0
    finally:
        window.session.revision -= 1
        _close(window)


def test_thumbnail_failure_marks_item_and_continues(qtbot, multi_page_path, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        calls = _record_thumbnail_jobs(window, monkeypatch)
        window.open_document(multi_page_path)
        calls[0][2](("THUMB", "渲染失敗", ()))
        assert "縮圖產生失敗" in window.thumbs.item(0).toolTip()
        assert [call[0] for call in calls] == [0, 1]
    finally:
        _close(window)


def test_unexpected_errors_point_to_log_file(qtbot, monkeypatch):
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning",
        lambda parent, title, message: warnings.append((title, message)))
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        window.error(("ERROR", "list index out of range", ()))
        window.error(("GEOMETRY", "圖章位置或大小無效。", ()))
        assert warnings[0][0] == "發生未預期的錯誤"
        assert str(log_path()) in warnings[0][1]
        assert warnings[1] == ("無法完成操作", "圖章位置或大小無效。")
        assert "list index out of range" in log_path().read_text(encoding="utf-8")
    finally:
        _close(window)


def test_worker_crash_is_logged(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    try:
        failures = []
        future = Future()
        future.set_exception(RuntimeError("process died"))
        window.jobs.pending.append((future, lambda value: None, failures.append))
        window.jobs.poll()
        assert failures == [("WORKER", "背景工作中斷，請重試。", ())]
        assert "背景工作程序中斷" in log_path().read_text(encoding="utf-8")
    finally:
        _close(window)
