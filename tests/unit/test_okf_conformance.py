"""OKF v0.2 conformance: every non-reserved concept file under docs/okf/ has parseable YAML
frontmatter with a non-empty `type`; no concept uses the reserved `index.md`/`log.md` names.
https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent-workspace"))
pytest.importorskip("yaml")

from knowledge_base.okf_format import read_concept  # noqa: E402
from knowledge_base.okf_export import write_bundle  # noqa: E402

RESERVED = {"index.md", "log.md"}


def _concepts(bundle: Path) -> list[Path]:
    return [p for p in bundle.rglob("*.md") if p.name not in RESERVED]


def _check_bundle(bundle: Path) -> None:
    concepts = _concepts(bundle)
    assert concepts, f"no concept files found under {bundle}"
    for path in concepts:
        frontmatter, _ = read_concept(path)
        assert isinstance(frontmatter, dict) and frontmatter, f"{path}: no parseable frontmatter"
        assert str(frontmatter.get("type") or "").strip(), f"{path}: missing or empty 'type'"


def test_project_docs_conform():
    _check_bundle(ROOT / "docs" / "okf" / "project")


def test_knowledge_bundle_conforms_if_built():
    bundle = ROOT / "docs" / "okf" / "knowledge"
    if not bundle.exists():
        pytest.skip("docs/okf/knowledge/ not built (run `python -m knowledge_base` first)")
    _check_bundle(bundle)


def test_exported_bundle_conforms(tmp_path):
    """write_bundle's own output, against a small synthetic kb (no workbook needed)."""
    kb = {
        "sheets": [{"name": "Online MS Economics", "fields": [
            {"name": "In-State Tuition Total", "group": "Tuition", "kind": "money"},
            {"name": "In-State Tuition Per Credit", "group": "Tuition", "kind": "money"},
        ]}],
        "programs": [{"id": 0, "sheet": "Online MS Economics", "university": "BOSTON COLLEGE",
                     "program": "Online MS Economics", "school": "", "url": "https://bc.edu/econ",
                     "purdue": False, "lastYearOnly": False}],
        "facts": [
            {"p": 0, "f": "In-State Tuition Per Credit", "v": "$1,200", "n": 1200, "c": "2026-2027",
             "s": "found", "u": "https://bc.edu/econ/tuition", "q": "Tuition is $1,200 per credit hour."},
            {"p": 0, "f": "In-State Tuition Total", "v": "$36,000", "n": 36000, "c": "2026-2027",
             "s": "computed", "u": None, "q": None},
        ],
    }
    bundle = write_bundle(kb, tmp_path, "test run")
    _check_bundle(bundle)
    frontmatter, body = read_concept(bundle / "online-ms-economics.md")
    assert frontmatter["type"] == "Program"
    assert frontmatter["sources"][0]["resource"] == "https://bc.edu/econ/tuition"
    assert "| College | In-State Tuition Total | In-State Tuition Per Credit |" in body  # rows=colleges, cols=fields
    assert "BOSTON COLLEGE" in body and "[$1,200](https://bc.edu/econ/tuition)" in body
    assert "[$36,000](/knowledge/computations/in-state-tuition-total.md)" in body  # computed cell links to its formula
    assert (bundle / "log.md").exists()
