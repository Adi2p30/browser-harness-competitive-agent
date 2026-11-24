"""Append-only CSV logs (reviews.csv, runs.csv) that gain columns over time.

New columns always go last. A log started before a column existed keeps its rows byte for byte; only its header
line is replaced (atomically, after a one-time backup), so its old rows, and rows an older checkout still appends,
read the new columns as empty.
"""
import csv
import io
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # no cross-process lock where fcntl is missing (Windows)
    fcntl = None

BACKUP_SUFFIX = ".pre-program-id"  # the one header upgrade so far added program_id / programs


@contextmanager
def locked(path: Path):
    """An exclusive lock on a sidecar ".<name>.lock": the log itself is replaced, so it can't hold the lock."""
    if fcntl is None:
        yield
        return
    with open(path.with_name(f".{path.name}.lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _line(columns: list[str], end: str) -> str:
    out = io.StringIO()
    csv.writer(out, lineterminator=end).writerow(columns)
    return out.getvalue()


def replace(path: Path, data: bytes) -> None:
    """Write data to path atomically: a temp file in the same folder, then os.replace."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def append_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    """Append rows to a CSV log, holding the lock across any header upgrade and the append. A header that is an older
    form of `columns` (a prefix of it; a UTF-8 BOM is ignored) is upgraded first, keeping a copy as
    <name>.pre-program-id; any other header raises ValueError. Lines end as the log's header line does (CRLF, csv's
    default, for a new log)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path):
        end, open_line = "\r\n", False
        if path.exists():
            data = path.read_bytes()
            open_line = bool(data) and not data.endswith(b"\n")  # a hand-edited log saved without a final newline
            first, _, rest = data.partition(b"\n")
            end = "\r\n" if first.endswith(b"\r") else "\n"
            head = next(csv.reader([first.decode("utf-8-sig").rstrip("\r")]), [])
            if head != columns:
                if len(head) >= len(columns) or head != columns[:len(head)]:
                    raise ValueError(f"{path}: its columns {head} are not an older form of {columns}; "
                                     "fix its header line by hand")
                backup = path.with_name(path.name + BACKUP_SUFFIX)
                if not backup.exists():
                    shutil.copy2(path, backup)
                replace(path, _line(columns, end).encode("utf-8") + rest)
                open_line = open_line and bool(rest)
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            if open_line:
                f.write(end)
            w = csv.DictWriter(f, columns, lineterminator=end)
            if new:
                w.writeheader()
            w.writerows(rows)
