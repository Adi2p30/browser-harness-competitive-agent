"""prod_pages.py, the admin app's production pages, against a fake prod.client session: the requests inbox, add a
program, pull, and publish with its confirm hash."""
import csv
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-workspace"))
sys.path.insert(0, str(ROOT / "webapp"))
pytest.importorskip("flask")
pytest.importorskip("yaml")
pytest.importorskip("bs4")
requests = pytest.importorskip("requests")
openpyxl = pytest.importorskip("openpyxl")

import app as webapp  # noqa: E402
from prod import client, diff  # noqa: E402

HEAD = ["University Name", "School/College Name", "Program Name", "Website Link", "Average GMAT", "Ranking (QS)"]
URL = "https://purdue.edu/hrm"


class Response:
    def __init__(self, status, data):
        self.status_code, self.data, self.text = status, data, json.dumps(data)

    def json(self):
        return self.data


class Session:
    """Answers each (method, path) with its next canned reply (or raises it) and records the call."""

    def __init__(self):
        self.headers, self.replies, self.calls = {}, {}, []

    def reply(self, method, path, *replies):
        self.replies.setdefault((method, path), []).extend(replies)

    def request(self, method, url, json=None, params=None, timeout=None):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        self.calls.append((method, path, json))
        reply = self.replies[(method, path)].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def posts(self, path):
        return [body for method, p, body in self.calls if method == "POST" and p == path]


@pytest.fixture
def admin(tmp_path, monkeypatch):
    """The admin app with a configured client on a fake session, and no workbook unless a test sets one."""
    monkeypatch.setattr(client, "ENV", tmp_path / "no-dotenv")
    monkeypatch.setenv("PROD_URL", "https://prod.test")
    monkeypatch.setenv("PROD_ADMIN_KEY", "k1")
    monkeypatch.delenv("KNOWLEDGE_DIR", raising=False)
    app = webapp.create_app("admin")
    state = {"xlsx": None}
    app.config.update(WORKBOOK=lambda: state["xlsx"], PROD_SESSION=Session())
    with app.test_client() as c:
        c.session, c.state = app.config["PROD_SESSION"], state
        page = c.get("/admin/programs/new").get_data(as_text=True)  # a real page hands out the token
        c.token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        yield c


def post(c, url, **form):
    return c.post(url, data={**form, "csrf_token": c.token})


REQUESTS = [{"id": 1, "sheet": "MS HRM", "university": "CLARK UNIVERSITY", "program": "MSHRM",
             "link": "https://clarku.edu/hrm", "note": "a peer", "submitted_at": "2026-11-20T10:00:00Z",
             "status": "new", "claimed_by": None, "admin_note": None, "program_id": None},
            {"id": 2, "sheet": "MS HRM", "university": "BOSTON COLLEGE", "program": "", "link": "", "note": "",
             "submitted_at": "2026-11-21T10:00:00Z", "status": "claimed", "claimed_by": "bo", "admin_note": None,
             "program_id": None}]


def test_inbox_lists_requests(admin):
    admin.session.reply("GET", "/api/requests", Response(200, {"requests": REQUESTS}))
    body = admin.get("/admin/requests").get_data(as_text=True)
    assert "#1 CLARK UNIVERSITY" in body and "a peer" in body and "claimed · bo" in body
    assert body.count(">Claim</button>") == 1 and body.count(">Add as program</button>") == 2
    assert body.index("#2 BOSTON COLLEGE") < body.index("#1 CLARK UNIVERSITY")  # newest first


def test_inbox_unconfigured_or_unreachable(admin, monkeypatch):
    monkeypatch.delenv("PROD_URL")
    r = admin.get("/admin/requests")
    assert r.status_code == 200 and "PROD_URL missing" in r.get_data(as_text=True)
    monkeypatch.setenv("PROD_URL", "https://prod.test")
    admin.session.reply("GET", "/api/requests", requests.ConnectionError("connection refused"))
    body = admin.get("/admin/requests").get_data(as_text=True)
    assert "Can't reach the production server" in body and "connection refused" in body
    assert "k1" not in body


def test_claim_and_reject(admin):
    admin.session.reply("POST", "/api/requests/1/claim", Response(200, {"claimed": True}),
                        Response(409, {"error": "claimed by bo"}))
    r = post(admin, "/admin/requests/1/claim")
    assert r.status_code == 302 and "claimed+request" in r.headers["Location"]
    r = post(admin, "/admin/requests/1/claim")
    assert "error=" in r.headers["Location"] and "claimed+by+bo" in r.headers["Location"]
    admin.session.reply("POST", "/api/requests/2/status", Response(200, {"ok": True}))
    r = post(admin, "/admin/requests/2/reject", note=" not a peer ", status="claimed")
    assert "Rejected+request" in r.headers["Location"] and "status=claimed" in r.headers["Location"]
    assert admin.session.posts("/api/requests/2/status") == [{"status": "rejected", "admin_note": "not a peer"}]


def test_add_as_program_then_pull(admin, tmp_path):
    admin.state["xlsx"] = tmp_path / "EDIT Test 2026-2027.xlsx"
    admin.session.reply("GET", "/api/requests", Response(200, {"requests": REQUESTS}))
    admin.session.reply("POST", "/api/programs", Response(201, {"id": 42}))
    admin.session.reply("GET", "/api/export", Response(200, {"version": 5, "programs": [], "facts": []}))
    r = post(admin, "/admin/requests/1/add")
    assert r.status_code == 302 and "Added+program+42" in r.headers["Location"]
    assert admin.session.posts("/api/programs") == [{"sheet": "MS HRM", "university": "CLARK UNIVERSITY",
                                                      "program": "MSHRM", "link": "https://clarku.edu/hrm",
                                                      "request_id": 1}]
    assert json.loads(client.snapshot_path(admin.state["xlsx"]).read_text())["version"] == 5


def test_add_program_form(admin):
    assert post(admin, "/admin/programs/new", sheet="MS HRM").status_code == 400  # no university
    admin.session.reply("POST", "/api/programs", Response(201, {"id": 43}))
    body = post(admin, "/admin/programs/new", sheet="MS HRM", university=" CLARK UNIVERSITY ").get_data(as_text=True)
    assert "Added program 43" in body and "--program 43" in body and "no snapshot was saved" in body
    assert admin.session.posts("/api/programs") == [{"sheet": "MS HRM", "university": "CLARK UNIVERSITY"}]


def test_pull_button(admin, tmp_path):
    admin.state["xlsx"] = tmp_path / "EDIT Test 2026-2027.xlsx"
    admin.session.reply("GET", "/api/export", Response(200, {"version": 6, "programs": [], "facts": []}))
    r = post(admin, "/admin/pull", back="publish")
    assert r.headers["Location"].startswith("/admin/publish") and "version+6" in r.headers["Location"]


# --- publish ---

def fact(field, value, version=1):
    return {"program_id": 7, "field": field, "value": value, "status": "found", "cycle": "2026-2027",
            "source_url": URL, "quote": f"quote {field}", "origin": "pipeline", "run": None, "reviewed_by": None,
            "version": version}


def snapshot(ranking="#7"):
    return {"version": 3, "facts": [fact("Ranking (QS)", ranking)],
            "programs": [{"id": 7, "sheet": "MS HRM", "university": "PURDUE UNIVERSITY", "school": "", "program": "",
                          "link": "", "ipeds_unitid": None, "active": 1, "legacy_key": "MS HRM:2", "added_by": "",
                          "added_at": ""}]}


def make_workbook(tmp_path: Path) -> Path:
    """Purdue (row 2) with a pipeline run that found GMAT 650 and QS #5; Clark (row 3) isn't in production."""
    path = tmp_path / "EDIT Test 2026-2027.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "MS HRM"
    for row in (HEAD, ["PURDUE UNIVERSITY", "Daniels", "HRM", URL], ["CLARK UNIVERSITY", "", "MSHRM", ""]):
        wb.active.append(row)
    wb.save(path)
    run = tmp_path / f"{path.stem}_field_pipeline" / "runs" / "2026-11-06_000000"
    run.mkdir(parents=True)
    cols = ["sheet", "row", "university", "program", "field", "status", "cycle", "value", "source_url", "quote",
            "detail"]
    with open(run / "decisions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for field, value in (("Average GMAT", "650"), ("Ranking (QS)", "#5")):
            w.writerow({"sheet": "MS HRM", "row": 2, "field": field, "status": "found", "cycle": "2026-2027",
                        "value": value, "source_url": URL, "quote": f"quote {field}"})
    return path


@pytest.fixture
def workbook(admin, tmp_path):
    admin.state["xlsx"] = make_workbook(tmp_path)
    return admin.state["xlsx"]


def shown(admin):
    """The publish page for snapshot(): (body, the digest it carries)."""
    admin.session.reply("GET", "/api/export", Response(200, snapshot()))
    body = admin.get("/admin/publish").get_data(as_text=True)
    return body, re.search(r'name="digest" value="([0-9a-f]{64})"', body).group(1)


def test_publish_page_shows_the_diff(admin, workbook):
    body, _ = shown(admin)
    assert "production version 3" in body and "2 changes" in body
    assert re.search(r"Average GMAT</td><td><span[^>]*>new</span></td>\s*<td></td><td><strong>650", body)
    assert re.search(r"Ranking \(QS\)</td><td><span[^>]*>changed</span></td>\s*<td>#7</td><td><strong>#5", body)
    assert "Not in production (1)" in body and "CLARK UNIVERSITY (row 3)" in body
    assert not admin.session.posts("/api/publish")


def test_publish_confirm_posts_and_shows_conflicts(admin, workbook):
    _, digest = shown(admin)
    snap = snapshot()
    changes = diff.local_changes(workbook, snap)
    gmat, ranking = sorted(changes, key=lambda c: c["field"])
    theirs = {**fact("Ranking (QS)", "#6", 4), "by": "bo", "at": "2026-11-23T09:00:00Z"}
    admin.session.reply("GET", "/api/export", Response(200, snap), Response(200, {**snap, "version": 4}))
    admin.session.reply("POST", "/api/publish", Response(200, {
        "version": 4, "applied": [gmat], "conflicts": [{"change": ranking, "current": theirs}], "rebuilt": True}))
    r = post(admin, "/admin/publish", digest=digest, note=" fall run ")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and admin.session.posts("/api/publish") == [{"changes": changes, "note": "fall run"}]
    assert "Published: version 4" in body and "1 change applied, 1 conflict." in " ".join(body.split())
    assert re.search(r"Ranking \(QS\)</td><td><strong>#6</strong></td><td>bo</td>\s*<td>2026-11-23 09:00:00</td>"
                     r"<td>#5</td>", body)
    assert json.loads(client.snapshot_path(workbook).read_text())["version"] == 4  # pulled after publishing


def test_publish_with_a_stale_hash_shows_the_new_diff(admin, workbook):
    _, digest = shown(admin)
    admin.session.reply("GET", "/api/export", Response(200, snapshot(ranking="#6")))  # someone published meanwhile
    r = post(admin, "/admin/publish", digest=digest)
    body = r.get_data(as_text=True)
    assert r.status_code == 409 and not admin.session.posts("/api/publish")
    assert "Nothing was published" in body and "<td>#6</td><td><strong>#5" in body
    assert re.search(r'name="digest" value="([0-9a-f]{64})"', body).group(1) != digest


def test_publish_needs_the_token(admin, workbook):
    assert admin.post("/admin/publish", data={"digest": "x"}).status_code == 403
    assert not admin.session.calls
