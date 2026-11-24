"""What an admin's laptop would publish: the local knowledge base (workbook + pipeline runs + reviews) compared with a
snapshot of the production database (GET /api/export).

Only facts a person or the pipeline produced are published: KB status "reviewed" (origin review) or a pipeline
status in FILLED (origin pipeline). Values typed into the workbook ("entered") and last year's values
("prior-year") came in with the import and are never published from here. A review decision "not-published" is
published as status "not-published" with no value, exactly as the importer writes it. Nothing else is removed.

The local knowledge base is built as the admin's pipeline sees it: from the saved snapshot
(<workbook>_field_pipeline/prod-snapshot.json) when there is one, so its programs carry production ids and match
by id. Otherwise (no snapshot saved yet) local programs match production ones by legacy_key "sheet:row" when the
university names agree; a program added through the API (no legacy_key) matches by sheet, university and program
name when exactly one does.
"""
from collections import defaultdict
from pathlib import Path

from field_pipeline.fields import FILLED
from field_pipeline.review import load_reviews
from field_pipeline.workbook import legacy_programs, load_snapshot, ref_of
from knowledge_base.__main__ import build, versions

from .importer import find_prior, not_published, origin, runs
from .values import to_text

COMPARED = ("value", "status", "cycle", "source_url", "quote")  # run and reviewed_by alone are not a change


def _new(value, status, cycle, source_url, quote, origin, run=None, reviewed_by=None) -> dict:
    return {"value": to_text(value), "status": status, "cycle": cycle or None, "source_url": source_url or None,
            "quote": quote or None, "origin": origin, "run": run, "reviewed_by": reviewed_by or None}


def _norm(s) -> str:
    return " ".join(str(s or "").split()).casefold()


def match(programs: list[dict], snapshot: dict) -> tuple[dict, list[dict]]:
    """({local program id: production id}, unmapped local programs each with a "reason")."""
    active = {p["id"] for p in snapshot["programs"] if p.get("active", 1)}
    by_key = {p["legacy_key"]: p for p in snapshot["programs"] if p.get("legacy_key") and p.get("active", 1)}
    keyless = defaultdict(list)
    for p in snapshot["programs"]:
        if not p.get("legacy_key") and p.get("active", 1):
            keyless[(p["sheet"], _norm(p["university"]), _norm(p["program"]))].append(p)
    ids, unmapped, taken = {}, [], set()
    for p in programs:
        if p["lastYearOnly"]:
            continue
        if p.get("program_id") in active:
            ids[p["id"]] = p["program_id"]
            taken.add(p["program_id"])
            continue
        if (target := by_key.get(f"{p['sheet']}:{p['row']}")) is not None:
            if _norm(target["university"]) != _norm(p["university"]):
                unmapped.append({**p, "reason": f"production row {target['legacy_key']} is {target['university']}"})
                continue
        else:
            found = keyless.get((p["sheet"], _norm(p["university"]), _norm(p["program"])), [])
            if len(found) != 1 or found[0]["id"] in taken:
                why = "more than one" if found else "no"
                unmapped.append({**p, "reason": f"{why} production program with this sheet, university and program"})
                continue
            target = found[0]
        taken.add(target["id"])
        ids[p["id"]] = target["id"]
    return ids, unmapped


def compare(workbook: Path, snapshot: dict, prior: Path | None = None) -> tuple[list[dict], list[dict]]:
    """(changes, unmapped). changes are in prod.db's change format; unmapped are the local programs that match no
    production program, each with a "reason"."""
    workbook = Path(workbook)
    prior = prior or find_prior(workbook)
    files = versions(workbook)
    local = load_snapshot(workbook)
    legacy = legacy_programs(local)
    reviews = load_reviews(workbook, local)
    kb = build(str(workbook), str(prior) if prior else None, files, reviews, local)
    run_of = runs(files, legacy)
    ids, unmapped = match(kb["programs"], snapshot)
    by_ref = {ref_of(p, legacy): ids[p["id"]] for p in kb["programs"] if p["id"] in ids}
    stored = {(f["program_id"], f["field"]): f for f in snapshot["facts"]}
    hidden = {k: rv for k, rv in reviews.items() if rv["decision"] == "not-published"}

    changes = []

    def offer(pid: int, field: str, new: dict) -> None:
        old = stored.get((pid, field))
        if old is None or any(old[k] != new[k] for k in COMPARED):
            changes.append({"program_id": pid, "field": field,
                            "old": old and {"value": old["value"], "status": old["status"]}, "new": new})

    programs = {p["id"]: p for p in kb["programs"]}
    for f in kb["facts"]:
        key = (ref_of(programs[f["p"]], legacy), f["f"])
        if (pid := ids.get(f["p"])) is None or key in hidden or not (f["s"] == "reviewed" or f["s"] in FILLED):
            continue
        o = origin(f["s"])
        offer(pid, f["f"], _new(f["v"], f["s"], f["c"], f["u"], f["q"], o,
                                run=run_of.get(key) if o == "pipeline" else None,
                                reviewed_by=reviews[key]["reviewer"] if o == "review" else None))
    for (ref, field), rv in sorted(hidden.items()):
        if (pid := by_ref.get(ref)) is not None:
            offer(pid, field, not_published(rv))
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
