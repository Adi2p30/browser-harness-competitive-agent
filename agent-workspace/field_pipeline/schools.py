"""Maintainers: add a school that isn't in the workbook yet, list the added ones, or remove one. Not in the web app.

    uv run --with openpyxl python -m field_pipeline.schools "../EDIT Competitive Analysis Research 2026-2027.xlsx" \\
        add --sheet "MS HRM - Residential" --university "Michigan State University" \\
        --school "Eli Broad College of Business" --program "Master of Human Resources and Labor Relations"
    ... list
    ... remove 3

(run from agent-workspace/). Added schools go to "<workbook>.added_schools.csv" next to the workbook, never into
the workbook itself, and every run, review and knowledge-base build reads them as rows numbered from 10001 (see
field_pipeline.workbook). Commit that file so the team gets the school; `add` prints the command that researches
just the new row.
"""
import argparse
import shlex
import sys
from pathlib import Path

from .workbook import ADDED_FIRST_ROW, add_school, added_path, load_added, remove_school


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx", type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="add a school to a program sheet")
    add.add_argument("--sheet", required=True, help="the program sheet, e.g. 'MS HRM - Residential'")
    add.add_argument("--university", required=True)
    add.add_argument("--school", default="", help="school / college name")
    add.add_argument("--program", default="", help="program name")
    add.add_argument("--link", default="", help="program page (optional: without it the pipeline searches)")
    add.add_argument("--by", default="", help="your name, stored with the school")
    sub.add_parser("list", help="list the added schools")
    rm = sub.add_parser("remove", help="remove an added school (its row number stays reserved)")
    rm.add_argument("id")
    args = ap.parse_args(argv)
    wb = args.xlsx.resolve()

    if args.cmd == "add":
        try:
            e = add_school(wb, args.sheet, args.university, args.school, args.program, args.link, args.by)
        except ValueError as exc:
            print(f"not added: {exc}", file=sys.stderr)
            return 1
        print(f"added #{e['id']}: {e['university']} on {e['sheet']} as row {e['row']} -> {added_path(wb).name}")
        print("research it with:\n  python -m field_pipeline "
              + shlex.join([str(wb), "--sheet", e["sheet"], "--row", f"{e['sheet']}:{e['row']}"]))
        print(f"commit {added_path(wb).name} so the team gets it")
    elif args.cmd == "list":
        entries = load_added(wb, include_removed=True)
        if not entries:
            print(f"no added schools ({added_path(wb).name} doesn't exist yet)")
        for e in entries:
            state = f"removed {e['removed_at'][:10]}" if e.get("removed_at") else f"row {e['row']}"
            name = " · ".join(v for v in (e["university"], e.get("school"), e.get("program")) if v)
            print(f"#{e['id']:>3}  {e['sheet']:32}  {name}  [{state}; added {e['added_at'][:10]}"
                  + (f" by {e['added_by']}" if e.get("added_by") else "") + "]")
    else:
        e = remove_school(wb, args.id)
        if e is None:
            print(f"no added school #{args.id} (or it was already removed)", file=sys.stderr)
            return 1
        print(f"removed #{e['id']}: {e['university']} on {e['sheet']}; row {ADDED_FIRST_ROW + int(e['id']) - 1} "
              "stays reserved and its results stay in the run folders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
