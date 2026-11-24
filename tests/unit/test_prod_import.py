"""prod.importer: the workbook's knowledge base becomes version 1 of an empty production database."""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-workspace"))
pytest.importorskip("bs4")
pytest.importorskip("requests")
openpyxl = pytest.importorskip("openpyxl")

from field_pipeline import review  # noqa: E402
from prod import db, importer  # noqa: E402

HEAD = ["University Name", "School/College Name", "Program Name", "Website Link", "Credit Hours", "Average GMAT",
        "Average Post-Grad Salary"]
DECISION_COLUMNS = ["sheet", "row", "university", "program", "field", "status", "cycle", "value", "source_url", "quote",
                    "detail"]
URL = "https://purdue.edu/hrm"
RUN = "2026-11-06_000000"


def sheet(path, rows):
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = "MS HRM"
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    book.save(path)


@pytest.fixture
def wb(tmp_path):
    """Purdue (row 2) and GWU (row 3), one pipeline run, one review and last year's workbook with an extra school.

    Purdue: Credit Hours typed 30 but found 36 (pipeline beats typed); Average GMAT found 650 but reviewed 640
    (review beats pipeline). GWU: Credit Hours typed 36.0, 33 last year (typed beats prior-year, "36" not "36.0");
    Average Post-Grad Salary only last year (prior-year). Clark is only in last year's workbook."""
    path = tmp_path / "EDIT Test 2026-2027.xlsx"
    sheet(path, [["PURDUE UNIVERSITY", "Daniels", "HRM", URL, 30],
                 ["GEORGE WASHINGTON UNIVERSITY", "GWSB", "MHRM", "https://gwu.edu/mhrm", 36.0]])
    sheet(tmp_path / "OLD Test 2025-2026.xlsx", [
        ["GEORGE WASHINGTON UNIVERSITY", "GWSB", "MHRM", "https://gwu.edu/mhrm", 33, None, 70000],
        ["CLARK UNIVERSITY", "School of Management", "MSHRM", "https://clarku.edu/hrm", None, 600]])
    run = tmp_path / f"{path.stem}_field_pipeline" / "runs" / RUN
    run.mkdir(parents=True)
    d = lambda row, univ, field, status, value="": {
        "sheet": "MS HRM", "row": row, "university": univ, "program": "", "field": field, "status": status,
        "cycle": "", "value": value, "source_url": URL if value else "", "quote": f"it is {value}" if value else "",
        "detail": ""}
    with open(run / "decisions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, DECISION_COLUMNS)
        w.writeheader()
        w.writerows([d(2, "PURDUE UNIVERSITY", "Credit Hours", "found", "36"),
                     d(2, "PURDUE UNIVERSITY", "Average GMAT", "found", "650"),
                     d(3, "GEORGE WASHINGTON UNIVERSITY", "Credit Hours", "not-found")])
    review.append_reviews(path, [{
        "sheet": "MS HRM", "row": 2, "university": "PURDUE UNIVERSITY", "program": "HRM", "field": "Average GMAT",
        "decision": "value", "value": "640", "source_url": URL, "quote": "Average GMAT 640", "note": "",
        "reviewer": "Ana", "at": "2026-11-20T10:00:00Z"}])
    return path


def imported(wb):
    summary = importer.import_workbook(wb, wb.parent / "prod.sqlite")
    return summary, db.snapshot(db.connect(wb.parent / "prod.sqlite", readonly=True))


def test_programs_and_legacy_keys(wb):
    summary, snap = imported(wb)
    progs = {p["university"]: p for p in snap["programs"]}
    assert {u: (p["legacy_key"], p["active"]) for u, p in progs.items()} == {
        "PURDUE UNIVERSITY": ("MS HRM:2", 1), "GEORGE WASHINGTON UNIVERSITY": ("MS HRM:3", 1),
        "CLARK UNIVERSITY": (None, 0)}  # only in last year's workbook: kept, inactive, no key
    assert progs["PURDUE UNIVERSITY"]["link"] == URL and progs["PURDUE UNIVERSITY"]["school"] == "Daniels"
    assert {p["added_by"] for p in snap["programs"]} == {"import"}
    assert (summary["programs"], summary["inactive_programs"], summary["version"]) == (2, 1, 1)


def test_one_fact_per_cell_by_precedence(wb):
    summary, snap = imported(wb)
    names = {p["id"]: p["university"].split()[0] for p in snap["programs"]}
    facts = {(names[f["program_id"]], f["field"]): f for f in snap["facts"]}
    got = {k: (f["value"], f["status"], f["origin"], f["run"], f["reviewed_by"]) for k, f in facts.items()}
    assert got == {
        ("PURDUE", "Credit Hours"): ("36", "found", "pipeline", RUN, None),
        ("PURDUE", "Average GMAT"): ("640", "reviewed", "review", None, "Ana"),
        ("GEORGE", "Credit Hours"): ("36", "entered", "workbook", None, None),
        ("GEORGE", "Average Post-Grad Salary"): ("70000", "prior-year", "prior-year", None, None),
        ("CLARK", "Average GMAT"): ("600", "prior-year", "prior-year", None, None)}
    assert facts[("PURDUE", "Credit Hours")]["source_url"] == URL
    assert facts[("PURDUE", "Average GMAT")]["quote"] == "Average GMAT 640"
    assert facts[("GEORGE", "Credit Hours")]["source_url"] is None  # blanks stored as NULL
    assert {f["version"] for f in snap["facts"]} == {1}
    assert summary["origins"] == {"pipeline": 1, "review": 1, "workbook": 1, "prior-year": 2}
    json.dumps(snap)


def test_publish_row_and_no_history(wb):
    importer.import_workbook(wb, wb.parent / "prod.sqlite", by="ana")
    conn = db.connect(wb.parent / "prod.sqlite")
    pub = dict(conn.execute("SELECT * FROM publishes").fetchone())
    assert (pub["version"], pub["by"], pub["n_applied"], pub["note"]) == (
        1, "ana", 5, "initial import from EDIT Test 2026-2027.xlsx")
    assert conn.execute("SELECT COUNT(*) FROM fact_history").fetchone()[0] == 0
    assert db.version(conn) == 1


def test_refuses_a_database_with_programs(wb):
    importer.import_workbook(wb, wb.parent / "prod.sqlite")
    with pytest.raises(importer.NotEmpty):
        importer.import_workbook(wb, wb.parent / "prod.sqlite")
    out = subprocess.run([sys.executable, "-m", "prod.importer", str(wb), str(wb.parent / "prod.sqlite")],
                         cwd=ROOT / "agent-workspace", capture_output=True, text=True)
    assert out.returncode == 1 and "already has programs" in out.stderr
    conn = db.connect(wb.parent / "prod.sqlite", readonly=True)
    assert conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0] == 3


def test_origin():
    assert [importer.origin(s) for s in ("reviewed", "found", "ipeds-proxy", "entered", "prior-year")] == [
        "review", "pipeline", "pipeline", "workbook", "prior-year"]
    with pytest.raises(ValueError):
        importer.origin("not-found")
