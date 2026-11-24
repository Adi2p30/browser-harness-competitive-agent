"""Routing-level checks for app.py, with field_pipeline itself stubbed out (no subprocess, no
browser, no RCAC call) — just the Flask routing/gating logic: the run form stays open while a
run is going (multiple runs), and a run started while another is active gets its own --db."""
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("yaml")
pytest.importorskip("openpyxl")
pytest.importorskip("bs4")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as webapp  # noqa: E402
from runner import Run  # noqa: E402


class _FakeProcess:
    def send_signal(self, *_):
        pass


def _fake_run(command):
    run = Run("r" + str(len(webapp._runs)), command, _FakeProcess())
    run.status = "running"
    return run


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(webapp, "_rcac_key_missing", lambda: None)
    calls = []

    def fake_start_run(xlsx, agent_workspace, **kwargs):
        calls.append(kwargs)
        return _fake_run([str(xlsx), "--db", str(kwargs["db"])] if kwargs.get("db") else [str(xlsx)])

    monkeypatch.setattr(webapp, "start_run", fake_start_run)
    webapp._runs.clear()
    with webapp.app.test_client() as c:
        yield c, calls


def test_index_shows_program_label_not_sheet(client):
    c, _ = client
    body = c.get("/").get_data(as_text=True)
    assert "Program(s)" in body
    assert "Sheet(s)" not in body


def test_second_run_while_first_running_gets_isolated_db(client):
    c, calls = client
    r1 = c.post("/run", data={"sheet": "Online MS Economics"})
    assert r1.status_code == 302
    r2 = c.post("/run", data={"sheet": "Online MBA"})
    assert r2.status_code == 302  # not redirected back to run 1 — both are allowed to run
    assert len(webapp._runs) == 2
    assert calls[0]["db"] is None        # first run: shared default cache, as before
    assert calls[1]["db"] is not None    # second run started while the first is active: isolated

    body = c.get("/").get_data(as_text=True)
    assert body.count('class="status running"') == 2


def _health_check(tmp_path, monkeypatch, recheck_rows):
    """A fake workbook with one finished source check folder."""
    xlsx = tmp_path / "EDIT Competitive Analysis Research 2026-2027.xlsx"
    xlsx.write_bytes(b"")
    d = tmp_path / f"{xlsx.stem}_field_pipeline" / "health" / "2026-12-01_090000"
    d.mkdir(parents=True)
    (d / "health.csv").write_text(
        "sheet,row,university,program,field,status,value,result,url,final_url,quote,snippet,score,detail\n"
        "MS HRM,3,GWU,HRM,Tuition,found,\"$1,250\",changed,https://x.edu/t,https://x.edu/t,\"$1,250 per credit\","
        "\"$1,310 per credit\",0.9,\"now 1,310 (was 1,250)\"\n"
        "MS HRM,3,GWU,HRM,Deadline,found,Jan 5,ok,https://x.edu/d,https://x.edu/d,Jan 5,,,\n")
    (d / "recheck.csv").write_text("sheet,row,university,program,field,result,url\n" + recheck_rows)
    monkeypatch.setattr(webapp, "_xlsx", lambda: xlsx)
    return xlsx, d


def test_health_page_lists_flagged_values(client, tmp_path, monkeypatch):
    c, _ = client
    _health_check(tmp_path, monkeypatch, "MS HRM,3,GWU,HRM,Tuition,changed,https://x.edu/t\n")
    body = c.get("/health").get_data(as_text=True)
    assert "Source health" in body and "$1,310 per credit" in body
    assert "Re-research flagged cells" in body and "changed 1" in body


def test_health_page_without_checks(client, tmp_path, monkeypatch):
    c, _ = client
    xlsx = tmp_path / "EDIT Competitive Analysis Research 2026-2027.xlsx"
    xlsx.write_bytes(b"")
    monkeypatch.setattr(webapp, "_xlsx", lambda: xlsx)
    assert "No source check yet" in c.get("/health").get_data(as_text=True)


def test_health_recheck_starts_pipeline_on_flagged_cells(client, tmp_path, monkeypatch):
    c, calls = client
    _, d = _health_check(tmp_path, monkeypatch, "MS HRM,3,GWU,HRM,Tuition,changed,https://x.edu/t\n")
    r = c.post("/health/recheck", data={"check": d.name, "browser": "local"})
    assert r.status_code == 302
    assert calls[-1]["recheck"] == d / "recheck.csv" and calls[-1]["overwrite"] is False


def test_health_start_blocked_while_a_run_is_going(client, tmp_path, monkeypatch):
    c, _ = client
    _health_check(tmp_path, monkeypatch, "")
    started = []
    monkeypatch.setattr(webapp, "start_health", lambda *a, **k: started.append(k) or _fake_run(["health"]))
    assert c.post("/health/run", data={"browser": "local", "all": "on"}).status_code == 302
    assert started[-1] == {"browser": "local", "links": False, "stale_days": 0}
    assert c.post("/health/run", data={"browser": "local"}).status_code == 409  # the fake health run is "running"


class _Path(type(Path())):  # a Path that can carry test notes
    pass


def _review_setup(tmp_path, monkeypatch, snapshot=True):
    """A queue of two cells of production program 7 (legacy row MS HRM:2), with the admin's snapshot pulled; or,
    without a snapshot, of workbook row MS HRM:2."""
    import json
    xlsx = _Path(tmp_path) / "EDIT Competitive Analysis Research 2026-2027.xlsx"
    xlsx.write_bytes(b"")
    snap = webapp.snapshot_path(xlsx)
    snap.parent.mkdir()
    if snapshot:
        snap.write_text(json.dumps({"version": 1, "facts": [], "programs": [
            {"id": 7, "sheet": "MS HRM", "university": "PURDUE", "school": "", "program": "HRM", "link": "",
             "active": 1, "legacy_key": "MS HRM:2"}]}))
    ref = 7 if snapshot else "MS HRM:2"
    base = {"program_id": 7 if snapshot else None, "sheet": "MS HRM", "row": 2, "university": "PURDUE",
            "program": "HRM",
            "status": "bad-value",
            "quote": "Average GMAT 650", "url": "https://purdue.edu/hrm", "detail": "names 2019-2020", "snippet": "",
            "why": "a value was found but failed a check (names 2019-2020)", "yesno": False,
            "run": "r1", "last_year": "640", "last_year_url": "", "program_url": "https://purdue.edu/hrm"}
    items = [{**base, "id": f"{ref}|Average GMAT", "field": "Average GMAT", "group": "candidate", "value": "650"},
             {**base, "id": f"{ref}|Ranking (QS)", "field": "Ranking (QS)", "group": "ranking", "value": "",
              "status": "not-found", "quote": "", "url": ""}]
    monkeypatch.setattr(webapp, "_xlsx", lambda: xlsx)
    asked = []

    def fake_queue(x, run):
        asked.append(run)
        done = webapp.review.load_reviews(x, webapp.load_snapshot(x))
        return {"items": [i for i in items if (ref, i["field"]) not in done], "run": run, "filled_elsewhere": 2,
                "reviewed": 0, "cells": {(ref, i["field"]) for i in items}, "reviews": done}

    monkeypatch.setattr(webapp, "_queue", fake_queue)
    monkeypatch.setattr(webapp.review, "list_runs", lambda x: [
        {"id": "2026-11-06_103455_fresh", "label": "2026-11-06 10:34 (fresh) · MS HRM · 4 rows"},
        {"id": "2026-10-23_124342_fresh", "label": "2026-10-23 12:43 (fresh) · MS HRM · 10 rows"}])
    xlsx.asked = asked
    return xlsx


def test_review_page_groups_and_candidate(client, tmp_path, monkeypatch):
    c, _ = client
    _review_setup(tmp_path, monkeypatch)
    body = c.get("/review").get_data(as_text=True)
    assert "Rejected value to check <span>1</span>" in body and "Accept 650" in body and "Last year" in body
    assert "Not ranked" in c.get("/review?group=ranking").get_data(as_text=True)


def test_review_decide_and_undo(client, tmp_path, monkeypatch):
    c, _ = client
    xlsx = _review_setup(tmp_path, monkeypatch)
    c.post("/review/name", data={"reviewer": "Ana"})
    r = c.post("/review/decide", data={"id": "7|Average GMAT", "decision": "accept", "group": "candidate"})
    assert r.status_code == 302 and "msg=" in r.headers["Location"]
    saved = reviews(xlsx)[(7, "Average GMAT")]
    assert (saved["value"], saved["reviewer"], saved["program_id"]) == ("650", "Ana", "7")
    assert "Accept 650" not in c.get("/review?group=candidate").get_data(as_text=True)
    assert 'name="id" value="7|Average GMAT"' in c.get("/review?group=reviewed").get_data(as_text=True)

    r = c.post("/review/decide", data={"id": "7|Ranking (QS)", "decision": "value", "value": ""})
    assert "error=" in r.headers["Location"]  # no value typed: nothing saved
    assert (7, "Ranking (QS)") not in reviews(xlsx)

    r = c.post("/review/undo", data={"id": "7|Average GMAT"})
    assert reviews(xlsx) == {} and "Average+GMAT+for+PURDUE" in r.headers["Location"]


def test_review_decide_and_undo_without_a_snapshot(client, tmp_path, monkeypatch):
    c, _ = client
    xlsx = _review_setup(tmp_path, monkeypatch, snapshot=False)
    r = c.post("/review/decide", data={"id": "MS HRM:2|Average GMAT", "decision": "accept", "group": "candidate"})
    assert r.status_code == 302 and "msg=" in r.headers["Location"]
    saved = reviews(xlsx)[("MS HRM:2", "Average GMAT")]
    assert (saved["value"], saved["row"], saved["program_id"]) == ("650", "2", "")
    assert 'value="MS HRM:2|Average GMAT"' in c.get("/review?group=reviewed").get_data(as_text=True)
    c.post("/review/undo", data={"id": "MS HRM:2|Average GMAT"})
    assert reviews(xlsx) == {}


def test_review_undo_of_a_decision_from_before_program_ids(client, tmp_path, monkeypatch):
    """A reviews.csv row without program_id is the program with its legacy key; the log gains the column."""
    c, _ = client
    xlsx = _review_setup(tmp_path, monkeypatch)
    path = webapp.review.reviews_path(xlsx)
    path.write_text("sheet,row,university,program,field,decision,value,source_url,quote,note,reviewer,at\n"
                    "MS HRM,2,PURDUE,HRM,Average GMAT,accept,650,,,,Bo,2026-11-01T00:00:00\n")
    assert reviews(xlsx)[(7, "Average GMAT")]["reviewer"] == "Bo"
    c.post("/review/undo", data={"id": "7|Average GMAT"})
    lines = path.read_text().splitlines()
    assert reviews(xlsx) == {} and lines[0].endswith(",reviewer,at,program_id")
    assert lines[1].endswith(",Bo,2026-11-01T00:00:00")  # the old row, unchanged
    assert path.with_name("reviews.csv.pre-program-id").exists()


def reviews(xlsx):
    return webapp.review.load_reviews(xlsx, webapp.load_snapshot(xlsx))


def test_review_export_and_import(client, tmp_path, monkeypatch):
    import io
    import openpyxl
    c, _ = client
    xlsx = _review_setup(tmp_path, monkeypatch)
    r = c.get("/review/export")
    assert r.status_code == 200
    book = openpyxl.load_workbook(io.BytesIO(r.data))
    ws = book["Review queue"]
    head = [x.value for x in ws[1]]
    for row in range(2, ws.max_row + 1):
        if ws.cell(row, head.index("Field") + 1).value == "Ranking (QS)":
            ws.cell(row, head.index("Decision") + 1).value = "not-ranked"
    out = io.BytesIO()
    book.save(out)
    out.seek(0)
    r = c.post("/review/import", data={"file": (out, "queue.xlsx")}, content_type="multipart/form-data")
    assert "Imported+1" in r.headers["Location"] or "Imported%201" in r.headers["Location"]
    assert head.index("Program ID") >= 0 and reviews(xlsx)[(7, "Ranking (QS)")]["value"] == "Not ranked"


def test_mdlite_tables_code_and_title():
    import mdlite
    html = mdlite.render("| College | Tuition |\n|---|---|\n| PURDUE | [$1,250](https://x.edu) |\n\n"
                         "```python\nround(a * b)  # x < y\n```\nSee `COMPUTED_TOTALS`.")
    assert '<div class="okf-table"><table>' in html
    assert '<div class="cell" title="$1,250"><a href="https://x.edu">$1,250</a></div>' in html
    assert '<pre class="code"><code>round(a * b)  # x &lt; y</code></pre>' in html
    assert "<code>COMPUTED_TOTALS</code>" in html and "```" not in html
    assert mdlite.drop_title("\n# MS HRM\n\nbody", "MS HRM") == "\nbody"
    assert mdlite.drop_title("# Other\nbody", "MS HRM") == "# Other\nbody"


def test_knowledge_pages_use_compact_layout(client):
    c, _ = client
    if not webapp.KNOWLEDGE.exists():
        pytest.skip("no OKF bundle built")
    body = c.get("/knowledge").get_data(as_text=True)
    assert 'class="page-knowledge"' in body and "<ul><li><a href=" in body  # a plain list of links
    assert body.count(">Knowledge</h1>") == 1  # the bundle's own '# Knowledge' heading is not repeated
    assert "zoom-bar" not in body  # the list of programs has no table to zoom
    page = next(webapp.KNOWLEDGE.glob("ms-*.md"), None)
    if page:
        body = c.get(f"/knowledge/{page.name}").get_data(as_text=True)
        assert 'class="zoom-bar"' in body and "knowledge.js" in body and 'class="okf-table"' in body


def test_run_refused_when_school_is_not_on_chosen_programs(client, monkeypatch):
    from field_pipeline.workbook import Row
    c, calls = client
    rows = [Row("1yr MBA", 2, "EMORY UNIVERSITY", "", "", []),
            Row("MS BAIM - Residential", 7, "CARNEGIE MELLON UNIVERSITY", "", "", [])]
    monkeypatch.setattr(webapp, "read_rows", lambda path, sheets=None, snapshot=None: ({"1yr MBA": [], "MS BAIM - Residential": []},
                                                                       rows))
    r = c.post("/run", data={"sheet": ["1yr MBA"], "only": ["CARNEGIE MELLON UNIVERSITY"]})
    assert r.status_code == 400 and not calls
    body = r.get_data(as_text=True)
    assert "Nothing to research" in body and "it is on: MS BAIM - Residential" in body
    assert c.post("/run", data={"sheet": ["MS BAIM - Residential"], "only": ["CARNEGIE MELLON UNIVERSITY"]}).status_code == 302
    assert calls[-1]["only"] == ["CARNEGIE MELLON UNIVERSITY"]


def test_review_is_by_run(client, tmp_path, monkeypatch):
    c, _ = client
    xlsx = _review_setup(tmp_path, monkeypatch)
    body = c.get("/review").get_data(as_text=True)
    assert xlsx.asked[-1] == "2026-11-06_103455_fresh"  # the newest run by default
    assert 'value="2026-11-06_103455_fresh" selected' in body and "All runs" in body
    assert "2 cells this" in body and "not listed" in body
    c.get("/review?run=2026-10-23_124342_fresh")
    assert xlsx.asked[-1] == "2026-10-23_124342_fresh"
    c.get("/review?run=all")
    assert xlsx.asked[-1] is None
    c.get("/review?run=nope")
    assert xlsx.asked[-1] == "2026-11-06_103455_fresh"  # unknown run: back to the newest
    r = c.post("/review/decide", data={"id": "7|Average GMAT", "decision": "accept",
                                       "run": "2026-10-23_124342_fresh", "group": "candidate"})
    assert "run=2026-10-23_124342_fresh" in r.headers["Location"]  # back to the same run's queue
    r = c.get("/review/export?run=2026-10-23_124342_fresh")
    assert "review_queue_2026-10-23_124342_fresh.xlsx" in r.headers["Content-Disposition"]
