from concurrent.futures import ProcessPoolExecutor
import multiprocessing
from PySide6.QtCore import QObject, QTimer

def _invoke(fn, args):
    try:
        return True, fn(*args)
    except Exception as exc:
        from pdf_editor.logs import get_logger
        code=getattr(exc,"code",None)
        if code is None:
            # 非預期例外保留完整堆疊，方便日後排查。
            get_logger().exception("背景工作 %s 發生未預期錯誤",getattr(fn,"__name__",fn))
        else:
            get_logger().warning("背景工作 %s 失敗：%s %s",getattr(fn,"__name__",fn),code,exc)
        return False, (code or "ERROR", str(exc),
            tuple(str(p) for p in getattr(exc,"completed",())))

class Jobs(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool=ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn"))
        self.pending=[]
        self.timer=QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(40)

    def submit(self, fn, args, success, failure):
        self.pending.append((self.pool.submit(_invoke,fn,args),success,failure))

    def poll(self):
        pending, self.pending=self.pending, []
        for future, success, failure in pending:
            if not future.done():
                self.pending.append((future,success,failure))
                continue
            try:
                ok,value=future.result()
            except Exception:
                from pdf_editor.logs import get_logger
                get_logger().exception("背景工作程序中斷")
                failure(("WORKER","背景工作中斷，請重試。",()))
                continue
            if ok:
                success(value)
            else:
                failure(value)

    def close(self):
        self.timer.stop()
        for f,_,_ in self.pending:
            f.cancel()
        self.pending.clear()
        self.pool.shutdown(wait=False,cancel_futures=True)

