"""prod.db: optimistic per-fact publishes, history, backups, requests and admin keys."""
import hashlib
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-workspace"))

from prod import db  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "prod.sqlite")
    db.create_program(c, sheet="MS HRM", university="PURDUE UNIVERSITY", legacy_key="MS HRM:2")
    db.create_program(c, sheet="MS HRM", university="CLARK UNIVERSITY")
    return c


def new(value, status="found", origin="review"):
    return {"value": value, "status": status, "cycle": "2026-2027", "source_url": "https://x.edu",
            "quote": f"it is {value}", "origin": origin, "run": None, "reviewed_by": "ana"}


def change(pid, field, old, value):
    return {"program_id": pid, "field": field, "old": old, "new": new(value)}


def facts(conn):
    return {(f["program_id"], f["field"]): f["value"] for f in db.snapshot(conn)["facts"]}


def counts(conn):
    return [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("facts", "fact_history", "publishes")]


def test_snapshot_shape(conn):
    snap = db.snapshot(conn)
    assert snap["version"] == 0 and snap["facts"] == []
    assert snap["programs"][0]["legacy_key"] == "MS HRM:2" and snap["programs"][1]["legacy_key"] is None
    assert snap["programs"][0]["active"] == 1 and snap["programs"][0]["added_at"].endswith("Z")


def test_non_overlapping_publishes_both_apply(conn):
    assert db.snapshot(conn)["version"] == 0  # both admins start from this
    a = db.apply_changes(conn, [change(1, "Credit Hours", None, "36")], by="ana")
    b = db.apply_changes(conn, [change(2, "Credit Hours", None, "30")], by="ben")
    assert (a["version"], b["version"]) == (1, 2) and not a["conflicts"] and not b["conflicts"]
    assert facts(conn) == {(1, "Credit Hours"): "36", (2, "Credit Hours"): "30"}
    assert db.snapshot(conn)["facts"][1]["version"] == 2


def test_overlap_reports_conflict_and_applies_the_rest(conn):
    db.apply_changes(conn, [change(1, "Credit Hours", None, "36")], by="ana")
    stale = change(1, "Credit Hours", None, "33")  # ben never saw ana's value
    res = db.apply_changes(conn, [stale, change(2, "Credit Hours", None, "30")], by="ben")
    assert res["version"] == 2 and res["applied"] == [change(2, "Credit Hours", None, "30")]
    [conflict] = res["conflicts"]
    assert conflict["change"] == stale
    cur = conflict["current"]
    assert (cur["value"], cur["status"], cur["by"], cur["version"]) == ("36", "found", "ana", 1) and cur["at"]
    assert facts(conn)[(1, "Credit Hours")] == "36"
    # based on the current value it applies
    ok = db.apply_changes(conn, [change(1, "Credit Hours", {"value": "36", "status": "found"}, "33")], by="ben")
    assert ok["version"] == 3 and not ok["conflicts"]


def test_history_and_publishes_rows(conn):
    db.apply_changes(conn, [change(1, "GMAT", None, "600")], by="ana", note="first")
    db.apply_changes(conn, [change(1, "GMAT", {"value": "600", "status": "found"}, "610")], by="ben")
    hist = [dict(r) for r in conn.execute("SELECT * FROM fact_history ORDER BY id")]
    assert [(h["old_value"], h["new_value"], h["version"], h["by"]) for h in hist] == [
        (None, "600", 1, "ana"), ("600", "610", 2, "ben")]
    pubs = [dict(r) for r in conn.execute("SELECT * FROM publishes ORDER BY version")]
    assert [(p["version"], p["by"], p["n_applied"], p["n_conflicts"], p["note"]) for p in pubs] == [
        (1, "ana", 1, 0, "first"), (2, "ben", 1, 0, "")]


def test_nothing_applied_keeps_version(conn):
    db.apply_changes(conn, [change(1, "GMAT", None, "600")], by="ana")
    res = db.apply_changes(conn, [change(1, "GMAT", None, "610")], by="ben")
    assert res["version"] == 1 and not res["applied"] and len(res["conflicts"]) == 1
    assert db.version(conn) == 1 and counts(conn) == [1, 1, 1]
    assert db.apply_changes(conn, [], by="ben")["version"] == 1


def test_backup_written(conn, tmp_path):
    db.apply_changes(conn, [change(1, "GMAT", None, "600")], by="ana")
    db.apply_changes(conn, [change(2, "GMAT", None, "550")], by="ana", backup_dir=tmp_path / "backups")
    [path] = (tmp_path / "backups").iterdir()
    assert path.name.startswith("prod-v1-") and path.name.endswith("Z.sqlite")
    backup = db.connect(path, readonly=True)
    assert db.version(backup) == 1 and facts(backup) == {(1, "GMAT"): "600"}


def test_backup_naming_and_pruning(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "BACKUPS_KEPT", 3)
    first, again = db.backup(conn, tmp_path), db.backup(conn, tmp_path)  # same version, same second
    assert first != again and first.exists() and first.name.startswith("prod-v0-")
    for i in range(4):
        db.apply_changes(conn, [change(1, f"F{i}", None, "x")], by="ana", backup_dir=tmp_path)
    names = sorted(p.name.split("-")[1] for p in tmp_path.glob("prod-v*.sqlite"))
    assert names == ["v1", "v2", "v3"] and not list(tmp_path.glob(".prod-backup-*"))


def test_concurrent_publish_waits_then_conflicts(tmp_path):
    path = tmp_path / "prod.sqlite"
    other = db.connect(path)
    pid = db.create_program(other, sheet="MS HRM", university="PURDUE UNIVERSITY")
    other.execute("BEGIN IMMEDIATE")
    db.connect(path).execute("SELECT 1")  # schema already exists: connecting needs no write lock
    other.execute("INSERT INTO facts(program_id, field, value, status, origin, version) VALUES(?,?,?,?,?,1)",
                  (pid, "GMAT", "600", "found", "review"))
    other.execute("INSERT INTO fact_history(program_id, field, new_value, new_status, version, by, at)"
                  " VALUES(?,?,?,?,1,'ben','2026-11-24T00:00:00Z')", (pid, "GMAT", "600", "found"))
    other.execute("INSERT INTO publishes(version, at, by, n_applied, n_conflicts) VALUES(1,'x','ben',1,0)")
    result = {}

    def publish():
        result.update(db.apply_changes(db.connect(path), [change(pid, "GMAT", None, "610")], by="ana"))

    t = threading.Thread(target=publish)
    t.start()
    time.sleep(0.5)
    assert t.is_alive() and not result  # blocked on ben's write lock
    other.execute("COMMIT")
    t.join(10)
    assert result["version"] == 1 and not result["applied"]
    [conflict] = result["conflicts"]
    assert (conflict["current"]["value"], conflict["current"]["by"]) == ("600", "ben")


def test_duplicate_pair_in_batch_rejected(conn):
    with pytest.raises(ValueError, match="twice"):
        db.apply_changes(conn, [change(1, "GMAT", None, "600"), change(1, "GMAT", None, "610")], by="ana")
    assert counts(conn) == [0, 0, 0]


def test_stored_null_matches_null_old(conn):
    missing = {**new(None, status="not-found"), "quote": None}
    db.apply_changes(conn, [{"program_id": 1, "field": "GMAT", "old": None, "new": missing}], by="ana")
    res = db.apply_changes(conn, [change(1, "GMAT", {"value": None, "status": "not-found"}, "600")], by="ben")
    assert res["version"] == 2 and not res["conflicts"] and facts(conn) == {(1, "GMAT"): "600"}


@pytest.mark.parametrize("bad", [
    {"program_id": 1, "old": None, "new": new("1")},  # no field
    {"program_id": 1, "field": "", "old": None, "new": new("1")},
    {"program_id": 1, "field": "GMAT", "new": new("1")},  # no old
    {"program_id": 1, "field": "GMAT", "old": {"value": "1"}, "new": new("1")},
    {"program_id": 1, "field": "GMAT", "old": "600", "new": new("1")},
    {"program_id": 1, "field": "GMAT", "old": None, "new": new(36)},  # JSON number
    {"program_id": 1, "field": "GMAT", "old": {"value": 36, "status": "found"}, "new": new("1")},
    {"program_id": 1, "field": "GMAT", "old": None, "new": new("1", origin="guess")},
    {"program_id": "1", "field": "GMAT", "old": None, "new": new("1")},
    {"program_id": [1], "field": "GMAT", "old": None, "new": new("1")},
    "not a change",
])
def test_malformed_change_rejected_before_backup(conn, tmp_path, bad):
    with pytest.raises(ValueError):
        db.apply_changes(conn, [change(2, "GMAT", None, "600"), bad], by="ana", backup_dir=tmp_path / "backups")
    assert counts(conn) == [0, 0, 0] and not (tmp_path / "backups").exists()


def test_path_with_spaces(tmp_path):
    path = tmp_path / "prod data" / "prod db.sqlite"
    db.create_program(db.connect(path), sheet="MS HRM", university="PURDUE UNIVERSITY")
    assert db.snapshot(db.connect(path, readonly=True))["programs"][0]["university"] == "PURDUE UNIVERSITY"


def test_unknown_program_raises_before_writing(conn, tmp_path):
    with pytest.raises(ValueError):
        db.apply_changes(conn, [change(1, "GMAT", None, "600"), change(99, "GMAT", None, "1")], by="ana",
                         backup_dir=tmp_path / "backups")
    assert counts(conn) == [0, 0, 0] and not (tmp_path / "backups").exists()


def test_failure_mid_transaction_rolls_back(conn):
    db.apply_changes(conn, [change(1, "GMAT", None, "600")], by="ana")
    conn.execute("CREATE TRIGGER boom BEFORE INSERT ON publishes BEGIN SELECT RAISE(ABORT, 'boom'); END")
    with pytest.raises(sqlite3.IntegrityError):
        db.apply_changes(conn, [change(1, "GMAT", {"value": "600", "status": "found"}, "610"),
                                change(2, "GMAT", None, "550")], by="ben")
    assert facts(conn) == {(1, "GMAT"): "600"} and counts(conn) == [1, 1, 1] and db.version(conn) == 1


def test_readonly_refuses_writes(conn, tmp_path):
    ro = db.connect(tmp_path / "prod.sqlite", readonly=True)
    assert len(db.snapshot(ro)["programs"]) == 2
    with pytest.raises(sqlite3.OperationalError):
        db.create_program(ro, sheet="x", university="y")
    with pytest.raises(sqlite3.OperationalError):
        db.apply_changes(ro, [change(1, "GMAT", None, "600")], by="ana")


def test_admin_keys(conn):
    key = db.add_admin(conn, "ana")
    assert db.check_key(conn, key) == "ana"
    assert db.check_key(conn, key + "x") is None and db.check_key(conn, None) is None and db.check_key(conn, "") is None
    stored = conn.execute("SELECT key_hash FROM admins WHERE name='ana'").fetchone()[0]
    assert stored == hashlib.sha256(key.encode()).hexdigest() and key not in stored
    assert "key_hash" not in db.list_admins(conn)[0]
    with pytest.raises(ValueError):
        db.add_admin(conn, "ana")
    assert db.revoke_admin(conn, "ana") and not db.revoke_admin(conn, "ana")
    assert db.check_key(conn, key) is None and db.list_admins(conn)[0]["revoked_at"]
    reissued = db.add_admin(conn, "ana")  # a revoked name gets a fresh key
    assert db.check_key(conn, reissued) == "ana" and db.check_key(conn, key) is None
    assert db.list_admins(conn)[0]["revoked_at"] is None and len(db.list_admins(conn)) == 1


def test_admin_rotate(conn):
    key = db.add_admin(conn, "ana")
    rotated = db.rotate_admin(conn, "ana")
    assert rotated != key and db.check_key(conn, rotated) == "ana" and db.check_key(conn, key) is None
    with pytest.raises(ValueError):
        db.rotate_admin(conn, "ben")


def test_request_claims(conn):
    rid = db.add_request(conn, sheet="MS HRM", university="MICHIGAN STATE", link="https://msu.edu")
    assert db.list_requests(conn, "new")[0]["id"] == rid
    assert db.claim_request(conn, rid, "ana") and db.claim_request(conn, rid, "ana")
    assert not db.claim_request(conn, rid, "ben") and not db.claim_request(conn, 999, "ben")
    assert db.list_requests(conn, "claimed")[0]["claimed_by"] == "ana"
    pid = db.create_program(conn, sheet="MS HRM", university="MICHIGAN STATE", added_by="ana")
    db.set_request_status(conn, rid, "done", admin_note="added", program_id=pid)
    r = db.list_requests(conn)[0]
    assert (r["status"], r["admin_note"], r["program_id"]) == ("done", "added", pid)
    assert not db.claim_request(conn, rid, "ben")
    db.set_request_status(conn, rid, "new")  # reopened: note and program kept, claim released
    r = db.list_requests(conn)[0]
    assert (r["status"], r["admin_note"], r["program_id"], r["claimed_by"]) == ("new", "added", pid, None)
    assert db.claim_request(conn, rid, "ben")
    with pytest.raises(ValueError):
        db.set_request_status(conn, rid, "lost")
    with pytest.raises(ValueError):
        db.set_request_status(conn, 999, "done")


def test_admins_cli(tmp_path):
    path = tmp_path / "prod.sqlite"

    def run(*args):
        return subprocess.run([sys.executable, "-m", "prod.admins", *args], cwd=ROOT / "agent-workspace",
                              capture_output=True, text=True)

    for cmd in (("list",), ("revoke", "ana"), ("rotate", "ana")):
        res = run(cmd[0], str(path), *cmd[1:])
        assert res.returncode == 1 and "no database" in res.stderr and not path.exists()
    added = run("add", str(path), "ana")
    assert added.returncode == 0 and "PROD_ADMIN_KEY" in added.stderr
    key = added.stdout.strip()
    assert db.check_key(db.connect(path), key) == "ana"
    assert "ana" in run("list", str(path)).stdout
    assert run("add", str(path), "ana").returncode == 1
    rotated = run("rotate", str(path), "ana")
    assert rotated.returncode == 0 and "PROD_ADMIN_KEY" in rotated.stderr
    assert db.check_key(db.connect(path), key) is None
    key = rotated.stdout.strip()
    assert db.check_key(db.connect(path), key) == "ana"
    assert run("revoke", str(path), "ana").returncode == 0
    assert "revoked" in run("list", str(path)).stdout and run("revoke", str(path), "ana").returncode == 1
    assert db.check_key(db.connect(path), key) is None
