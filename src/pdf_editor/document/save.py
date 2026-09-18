import os
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
import pymupdf
from pdf_editor.errors import EditorError, BatchPublishError
from pdf_editor.persistent_overlays import embed_workspace

# 覆蓋原檔前保留的備份數量上限與保存期限。
BACKUP_LIMIT_BYTES = 500_000_000
BACKUP_KEEP_DAYS = 60


def backup_folder():
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    return base / "LocalPDFEditor" / "backups"


def backup_original(path, folder=None):
    """覆蓋原檔前把目前檔案複製到備份資料夾，回傳備份路徑。

    來源不存在或備份失敗時回傳 None；備份失敗不應阻擋儲存，
    但呼叫端可據此提醒使用者。
    """
    path = Path(path)
    if not path.is_file():
        return None
    folder = Path(folder or backup_folder())
    try:
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = folder / f"{stamp}_{path.stem}.pdf"
        index = 1
        while target.exists():
            target = folder / f"{stamp}-{index}_{path.stem}.pdf"
            index += 1
        shutil.copy2(path, target)
    except OSError:
        return None
    prune_backups(folder)
    return target


def prune_backups(folder=None, limit=BACKUP_LIMIT_BYTES, keep_days=BACKUP_KEEP_DAYS):
    """刪除過期或超過總容量的備份，回傳刪除數量。"""
    folder = Path(folder or backup_folder())
    try:
        files = [(item.stat().st_mtime, item.stat().st_size, item)
            for item in folder.glob("*.pdf") if item.is_file()]
    except OSError:
        return 0
    deadline = (datetime.now() - timedelta(days=keep_days)).timestamp()
    removed = 0
    total = sum(size for _mtime, size, _item in files)
    for mtime, size, item in sorted(files):
        if mtime >= deadline and total <= limit:
            break
        try:
            item.unlink()
        except OSError:
            continue
        total -= size
        removed += 1
    return removed

def same_file(a, b):
    a, b = Path(a), Path(b)
    return a.resolve() == b.resolve() or (a.exists() and b.exists() and os.path.samefile(a, b))

def validated_bytes(pdf):
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        count = doc.page_count
        if count == 0:
            raise EditorError("EMPTY", "無法儲存空白文件。")
        out = doc.tobytes(garbage=4, deflate=True)
    with pymupdf.open(stream=out, filetype="pdf") as check:
        if check.page_count != count:
            raise EditorError("VERIFY", "輸出文件驗證失敗。")
        for p in check:
            p.get_displaylist()
    return out

def write_pdf(pdf, target, overwrite=False, sources=(), allow_source=False):
    target = Path(target)
    if not allow_source and any(same_file(target, s) for s in sources):
        raise EditorError("SOURCE", "請另存新檔，不能覆寫來源文件。")
    if target.exists() and not overwrite:
        raise EditorError("EXISTS", "檔案已存在，請更換檔名。")
    temp = None
    try:
        data = validated_bytes(pdf)
        fd, name = tempfile.mkstemp(prefix=".pdf-editor-", suffix=".tmp", dir=target.parent)
        temp = Path(name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temp, target)
        else:
            # Windows rename 不會覆寫競爭期間出現的目標。
            os.rename(temp, target)
        return target
    except EditorError:
        raise
    except Exception as exc:
        raise EditorError("SAVE_FAILED", "儲存失敗，請檢查空間、路徑權限及檔案是否被占用。") from exc
    finally:
        if temp:
            temp.unlink(missing_ok=True)

def save_as(session, target, overwrite=False, allow_source=False):
    session._check_edit()
    revision = session.revision
    fingerprint = session.history.current[2]
    pdf = embed_workspace(session.pdf, session.overlays) if session.overlays else session.pdf
    result = write_pdf(pdf, target, overwrite, (session.source,), allow_source)
    if session.revision == revision:
        session.saved_fingerprint = fingerprint
    return result

def publish_batch(documents, targets, sources=()):
    targets = tuple(Path(t) for t in targets)
    if len(documents) != len(targets) or len({str(t.resolve()).casefold() for t in targets}) != len(targets):
        raise EditorError("TARGETS", "輸出檔名重複或數量不符。")
    for t in targets:
        if t.exists() or any(same_file(t, s) for s in sources):
            raise EditorError("EXISTS", "輸出檔案已存在或與來源相同。")
    staged, completed = [], []
    try:
        for pdf, target in zip(documents, targets):
            data = validated_bytes(pdf)
            fd, name = tempfile.mkstemp(prefix=".pdf-editor-", dir=target.parent)
            staged.append(Path(name))
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        for temp, target in zip(staged, targets):
            os.rename(temp, target)
            completed.append(target)
        return tuple(completed)
    except Exception as exc:
        raise BatchPublishError("批次輸出未全部完成，請檢查已完成清單。", completed) from exc
    finally:
        for temp in staged:
            temp.unlink(missing_ok=True)

