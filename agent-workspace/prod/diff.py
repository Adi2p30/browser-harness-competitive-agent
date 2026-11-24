"""What an admin's laptop would publish: the local knowledge base (workbook + pipeline runs + reviews) compared with a
snapshot of the production database (GET /api/export).

Only facts a person or the pipeline produced are published: KB status "reviewed" (origin review) or a pipeline
status in FILLED (origin pipeline). Values typed into the workbook ("entered") and last year's values
("prior-year") came in with the import and are never published from here. A review decision "not-published" is
published as status "not-published" with no value. Nothing else is ever removed.
"""
import csv
from collections import defaultdict
from pathlib import Path

from field_pipeline.fields import FILLED
from field_pipeline.review import load_reviews
from knowledge_base.__main__ import build, versions

from .values import to_text

REVIEWED_CYCLE = "2026-2027"


def default_prior(workbook: Path) -> Path | None:
    """Last year's workbook as knowledge_base.main() finds it: the one OLD *.xlsx next to this one."""
    olds = sorted(workbook.parent.glob("OLD *.xlsx"))
    return olds[0] if len(olds) == 1 else None


def runs(files: list[Path]) -> dict[tuple[str, int, str], str | None]:
    """{(sheet, row, field): version folder} of the run whose filled value the KB uses (the newest, as in
    knowledge_base.decisions); None for the unversioned decisions.csv."""
    out = {}
    for path in files:
        run = path.parent.name if path.parent.parent.name == "runs" else None
        with open(path, newline="", encoding="utf-8") as f:
            out.update({(d["sheet"], int(d["row"]), d["field"]): run for d in csv.DictReader(f)
                        if d["status"] in FILLED})
    return out


def _new(value, status, cycle, source_url, quote, origin, run=None, reviewed_by=None) -> dict:
    return {"value": to_text(value), "status": status, "cycle": cycle or None, "source_url": source_url or None,
            "quote": quote or None, "origin": origin, "run": run, "reviewed_by": reviewed_by or None}


def compare(workbook: Path, snapshot: dict, prior: Path | None = None) -> tuple[list[dict], list[dict]]:
    """(changes, unmapped). changes are in prod.db's change format; unmapped are the local programs (with a workbook
    row) that have no production program with legacy_key "sheet:row"."""
    workbook = Path(workbook)
    prior = prior or default_prior(workbook)
    files = versions(workbook)
    reviews = load_reviews(workbook)
    kb = build(str(workbook), str(prior) if prior else None, files, reviews)
    run_of = runs(files)
    prod = {p["legacy_key"]: p for p in snapshot["programs"] if p.get("legacy_key")}
    stored = {(f["program_id"], f["field"]): f for f in snapshot["facts"]}

    def old(pid, field):
        f = stored.get((pid, field))
        return {"value": f["value"], "status": f["status"]} if f else None

    by_row, unmapped = {}, []
    for p in kb["programs"]:
        if p["lastYearOnly"]:
            continue
        if (target := prod.get(f"{p['sheet']}:{p['row']}")) is None:
            unmapped.append(p)
        else:
            by_row[(p["sheet"], p["row"])] = target["id"]
    hidden = {k for k, rv in reviews.items() if rv["decision"] == "not-published"}

    changes = []
    for f in kb["facts"]:
        p = kb["programs"][f["p"]]
        key = (p["sheet"], p["row"], f["f"])
        pid = by_row.get(key[:2])
        if pid is None or key in hidden:
            continue
        if f["s"] == "reviewed":
            rv = reviews[key]
            new = _new(f["v"], "reviewed", f["c"], f["u"], f["q"], "review", reviewed_by=rv["reviewer"])
        elif f["s"] in FILLED:
            new = _new(f["v"], f["s"], f["c"], f["u"], f["q"], "pipeline", run=run_of.get(key))
        else:
            continue
        o = old(pid, f["f"])
        if o is None or (o["value"], o["status"]) != (new["value"], new["status"]):
            changes.append({"program_id": pid, "field": f["f"], "old": o, "new": new})
    for key in sorted(hidden):
        if (pid := by_row.get(key[:2])) is None:
            continue
        o, rv = old(pid, key[2]), reviews[key]
        if o is None or o["status"] != "not-published":
            changes.append({"program_id": pid, "field": key[2], "old": o,
                            "new": _new(None, "not-published", REVIEWED_CYCLE, rv["source_url"], rv["quote"],
                                        "review", reviewed_by=rv["reviewer"])})
    return changes, unmapped


def local_changes(workbook: Path, snapshot: dict, prior: Path | None = None) -> list[dict]:
    """Changes to publish (see compare)."""
    return compare(workbook, snapshot, prior)[0]


def _shown(fact: dict) -> str:
    return fact["value"] if fact["value"] is not None else f"({fact['status']})"


def format_diff(changes: list[dict], snapshot: dict) -> str:
    """Changes grouped by program ("MS HRM · CLARK UNIVERSITY"): `+ Field: value` new, `~ Field: old → new`
    changed, `- Field: not published`; then a total line."""
    names = {p["id"]: f"{p['sheet']} · {p['university']}" for p in snapshot["programs"]}
    groups = defaultdict(list)
    for c in changes:
        new, old = c["new"], c["old"]
        if new["status"] == "not-published" and new["value"] is None:
            line = f"  - {c['field']}: not published"
        elif old is None:
            line = f"  + {c['field']}: {_shown(new)}"
        else:
            line = f"  ~ {c['field']}: {_shown(old)} → {_shown(new)}"
        groups[names.get(c["program_id"], f"program {c['program_id']}")].append(line)
    out = [part for name in sorted(groups) for part in (name, *groups[name])]
    out.append(f"{len(changes)} change(s) across {len(groups)} program(s)")
    return "\n".join(out)
