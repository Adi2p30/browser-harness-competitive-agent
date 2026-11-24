"""Schools in "<workbook>.added_schools.csv" (the retired maintainer command's file, still read by the importer): merged
into the workbook's rows with fixed row numbers, and written into a run's output copy as real rows."""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent-workspace"))
openpyxl = pytest.importorskip("openpyxl")

from field_pipeline.workbook import ADDED_FIRST_ROW, added_path, load_added, read_rows, write  # noqa: E402

HEAD = ["University Name", "School/College Name", "Program Name", "Website Link", "Credit Hours", "Average GMAT"]
SHEET = "MS HRM - Residential"


def add(workbook, *schools):
    """Write the side file: (university, school, program, link[, removed_at]) per school, ids from 1."""
    with open(added_path(workbook), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "sheet", "university", "school", "program", "website_link", "added_by", "added_at",
                    "removed_at"])
        for i, (univ, school, program, link, *removed) in enumerate(schools, 1):
            w.writerow([i, SHEET, univ, school, program, link, "", "2026-11-01T00:00:00", *(removed or [""])])


@pytest.fixture
def wb(tmp_path):
    path = tmp_path / "EDIT Test 2026-2027.xlsx"
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = SHEET
    ws.append(HEAD)
    ws.append(["PURDUE UNIVERSITY", "Daniels", "MS HRM", "https://purdue.edu/hrm", 36])
    ws.append(["CLARK UNIVERSITY", "School of Management", "MSM", ""])
    ws.cell(row=40, column=7).value = None  # formatting far below the last school must not count as a row
    ws.cell(row=40, column=1).fill = openpyxl.styles.PatternFill("solid", start_color="FFF2CC", end_color="FFF2CC")
    notes = book.create_sheet("Information to look for")
    notes.append(["Field", "Where"])
    book.save(path)
    return path


def test_added_school_becomes_a_row(wb):
    add(wb, ("MICHIGAN STATE UNIVERSITY", "Broad College", "MHRLR", "https://hrlr.msu.edu"),
        ("EMORY UNIVERSITY", "", "", "", "2026-11-02T00:00:00"))
    assert added_path(wb).name == "EDIT Test 2026-2027.added_schools.csv"
    assert [e["university"] for e in load_added(wb)] == ["MICHIGAN STATE UNIVERSITY"]  # removed ones are left out
    rows = {r.row: r for r in read_rows(str(wb))[1]}
    assert ADDED_FIRST_ROW + 1 not in rows
    new = rows[ADDED_FIRST_ROW]
    assert (new.sheet, new.university, new.program, new.start_url) == (SHEET, "MICHIGAN STATE UNIVERSITY", "MHRLR",
                                                                       "https://hrlr.msu.edu")
    assert new.blanks == ["Credit Hours", "Average GMAT"]  # every field is left to research
    assert new.key == f"{SHEET}::{ADDED_FIRST_ROW}::MICHIGAN STATE UNIVERSITY::MHRLR"
    assert [r.row for r in read_rows(str(wb), ["Other"])[1]] == []


def test_output_copy_lists_added_schools_under_the_last_school(wb, tmp_path):
    add(wb, ("EMORY UNIVERSITY", "Goizueta", "MS HRM", "https://emory.edu/hrm"), ("BABSON COLLEGE", "", "", ""))
    a, b = ADDED_FIRST_ROW, ADDED_FIRST_ROW + 1
    out = tmp_path / "out.xlsx"
    n = write(str(wb), str(out), [
        (SHEET, b, "Credit Hours", "30", "found", None),
        (SHEET, a, "Average GMAT", "Not required", "not-required", None),
        (SHEET, a, "Credit Hours", "36", "found", None),
        (SHEET, 2, "Average GMAT", "650", "found", None),
    ])
    assert n == 4
    ws = openpyxl.load_workbook(out)[SHEET]
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=1, max_row=5, max_col=6)]
    assert rows[3] == ["BABSON COLLEGE", None, None, None, "30", None]  # first written, first placed
    assert rows[4] == ["EMORY UNIVERSITY", "Goizueta", "MS HRM", "https://emory.edu/hrm", "36", "Not required"]
    assert rows[1][5] == "650"
    assert openpyxl.load_workbook(wb)[SHEET].max_row < ADDED_FIRST_ROW  # the input workbook is never touched


def test_knowledge_base_sees_added_school(wb):
    pytest.importorskip("bs4")
    pytest.importorskip("requests")
    from knowledge_base.__main__ import current_values
    add(wb, ("EMORY UNIVERSITY", "Goizueta Business School", "MS HRM", ""))
    typed = current_values(str(wb))[(SHEET, ADDED_FIRST_ROW)]
    assert typed == {"University Name": "EMORY UNIVERSITY", "School/College Name": "Goizueta Business School",
                     "Program Name": "MS HRM"}
