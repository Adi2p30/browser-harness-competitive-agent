"""webapp/api.py: the admin API over prod.db and the public request form, through the Flask test client."""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-workspace"))
sys.path.insert(0, str(ROOT / "webapp"))
pytest.importorskip("flask")
pytest.importorskip("yaml")
pytest.importorskip("bs4")
openpyxl = pytest.importorskip("openpyxl")

import api  # noqa: E402
import app as webapp  # noqa: E402
from prod import db  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROD_DB", str(tmp_path / "prod.sqlite"))
    monkeypatch.setenv("PROD_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("PROD_TEMPLATE", str(tmp_path / "missing.xlsx"))  # never the real workbook
    api.reset_rate_limit()
    conn = db.connect(tmp_path / "prod.sqlite")
    keys = {name: db.add_admin(conn, name) for name in ("ana", "ben")}
    db.create_program(conn, sheet="MS HRM", university="PURDUE UNIVERSITY", legacy_key="MS HRM:2")
    yield types.SimpleNamespace(conn=conn, keys=keys, tmp=tmp_path)
    conn.close()


@pytest.fixture
def client(env):
    with webapp.app.test_client() as c:
        yield c


def auth(env, name="ana"):
    return {"Authorization": f"Bearer {env.keys[name]}"}


def change(old, value):
    return {"program_id": 1, "field": "Credit Hours", "old": old,
            "new": {"value": value, "status": "found", "cycle": "2026-2027", "source_url": "https://x.edu",
                    "quote": f"it is {value}", "origin": "review", "run": None, "reviewed_by": "ana"}}


@pytest.fixture
def no_rebuild(monkeypatch):
    monkeypatch.setitem(sys.modules, "knowledge_base.from_db", None)  # import raises ImportError


def test_401_for_missing_bad_or_revoked_key(env, client):
    assert client.get("/api/export").status_code == 401
    bad = client.get("/api/export", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401 and "error" in bad.get_json()
    assert client.get("/api/export", headers={"Authorization": env.keys["ana"]}).status_code == 401  # no scheme
    db.revoke_admin(env.conn, "ana")
    assert client.get("/api/export", headers=auth(env)).status_code == 401
    assert client.get("/api/export", headers=auth(env, "ben")).status_code == 200


def test_export_returns_snapshot(env, client):
    snap = client.get("/api/export", headers=auth(env)).get_json()
    assert snap["version"] == 0 and snap["facts"] == []
    assert snap["programs"][0]["legacy_key"] == "MS HRM:2"


def test_publish_applies_then_reports_stale_old_as_conflict(env, client, no_rebuild):
    r = client.post("/api/publish", headers=auth(env), json={"changes": [change(None, "36")], "note": "first"})
    out = r.get_json()
    assert r.status_code == 200 and out["version"] == 1 and len(out["applied"]) == 1 and not out["conflicts"]
    assert tuple(env.conn.execute("SELECT by, note FROM publishes").fetchone()) == ("ana", "first")
    assert list((env.tmp / "backups").glob("prod-v0-*.sqlite"))
    stale = client.post("/api/publish", headers=auth(env, "ben"), json={"changes": [change(None, "30")]}).get_json()
    assert stale["version"] == 1 and not stale["applied"] and "rebuilt" not in stale
    assert stale["conflicts"][0]["current"]["value"] == "36" and stale["conflicts"][0]["current"]["by"] == "ana"


@pytest.mark.parametrize("body", [{}, {"changes": "x"}, {"changes": [change(None, "36")], "note": 5},
                                  {"changes": [change(None, "36"), change(None, "37")]},
                                  {"changes": [{**change(None, "36"), "program_id": 99}]}])
def test_publish_400_on_bad_input_writes_nothing(env, client, body):
    r = client.post("/api/publish", headers=auth(env), json=body)
    assert r.status_code == 400 and r.get_json()["error"]
    assert env.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    assert db.version(env.conn) == 0


def test_publish_succeeds_when_rebuild_unavailable(env, client, no_rebuild):
    out = client.post("/api/publish", headers=auth(env), json={"changes": [change(None, "36")]}).get_json()
    assert out["rebuilt"] is False and out["version"] == 1


def test_publish_rebuilds_knowledge(env, client, monkeypatch):
    calls = []
    module = types.ModuleType("knowledge_base.from_db")
    module.write_from_db = lambda conn, template, out: calls.append((db.version(conn), template, out))
    monkeypatch.setitem(sys.modules, "knowledge_base.from_db", module)
    out = client.post("/api/publish", headers=auth(env), json={"changes": [change(None, "36")]}).get_json()
    assert out["rebuilt"] is True and calls == [(1, None, env.tmp / "knowledge")]


def test_requests_claim_conflict_status_and_404(env, client):
    rid = db.add_request(env.conn, sheet="MS HRM", university="CLARK UNIVERSITY")
    assert [r["id"] for r in client.get("/api/requests?status=new", headers=auth(env)).get_json()["requests"]] == [rid]
    assert client.get("/api/requests?status=bogus", headers=auth(env)).status_code == 400
    assert client.post(f"/api/requests/{rid}/claim", headers=auth(env)).get_json() == {"claimed": True}
    taken = client.post(f"/api/requests/{rid}/claim", headers=auth(env, "ben"))
    assert taken.status_code == 409 and taken.get_json() == {"error": "claimed by ana"}
    assert client.get("/api/requests?status=new", headers=auth(env)).get_json()["requests"] == []
    r = client.post(f"/api/requests/{rid}/status", headers=auth(env), json={"status": "rejected", "admin_note": "dup"})
    assert r.get_json() == {"ok": True}
    row = db.list_requests(env.conn)[0]
    assert (row["status"], row["admin_note"]) == ("rejected", "dup")
    assert client.post(f"/api/requests/{rid}/status", headers=auth(env), json={"status": "gone"}).status_code == 400
    assert client.post("/api/requests/99/status", headers=auth(env), json={"status": "done"}).status_code == 404
    assert client.post("/api/requests/99/claim", headers=auth(env)).status_code == 404
    assert client.get("/api/nowhere", headers=auth(env)).get_json()["error"]


def test_create_program_from_request_marks_it_done(env, client):
    rid = db.add_request(env.conn, sheet="MS HRM", university="CLARK UNIVERSITY")
    r = client.post("/api/programs", headers=auth(env),
                    json={"sheet": "MS HRM", "university": "CLARK UNIVERSITY", "program": "MSHRM", "request_id": rid})
    assert r.status_code == 201
    pid = r.get_json()["id"]
    prog = env.conn.execute("SELECT * FROM programs WHERE id=?", (pid,)).fetchone()
    assert (prog["university"], prog["added_by"], prog["legacy_key"]) == ("CLARK UNIVERSITY", "ana", None)
    req = db.list_requests(env.conn)[0]
    assert (req["status"], req["program_id"]) == ("done", pid)
    assert client.post("/api/programs", headers=auth(env), json={"sheet": "MS HRM"}).status_code == 400
    assert client.post("/api/programs", headers=auth(env), json={"sheet": "MS HRM", "university": 7}).status_code == 400
    assert client.post("/api/programs", headers=auth(env),
                       json={"sheet": "MS HRM", "university": "X", "request_id": 99}).status_code == 404


def form(**kw):
    return {"sheet": "MS HRM", "university": "CLARK UNIVERSITY", "program": "", "link": "", "note": "",
            "website": "", **kw}


def test_public_request_stores_and_redirects(env, client):
    page = client.get("/request").get_data(as_text=True)
    assert 'name="website"' in page and '<input type="text" name="sheet"' in page  # no template: free text
    r = client.post("/request", data=form(note="please add"))
    assert r.status_code == 302 and r.headers["Location"].endswith("/request?sent=1")
    assert [(q["university"], q["note"], q["status"]) for q in db.list_requests(env.conn)] == \
        [("CLARK UNIVERSITY", "please add", "new")]
    assert "Thanks" in client.get("/request?sent=1").get_data(as_text=True)


def test_public_request_lists_template_sheets(env, client, monkeypatch):
    path = env.tmp / "EDIT Test 2026-2027.xlsx"
    book = openpyxl.Workbook()
    book.active.title = "MS HRM"
    book.active.append(["University Name", "School/College Name", "Program Name", "Website Link"])
    book.save(path)
    monkeypatch.setenv("PROD_TEMPLATE", str(path))
    assert "<option>MS HRM</option>" in client.get("/request").get_data(as_text=True)


def test_public_request_honeypot_dropped(env, client):
    r = client.post("/request", data=form(website="http://spam"))
    assert r.status_code == 302 and db.list_requests(env.conn) == []


def test_public_request_rate_limited(env, client):
    for _ in range(5):
        assert client.post("/request", data=form()).status_code == 302
    assert client.post("/request", data=form()).status_code == 429
    assert len(db.list_requests(env.conn)) == 5


def test_public_request_needs_university(env, client):
    r = client.post("/request", data=form(university="  "))
    assert r.status_code == 400 and "required" in r.get_data(as_text=True)
    assert db.list_requests(env.conn) == []


def test_503_without_prod_db(env, client, monkeypatch):
    monkeypatch.delenv("PROD_DB")
    for r in (client.get("/api/export", headers=auth(env)), client.get("/request"), client.post("/request", data=form())):
        assert r.status_code == 503 and r.get_json() == {"error": "PROD_DB not configured"}
    assert client.get("/knowledge").status_code == 200  # the rest of the app keeps working
