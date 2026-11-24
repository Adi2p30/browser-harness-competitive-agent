"""Knowledge base and workbook export from the production database (knowledge_base.from_db, prod.exporter)."""
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-workspace"))
pytest.importorskip("bs4")
pytest.importorskip("requests")
pytest.importorskip("yaml")
openpyxl = pytest.importorskip("openpyxl")

from knowledge_base.__main__ import build  # noqa: E402
from knowledge_base.from_db import build_from_db, write_from_db  # noqa: E402
from prod import db, exporter  # noqa: E402

HEAD = ["University Name", "School/College Name", "Program Name", "Website Link", "Credit Hours", "Average GMAT",
        "Female %", "Ranking (QS)"]
URL = "https://purdue.edu/hrm"


@pytest.fixture
def wb(tmp_path):
    """A one-sheet template: Purdue row 2, GWU row 3 with Credit Hours typed."""
    path = tmp_path / "EDIT Test 2026-2027.xlsx"
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = "MS HRM"
    ws.append(HEAD)
    ws.append(["PURDUE UNIVERSITY", "Daniels", "HRM", URL])
    ws.append(["GEORGE WASHINGTON UNIVERSITY", "GWSB", "MHRM", "https://gwu.edu/mhrm", 36])
    book.save(path)
    return path


def new(value, status="found", url=URL, quote=""):
    return {"value": value, "status": status, "cycle": "2026-2027", "source_url": url, "quote": quote,
            "origin": "pipeline", "run": None, "reviewed_by": None}


@pytest.fixture
def prod(tmp_path):
    """purdue (MS HRM:2), gwu (MS HRM:3), an inactive last-year-only school, an added school (MS HRM:10001) and
    one added with no legacy_key."""
    path = tmp_path / "prod.sqlite"
    conn = db.connect(path)
    ids = {
        "purdue": db.create_program(conn, sheet="MS HRM", university="PURDUE UNIVERSITY", school="Daniels",
                                    program="HRM", link=URL, legacy_key="MS HRM:2"),
        "gwu": db.create_program(conn, sheet="MS HRM", university="GEORGE WASHINGTON UNIVERSITY", school="GWSB",
                                 program="MHRM", link="https://gwu.edu/mhrm", legacy_key="MS HRM:3"),
        "old": db.create_program(conn, sheet="MS HRM", university="OLD COLLEGE"),
        "clark": db.create_program(conn, sheet="MS HRM", university="CLARK UNIVERSITY", program="MSHRM",
                                   link="https://clark.edu/hrm"),
        "added": db.create_program(conn, sheet="MS HRM", university="BOSTON COLLEGE", legacy_key="MS HRM:10001"),
    }
    conn.execute("UPDATE programs SET active=0 WHERE id=?", (ids["old"],))
    changes = [
        (ids["purdue"], "Credit Hours", new("36", quote="The program requires 36 credit hours. " + "x" * 400)),
        (ids["purdue"], "Female %", new("0.28")),
        (ids["purdue"], "Website Link", new("https://ignored.example")),  # an ID column: not a field
        (ids["gwu"], "Credit Hours", new("36", status="entered", url=None)),
        (ids["old"], "Credit Hours", new("30", status="prior-year")),
        (ids["clark"], "Average GMAT", new("650", status="reviewed", url="https://clark.edu/gmat")),
        (ids["added"], "Credit Hours", new("32")),
    ]
    out = db.apply_changes(conn, [{"program_id": p, "field": f, "old": None, "new": n} for p, f, n in changes],
                           by="ana")
    assert not out["conflicts"]
    conn.close()
    return path, ids


def kb_of(path, wb):
    conn = db.connect(path, readonly=True)
    try:
        return build_from_db(conn, wb)
    finally:
        conn.close()


def test_build_from_db_matches_build(wb, prod):
    path, ids = prod
    kb, ref = kb_of(path, wb), build(str(wb), None, [], reviews={})
    assert kb.keys() == ref.keys() and kb["sheets"] == ref["sheets"]  # fields in build()'s header-derived order
    assert [p["id"] for p in kb["programs"]] == [ids[k] for k in ("purdue", "gwu", "added", "clark")]  # no inactive
    progs = {p["id"]: p for p in kb["programs"]}
    assert progs[ids["purdue"]].keys() == ref["programs"][0].keys()
    assert progs[ids["purdue"]]["purdue"] and progs[ids["purdue"]]["row"] == 2 and progs[ids["clark"]]["row"] is None
    assert progs[ids["added"]]["row"] == 10001 and progs[ids["clark"]]["url"] == "https://clark.edu/hrm"
    assert not any(p["lastYearOnly"] for p in kb["programs"])
    facts = {(f["p"], f["f"]): f for f in kb["facts"]}
    gwu_ref = next(f for f in ref["facts"] if f["f"] == "Credit Hours")  # typed in the workbook
    assert facts[(ids["gwu"], "Credit Hours")] == {**gwu_ref, "p": ids["gwu"]}
    assert facts[(ids["purdue"], "Female %")]["v"] == "28%" and facts[(ids["purdue"], "Female %")]["n"] == 28
    assert facts[(ids["clark"], "Average GMAT")]["s"] == "reviewed"
    assert (ids["purdue"], "Website Link") not in facts and all(f["p"] != ids["old"] for f in kb["facts"])
    assert all("d" not in f for f in kb["facts"])


def test_write_from_db_writes_bundle(wb, prod, tmp_path):
    path, _ = prod
    bundle = write_from_db(path, wb, repo_root=tmp_path)
    assert bundle == tmp_path / "docs" / "okf" / "knowledge"
    page = (bundle / "ms-hrm.md").read_text(encoding="utf-8")
    assert f"[36]({URL})" in page and "CLARK UNIVERSITY" in page and "OLD COLLEGE" not in page
    assert "prod v1" in (bundle / "log.md").read_text(encoding="utf-8")
    other = tmp_path / "srv" / "docs" / "okf" / "knowledge"
    assert write_from_db(path, wb, knowledge_dir=other) == other and (other / "ms-hrm.md").exists()
    with pytest.raises(ValueError):
        write_from_db(path, wb, knowledge_dir=tmp_path / "kb")


def test_export_fills_copy_and_places_new_programs(wb, prod, tmp_path):
    path, _ = prod
    before = hashlib.sha256(wb.read_bytes()).hexdigest()
    out = tmp_path / "out.xlsx"
    assert exporter.export_workbook(path, wb, out) == 5  # purdue 2, gwu 1, added 1, clark 1
    assert hashlib.sha256(wb.read_bytes()).hexdigest() == before
    ws = openpyxl.load_workbook(out)["MS HRM"]
    col = {h: i + 1 for i, h in enumerate(HEAD)}
    cell = ws.cell(row=2, column=col["Credit Hours"])
    assert cell.value == "36" and cell.hyperlink.target == URL and cell.fill.fill_type is None
    assert cell.comment.text.startswith(f"found · {URL}\n\"The program requires") and len(cell.comment.text) < 400
    assert ws.cell(row=2, column=col["Website Link"]).value == URL  # ID column not overwritten
    assert ws.cell(row=3, column=col["Credit Hours"]).comment.text == "entered"
    assert [ws.cell(row=r, column=1).value for r in (4, 5, 6)] == ["BOSTON COLLEGE", "CLARK UNIVERSITY", None]
    assert ws.cell(row=5, column=col["Program Name"]).value == "MSHRM"
    assert ws.cell(row=5, column=col["Website Link"]).value == "https://clark.edu/hrm"
    gmat = ws.cell(row=5, column=col["Average GMAT"])
    assert gmat.value == "650" and gmat.hyperlink.target == "https://clark.edu/gmat"
    assert ws.cell(row=4, column=col["Credit Hours"]).value == "32"
    with pytest.raises(ValueError):
        exporter.export_workbook(path, wb, wb)


def test_export_cli(wb, prod, tmp_path, monkeypatch, capsys):
    path, _ = prod
    out = tmp_path / "cli.xlsx"
    monkeypatch.setattr(sys, "argv", ["prod.exporter", str(path), str(wb), str(out)])
    exporter.main()
    assert "5 cells" in capsys.readouterr().out and out.exists()
