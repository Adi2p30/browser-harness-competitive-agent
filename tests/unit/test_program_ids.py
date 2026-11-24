"""With the admin's production snapshot, the local pipeline, review queue, knowledge base and diff identify programs by
production id: programs added through the API (no workbook row) included, files from before program ids mapped by
legacy_key."""
import csv
import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent-workspace"))
pytest.importorskip("bs4")
pytest.importorskip("requests")
openpyxl = pytest.importorskip("openpyxl")

from field_pipeline import review  # noqa: E402
from field_pipeline import __main__ as pipeline  # noqa: E402
from field_pipeline.store import Store  # noqa: E402
from field_pipeline.workbook import legacy_ids, read_rows, snapshot_path  # noqa: E402
from knowledge_base.__main__ import build, versions  # noqa: E402
from prod import db, diff  # noqa: E402

HEAD = ["University Name", "School/College Name", "Program Name", "Website Link", "Credit Hours", "Average GMAT",
        "Ranking (QS)", "Female %"]
DECISION_COLUMNS = ["program_id", "sheet", "row", "university", "program", "field", "status", "cycle", "value",
                    "source_url", "quote", "detail"]
URL = "https://purdue.edu/hrm"
RUN = "2026-11-24_100000"


def new(value, status="found", origin="pipeline"):
    return {"value": value, "status": status, "cycle": "2026-2027", "source_url": URL, "quote": f"it is {value}",
            "origin": origin, "run": None, "reviewed_by": None}


@pytest.fixture
def wb(tmp_path):
    """MS HRM rows: Purdue (row 2), GWU (row 3). Production: Purdue = program 1 (legacy MS HRM:2) with a current
    Credit Hours and last year's GMAT; Emory = program 2, added through the API, with a reviewed QS ranking."""
    path = tmp_path / "EDIT Test 2026-2027.xlsx"
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = "MS HRM"
    ws.append(HEAD)
    ws.append(["PURDUE UNIVERSITY", "Daniels", "HRM", URL])
    ws.append(["GEORGE WASHINGTON UNIVERSITY", "GWSB", "MHRM", "https://gwu.edu/mhrm"])
    book.save(path)
    conn = db.connect(tmp_path / "prod.sqlite")
    purdue = db.create_program(conn, sheet="MS HRM", university="PURDUE UNIVERSITY", school="Daniels", program="HRM",
                               link=URL, legacy_key="MS HRM:2")
    emory = db.create_program(conn, sheet="MS HRM", university="EMORY UNIVERSITY", program="MS HRM",
                              link="https://emory.edu/hrm")
    assert (purdue, emory) == (1, 2)
    db.apply_changes(conn, [{"program_id": p, "field": f, "old": None, "new": n} for p, f, n in [
        (1, "Credit Hours", new("36")), (1, "Average GMAT", new("600", "prior-year", "prior-year")),
        (2, "Ranking (QS)", new("#3", "reviewed", "review"))]], by="import")
    pull(path, conn)
    conn.close()
    return path


def pull(workbook, conn) -> dict:
    """What `prod.client pull` does."""
    snap = json.loads(json.dumps(db.snapshot(conn)))
    snapshot_path(workbook).parent.mkdir(parents=True, exist_ok=True)
    snapshot_path(workbook).write_text(json.dumps(snap))
    return snap


def snap_of(wb) -> dict:
    return json.loads(snapshot_path(wb).read_text())


def decisions(wb, rows, run=RUN):
    """rows: (program_id or None, sheet row or None, field, status, value)."""
    path = wb.parent / f"{wb.stem}_field_pipeline" / "runs" / run / "decisions.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, DECISION_COLUMNS)
        w.writeheader()
        for pid, row, field, status, value in rows:
            w.writerow({"program_id": pid, "sheet": "MS HRM", "row": row, "university": "", "program": "",
                        "field": field, "status": status, "cycle": "", "value": value,
                        "source_url": URL if value else "", "quote": f"it is {value}" if value else "", "detail": ""})


def test_rows_come_from_the_snapshot(wb):
    cols, rows = read_rows(str(wb), snapshot=snap_of(wb))
    assert [(r.program_id, r.row, r.university, r.key, r.ref) for r in rows] == [
        (1, 2, "PURDUE UNIVERSITY", "p1", 1), (2, None, "EMORY UNIVERSITY", "p2", 2)]  # GWU isn't in production
    purdue, emory = rows
    # current facts and the identifying columns a program has are not blanks; last year's values are
    assert purdue.blanks == ["Average GMAT", "Ranking (QS)", "Female %"]
    assert emory.blanks == ["School/College Name", "Credit Hours", "Average GMAT", "Female %"]
    assert (emory.start_url, purdue.school) == ("https://emory.edu/hrm", "Daniels") and cols["MS HRM"] == HEAD
    moved = snap_of(wb)
    moved["programs"][0]["legacy_key"] = "MS HRM:3"  # the workbook's row 3 is another school: not its row
    assert [r.row for r in read_rows(str(wb), snapshot=moved)[1]] == [None, None]
    moved["programs"][1]["active"] = 0
    assert [r.program_id for r in read_rows(str(wb), snapshot=moved)[1]] == [1]
    assert [r.row for r in read_rows(str(wb))[1]] == [2, 3]  # no snapshot: the workbook's rows


def test_knowledge_base_and_diff_by_program_id(wb):
    """New decisions carry program_id; old rows and reviews without it map by legacy_key. Both programs reach the
    knowledge base and the diff; once published, a fresh snapshot has nothing left to publish."""
    decisions(wb, [(1, 2, "Average GMAT", "found", "650"), (2, "", "Credit Hours", "found", "30"),
                   (2, "", "Female %", "not-found", ""), ("", 2, "Female %", "found", "40%")])
    review.append_reviews(wb, [
        {"program_id": 2, "sheet": "MS HRM", "row": None, "university": "EMORY UNIVERSITY", "field": "Average GMAT",
         "decision": "value", "value": "700", "source_url": URL, "reviewer": "ana", "at": "2026-11-24T10:00:00"},
        {"sheet": "MS HRM", "row": 2, "university": "PURDUE UNIVERSITY", "field": "Ranking (QS)",
         "decision": "not-ranked", "value": "Not ranked", "reviewer": "bo", "at": "2026-11-24T10:00:00"}])
    kb = build(str(wb), None, versions(wb), snapshot=snap_of(wb))
    assert [(p["id"], p["program_id"], p["row"]) for p in kb["programs"]] == [(1, 1, 2), (2, 2, None)]
    assert {(f["p"], f["f"]): (f["v"], f["s"]) for f in kb["facts"]} == {
        (1, "Average GMAT"): ("650", "found"), (1, "Female %"): ("40%", "found"),
        (1, "Ranking (QS)"): ("Not ranked", "reviewed"), (2, "Credit Hours"): ("30", "found"),
        (2, "Average GMAT"): ("700", "reviewed")}

    changes, unmapped = diff.compare(wb, snap_of(wb))
    by = {(c["program_id"], c["field"]): c for c in changes}
    assert unmapped == [] and set(by) == {(1, "Average GMAT"), (1, "Female %"), (1, "Ranking (QS)"),
                                          (2, "Credit Hours"), (2, "Average GMAT")}
    assert by[(1, "Average GMAT")]["old"] == {"value": "600", "status": "prior-year"}
    assert (by[(2, "Credit Hours")]["new"]["run"], by[(2, "Average GMAT")]["new"]["reviewed_by"]) == (RUN, "ana")
    assert by[(1, "Ranking (QS)")]["new"]["reviewed_by"] == "bo"

    conn = db.connect(wb.parent / "prod.sqlite")
    assert not db.apply_changes(conn, changes, by="ana")["conflicts"]
    snap = pull(wb, conn)
    assert diff.compare(wb, snap) == ([], [])
    assert [r.blanks for r in read_rows(str(wb), snapshot=snap)[1]] == [[], ["School/College Name", "Female %"]]


def test_store_migration(wb, tmp_path):
    store = Store(tmp_path / "p.sqlite")
    old, other, renamed = "MS HRM::2::PURDUE UNIVERSITY::HRM", "MS HRM::3::GEORGE WASHINGTON UNIVERSITY::MHRM", \
        "MS HRM::2::PURDUE::HRM"
    for key, value in ((old, "650"), (other, "700"), (renamed, "600")):
        store.save_result(key, "Average GMAT", "scores", {"status": "found", "value": value})
        store.add_candidate(key, URL, "*", 100, "start")
    store.save_result(old, "Credit Hours", "structure", {"status": "found", "value": "36"})
    store.save_result("p1", "Credit Hours", "structure", {"status": "found", "value": "33"})  # already moved: kept
    assert store.migrate_keys(snap_of(wb)) == (1, 3)
    assert {f: r["value"] for f, r in store.results("p1").items()} == {"Average GMAT": "650", "Credit Hours": "33"}
    assert store.start_url("p1") == URL
    assert store.results(other) and store.results(renamed)  # no production program made these keys: left alone
    assert list(store.results(old)) == ["Credit Hours"]  # the row the new key already had stays under the old one
    assert store.migrate_keys(snap_of(wb)) == (0, 3) and store.results("p1")["Average GMAT"]["value"] == "650"


def test_review_queue_by_program_id_and_excel_round_trip(wb, tmp_path):
    decisions(wb, [(1, 2, "Average GMAT", "bad-value", "650"), (2, "", "Female %", "not-found", ""),
                   (2, "", "Credit Hours", "not-found", "")])
    items = review.build_queue(wb, db=tmp_path / "p.sqlite")
    assert {i["id"]: (i["program_id"], i["row"]) for i in items} == {
        "1|Average GMAT": (1, 2), "2|Female %": (2, None), "2|Credit Hours": (2, None)}
    path = review.export_xlsx(items, tmp_path / "queue.xlsx")
    book = openpyxl.load_workbook(path)
    ws = book["Review queue"]
    col = {c.value: c.column for c in ws[1]}
    for r in range(2, ws.max_row + 1):
        ws.cell(r, col["Decision"]).value = {"Female %": "value", "Credit Hours": "value", "Average GMAT": "accept"}[
            ws.cell(r, col["Field"]).value]
        ws.cell(r, col["Value"]).value = "40%" if ws.cell(r, col["Field"]).value == "Female %" else None
    book.save(path)
    rows, problems = review.import_xlsx(path, items, "Excel")
    assert sorted((r["program_id"], r["field"], r["value"]) for r in rows) == [(1, "Average GMAT", "650"),
                                                                               (2, "Female %", "40%")]
    assert len(problems) == 1 and "type the value" in problems[0]
    review.append_reviews(wb, rows)
    assert [i["id"] for i in review.build_queue(wb, db=tmp_path / "p.sqlite")] == ["2|Credit Hours"]

    ws.delete_cols(col["Program ID"])  # an export from before program ids: matched by sheet, row and field
    book.save(path)
    assert [r["field"] for r in review.import_xlsx(path, items, "")[0]] == ["Average GMAT"]


def test_recheck_rows_map_by_program_id_or_legacy_key(wb, tmp_path):
    path = tmp_path / "recheck.csv"
    path.write_text("program_id,sheet,row,field\n2,MS HRM,,Credit Hours\n,MS HRM,2,Average GMAT\n,MS HRM,9,X\n")
    assert pipeline.read_recheck(path, legacy_ids(snap_of(wb))) == {2: {"Credit Hours"}, 1: {"Average GMAT"},
                                                                    None: {"X"}}


class FakeDiscoverer:
    keys = []

    def __init__(self, *a, **k):
        self.searcher = Namespace(stats={}, disabled=set())

    def find_start(self, *a):
        return ""

    def run(self, row_key, *a, **k):
        FakeDiscoverer.keys.append(row_key)
        return []


def run_args(**kw):
    return Namespace(**{"per_group": 4, "no_search": True, "sheet": None, "only": None, "program": None, "limit": None,
                        "recheck": None, "no_prior": True, "prior": None, "no_ipeds": True, "no_find_links": True,
                        "reextract": False, "overwrite": False, "fresh": False, "chunks": 18, "workers": 1,
                        "no_second_pass": True, **kw})


def test_pipeline_run_by_program_id(wb, tmp_path, monkeypatch):
    """A run researches production programs by id (--program picks them), stores results under p<id>, writes
    program_id into decisions.csv and leaves a program with no workbook row out of the workbook copy."""
    monkeypatch.setattr(pipeline, "Discoverer", FakeDiscoverer)
    monkeypatch.setattr(pipeline, "extract_all", lambda row, program, today, group_fields, *a, **k: {
        c: {"status": "found", "value": "1", "url": URL, "quote": "it is 1"} for fs in group_fields.values()
        for c in fs})
    fetcher = Namespace(begin_school=lambda: None, browser_ok=0, browser_fail=0, sessions=0)
    store = Store(tmp_path / "p.sqlite")
    store.save_result("MS HRM::2::PURDUE UNIVERSITY::HRM", "Female %", "class",
                      {"status": "found", "value": "41%", "url": URL, "quote": "41%"})
    out = tmp_path / "out"
    out.mkdir()
    FakeDiscoverer.keys = []
    summary = pipeline.run(run_args(), wb, out, store, fetcher)
    assert summary["rows"] == 2 and FakeDiscoverer.keys == ["p1", "p2"]
    with open(out / "decisions.csv", newline="", encoding="utf-8") as f:
        got = {(d["program_id"], d["row"], d["field"]): d["value"] for d in csv.DictReader(f)}
    assert got[("1", "2", "Female %")] == "41%"  # the stored result moved to p1 and was reused
    assert {k[:2] for k in got} == {("1", "2"), ("2", "")} and ("2", "", "Credit Hours") in got
    assert ("1", "2", "Credit Hours") not in got  # production has a current value: not researched
    copy = openpyxl.load_workbook(out / f"{wb.stem}_field_pipeline.xlsx")["MS HRM"]
    assert copy.cell(2, HEAD.index("Average GMAT") + 1).value == "1"
    assert copy.max_row == 3  # Emory has no workbook row: in decisions.csv only

    FakeDiscoverer.keys = []
    out2 = tmp_path / "out2"
    out2.mkdir()
    assert pipeline.run(run_args(program=[2]), wb, out2, store, fetcher)["rows"] == 1
    assert FakeDiscoverer.keys == []  # every cell of p2 already stored
    snapshot_path(wb).unlink()
    with pytest.raises(pipeline.NothingToDo, match="needs the production snapshot"):
        pipeline.run(run_args(program=[2]), wb, out2, store, fetcher)


def test_runs_csv_gains_the_programs_column(tmp_path):
    runs = tmp_path / "Book_field_pipeline" / "runs"
    runs.mkdir(parents=True)
    old_head = [c for c in pipeline.RUN_COLUMNS if c != "programs"]
    (runs.parent / "runs.csv").write_text(",".join(old_head) + "\n" + ",".join(["2026-11-01_000000"] + [""] * 12)
                                          + "\n")
    out = pipeline.version_dir(tmp_path / "Book.xlsx", False)
    pipeline.record_run(out, 0, "done", Namespace(fresh=False, browser="local", sheet=["MS HRM"], only=None,
                                                  program=[2, 5], limit=None), {"rows": 2})
    with open(runs.parent / "runs.csv", newline="", encoding="utf-8") as f:
        got = list(csv.DictReader(f))
    assert [(r["version"], r["programs"]) for r in got] == [("2026-11-01_000000", ""), (out.name, "2; 5")]
    (out / "decisions.csv").write_text(",".join(DECISION_COLUMNS) + "\n")
    assert review.list_runs(tmp_path / "Book.xlsx")[0]["label"].endswith("· MS HRM · program 2; 5 · 2 rows")
