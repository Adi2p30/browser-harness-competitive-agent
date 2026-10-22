import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent-workspace"))
pytest.importorskip("bs4")
pytest.importorskip("openpyxl")

from knowledge_base.__main__ import col_key, match_prior, text  # noqa: E402


def rec(univ, program=""):
    return (univ, {"Program Name": program})


def test_match_prior_names():
    recs = [rec("purdue university global"), rec("university of washington"), rec("purdue university"),
            rec("university of north carolina at chapel hill")]
    assert match_prior("PURDUE UNIVERSITY - WEST LAFAYETTE", "", recs, set()) == 2
    assert match_prior("WASHINGTON STATE UNIVERSITY- PULLMAN", "", recs, set()) is None
    assert match_prior("UNIVERSITY OF NORTH CAROLINA-CHAPEL HILL", "", recs, set()) == 3
    assert match_prior("PURDUE UNIVERSITY - WEST LAFAYETTE", "", recs, {2}) is None


def test_match_prior_program_tiebreak():
    recs = [rec("boston college", "Woods College Applied Economics"), rec("boston college", "Online MBA")]
    assert match_prior("BOSTON COLLEGE", "APPLIED ECONOMICS", recs, set()) == 0
    assert match_prior("BOSTON COLLEGE", "", recs, set()) is None


def test_text_and_columns():
    assert text("=(($Q2*AC2)/SUM($AR2:$AS2))*12") == ""
    assert text("-") == "" and text(" — ") == "" and text(3.0) == "3" and text("$1,250 ") == "$1,250"
    assert col_key("Female %") == col_key("Female (%)")


def test_decisions_newest_version_wins(tmp_path):
    from knowledge_base.__main__ import decisions, versions
    head = "sheet,row,university,program,field,status,cycle,value,source_url,quote,detail\n"
    base = tmp_path / "Book_field_pipeline"
    (base / "runs" / "2026-10-01_100000").mkdir(parents=True)
    (base / "runs" / "2026-10-02_100000").mkdir(parents=True)
    (base / "decisions.csv").write_text(head + "S,2,U,P,Average GMAT,found,,600,,,\n")
    (base / "runs/2026-10-01_100000/decisions.csv").write_text(head + "S,2,U,P,Average GMAT,found,,610,,,\n")
    (base / "runs/2026-10-02_100000/decisions.csv").write_text(head + "S,2,U,P,Average GMAT,not-found,,,,,\n")
    files = versions(tmp_path / "Book.xlsx")
    assert [f.parent.name for f in files] == ["Book_field_pipeline", "2026-10-01_100000", "2026-10-02_100000"]
    assert decisions(files)[("S", 2, "Average GMAT")]["value"] == "610"  # a later blank does not erase it
