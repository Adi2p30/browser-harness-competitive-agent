"""Load the workbook's knowledge base into an empty production database as version 1.

  python -m prod.importer "<workbook>.xlsx" prod.sqlite [--prior "OLD <...>.xlsx"] [--by NAME]

The knowledge base is built exactly as `python -m knowledge_base` builds it (every pipeline version, reviews.csv,
the one OLD *.xlsx next to the workbook), so each (program, field) keeps the value that wins there. Programs get
`legacy_key` "sheet:row"; schools only in last year's workbook are kept inactive with no key. The knowledge base is
built from the workbook's rows even when a production snapshot sits next to it: the import is what makes the ids.
A review decision "not-published" becomes a fact with that status and no value, as prod.diff publishes it. The import writes no
fact_history rows: its publishes row is the record, and history starts with the first publish. A database that
already has programs or publishes is refused.
"""
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from field_pipeline.fields import FILLED
from field_pipeline.review import load_reviews
from field_pipeline.workbook import ref_of
from knowledge_base.__main__ import build, versions

from . import db
from .values import to_text


class NotEmpty(Exception):
    """The database already holds programs or publishes."""


def origin(status: str) -> str:
    """Where a knowledge-base fact came from, by its status."""
    if status == "reviewed":
        return "review"
    if status in FILLED:
        return "pipeline"
    if status == "entered":
        return "workbook"
    if status == "prior-year":
        return "prior-year"
    raise ValueError(f"unknown knowledge-base status {status!r}")


def runs(files: list[Path], legacy: dict[str, int] | None = None) -> dict[tuple[int | str, str], str | None]:
    """{(program ref, field): version folder of the run whose value won}, by knowledge_base.decisions' rule (refs as
    field_pipeline.workbook.ref_of gives them); None for the decisions.csv written before versioning."""
    out = {}
    for path in files:
        run = path.parent.name if path.parent.parent.name == "runs" else None
        with open(path, newline="", encoding="utf-8") as f:
            out.update({(ref, d["field"]): run for d in csv.DictReader(f)
                        if d["status"] in FILLED and (ref := ref_of(d, legacy)) is not None})
    return out


NOT_PUBLISHED_CYCLE = "2026-2027"


def not_published(review: dict) -> dict:
    """The fact (NEW_FIELDS) for a review decision "not-published"; the importer and prod.diff both write this."""
    return {"value": None, "status": "not-published", "cycle": NOT_PUBLISHED_CYCLE,
            "source_url": review["source_url"] or None, "quote": review["quote"] or None, "origin": "review",
            "run": None, "reviewed_by": review["reviewer"] or None}


FACT = (f"INSERT INTO facts(program_id, field, {', '.join(db.NEW_FIELDS)}, version)"
        f" VALUES({', '.join('?' * (len(db.NEW_FIELDS) + 3))})")  # a repeated (program, field) raises


def find_prior(workbook: Path) -> Path | None:
    """The one OLD *.xlsx next to the workbook, as knowledge_base finds it."""
    olds = sorted(workbook.parent.glob("OLD *.xlsx"))
    return olds[0] if len(olds) == 1 else None


def import_workbook(workbook: Path, db_path: Path, prior: Path | None = None, by: str = "import") -> dict:
    """Import into db_path (created if missing) and return the summary. Raises FileNotFoundError for a missing
    workbook or prior and NotEmpty for a database with programs or publishes, before writing anything; any other
    failure rolls the whole import back."""
    workbook = Path(workbook)
    prior = Path(prior) if prior else find_prior(workbook)
    for path, what in ((workbook, "workbook"), (prior, "prior workbook")):
        if path and not path.is_file():
            raise FileNotFoundError(f"no {what} at {path}")
    conn = db.connect(db_path)
    try:
        def check():
            if conn.execute("SELECT EXISTS(SELECT 1 FROM programs) OR EXISTS(SELECT 1 FROM publishes)").fetchone()[0]:
                raise NotEmpty(f"{db_path} already has programs or publishes; the import only fills an empty database")

        check()
        files = versions(workbook)
        reviews = load_reviews(workbook)
        kb = build(str(workbook), str(prior) if prior else None, files, reviews)
        run_of = runs(files)
        ids, origins = {}, Counter()
        with db._tx(conn):
            check()  # again inside the write lock, against a concurrent import
            for p in kb["programs"]:
                pid = db.create_program(
                    conn, sheet=p["sheet"], university=p["university"], school=p["school"] or "",
                    program=p["program"] or "", link=p["url"] or "", added_by=by,
                    legacy_key=None if p["lastYearOnly"] else f"{p['sheet']}:{p['row']}")
                if p["lastYearOnly"]:
                    conn.execute("UPDATE programs SET active=0 WHERE id=?", (pid,))
                ids[p["id"]] = pid
            hidden = {k: rv for k, rv in reviews.items() if rv["decision"] == "not-published"}
            programs = {p["id"]: p for p in kb["programs"]}
            for f in kb["facts"]:
                key = (ref_of(programs[f["p"]], None), f["f"])
                if (value := to_text(f["v"])) is None or key in hidden:
                    continue
                o = origin(f["s"])
                conn.execute(FACT, (
                    ids[f["p"]], f["f"], value, f["s"], f["c"] or None, f["u"] or None, f["q"] or None, o,
                    run_of.get(key) if o == "pipeline" else None,
                    (reviews[key]["reviewer"] or None) if o == "review" else None, 1))
                origins[o] += 1
            rows = {ref_of(p, None): ids[p["id"]] for p in kb["programs"] if not p["lastYearOnly"]}
            for (ref, field), rv in sorted(hidden.items()):
                if ref in rows:
                    conn.execute(FACT, (rows[ref], field,
                                        *(not_published(rv)[k] for k in db.NEW_FIELDS), 1))
                    origins["review"] += 1
            conn.execute("INSERT INTO publishes(version, at, by, n_applied, n_conflicts, note) VALUES(1,?,?,?,0,?)",
                         (db.now(), by, sum(origins.values()), f"initial import from {workbook.name}"))
    finally:
        conn.close()
    inactive = sum(p["lastYearOnly"] for p in kb["programs"])
    return {"version": 1, "programs": len(kb["programs"]) - inactive, "inactive_programs": inactive,
            "facts": sum(origins.values()), "origins": dict(origins), "pipeline_versions": len(files),
            "prior": prior.name if prior else None}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workbook", type=Path)
    ap.add_argument("db", type=Path, help="production SQLite file (created if missing; must be empty)")
    ap.add_argument("--prior", type=Path, help="last year's workbook (default: the one OLD *.xlsx next to it)")
    ap.add_argument("--by", default="import", help="name recorded as added_by and on the publish")
    args = ap.parse_args()
    try:
        s = import_workbook(args.workbook, args.db, args.prior, args.by)
    except (NotEmpty, FileNotFoundError) as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    print(f"version {s['version']}: {s['programs']} active + {s['inactive_programs']} inactive programs, "
          f"{s['facts']} facts {s['origins']} from {s['pipeline_versions']} pipeline version(s), "
          f"prior {s['prior'] or 'none'} -> {args.db}")


if __name__ == "__main__":
    main()
