"""app.create_app's two modes: what public mode serves (and doesn't), /download.xlsx, the knowledge folder, and the
admin app's host and CSRF checks."""
import io
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-workspace"))
sys.path.insert(0, str(ROOT / "webapp"))
sys.path.insert(0, str(ROOT / "deploy"))
pytest.importorskip("flask")
pytest.importorskip("yaml")
pytest.importorskip("bs4")
openpyxl = pytest.importorskip("openpyxl")

import api  # noqa: E402
import app as webapp  # noqa: E402
import serve  # noqa: E402
from prod import db, exporter  # noqa: E402

ADMIN_URLS = ["/run", "/run/r1", "/run/r1/events", "/run/r1/stop", "/health", "/health/run", "/health/recheck",
              "/review", "/review/decide", "/review/undo", "/review/name", "/review/export", "/review/import",
              "/admin/requests", "/admin/requests/1/claim", "/admin/requests/1/add", "/admin/requests/1/reject",
              "/admin/publish", "/admin/programs/new", "/admin/pull"]
HEAD = ["University Name", "School/College Name", "Program Name", "Website Link", "Credit Hours"]


@pytest.fixture
def site(tmp_path, monkeypatch):
    """The public app on a tmp database (one program with one fact), template and knowledge folder."""
    template = tmp_path / "template.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "MS HRM"
    wb.active.append(HEAD)
    wb.active.append(["PURDUE UNIVERSITY", "Daniels", "HRM", "https://purdue.edu/hrm"])
    wb.save(template)
    knowledge = tmp_path / "docs" / "okf" / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "index.md").write_text("---\ntitle: Knowledge\n---\n# Knowledge\n\n- [MS HRM](ms-hrm.md)\n")
    (knowledge / "ms-hrm.md").write_text("---\ntitle: MS HRM\nstatus: stable\n---\nPurdue: 36 credit hours\n")
    monkeypatch.setenv("PROD_DB", str(tmp_path / "prod.sqlite"))
    monkeypatch.setenv("PROD_TEMPLATE", str(template))
    monkeypatch.setenv("KNOWLEDGE_DIR", str(knowledge))
    api.reset_rate_limit()
    conn = db.connect(tmp_path / "prod.sqlite")
    key = db.add_admin(conn, "ana")
    pid = db.create_program(conn, sheet="MS HRM", university="PURDUE UNIVERSITY", legacy_key="MS HRM:2")
    db.apply_changes(conn, [{"program_id": pid, "field": "Credit Hours", "old": None,
                             "new": {"value": "36", "status": "found", "cycle": "2026-2027",
                                     "source_url": "https://purdue.edu/hrm", "quote": "36 credit hours",
                                     "origin": "pipeline"}}], "ana")
    conn.close()
    app = webapp.create_app("public")
    with app.test_client() as c:
        c.key, c.app, c.tmp = key, app, tmp_path
        yield c


def test_public_mode_has_no_admin_routes(site):
    for url in ADMIN_URLS:
        assert site.get(url).status_code == 404, url
        assert site.post(url).status_code == 404, url
    serve.check_public(site.app)
    assert not site.app.debug


def test_public_home_request_form_and_api(site):
    r = site.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/knowledge")
    assert "MS HRM" in site.get("/request").get_data(as_text=True)  # sheet names from the template
    r = site.post("/request", data={"sheet": "MS HRM", "university": "CLARK UNIVERSITY"})
    assert r.status_code == 302 and "sent=1" in r.headers["Location"]
    assert site.get("/api/export").status_code == 401
    snap = site.get("/api/export", headers={"Authorization": f"Bearer {site.key}"}).get_json()
    assert snap["version"] == 1 and snap["programs"][0]["university"] == "PURDUE UNIVERSITY"


def test_public_nav_and_knowledge_from_knowledge_dir(site):
    body = site.get("/knowledge").get_data(as_text=True)
    assert "Competitive analysis" in body and 'href="/request"' in body and 'href="/download.xlsx"' in body
    for admin in ('href="/"', "/run", "/review", "/health", "/admin"):
        assert admin not in body
    assert "Purdue: 36 credit hours" in site.get("/knowledge/ms-hrm.md").get_data(as_text=True)
    assert site.get("/knowledge/../../template.xlsx").status_code == 404


def test_download_streams_the_export_and_writes_nothing_in_the_repo(site, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(site.tmp / "t"))
    (site.tmp / "t").mkdir()
    repo = lambda: ({p.name for p in ROOT.iterdir()}, {p: p.stat().st_mtime_ns for p in ROOT.glob("*.xlsx")})
    repo_before = repo()
    builds = []
    real = exporter.export_workbook
    monkeypatch.setattr(exporter, "export_workbook", lambda *a: builds.append(a) or real(*a))
    r = site.get("/download.xlsx")
    assert r.status_code == 200 and r.mimetype.endswith("spreadsheetml.sheet")
    assert "attachment" in r.headers["Content-Disposition"] and "v1.xlsx" in r.headers["Content-Disposition"]
    ws = openpyxl.load_workbook(io.BytesIO(r.data))["MS HRM"]
    assert ws.cell(2, HEAD.index("Credit Hours") + 1).value == "36"
    assert site.get("/download.xlsx").data == r.data and len(builds) == 1  # the same version: built once
    assert list((site.tmp / "t").iterdir()) == []  # the temp folder is gone
    assert repo() == repo_before
    assert [p.name for p in site.tmp.rglob("*.xlsx")] == ["template.xlsx"]


def test_download_503_when_not_set_up(site, monkeypatch):
    monkeypatch.delenv("PROD_DB")
    assert site.get("/download.xlsx").status_code == 503
    monkeypatch.setenv("PROD_DB", str(site.tmp / "missing.sqlite"))
    assert site.get("/download.xlsx").status_code == 503


def test_knowledge_dir_must_be_the_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path / "elsewhere"))
    with pytest.raises(ValueError, match="docs/okf/knowledge"):
        webapp.create_app("public")
    with pytest.raises(ValueError, match="APP_MODE"):
        webapp.create_app("debug")


@pytest.fixture
def admin(tmp_path, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_DIR", raising=False)
    monkeypatch.delenv("PROD_DB", raising=False)
    monkeypatch.setattr(webapp, "_xlsx", lambda: None)
    app = webapp.create_app("admin")
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["csrf"] = "tok"
        yield c


def test_admin_forms_need_the_csrf_token(admin):
    assert admin.post("/review/name", data={"reviewer": "Ana"}).status_code == 403
    assert admin.post("/review/name", data={"reviewer": "Ana", "csrf_token": "nope"}).status_code == 403
    assert admin.post("/run/r1/stop").status_code == 403
    assert admin.post("/admin/pull").status_code == 403
    assert admin.post("/review/name", data={"reviewer": "Ana", "csrf_token": "tok"}).status_code == 302
    assert admin.post("/review/name", data={"reviewer": "Ana"}, headers={"X-CSRF-Token": "tok"}).status_code == 302


def test_admin_checks_origin_and_host(admin):
    form = {"reviewer": "Ana", "csrf_token": "tok"}
    assert admin.post("/review/name", data=form, headers={"Origin": "http://evil.test"}).status_code == 403
    assert admin.post("/review/name", data=form, headers={"Referer": "http://localhost:5001/x"}).status_code == 403
    assert admin.post("/review/name", data=form, headers={"Origin": "http://localhost"}).status_code == 302
    assert admin.get("/review", headers={"Host": "rebound.evil.test"}).status_code == 400  # DNS rebinding
    assert admin.get("/review", headers={"Host": "127.0.0.1:5000"}).status_code == 200


def test_admin_api_is_exempt_and_nav_lists_admin_pages(admin):
    assert admin.post("/request", data={"sheet": "MS HRM", "university": "X"}).status_code == 503  # no PROD_DB
    body = admin.get("/knowledge").get_data(as_text=True)
    for link in ('href="/"', 'href="/review"', 'href="/health"', 'href="/admin/requests"', 'href="/admin/publish"'):
        assert link in body
    assert "/download.xlsx" not in body and 'href="/request"' not in body


def test_no_debug_anywhere():
    for path in [*ROOT.glob("webapp/*.py"), ROOT / "deploy" / "serve.py", ROOT / "run-webapp.sh"]:
        text = path.read_text()
        assert "debug=True" not in text and "use_debugger" not in text, path
    assert not webapp.app.debug and not webapp.create_app("public").debug
