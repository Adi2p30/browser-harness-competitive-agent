"""Schools added by a maintainer (field_pipeline.workbook.add_school): stored beside the workbook, merged into its
rows with fixed row numbers, and written into a run's output copy as real rows."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent-workspace"))
openpyxl = pytest.importorskip("openpyxl")

from field_pipeline.workbook import (ADDED_FIRST_ROW, add_school, added_path, load_added, read_rows,  # noqa: E402
                                     remove_school, write)

HEAD = ["University Name", "School/College Name", "Program Name", "Website Link", "Credit Hours", "Average GMAT"]
SHEET = "MS HRM - Residential"


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
    e = add_school(wb, SHEET, "Michigan State University", "Broad College", "MHRLR", "https://hrlr.msu.edu", "Ana")
    assert (e["id"], e["row"], e["university"]) == ("1", ADDED_FIRST_ROW, "MICHIGAN STATE UNIVERSITY")
    assert added_path(wb).name == "EDIT Test 2026-2027.added_schools.csv"
    rows = {r.row: r for r in read_rows(str(wb))[1]}
    new = rows[ADDED_FIRST_ROW]
    assert (new.sheet, new.university, new.program, new.start_url) == (SHEET, "MICHIGAN STATE UNIVERSITY", "MHRLR",
                                                                       "https://hrlr.msu.edu")
    assert new.blanks == ["Credit Hours", "Average GMAT"]  # every field is left to research
    assert new.key == f"{SHEET}::{ADDED_FIRST_ROW}::MICHIGAN STATE UNIVERSITY::MHRLR"
    assert [r.row for r in read_rows(str(wb), ["Other"])[1]] == []


def test_add_school_refuses_bad_input(wb):
    with pytest.raises(ValueError, match="already on MS HRM - Residential \\(row 3\\)"):
        add_school(wb, SHEET, "clark university", program="msm")  # same school and program as a workbook row
    add_school(wb, SHEET, "Clark University", program="MS Finance")  # another program of the same school is fine
    with pytest.raises(ValueError, match="the added schools"):
        add_school(wb, SHEET, "CLARK UNIVERSITY", program="ms  finance")
    with pytest.raises(ValueError, match="university's name"):
        add_school(wb, SHEET, "  ")
    with pytest.raises(ValueError, match="web address"):
        add_school(wb, SHEET, "Emory University", website_link="emory.edu")
    with pytest.raises(ValueError, match="no program sheet"):
        add_school(wb, "Information to look for", "Emory University")


def test_removed_school_keeps_its_row_number_reserved(wb):
    first = add_school(wb, SHEET, "Emory University")
    second = add_school(wb, SHEET, "Babson College")
    assert remove_school(wb, first["id"])["university"] == "EMORY UNIVERSITY"
    assert remove_school(wb, first["id"]) is None  # already removed
    assert [e["university"] for e in load_added(wb)] == ["BABSON COLLEGE"]
    assert ADDED_FIRST_ROW not in {r.row for r in read_rows(str(wb))[1]}
    assert second["row"] == ADDED_FIRST_ROW + 1 and add_school(wb, SHEET, "Vanderbilt University")["row"] == ADDED_FIRST_ROW + 2


def test_output_copy_lists_added_schools_under_the_last_school(wb, tmp_path):
    a = add_school(wb, SHEET, "Emory University", "Goizueta", "MS HRM", "https://emory.edu/hrm")
    b = add_school(wb, SHEET, "Babson College")
    out = tmp_path / "out.xlsx"
    n = write(str(wb), str(out), [
        (SHEET, b["row"], "Credit Hours", "30", "found", None),
        (SHEET, a["row"], "Average GMAT", "Not required", "not-required", None),
        (SHEET, a["row"], "Credit Hours", "36", "found", None),
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
    e = add_school(wb, SHEET, "Emory University", "Goizueta Business School", "MS HRM")
    typed = current_values(str(wb))[(SHEET, e["row"])]
    assert typed == {"University Name": "EMORY UNIVERSITY", "School/College Name": "Goizueta Business School",
                     "Program Name": "MS HRM"}


def test_maintainer_command(wb, capsys):
    from field_pipeline.schools import main
    assert main([str(wb), "add", "--sheet", SHEET, "--university", "Emory University", "--program", "MS HRM",
                 "--by", "Ana"]) == 0
    out = capsys.readouterr().out
    assert f"as row {ADDED_FIRST_ROW}" in out and f"--row '{SHEET}:{ADDED_FIRST_ROW}'" in out
    assert main([str(wb), "add", "--sheet", SHEET, "--university", "emory university", "--program", "ms hrm"]) == 1
    assert "already on" in capsys.readouterr().err
    assert main([str(wb), "list"]) == 0 and "EMORY UNIVERSITY · MS HRM" in capsys.readouterr().out
    assert main([str(wb), "remove", "1"]) == 0 and main([str(wb), "remove", "1"]) == 1
    capsys.readouterr()
    main([str(wb), "list"])
    assert "removed" in capsys.readouterr().out
