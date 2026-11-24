"""prod.diff (what a laptop would publish) and prod.client (the admin's HTTP client and CLI), with no network."""
import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-workspace"))
pytest.importorskip("bs4")
requests = pytest.importorskip("requests")
openpyxl = pytest.importorskip("openpyxl")

from prod import client, db, diff, importer  # noqa: E402

HEAD = ["University Name", "School/College Name", "Program Name", "Website Link", "Credit Hours", "Average GMAT",
        "Ranking (QS)", "Average Post-Grad Salary", "City Trek Opportunities (yes/no)", "Female %"]
DECISION_COLUMNS = ["sheet", "row", "university", "program", "field", "status", "cycle", "value", "source_url", "quote",
                    "detail"]
REVIEW_COLUMNS = ["sheet", "row", "university", "program", "field", "decision", "value", "source_url", "quote", "note",
                  "reviewer", "at"]
URL = "https://purdue.edu/hrm"
RUN = "2026-11-06_000000"
KEY = "secret-key"


def book(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MS HRM"
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    wb.save(path)


def decisions(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    d = lambda row, field, status, value="": {
        "sheet": "MS HRM", "row": row, "university": "", "program": "", "field": field, "status": status, "cycle": "",
        "value": value, "source_url": URL if value else "", "quote": f"quote {field}" if value else "", "detail": ""}
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, DECISION_COLUMNS)
        w.writeheader()
        w.writerows(d(*r) for r in rows)


@pytest.fixture
def wb(tmp_path):
    """Purdue row 2 (pipeline + reviews), GWU row 3 (Credit Hours typed, GMAT from last year), Clark row 4 (not in
    the snapshot fixture)."""
    path = tmp_path / "EDIT Test 2026-2027.xlsx"
    book(path, [["PURDUE UNIVERSITY", "Daniels", "HRM", URL], ["GEORGE WASHINGTON UNIVERSITY", "GWSB", "MHRM",
                "https://gwu.edu/mhrm", 36], ["CLARK UNIVERSITY", "", "MSHRM", "https://clarku.edu/hrm"]])
    book(tmp_path / "OLD Test 2025-2026.xlsx", [["GEORGE WASHINGTON UNIVERSITY", "", "MHRM", "", "", 700]])
    base = tmp_path / f"{path.stem}_field_pipeline"
    decisions(base / "runs" / RUN / "decisions.csv", [
        (2, "Credit Hours", "found", " 36  credit hours "), (2, "Average GMAT", "found", "650"),
        (2, "Ranking (QS)", "found", "#5"), (2, "Average Post-Grad Salary", "found", "$80,000"),
        (3, "Credit Hours", "not-found"), (4, "Credit Hours", "found", "30")])
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


def fact(pid, field, value, status="found", quote=None):
    return {"program_id": pid, "field": field, "value": value, "status": status, "cycle": "2026-2027",
            "source_url": URL, "quote": quote if quote is not None or value is None else f"quote {field}",
            "origin": "pipeline", "run": None, "reviewed_by": None, "version": 1}


def program(pid, university, key, name="", active=1):
    return {"id": pid, "sheet": "MS HRM", "university": university, "school": "", "program": name, "link": "",
            "ipeds_unitid": None, "active": active, "legacy_key": key, "added_by": "", "added_at": ""}


@pytest.fixture
def snap():
    return {"version": 3,
            "programs": [program(7, "PURDUE UNIVERSITY", "MS HRM:2"),
                         program(8, "GEORGE WASHINGTON UNIVERSITY", "MS HRM:3")],
            "facts": [fact(7, "Credit Hours", "36 credit hours"), fact(7, "Ranking (QS)", "#7"),
                      fact(7, "Average Post-Grad Salary", "$80,000"),
                      fact(7, "City Trek Opportunities (yes/no)", None, "not-published")]}


def test_local_changes(wb, snap):
    changes, unmapped = diff.compare(wb, snap)
    by = {(c["program_id"], c["field"]): c for c in changes}
    assert set(by) == {(7, "Average GMAT"), (7, "Ranking (QS)"), (7, "Female %"), (7, "Average Post-Grad Salary")}
    gmat = by[(7, "Average GMAT")]  # new fact: no old
    assert gmat["old"] is None and gmat["new"] == {
        "value": "650", "status": "found", "cycle": "2026-2027", "source_url": URL, "quote": "quote Average GMAT",
        "origin": "pipeline", "run": RUN, "reviewed_by": None}
    assert by[(7, "Ranking (QS)")]["old"] == {"value": "#7", "status": "found"}  # changed: old from the snapshot
    assert by[(7, "Ranking (QS)")]["new"]["value"] == "#5"
    female = by[(7, "Female %")]["new"]
    assert (female["value"], female["status"], female["origin"], female["reviewed_by"]) == ("40%", "reviewed",
                                                                                          "review", "ana")
    hidden = by[(7, "Average Post-Grad Salary")]  # not-published review beats the pipeline value
    assert hidden["old"] == {"value": "$80,000", "status": "found"}
    assert hidden["new"] == {"value": None, "status": "not-published", "cycle": "2026-2027", "source_url": URL,
                             "quote": None, "origin": "review", "run": None, "reviewed_by": "bo"}
    # Credit Hours (whitespace normalised, same as the snapshot) and City Trek (already not-published) are unchanged;
    # GWU's typed Credit Hours ("entered") and last year's GMAT ("prior-year") are never published.
    assert not any(c["program_id"] == 8 for c in changes)
    assert [(p["university"], p["row"]) for p in unmapped] == [("CLARK UNIVERSITY", 4)]
    assert "no production program" in unmapped[0]["reason"]
    assert diff.local_changes(wb, snap) == changes


def test_compared_fields(wb, snap):
    """A new quote, source or cycle is a change; a different run or reviewer alone is not."""
    facts = {f["field"]: f for f in snap["facts"]}
    facts["Credit Hours"]["quote"] = "older quote"
    snap["facts"].append({**fact(7, "Female %", "40%", "reviewed"), "quote": None, "run": "x",
                          "reviewed_by": "someone else"})
    changed = {c["field"]: c for c in diff.local_changes(wb, snap)}
    assert changed["Credit Hours"]["old"] == {"value": "36 credit hours", "status": "found"}
    assert "Female %" not in changed


def test_kb_keeps_the_values_that_are_never_published(wb):
    """Guards test_local_changes: GWU really has an entered and a prior-year fact in the local KB."""
    from knowledge_base.__main__ import build, versions
    kb = build(str(wb), str(importer.find_prior(wb)), versions(wb))
    gwu = next(p["id"] for p in kb["programs"] if p["row"] == 3)
    assert {f["f"]: f["s"] for f in kb["facts"] if f["p"] == gwu} == {"Credit Hours": "entered",
                                                                     "Average GMAT": "prior-year"}


def test_values_normalised():
    assert diff._new(36.0, "found", "", "", "", "pipeline")["value"] == "36"
    assert diff._new("—", "found", "", "", "", "pipeline")["value"] is None


def test_fresh_import_has_nothing_to_publish(wb):
    """Import, export, diff: no phantom changes (not-published reviews included) and every program mapped."""
    from field_pipeline.workbook import added_path
    added_path(wb).write_text("id,sheet,university,school,program,website_link,added_by,added_at,removed_at\n"
                              "1,MS HRM,BOSTON COLLEGE,,MS HRM,https://bc.edu/hrm,,,\n")
    decisions(wb.parent / f"{wb.stem}_field_pipeline" / "runs" / "2026-11-10_000000" / "decisions.csv",
              [(10001, "Credit Hours", "found", "32"), (2, "Average GMAT", "found", "660")])
    importer.import_workbook(wb, wb.parent / "prod.sqlite")
    snap = db.snapshot(db.connect(wb.parent / "prod.sqlite", readonly=True))
    assert {(f["field"], f["status"]) for f in snap["facts"] if f["value"] is None} == {
        ("Average Post-Grad Salary", "not-published"), ("City Trek Opportunities (yes/no)", "not-published")}
    assert diff.compare(wb, json.loads(json.dumps(snap))) == ([], [])


def test_program_added_through_the_api_matches_by_name(wb, snap):
    snap["programs"].append(program(9, "Clark  university", None, "mshrm"))
    changes, unmapped = diff.compare(wb, snap)
    assert unmapped == [] and {(c["program_id"], c["field"]) for c in changes if c["program_id"] == 9} == {
        (9, "Credit Hours")}
    snap["programs"].append(program(10, "CLARK UNIVERSITY", None, "MSHRM"))  # two candidates: no guess
    _, unmapped = diff.compare(wb, snap)
    assert [p["row"] for p in unmapped] == [4] and "more than one" in unmapped[0]["reason"]
    snap["programs"] = snap["programs"][:2] + [program(9, "CLARK UNIVERSITY", None, "MSHRM", active=0)]
    assert [p["row"] for p in diff.compare(wb, snap)[1]] == [4]  # inactive programs are not matched


def test_legacy_key_with_another_university_is_unmapped(wb, snap):
    snap["programs"][0]["legacy_key"], snap["programs"][1]["legacy_key"] = "MS HRM:3", "MS HRM:2"
    changes, unmapped = diff.compare(wb, snap)
    reasons = {p["university"]: p["reason"] for p in unmapped}
    assert reasons["PURDUE UNIVERSITY"] == "production row MS HRM:2 is GEORGE WASHINGTON UNIVERSITY"
    assert "GEORGE WASHINGTON UNIVERSITY" in reasons and changes == []


def test_format_diff(wb, snap):
    out = diff.format_diff(diff.local_changes(wb, snap), snap).splitlines()
    assert out[0] == "MS HRM · PURDUE UNIVERSITY"
    assert {"  + Average GMAT: 650", "  ~ Ranking (QS): #7 → #5", "  - Average Post-Grad Salary: not published",
            "  + Female %: 40%"} <= set(out)
    assert out[-1] == "4 change(s) across 1 program(s)"


class Response:
    def __init__(self, status, data=None, text=None):
        self.status_code, self.data = status, data
        self.text = json.dumps(data) if text is None else text

    def json(self):
        if self.data is None:
            raise ValueError("not JSON")
        return self.data


class Session:
    """Answers each request with the next canned response (or raises the canned exception) for its (method, path),
    and records it."""

    def __init__(self, replies):
        self.headers, self.replies, self.calls = {}, replies, []

    def request(self, method, url, json=None, params=None, timeout=None):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        self.calls.append({"method": method, "path": path, "json": json, "params": params, "timeout": timeout,
                           "auth": self.headers.get("Authorization")})
        reply = self.replies[(method, path)].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_client_sends_bearer_and_raises_server_error():
    s = Session({("GET", "/api/requests"): [Response(200, {"requests": []})],
                 ("POST", "/api/requests/4/claim"): [Response(409, {"error": "claimed by ana"})]})
    c = client.Client("https://prod.test/", "k1", session=s)
    assert c.requests("new") == []
    assert s.calls[0]["auth"] == "Bearer k1" and s.calls[0]["params"] == {"status": "new"}
    assert s.calls[0]["timeout"] >= 30
    with pytest.raises(client.ClientError, match="409 claimed by ana"):
        c.claim(4)


@pytest.mark.parametrize("reply, message", [
    (requests.ConnectionError("connection refused"), "GET /api/export: connection refused"),
    (Response(401, {"error": "bad key"}), "401 bad key"),
    (Response(502, text="<html>Bad Gateway</html>"), "502 <html>Bad Gateway</html>"),
    (Response(200, ["not", "a", "dict"]), "not a JSON object"),
    (Response(200, text="<html>login</html>"), "not a JSON object"),
    (requests.exceptions.InvalidHeader(f"Invalid header value b'Bearer {KEY}'"), "Bearer <key>"),
])
def test_client_errors(reply, message):
    c = client.Client("https://prod.test", KEY, session=Session({("GET", "/api/export"): [reply]}))
    with pytest.raises(client.ClientError, match=message) as e:
        c.export()
    assert KEY not in str(e.value)


def test_key_and_url_checks():
    for bad in (f"{KEY} x", f"{KEY}\n", f"{KEY}\x00", ""):
        with pytest.raises(client.ClientError) as e:
            client.Client("https://prod.test", bad, session=Session({}))
        assert KEY not in str(e.value) and "PROD_ADMIN_KEY" in str(e.value)
    with pytest.raises(client.ClientError, match="https"):
        client.Client("http://prod.test", KEY, session=Session({}))
    for ok in ("http://localhost:5000", "http://127.0.0.1:8000/", "https://prod.test"):
        client.Client(ok, KEY, session=Session({}))


def test_settings_from_dotenv(tmp_path, monkeypatch):
    dotenv = tmp_path / "dotenv"  # the real one is ../.env; the name only matters to client.ENV
    dotenv.write_text(f"OTHER=1\nPROD_URL = 'https://prod.test'\nPROD_ADMIN_KEY=\"{KEY}\"  \n")
    monkeypatch.setattr(client, "ENV", dotenv)
    for name in ("PROD_URL", "PROD_ADMIN_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert (client.setting("PROD_URL"), client.setting("PROD_ADMIN_KEY")) == ("https://prod.test", KEY)
    monkeypatch.setenv("PROD_URL", "  https://env.test ")  # the environment wins, stripped
    assert client.setting("PROD_URL") == "https://env.test"
    with pytest.raises(client.ClientError, match="NOPE missing"):
        client.setting("NOPE")


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "ENV", tmp_path / "no-dotenv")
    monkeypatch.setenv("PROD_URL", "https://prod.test")
    monkeypatch.setenv("PROD_ADMIN_KEY", KEY)


def published(n, conflicts=()):
    return Response(200, {"version": 4, "applied": ["x"] * n, "conflicts": list(conflicts)})


def test_publish_cli_posts_the_diff(wb, snap, env, capsys):
    after = {**snap, "version": 4}
    s = Session({("GET", "/api/export"): [Response(200, snap), Response(200, after)],
                 ("POST", "/api/publish"): [published(4)]})
    assert client.main(["publish", str(wb), "--yes", "--note", "fall run"], session=s) == 0
    post = next(c for c in s.calls if c["method"] == "POST")
    assert post["json"] == {"changes": diff.local_changes(wb, snap), "note": "fall run"}
    assert post["timeout"] == 300
    assert json.loads(client.snapshot_path(wb).read_text())["version"] == 4  # the fresh snapshot after publishing
    out = capsys.readouterr().out
    assert "4 applied, 0 conflict(s)" in out and KEY not in out


def test_publish_conflicts_exit_3(wb, snap, env, capsys):
    mine = diff.local_changes(wb, snap)[0]
    theirs = {**fact(7, mine["field"], "#6"), "by": "bo", "at": "2026-11-23T09:00:00Z"}
    s = Session({("GET", "/api/export"): [Response(200, snap), Response(200, snap)],
                 ("POST", "/api/publish"): [published(0, [{"change": mine, "current": theirs}])]})
    assert client.main(["publish", str(wb), "--yes"], session=s) == 3
    out = capsys.readouterr().out
    assert f"/ {mine['field']}: theirs #6 (found) by bo at 2026-11-23T09:00:00Z; yours" in out


def test_publish_when_the_refresh_fails(wb, snap, env, capsys):
    s = Session({("GET", "/api/export"): [Response(200, snap), requests.ConnectionError("gone")],
                 ("POST", "/api/publish"): [published(4)]})
    assert client.main(["publish", str(wb), "--yes"], session=s) == 0
    assert "published (version 4); couldn't refresh the snapshot, run pull" in capsys.readouterr().out
    assert json.loads(client.snapshot_path(wb).read_text())["version"] == 3


@pytest.mark.parametrize("answer", ["n", "", EOFError(), KeyboardInterrupt()])
def test_publish_declined(wb, snap, env, monkeypatch, capsys, answer):
    def reply(_):
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr("builtins.input", reply)
    s = Session({("GET", "/api/export"): [Response(200, snap)]})
    assert client.main(["publish", str(wb)], session=s) == 0
    assert "Not published." in capsys.readouterr().out
    assert not any(c["method"] == "POST" for c in s.calls)


def test_publish_nothing_and_diff_needs_pull(wb, snap, env, capsys):
    s = Session({("GET", "/api/export"): [Response(200, snap)]})
    assert client.main(["diff", str(wb)], session=s) == 1
    assert "pull" in capsys.readouterr().err
    stored = {(f["program_id"], f["field"]): f for f in snap["facts"]}  # the snapshot after these changes landed
    for c in diff.local_changes(wb, snap):
        stored[(c["program_id"], c["field"])] = {"program_id": c["program_id"], "field": c["field"], **c["new"],
                                                 "version": 4}
    snap["facts"] = list(stored.values())
    assert client.main(["publish", str(wb), "--yes"], session=s) == 0
    assert "Nothing to publish." in capsys.readouterr().out
    assert not any(c["method"] == "POST" for c in s.calls)


def test_request_commands_map_their_arguments(env, capsys):
    ok = lambda: Response(200, {"ok": True})
    s = Session({("POST", "/api/requests/5/status"): [ok(), ok(), ok()],
                 ("POST", "/api/programs"): [Response(201, {"id": 42})]})
    assert client.main(["done", "5", "--program", "42", "--note", "added"], session=s) == 0
    assert client.main(["done", "5"], session=s) == 0
    assert client.main(["reject", "5", "--note", "not a peer"], session=s) == 0
    assert client.main(["add-program", "--sheet", "MS HRM", "--university", "CLARK UNIVERSITY", "--program", "MSHRM",
                        "--request", "5"], session=s) == 0
    assert [c["json"] for c in s.calls] == [
        {"status": "done", "admin_note": "added", "program_id": 42}, {"status": "done"},
        {"status": "rejected", "admin_note": "not a peer"},
        {"sheet": "MS HRM", "university": "CLARK UNIVERSITY", "program": "MSHRM", "request_id": 5}]
    assert "added program 42" in capsys.readouterr().out
