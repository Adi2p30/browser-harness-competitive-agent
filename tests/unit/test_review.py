"""Review queue (field_pipeline.review): grouping, decisions, the knowledge base using them, Excel round trip."""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent-workspace"))
pytest.importorskip("bs4")
pytest.importorskip("requests")
openpyxl = pytest.importorskip("openpyxl")

from field_pipeline import review  # noqa: E402
from field_pipeline.health import quote_key  # noqa: E402
from field_pipeline.store import Store  # noqa: E402

HEAD = ["University Name", "School/College Name", "Program Name", "Website Link", "Credit Hours", "Average GMAT",
        "Ranking (QS)", "In-State Tuition Total", "City Trek Opportunities (yes/no)", "Average Post-Grad Salary",
        "Female %"]
DECISION_COLUMNS = ["sheet", "row", "university", "program", "field", "status", "cycle", "value", "source_url", "quote",
                    "detail"]
URL = "https://purdue.edu/hrm"


@pytest.fixture
def wb(tmp_path):
    """A one-sheet workbook (Purdue row 2, GWU row 3 with Credit Hours typed) and one pipeline run."""
    path = tmp_path / "EDIT Test 2026-2027.xlsx"
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = "MS HRM"
    ws.append(HEAD)
    ws.append(["PURDUE UNIVERSITY", "Daniels", "HRM", URL])
    ws.append(["GEORGE WASHINGTON UNIVERSITY", "GWSB", "MHRM", "https://gwu.edu/mhrm", 36])
    book.save(path)
    run = tmp_path / f"{path.stem}_field_pipeline" / "runs" / "2026-11-06_000000"
    run.mkdir(parents=True)
    d = lambda row, univ, field, status, value="", quote="", detail="": {
        "sheet": "MS HRM", "row": row, "university": univ, "program": "", "field": field, "status": status,
        "cycle": "", "value": value, "source_url": URL if value else "", "quote": quote, "detail": detail}
    with open(run / "decisions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, DECISION_COLUMNS)
        w.writeheader()
        w.writerows([
            d(2, "PURDUE UNIVERSITY", "Credit Hours", "found", "36", "The program requires 36 credit hours."),
            d(2, "PURDUE UNIVERSITY", "Average GMAT", "bad-value", "650", "Average GMAT 650 (2019)", "names 2019-2020"),
            d(2, "PURDUE UNIVERSITY", "Ranking (QS)", "not-found"),
            d(2, "PURDUE UNIVERSITY", "In-State Tuition Total", "not-found"),
            d(2, "PURDUE UNIVERSITY", "City Trek Opportunities (yes/no)", "not-found"),
            d(2, "PURDUE UNIVERSITY", "Average Post-Grad Salary", "not-published"),
            d(3, "GEORGE WASHINGTON UNIVERSITY", "Credit Hours", "not-found"),  # typed into the workbook
            d(3, "GEORGE WASHINGTON UNIVERSITY", "Female %", "not-found"),
        ])
    return path


def queue(wb):
    return review.build_queue(wb, db=wb.parent / "p.sqlite")


def by_field(items):
    return {(i["row"], i["field"]): i for i in items}


def test_groups(wb):
    items = by_field(queue(wb))
    assert {k: i["group"] for k, i in items.items()} == {
        (2, "Average GMAT"): "candidate", (2, "Ranking (QS)"): "ranking", (2, "In-State Tuition Total"): "derived",
        (2, "City Trek Opportunities (yes/no)"): "yesno", (2, "Average Post-Grad Salary"): "not-published",
        (3, "Female %"): "research"}
    assert items[(2, "City Trek Opportunities (yes/no)")]["yesno"] and not items[(3, "Female %")]["yesno"]
    assert items[(3, "Female %")]["why"] == "not on any page the pipeline read"
    gmat = items[(2, "Average GMAT")]
    assert gmat["value"] == "650" and gmat["quote"] and gmat["url"] == URL and gmat["program_url"] == URL
    assert [i["group"] for i in queue(wb)][:2] == ["candidate", "research"]  # most useful groups first


def test_decisions_leave_the_queue_and_reach_the_knowledge_base(wb):
    from knowledge_base.__main__ import build, versions
    items = by_field(queue(wb))
    review.append_reviews(wb, [
        review.make_review(items[(2, "Average GMAT")], "accept", reviewer="Ana"),
        review.make_review(items[(2, "Ranking (QS)")], "not-ranked"),
        review.make_review(items[(2, "Average Post-Grad Salary")], "not-published", note="checked the report"),
        review.make_review(items[(3, "Female %")], "value", "41%", "https://gwu.edu/profile", "class profile"),
    ])
    left = by_field(queue(wb))
    assert set(left) == {(2, "In-State Tuition Total"), (2, "City Trek Opportunities (yes/no)")}
    kb = build(str(wb), None, versions(wb))
    facts = {(kb["programs"][f["p"]]["row"], f["f"]): f for f in kb["facts"]}
    gmat = facts[(2, "Average GMAT")]
    assert (gmat["v"], gmat["s"], gmat["q"], gmat["u"]) == ("650", "reviewed", "Average GMAT 650 (2019)", URL)
    assert "by Ana" in facts[(2, "Average GMAT")]["d"]
    assert facts[(2, "Ranking (QS)")]["v"] == "Not ranked"
    assert facts[(3, "Female %")]["v"] == "41%" and facts[(3, "Female %")]["u"] == "https://gwu.edu/profile"
    assert (2, "Average Post-Grad Salary") not in facts  # confirmed blank: no value, and out of the queue

    done = review.load_reviews(wb)
    review.append_reviews(wb, [review.undo_review(done[("MS HRM:3", "Female %")])])
    assert (3, "Female %") in by_field(queue(wb))


def test_make_review_checks_the_decision():
    item = {"group": "research", "sheet": "S", "row": 2, "university": "U", "program": "", "field": "F",
            "value": "", "url": "", "quote": ""}
    with pytest.raises(ValueError, match="not a choice"):
        review.make_review(item, "accept")
    with pytest.raises(ValueError, match="type the value"):
        review.make_review(item, "value")
    with pytest.raises(ValueError, match="web address"):
        review.make_review(item, "value", "3", "purdue.edu")
    assert review.make_review({**item, "group": "yesno", "yesno": True}, "no")["value"] == "No"
    with pytest.raises(ValueError, match="not a yes/no"):
        review.make_review({**item, "group": "yesno", "yesno": False}, "no")
    stale = {**item, "group": "stale", "value": "$1,250", "url": URL, "quote": "Tuition is $1,250"}
    kept = review.make_review(stale, "keep")
    assert kept["value"] == "$1,250" and kept["source_url"] == URL and kept["quote"] == ""


def test_changed_source_joins_the_queue(wb):
    db = wb.parent / "p.sqlite"
    Store(db).save_health([{"url": URL, "quote_key": quote_key("The program requires 36 credit hours."),
                            "result": "changed", "snippet": "The program requires 33 credit hours.",
                            "detail": "now 33 (was 36)", "checked_at": 1e9}])
    stale = by_field(queue(wb))[(2, "Credit Hours")]
    assert stale["group"] == "stale" and stale["value"] == "36" and "33" in stale["snippet"]
    review.append_reviews(wb, [review.make_review(stale, "value", "33", URL)])
    assert (2, "Credit Hours") not in by_field(queue(wb))  # the reviewed fact has a new quote key: not flagged


def test_excel_round_trip(wb, tmp_path):
    items = queue(wb)
    path = review.export_xlsx(items, tmp_path / "out" / "queue.xlsx")
    book = openpyxl.load_workbook(path)
    ws = book["Review queue"]
    head = [c.value for c in ws[1]]
    col = {h: i + 1 for i, h in enumerate(head)}
    for r in range(2, ws.max_row + 1):
        field = ws.cell(r, col["Field"]).value
        if field == "Ranking (QS)":
            ws.cell(r, col["Decision"]).value = "not-ranked"
        if field == "Female %":
            ws.cell(r, col["Decision"]).value = "value"  # no Value: reported, not saved
        if field == "Average GMAT":
            ws.cell(r, col["Decision"]).value = "Accept"
    book.save(path)
    rows, problems = review.import_xlsx(path, items, "Excel")
    assert sorted((r["field"], r["decision"], r["value"]) for r in rows) == [
        ("Average GMAT", "accept", "650"), ("Ranking (QS)", "not-ranked", "Not ranked")]
    assert len(problems) == 1 and "Female %" in problems[0] and "type the value" in problems[0]
    assert all(r["reviewer"] == "Excel" for r in rows)


def test_queue_by_run(wb):
    run2 = wb.parent / f"{wb.stem}_field_pipeline" / "runs" / "2026-11-19_090000_fresh"
    run2.mkdir(parents=True)
    with open(run2 / "decisions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, DECISION_COLUMNS)
        w.writeheader()
        w.writerow({"sheet": "MS HRM", "row": 3, "university": "GEORGE WASHINGTON UNIVERSITY", "program": "",
                    "field": "Female %", "status": "found", "cycle": "", "value": "41%", "source_url": URL,
                    "quote": "41% of the class are women", "detail": ""})
        w.writerow({"sheet": "MS HRM", "row": 3, "university": "GEORGE WASHINGTON UNIVERSITY", "program": "",
                    "field": "Class Size", "status": "not-found", "cycle": "", "value": "", "source_url": "",
                    "quote": "", "detail": ""})
    runs = review.list_runs(wb)
    assert [r["id"] for r in runs] == ["2026-11-19_090000_fresh", "2026-11-06_000000"]  # newest first
    assert runs[0]["label"].startswith("2026-11-19 09:00 (fresh)")
    db = wb.parent / "p.sqlite"
    newest = review.queue(wb, db=db, run="2026-11-19_090000_fresh")
    assert {(i["row"], i["field"]) for i in newest["items"]} == {(3, "Class Size")}
    first = review.queue(wb, db=db, run="2026-11-06_000000")
    assert (3, "Female %") not in {(i["row"], i["field"]) for i in first["items"]}  # filled by the newer run (GWU Credit Hours: typed in the workbook)
    assert first["filled_elsewhere"] == 2 and (2, "Average GMAT") in {(i["row"], i["field"]) for i in first["items"]}
    every = review.queue(wb, db=db)
    assert {(3, "Class Size"), (2, "Average GMAT")} <= {(i["row"], i["field"]) for i in every["items"]}
    with pytest.raises(ValueError, match="no run"):
        review.queue(wb, db=db, run="nope")
