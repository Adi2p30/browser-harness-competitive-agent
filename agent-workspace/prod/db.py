"""Production SQLite layer: programs, one fact per (program, field), every change to a fact, versioned publishes,
school requests and admin keys.

Connections are in autocommit mode; multi-statement writes take `BEGIN IMMEDIATE`, so a publish's conflict check
and its writes are atomic against other processes. The current version is the newest publish (0 before any).
"""
import hashlib
import hmac
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ORIGINS = ("pipeline", "review", "workbook", "prior-year")
REQUEST_STATUSES = ("new", "claimed", "done", "rejected")
NEW_FIELDS = ("value", "status", "cycle", "source_url", "quote", "origin", "run", "reviewed_by")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS programs(
    id INTEGER PRIMARY KEY, sheet TEXT NOT NULL, university TEXT NOT NULL, school TEXT, program TEXT, link TEXT,
    ipeds_unitid INTEGER, active INTEGER NOT NULL DEFAULT 1, legacy_key TEXT UNIQUE, added_by TEXT, added_at TEXT);
CREATE TABLE IF NOT EXISTS facts(
    program_id INTEGER NOT NULL REFERENCES programs(id), field TEXT NOT NULL, value TEXT, status TEXT NOT NULL,
    cycle TEXT, source_url TEXT, quote TEXT, origin TEXT NOT NULL CHECK(origin IN {ORIGINS}), run TEXT,
    reviewed_by TEXT, version INTEGER NOT NULL, PRIMARY KEY(program_id, field));
CREATE TABLE IF NOT EXISTS fact_history(
    id INTEGER PRIMARY KEY, program_id INTEGER, field TEXT, old_value TEXT, old_status TEXT, new_value TEXT,
    new_status TEXT, source_url TEXT, quote TEXT, version INTEGER, by TEXT, at TEXT);
CREATE INDEX IF NOT EXISTS fact_history_fact ON fact_history(program_id, field);
CREATE TABLE IF NOT EXISTS publishes(
    version INTEGER PRIMARY KEY, at TEXT, by TEXT, n_applied INTEGER, n_conflicts INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS requests(
    id INTEGER PRIMARY KEY, sheet TEXT, university TEXT, program TEXT, link TEXT, note TEXT, submitted_at TEXT,
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN {REQUEST_STATUSES}), claimed_by TEXT, admin_note TEXT,
    program_id INTEGER);
CREATE TABLE IF NOT EXISTS admins(
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, key_hash TEXT NOT NULL, created_at TEXT, revoked_at TEXT);
"""


def now() -> str:
    """Current UTC time, ISO-8601 to the second: 2026-11-24T15:04:05Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    """Open the database. Read-only connections refuse writes; otherwise the file and schema are created."""
    path = Path(path)
    if readonly:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, isolation_level=None, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _tx(conn: sqlite3.Connection, mode: str = "IMMEDIATE"):
    conn.execute(f"BEGIN {mode}")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def version(conn: sqlite3.Connection) -> int:
    """Newest published version, 0 before the first publish."""
    return conn.execute("SELECT COALESCE(MAX(version), 0) FROM publishes").fetchone()[0]


def snapshot(conn: sqlite3.Connection) -> dict:
    """Every program and fact at one consistent version."""
    with _tx(conn, "DEFERRED"):
        return {"version": version(conn),
                "programs": [dict(r) for r in conn.execute("SELECT * FROM programs ORDER BY id")],
                "facts": [dict(r) for r in conn.execute("SELECT * FROM facts ORDER BY program_id, field")]}


def create_program(conn: sqlite3.Connection, *, sheet: str, university: str, school: str = "", program: str = "",
                   link: str = "", legacy_key: str | None = None, ipeds_unitid: int | None = None,
                   added_by: str = "") -> int:
    """Insert a program and return its id."""
    cur = conn.execute(
        "INSERT INTO programs(sheet, university, school, program, link, ipeds_unitid, legacy_key, added_by, added_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (sheet, university, school, program, link, ipeds_unitid, legacy_key, added_by, now()))
    return cur.lastrowid


def _fact(conn: sqlite3.Connection, program_id: int, field: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM facts WHERE program_id=? AND field=?", (program_id, field)).fetchone()


UPSERT_FACT = (f"INSERT INTO facts(program_id, field, {', '.join(NEW_FIELDS)}, version)"
               f" VALUES({', '.join('?' * (len(NEW_FIELDS) + 3))})"
               f" ON CONFLICT(program_id, field) DO UPDATE SET"
               f" {', '.join(f'{k}=excluded.{k}' for k in NEW_FIELDS)}, version=excluded.version")


def _matches(row: sqlite3.Row | None, old: dict | None) -> bool:
    """Whether the stored fact is still what the change was based on."""
    if row is None or old is None:
        return row is None and old is None
    return (row["value"], row["status"]) == (old["value"], old["status"])


def _current(conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict | None:
    """A conflicting fact as it stands, with who last changed it and when."""
    if row is None:
        return None
    last = conn.execute("SELECT by, at FROM fact_history WHERE program_id=? AND field=? ORDER BY id DESC LIMIT 1",
                        (row["program_id"], row["field"])).fetchone()
    return {**dict(row), "by": last and last["by"], "at": last and last["at"]}


def _text(x) -> bool:
    return x is None or isinstance(x, str)


def _check(changes: list[dict], conn: sqlite3.Connection) -> None:
    """Raise ValueError for any malformed change, unknown program or repeated (program, field)."""
    known = {r[0] for r in conn.execute("SELECT id FROM programs")}
    seen = set()
    for c in changes:
        if not isinstance(c, dict):
            raise ValueError(f"change must be a dict: {c!r}")
        pid, field, old, new = c.get("program_id"), c.get("field"), c.get("old"), c.get("new")
        where = f"change {pid!r}/{field!r}"
        if not isinstance(pid, int) or isinstance(pid, bool) or pid not in known:
            raise ValueError(f"{where}: unknown program_id")
        if not isinstance(field, str) or not field:
            raise ValueError(f"{where}: field must be a non-empty string")
        if (pid, field) in seen:
            raise ValueError(f"{where}: appears twice in one batch")
        seen.add((pid, field))
        if "old" not in c or old is not None and not (
                isinstance(old, dict) and {"value", "status"} <= old.keys() and _text(old["value"])
                and _text(old["status"])):
            raise ValueError(f"{where}: old must be None or {{value, status}} with string or null values")
        if not isinstance(new, dict) or not all(_text(new.get(k)) for k in NEW_FIELDS):
            raise ValueError(f"{where}: new must be a dict of strings or nulls over {NEW_FIELDS}")
        if not new.get("status") or new.get("origin") not in ORIGINS:
            raise ValueError(f"{where}: new needs a status and an origin in {ORIGINS}")


BACKUPS_KEPT = 50


def backup(conn: sqlite3.Connection, backup_dir: Path) -> Path:
    """Copy the database to backup_dir/prod-v<version>-<UTC time>.sqlite, named from the copy's own version so it
    is never wrong, and never over an existing file. Keeps the newest BACKUPS_KEPT copies."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    tmp = backup_dir / f".prod-backup-{secrets.token_hex(8)}.tmp"
    try:
        dest = sqlite3.connect(tmp)
        try:
            conn.backup(dest)  # outside any write transaction: backing up inside one blocks
            v = version(dest)
        finally:
            dest.close()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path, n = backup_dir / f"prod-v{v}-{stamp}.sqlite", 1
        while path.exists():
            n += 1
            path = backup_dir / f"prod-v{v}-{stamp}-{n}.sqlite"
        tmp.rename(path)
    finally:
        tmp.unlink(missing_ok=True)
    old = sorted(backup_dir.glob("prod-v*.sqlite"), key=lambda p: (p.stat().st_mtime_ns, p.name))
    for p in old[:-BACKUPS_KEPT]:
        p.unlink()
    return path


def apply_changes(conn: sqlite3.Connection, changes: list[dict], by: str, note: str = "",
                  backup_dir: Path | None = None) -> dict:
    """Publish changes in one transaction. Each applies only if the stored (value, status) still equals its `old`
    (None: no fact yet); otherwise it is returned as a conflict with the current fact and who last changed it.
    The version goes up by one when anything applied. Bad input raises ValueError before anything is written."""
    _check(changes, conn)
    if backup_dir is not None:
        backup(conn, backup_dir)
    applied, conflicts = [], []
    with _tx(conn):
        at = now()
        current_version = version(conn)
        new_version = current_version + 1
        for c in changes:
            row, new = _fact(conn, c["program_id"], c["field"]), c["new"]
            if not _matches(row, c["old"]):
                conflicts.append({"change": c, "current": _current(conn, row)})
                continue
            conn.execute(UPSERT_FACT, (c["program_id"], c["field"], *(new.get(k) for k in NEW_FIELDS), new_version))
            conn.execute(
                "INSERT INTO fact_history(program_id, field, old_value, old_status, new_value, new_status,"
                " source_url, quote, version, by, at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (c["program_id"], c["field"], row and row["value"], row and row["status"], new.get("value"),
                 new["status"], new.get("source_url"), new.get("quote"), new_version, by, at))
            applied.append(c)
        if applied:
            conn.execute("INSERT INTO publishes(version, at, by, n_applied, n_conflicts, note) VALUES(?,?,?,?,?,?)",
                         (new_version, at, by, len(applied), len(conflicts), note))
    return {"version": new_version if applied else current_version, "applied": applied, "conflicts": conflicts}


# --- requests: a school/program someone wants added ---

def add_request(conn: sqlite3.Connection, *, sheet: str, university: str, program: str = "", link: str = "",
                note: str = "") -> int:
    """File a new request and return its id."""
    return conn.execute("INSERT INTO requests(sheet, university, program, link, note, submitted_at)"
                        " VALUES(?,?,?,?,?,?)", (sheet, university, program, link, note, now())).lastrowid


def list_requests(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    """Requests, oldest first, optionally only those with `status`."""
    if status is None:
        return [dict(r) for r in conn.execute("SELECT * FROM requests ORDER BY id")]
    return [dict(r) for r in conn.execute("SELECT * FROM requests WHERE status=? ORDER BY id", (status,))]


def claim_request(conn: sqlite3.Connection, request_id: int, admin: str) -> bool:
    """Claim a new request. True if `admin` now holds it (re-claiming your own is fine), False otherwise."""
    if conn.execute("UPDATE requests SET status='claimed', claimed_by=? WHERE id=? AND status='new'",
                    (admin, request_id)).rowcount:
        return True
    row = conn.execute("SELECT status, claimed_by FROM requests WHERE id=?", (request_id,)).fetchone()
    return bool(row) and row["status"] == "claimed" and row["claimed_by"] == admin


def set_request_status(conn: sqlite3.Connection, request_id: int, status: str, admin_note: str | None = None,
                       program_id: int | None = None) -> None:
    """Set a request's status. admin_note and program_id (the program it became) are kept when None; going back
    to 'new' releases the claim."""
    if status not in REQUEST_STATUSES:
        raise ValueError(f"status must be one of {REQUEST_STATUSES}")
    if not conn.execute(
            "UPDATE requests SET status=?1, admin_note=COALESCE(?2, admin_note), program_id=COALESCE(?3, program_id),"
            " claimed_by=CASE WHEN ?1='new' THEN NULL ELSE claimed_by END WHERE id=?4",
            (status, admin_note, program_id, request_id)).rowcount:
        raise ValueError(f"unknown request {request_id}")


# --- admins: one key each, stored as a sha256 hash ---

def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _issue(conn: sqlite3.Connection, name: str, allow_active: bool) -> str:
    key = secrets.token_urlsafe(32)
    with _tx(conn):
        row = conn.execute("SELECT revoked_at FROM admins WHERE name=?", (name,)).fetchone()
        if row and row["revoked_at"] is None and not allow_active:
            raise ValueError(f"admin {name!r} already exists")
        if row is None and allow_active:
            raise ValueError(f"no admin named {name!r}")
        conn.execute("INSERT INTO admins(name, key_hash, created_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE"
                     " SET key_hash=excluded.key_hash, created_at=excluded.created_at, revoked_at=NULL",
                     (name, _hash(key), now()))
    return key


def add_admin(conn: sqlite3.Connection, name: str) -> str:
    """Create an admin, or reissue a revoked one, and return their key. Only its hash is stored, so this is the one
    time it is shown. An active admin raises ValueError."""
    return _issue(conn, name, allow_active=False)


def rotate_admin(conn: sqlite3.Connection, name: str) -> str:
    """Replace an existing admin's key (revoking the old one) and return the new key."""
    return _issue(conn, name, allow_active=True)


def revoke_admin(conn: sqlite3.Connection, name: str) -> bool:
    """Revoke an active admin's key. False if there is no such active admin."""
    return bool(conn.execute("UPDATE admins SET revoked_at=? WHERE name=? AND revoked_at IS NULL",
                             (now(), name)).rowcount)


def list_admins(conn: sqlite3.Connection) -> list[dict]:
    """Every admin, revoked ones included, without key hashes."""
    return [dict(r) for r in conn.execute("SELECT id, name, created_at, revoked_at FROM admins ORDER BY id")]


def check_key(conn: sqlite3.Connection, key: str | None) -> str | None:
    """Name of the active admin holding `key`, else None."""
    if not key:
        return None
    h = _hash(key)
    for row in conn.execute("SELECT name, key_hash FROM admins WHERE revoked_at IS NULL"):
        if hmac.compare_digest(h, row["key_hash"]):
            return row["name"]
    return None
