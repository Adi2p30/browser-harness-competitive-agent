"""prod.diff (what a laptop would publish) and prod.client (the admin's HTTP client and CLI), with no network."""
import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-workspace"))
pytest.importorskip("bs4")
pytest.importorskip("requests")
openpyxl = pytest.importorskip("openpyxl")

from prod import client, diff  # noqa: E402

HEAD = ["University Name", "School/College Name", "Program Name", "Website Link", "Credit Hours", "Average GMAT",
        "Ranking (QS)", "Average Post-Grad Salary", "City Trek Opportunities (yes/no)", "Female %"]
DECISION_COLUMNS = ["sheet", "row", "university", "program", "field", "status", "cycle", "value", "source_url", "quote",
                    "detail"]
REVIEW_COLUMNS = ["sheet", "row", "university", "program", "field", "decision", "value", "source_url", "quote", "note",
                  "reviewer", "at"]
URL = "https://purdue.edu/hrm"
RUN = "2026-11-06_000000"


def book(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MS HRM"
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    wb.save(path)


@pytest.fixture
def wb(tmp_path):
    """Purdue row 2 (pipeline + reviews), GWU row 3 (Credit Hours typed, GMAT from last year), Clark row 4 (not in
    production)."""
    path = tmp_path / "EDIT Test 2026-2027.xlsx"
    book(path, [["PURDUE UNIVERSITY", "Daniels", "HRM", URL], ["GEORGE WASHINGTON UNIVERSITY", "GWSB", "MHRM",
                "https://gwu.edu/mhrm", 36], ["CLARK UNIVERSITY", "", "MSHRM", "https://clarku.edu/hrm"]])
    book(tmp_path / "OLD Test 2025-2026.xlsx", [["GEORGE WASHINGTON UNIVERSITY", "", "MHRM", "", "", 700]])
    base = tmp_path / f"{path.stem}_field_pipeline"
    (base / "runs" / RUN).mkdir(parents=True)
    d = lambda row, field, status, value="": {
        "sheet": "MS HRM", "row": row, "university": "", "program": "", "field": field, "status": status, "cycle": "",
        "value": value, "source_url": URL if value else "", "quote": f"says {value}" if value else "", "detail": ""}
    with open(base / "runs" / RUN / "decisions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, DECISION_COLUMNS)
        w.writeheader()
        w.writerows([d(2, "Credit Hours", "found", " 36  credit hours "), d(2, "Average GMAT", "found", "650"),
                     d(2, "Ranking (QS)", "found", "#5"), d(2, "Average Post-Grad Salary", "found", "$80,000"),
                     d(3, "Credit Hours", "not-found"), d(4, "Credit Hours", "found", "30")])
    r = lambda field, decision, value="", reviewer="ana": {
        "sheet": "MS HRM", "row": 2, "university": "PURDUE UNIVERSITY", "program": "HRM", "field": field,
        "decision": decision, "value": value, "source_url": URL, "quote": "", "note": "", "reviewer": reviewer,
        "at": "2026-11-20T10:00:00"}
    with open(base / "reviews.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, REVIEW_COLUMNS)
        w.writeheader()
        w.writerows([r("Female %", "value", "40%"), r("Average Post-Grad Salary", "not-published", reviewer="bo"),
                     r("City Trek Opportunities (yes/no)", "not-published")])
    return path


def fact(pid, field, value, status="found"):
    return {"program_id": pid, "field": field, "value": value, "status": status, "cycle": "2026-2027",
            "source_url": URL, "quote": "", "origin": "pipeline", "run": None, "reviewed_by": None, "version": 1}


def program(pid, university, key):
    return {"id": pid, "sheet": "MS HRM", "university": university, "school": "", "program": "", "link": "",
            "ipeds_unitid": None, "active": 1, "legacy_key": key, "added_by": "", "added_at": ""}


@pytest.fixture
def snap():
    return {"version": 3,
            "programs": [program(7, "PURDUE UNIVERSITY", "MS HRM:2"), program(8, "GEORGE WASHINGTON UNIVERSITY",
                                                                             "MS HRM:3")],
            "facts": [fact(7, "Credit Hours", "36 credit hours"), fact(7, "Ranking (QS)", "#7"),
                      fact(7, "Average Post-Grad Salary", "$80,000"),
                      fact(7, "City Trek Opportunities (yes/no)", None, "not-published")]}


def test_local_changes(wb, snap):
    changes, unmapped = diff.compare(wb, snap)
    by = {(c["program_id"], c["field"]): c for c in changes}
    assert set(by) == {(7, "Average GMAT"), (7, "Ranking (QS)"), (7, "Female %"), (7, "Average Post-Grad Salary")}
    gmat = by[(7, "Average GMAT")]  # new fact: no old
    assert gmat["old"] is None and gmat["new"] == {
        "value": "650", "status": "found", "cycle": "2026-2027", "source_url": URL, "quote": "says 650",
        "origin": "pipeline", "run": RUN, "reviewed_by": None}
    assert by[(7, "Ranking (QS)")]["old"] == {"value": "#7", "status": "found"}  # changed: old from the snapshot
    assert by[(7, "Ranking (QS)")]["new"]["value"] == "#5"
    female = by[(7, "Female %")]["new"]
    assert (female["value"], female["status"], female["origin"], female["reviewed_by"]) == ("40%", "reviewed",
                                                                                          "review", "ana")
    hidden = by[(7, "Average Post-Grad Salary")]  # not-published review beats the pipeline value
    assert hidden["old"] == {"value": "$80,000", "status": "found"}
    assert hidden["new"]["value"] is None and hidden["new"]["status"] == "not-published"
    assert (hidden["new"]["origin"], hidden["new"]["reviewed_by"]) == ("review", "bo")
    # Credit Hours (whitespace normalised, same as the snapshot) and City Trek (already not-published) are unchanged;
    # GWU's typed Credit Hours ("entered") and last year's GMAT ("prior-year") are never published.
    assert not any(c["program_id"] == 8 for c in changes)
    assert [(p["university"], p["row"]) for p in unmapped] == [("CLARK UNIVERSITY", 4)]
    assert diff.local_changes(wb, snap) == changes


def test_kb_keeps_the_values_that_are_never_published(wb):
    """Guards the test above: GWU really has an entered and a prior-year fact in the local KB."""
    from knowledge_base.__main__ import build, versions
    kb = build(str(wb), str(diff.default_prior(wb)), versions(wb))
    gwu = next(p["id"] for p in kb["programs"] if p["row"] == 3)
    assert {f["f"]: f["s"] for f in kb["facts"] if f["p"] == gwu} == {"Credit Hours": "entered",
                                                                     "Average GMAT": "prior-year"}


def test_values_normalised():
    assert diff._new(36.0, "found", "", "", "", "pipeline")["value"] == "36"
    assert diff._new("—", "found", "", "", "", "pipeline")["value"] is None


def test_format_diff(wb, snap):
    out = diff.format_diff(diff.local_changes(wb, snap), snap).splitlines()
    assert out[0] == "MS HRM · PURDUE UNIVERSITY"
    assert {"  + Average GMAT: 650", "  ~ Ranking (QS): #7 → #5", "  - Average Post-Grad Salary: not published",
            "  + Female %: 40%"} <= set(out)
    assert out[-1] == "4 change(s) across 1 program(s)"


class Response:
    def __init__(self, status, data):
        self.status_code, self.data, self.text = status, data, json.dumps(data)

    def json(self):
        return self.data


class Session:
    """Answers each request with the next canned response for its (method, path) and records it."""

    def __init__(self, replies):
        self.headers, self.replies, self.calls = {}, replies, []

    def request(self, method, url, json=None, params=None, timeout=None):
        path = url.split("://", 1)[1].split("/", 1)[1]
        self.calls.append({"method": method, "path": "/" + path, "json": json, "params": params, "timeout": timeout,
                           "auth": self.headers.get("Authorization")})
        return self.replies[(method, "/" + path)].pop(0)


def test_client_sends_bearer_and_raises_server_error():
    s = Session({("GET", "/api/requests"): [Response(200, {"requests": []})],
                 ("POST", "/api/requests/4/claim"): [Response(409, {"error": "claimed by ana"})]})
    c = client.Client("https://prod.test/", "k1", session=s)
    assert c.requests("new") == []
    assert s.calls[0]["auth"] == "Bearer k1" and s.calls[0]["params"] == {"status": "new"}
    assert s.calls[0]["timeout"] >= 30
    with pytest.raises(client.ClientError, match="409 claimed by ana"):
        c.claim(4)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PROD_URL", "https://prod.test")
    monkeypatch.setenv("PROD_ADMIN_KEY", "secret-key")


def test_publish_cli_posts_the_diff(wb, snap, env, capsys):
    after = {**snap, "version": 4}
    s = Session({("GET", "/api/export"): [Response(200, snap), Response(200, after)],
                 ("POST", "/api/publish"): [Response(200, {"version": 4, "applied": ["x"] * 4, "conflicts": []})]})
    assert client.main(["publish", str(wb), "--yes", "--note", "fall run"], session=s) == 0
    post = next(c for c in s.calls if c["method"] == "POST")
    assert post["json"] == {"changes": diff.local_changes(wb, snap), "note": "fall run"}
    assert json.loads(client.snapshot_path(wb).read_text())["version"] == 4  # the fresh snapshot after publishing
    out = capsys.readouterr().out
    assert "4 applied, 0 conflict(s)" in out and "secret-key" not in out


def test_publish_conflicts_exit_2(wb, snap, env, capsys):
    mine = diff.local_changes(wb, snap)[0]
    theirs = {**fact(7, mine["field"], "#6"), "by": "bo", "at": "2026-11-23T09:00:00Z"}
    s = Session({("GET", "/api/export"): [Response(200, snap), Response(200, snap)],
                 ("POST", "/api/publish"): [Response(200, {"version": 4, "applied": [], "conflicts": [
                     {"change": mine, "current": theirs}]})]})
    assert client.main(["publish", str(wb), "--yes"], session=s) == 2
    out = capsys.readouterr().out
    assert f"/ {mine['field']}: theirs #6 (found) by bo at 2026-11-23T09:00:00Z; yours" in out


def test_publish_nothing_and_diff_needs_pull(wb, snap, env, capsys):
    s = Session({("GET", "/api/export"): [Response(200, snap)]})
    assert client.main(["diff", str(wb)], session=s) == 1
    assert "pull" in capsys.readouterr().err
    snap["facts"] += [fact(7, c["field"], c["new"]["value"], c["new"]["status"]) for c in diff.local_changes(wb, snap)]
    assert client.main(["publish", str(wb), "--yes"], session=s) == 0
    assert "Nothing to publish." in capsys.readouterr().out
    assert not any(c["method"] == "POST" for c in s.calls)
