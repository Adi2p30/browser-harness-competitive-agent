"""SQLite store shared by every stage. Pages are keyed by URL and reused across rows, sheets and runs.
With `fresh_since` (a --fresh run) anything stored before that time is invisible: pages are fetched again and
every field is researched again; what this run stores is still shared by later rows and later runs."""
import json
import sqlite3
import threading
import time
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "pipeline.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY, final_url TEXT, kind TEXT, title TEXT, text TEXT, links TEXT,
    via TEXT, status TEXT, fetched_at REAL);
CREATE TABLE IF NOT EXISTS candidates (
    row_key TEXT, url TEXT, grp TEXT, score REAL, source TEXT, PRIMARY KEY (row_key, url, grp));
CREATE TABLE IF NOT EXISTS results (
    row_key TEXT, field TEXT, grp TEXT, value TEXT, status TEXT, url TEXT, quote TEXT, detail TEXT, at REAL,
    cycle TEXT, PRIMARY KEY (row_key, field));
"""


class Store:
    def __init__(self, path: Path = DEFAULT_DB, fresh_since: float | None = None):
        self.since = fresh_since or 0.0
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        if "cycle" not in {c[1] for c in self.db.execute("PRAGMA table_info(results)")}:  # databases from before cycles
            self.db.execute("ALTER TABLE results ADD COLUMN cycle TEXT")
        self.lock = threading.Lock()

    # pages
    def page(self, url: str) -> dict | None:
        row = self.db.execute("SELECT url, final_url, kind, title, text, links, via, status FROM pages "
                              "WHERE url=? AND fetched_at >= ?", (url, self.since)).fetchone()
        if not row:
            return None
        keys = ("url", "final_url", "kind", "title", "text", "links", "via", "status")
        page = dict(zip(keys, row))
        page["links"] = json.loads(page["links"] or "[]")
        return page

    def save_page(self, page: dict) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?,?,?,?)",
                            (page["url"], page.get("final_url"), page.get("kind"), page.get("title"), page.get("text"),
                             json.dumps(page.get("links") or []), page.get("via"), page["status"], time.time()))
            self.db.commit()

    # discovery candidates
    def add_candidate(self, row_key: str, url: str, grp: str, score: float, source: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO candidates VALUES (?,?,?,?,?) ON CONFLICT(row_key, url, grp) DO UPDATE SET "
                            "score=max(score, excluded.score)", (row_key, url, grp, score, source))
            self.db.commit()

    def start_url(self, row_key: str) -> str | None:
        """The start link an earlier run used for this row (it may have been found by search)."""
        if self.since:
            return None
        row = self.db.execute("SELECT url FROM candidates WHERE row_key=? AND source='start'", (row_key,)).fetchone()
        return row[0] if row else None

    def row_pages(self, row_key: str) -> list[dict]:
        """Readable pages fetched for a row."""
        urls = [u for (u,) in self.db.execute("SELECT DISTINCT url FROM candidates WHERE row_key=?", (row_key,))]
        pages = [self.page(u) for u in urls]
        return [p for p in pages if p and p["status"] == "ok"]

    # results
    def save_result(self, row_key: str, field: str, grp: str, r: dict) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (row_key, field, grp, r.get("value"), r["status"], r.get("url"), r.get("quote"),
                             r.get("detail"), time.time(), r.get("cycle")))
            self.db.commit()

    def results(self, row_key: str) -> dict[str, dict]:
        keys = ("field", "grp", "value", "status", "url", "quote", "detail", "cycle")
        rows = self.db.execute(f"SELECT {', '.join(keys)} FROM results WHERE row_key=? AND at >= ?",
                               (row_key, self.since)).fetchall()
        return {r[0]: dict(zip(keys, r)) for r in rows}
