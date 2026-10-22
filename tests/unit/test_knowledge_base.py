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
