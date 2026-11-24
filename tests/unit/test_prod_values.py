import datetime
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-workspace"))
pytest.importorskip("openpyxl")
pytest.importorskip("bs4")

from prod.values import to_text  # noqa: E402


@pytest.mark.parametrize("value, expected", [
    (None, None), ("", None), ("  ", None), ("—", None), ("=B2*3", None),
    (36, "36"), (36.0, "36"), (3.5, "3.5"), ("  $1,210  per credit ", "$1,210 per credit"),
    (datetime.datetime(2026, 12, 1), "2026-12-01"), (datetime.date(2026, 1, 15), "2026-01-15"),
])
def test_to_text(value, expected):
    assert to_text(value) == expected
