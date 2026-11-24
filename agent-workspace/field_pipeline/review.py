"""Review queue: every cell the pipeline left unfilled (or whose source changed), grouped by what a person needs to
do, plus a log of the decisions people make on them.

    uv run --with openpyxl --with beautifulsoup4 --with requests --with pypdf \\
        python -m field_pipeline.review "../EDIT Competitive Analysis Research 2026-2027.xlsx" --export

(run from agent-workspace/). The web app's Review page works on the same queue; --export / --import round-trip
it through an Excel file for people who would rather work in a spreadsheet.

The queue is per run (--run, or the Review page's run picker): the cells that run left unfilled. Without a run it
covers every run, using the newest decision per cell.

A cell is in the queue when the pipeline decision for it is not a filled status, nothing else fills it
(another run, a value typed into the workbook, an earlier review), or when the monthly source check
(health.py) found its source changed, missing or dead. Groups, in the order worth working through:

  candidate      the pipeline found a value but a check rejected it: the comment's value + quote, accept or not
  stale          the source no longer has the quote: type the new value, keep the current one, or blank it
  research       not found anywhere the pipeline looked: research by hand
  derived        tuition totals: computed once credit hours (and per-credit tuition) are in
  yesno          yes/no extras the site never mentions: answer No, or research
  ranking        rankings not found: most likely the program is not ranked
  not-published  class-profile / score / outcome fields the program's pages do not state: confirm

Decisions go to <workbook>_field_pipeline/reviews.csv (append-only; the newest decision per cell wins, `undo`
returns a cell to the queue). knowledge_base uses reviewed values ahead of every pipeline value. A cell is a
(program ref, field) pair (field_pipeline.workbook.ref_of); its queue id is "<ref>|<field>", "<program id>|<field>"
once a production snapshot is pulled.
"""
import argparse
import csv
import re
import threading
from datetime import datetime
from pathlib import Path

from .fields import FILLED, field_for
from .csvlog import append_csv
from .workbook import legacy_programs, load_snapshot, ref_of

GROUPS = {
    "candidate": "Rejected value to check",
    "stale": "Source changed",
    "research": "Research by hand",
    "derived": "Computed once credit hours are in",
    "yesno": "Yes/no not mentioned on the site",
    "ranking": "Ranking not found",
    "not-published": "Not published: confirm",
}
REJECTED = {"bad-value", "bad-quote", "other-program", "off-cycle", "bad-source", "out-of-range"}
DERIVED = re.compile(r"tuition total", re.I)
YESNO_NAME = re.compile(r"yes\s*/\s*no|\bbrief (explanation|description)\b|^student experience\b", re.I)
# what each group may decide (value = type a value; accept = take the rejected candidate; keep = current value
# still right; not-published = leave the cell blank)
CHOICES = {
    "candidate": ("accept", "value", "not-published"),
    "stale": ("value", "keep", "not-published"),
    "research": ("value", "not-published"),
    "derived": ("value", "not-published"),
    "yesno": ("no", "value", "not-published"),
    "ranking": ("not-ranked", "value", "not-published"),
    "not-published": ("not-published", "value"),
}
YESNO_QUESTION = re.compile(r"yes\s*/\s*no|\(y/n\)|^student experience\b", re.I)
WHY = {  # pipeline statuses in plain words
    "not-found": "not on any page the pipeline read",
    "no-start-link": "no program page and no university site to research from",
    "llm-error": "the extraction call failed",
    "not-published": "the program's pages were read and none states it",
    "bad-value": "a value was found but failed a check",
    "bad-quote": "the quote given for the value is not on the page",
    "other-program": "the value found is for another program or level",
    "off-cycle": "the value found is for a year that is too old",
    "bad-source": "the ranking was not from its publisher",
    "out-of-range": "the value found is implausible",
}
DECISIONS = ("accept", "value", "keep", "no", "not-ranked", "not-published", "undo")
REVIEW_COLUMNS = ["sheet", "row", "university", "program", "field", "decision", "value", "source_url", "quote", "note",
                  "reviewer", "at", "program_id"]  # new columns go last (csvlog)
_lock = threading.Lock()


def reviews_path(workbook: Path) -> Path:
    return workbook.with_name(f"{workbook.stem}_field_pipeline") / "reviews.csv"


def load_reviews(workbook: Path, snapshot: dict | None = None) -> dict[tuple[int | str, str], dict]:
    """{(program ref, field): newest decision}, refs as workbook.ref_of gives them with this snapshot (None: the
    workbook's rows); cells whose newest decision is `undo`, and decisions on no known program, are left out."""
    path = reviews_path(workbook)
    out: dict[tuple[int | str, str], dict] = {}
    if not path.exists():
        return out
    legacy = legacy_programs(snapshot)
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if (ref := ref_of(r, legacy)) is None:
                continue
            key = (ref, r["field"])
            if r["decision"] == "undo":
                out.pop(key, None)
            else:
                out[key] = r
    return out


def append_reviews(workbook: Path, rows: list[dict]) -> None:
    with _lock:
        append_csv(reviews_path(workbook), REVIEW_COLUMNS, rows)


def group_of(field: str, status: str, value: str) -> str:
    if status in REJECTED and value:
        return "candidate"
    if status == "not-published":
        return "not-published"
    f = field_for(field)
    if f and f.group.startswith("rank_"):
        return "ranking"
    if DERIVED.search(field):
        return "derived"
    if (f and f.kind == "yesno") or YESNO_NAME.search(field):
        return "yesno"
    return "research"


def why(status: str, detail: str) -> str:
    text = WHY.get(status, status)
    return f"{text} ({detail})" if detail else text


def item_id(ref: int | str, field: str) -> str:
    return f"{ref}|{field}"


def run_id(path: Path) -> str:
    """A run's id: its version folder name ("base" for the decisions.csv written before versioned runs)."""
    return "base" if path.parent.name.endswith("_field_pipeline") else path.parent.name


def list_runs(workbook: Path) -> list[dict]:
    """Runs with a decisions.csv, newest first: {"id", "label"}, labelled from runs.csv when it lists them."""
    from knowledge_base.__main__ import versions
    workbook = Path(workbook)
    index = workbook.with_name(f"{workbook.stem}_field_pipeline") / "runs.csv"
    info = {}
    if index.exists():
        with open(index, newline="", encoding="utf-8-sig") as f:
            info = {r["version"]: r for r in csv.DictReader(f)}
    out = []
    for path in reversed(versions(workbook)):
        rid = run_id(path)
        m = re.match(r"(\d{4}-\d\d-\d\d)_(\d\d)(\d\d)\d\d(.*)", rid)
        label = (f"{m.group(1)} {m.group(2)}:{m.group(3)}" + (" (fresh)" if "fresh" in m.group(4) else "")
                 if m else "Earliest run")
        if r := info.get(rid):
            sheets = r.get("sheets") or "all"
            label += f" · {'all programs' if sheets == 'all' else sheets}"
            if r.get("only"):
                label += f" · {r['only']}"
            if r.get("programs"):
                label += f" · program {r['programs']}"
            if r.get("rows"):
                label += f" · {r['rows']} row{'s' if r['rows'] != '1' else ''}"
        out.append({"id": rid, "label": label})
    return out


def build_queue(workbook: Path, prior: Path | None = None, db: Path | None = None, run: str | None = None) -> list[dict]:
    """The review queue's items (see `queue`)."""
    return queue(workbook, prior, db, run)["items"]


def queue(workbook: Path, prior: Path | None = None, db: Path | None = None, run: str | None = None) -> dict:
    """{"items", "run", "filled_elsewhere", "reviewed", "cells", "reviews"}: the review queue, most useful groups first,
    then by sheet, school and field. run = a run id (list_runs): only the cells that run left unfilled, and only
    changed sources whose value came from that run; None = every run, the newest result per cell. Cells already
    reviewed, or filled since by another run or the workbook, are left out and counted. cells and reviews are keyed
    (program ref, field), as load_reviews keys them, with the workbook's snapshot if it has one."""
    from knowledge_base.__main__ import attach_health, build, versions  # knowledge_base imports field_pipeline
    from .store import DEFAULT_DB
    workbook = Path(workbook)
    if prior is None:
        olds = sorted(workbook.parent.glob("OLD *.xlsx"))
        prior = olds[0] if len(olds) == 1 else None
    files = versions(workbook)
    scope = [p for p in files if run is None or run_id(p) == run]
    if run is not None and not scope:
        raise ValueError(f"no run '{run}'")
    snap = load_snapshot(workbook)
    legacy = legacy_programs(snap)
    reviewed = load_reviews(workbook, snap)
    kb = build(str(workbook), str(prior) if prior else None, files, reviewed, snap)
    attach_health(kb, db or DEFAULT_DB)
    by_ref = {ref_of(p, legacy): p for p in kb["programs"] if not p["lastYearOnly"]}
    programs = {p["id"]: p for p in kb["programs"]}
    facts = {}
    for f in kb["facts"]:
        p = programs[f["p"]]
        if not p["lastYearOnly"]:
            facts[(ref_of(p, legacy), f["f"])] = (f, p)

    latest: dict[tuple[int | str, str], dict] = {}  # newest pipeline decision per cell, within the scope
    for path in scope:
        with open(path, newline="", encoding="utf-8") as fh:
            for d in csv.DictReader(fh):
                if (ref := ref_of(d, legacy)) is not None:
                    latest[(ref, d["field"])] = {**d, "run": run_id(path)}

    items, elsewhere, done = [], 0, 0
    for key, d in latest.items():
        fact, p = facts.get(key, (None, by_ref.get(key[0])))
        if d["status"] in FILLED or p is None:
            continue
        if key in reviewed:
            done += 1
            continue
        if fact and fact["s"] != "prior-year":
            elsewhere += 1  # another run or the workbook has a value: nothing to review
            continue
        last = fact if fact and fact["s"] == "prior-year" else None
        items.append({"group": group_of(d["field"], d["status"], d.get("value") or ""), "status": d["status"],
                      "value": d.get("value") or "", "quote": d.get("quote") or "", "url": d.get("source_url") or "",
                      "detail": d.get("detail") or "", "why": why(d["status"], d.get("detail") or ""),
                      "run": d["run"], "snippet": "",
                      "last_year": last["v"] if last else "", "last_year_url": last["u"] if last else ""}
                     | _where(p, key))
    for key, (fact, p) in facts.items():  # values whose source the monthly check flagged
        if fact.get("h") not in ("changed", "missing", "dead") or fact["s"] == "prior-year":
            continue
        if run is not None and not ((d := latest.get(key)) and d["status"] in FILLED and d["value"] == fact["v"]):
            continue  # this value did not come from the chosen run
        items.append({"group": "stale", "status": fact["s"], "value": fact["v"], "quote": fact.get("q") or "",
                      "url": fact.get("u") or "", "detail": f"source {fact['h']} (checked {fact.get('ht')})",
                      "why": {"changed": "the source page now says something different",
                              "missing": "the source page no longer has the quote",
                              "dead": "the source page is gone"}[fact["h"]] + f" (checked {fact.get('ht')})",
                      "snippet": fact.get("hn") or "", "run": run or "", "last_year": "", "last_year_url": ""}
                     | _where(p, key))
    order = list(GROUPS)
    items.sort(key=lambda i: (order.index(i["group"]), i["sheet"], i["university"], i["row"] or 0, i["field"]))
    return {"items": items, "run": run, "filled_elsewhere": elsewhere, "reviewed": done, "cells": set(latest),
            "reviews": reviewed}


def _where(p: dict, key: tuple) -> dict:
    f = field_for(key[1])
    return {"id": item_id(*key), "program_id": p["program_id"], "sheet": p["sheet"], "row": p["row"], "field": key[1],
            "university": p["university"], "yesno": bool((f and f.kind == "yesno") or YESNO_QUESTION.search(key[1])),
            "program": p["program"], "program_url": p.get("url") or ""}


def make_review(item: dict, decision: str, value: str = "", source_url: str = "", note: str = "",
                reviewer: str = "") -> dict:
    """A reviews.csv row for one decision on a queue item; ValueError when the decision does not fit the item."""
    value, source_url, note = (value or "").strip(), (source_url or "").strip(), (note or "").strip()
    if decision not in CHOICES[item["group"]] and decision != "value":
        raise ValueError(f"'{decision}' is not a choice for {GROUPS[item['group']].lower()}")
    if source_url and not source_url.startswith(("http://", "https://")):
        raise ValueError("the source must be a web address (http/https)")
    quote = ""
    if decision == "accept":
        value, source_url, quote = item["value"], item["url"], item["quote"]
    elif decision == "keep":
        value, source_url = item["value"], source_url or item["url"]  # no quote: the health check then only
    elif decision == "no":                                            # checks the page still opens
        if not item.get("yesno", True):
            raise ValueError("this is not a yes/no question; leave it blank or type a description")
        value = "No"
    elif decision == "not-ranked":
        value = "Not ranked"
    elif decision == "not-published":
        value = ""
    elif not value:
        raise ValueError("type the value first")
    return {"program_id": item.get("program_id"), "sheet": item["sheet"], "row": item["row"],
            "university": item["university"], "program": item["program"], "field": item["field"], "decision": decision, "value": value, "source_url": source_url, "quote": quote,
            "note": note, "reviewer": (reviewer or "").strip(), "at": datetime.now().isoformat(timespec="seconds")}


def undo_review(review: dict, reviewer: str = "") -> dict:
    return {**{k: review.get(k) for k in ("program_id", "sheet", "row", "university", "program", "field")},
            "decision": "undo",
            "value": "", "source_url": "", "quote": "", "note": "", "reviewer": reviewer,
            "at": datetime.now().isoformat(timespec="seconds")}


# Excel round trip ---------------------------------------------------------------------------------------------------
EXPORT_COLUMNS = [("Group", 22), ("Sheet", 18), ("Row", 6), ("Program ID", 10), ("University", 26), ("Program", 22), ("Field", 26),
                  ("Pipeline status", 14), ("Candidate value", 22), ("Candidate quote", 40), ("Candidate source", 30),
                  ("Why", 30), ("Page now says", 30), ("Last year's value", 16), ("Program page", 30),
                  ("Decision", 14), ("Value", 18), ("Source URL", 30), ("Note", 24)]
INPUT_FROM = "Decision"


def export_xlsx(items: list[dict], path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "Review queue"
    ws.append([c for c, _ in EXPORT_COLUMNS])
    for i in items:
        ws.append([GROUPS[i["group"]], i["sheet"], i["row"], i["program_id"], i["university"], i["program"], i["field"], i["status"],
                   i["value"], i["quote"], i["url"], i["why"], i["snippet"], i["last_year"], i["program_url"],
                   None, None, None, None])
    first_input = [c for c, _ in EXPORT_COLUMNS].index(INPUT_FROM) + 1
    for col, (name, width) in enumerate(EXPORT_COLUMNS, 1):
        letter = ws.cell(1, col).column_letter
        ws.column_dimensions[letter].width = width
        ws.cell(1, col).font = Font(bold=True)
        if col >= first_input:
            ws.cell(1, col).fill = PatternFill("solid", fgColor="FFF2CC")
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    dv = DataValidation(type="list", formula1='"' + ",".join(d for d in DECISIONS if d != "undo") + '"',
                        allow_blank=True)
    letter = ws.cell(1, first_input).column_letter
    dv.add(f"{letter}2:{letter}{max(2, ws.max_row)}")
    ws.add_data_validation(dv)
    how = wb.create_sheet("How to")
    for line in [
        "Fill the yellow columns, leave the rest as they are, then import the file back:",
        "  python -m field_pipeline.review <workbook> --import <this file> --reviewer <your name>",
        "",
        "Decision:",
        "  accept         take the candidate value and quote (rejected-value rows)",
        "  value          use the Value column (put where you found it in Source URL)",
        "  keep           the current value is still right (source-changed rows)",
        "  no             the answer is No (yes/no rows)",
        "  not-ranked     the program is not ranked (ranking rows)",
        "  not-published  leave the cell blank: the program does not publish it",
        "Rows with no Decision are skipped.",
    ]:
        how.append([line])
    how.column_dimensions["A"].width = 100
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def import_xlsx(path: Path, items: list[dict], reviewer: str) -> tuple[list[dict], list[str]]:
    """(reviews to append, problems) from a filled-in export. Rows match queue items by program id and field, or
    (exports without program ids) by sheet, row and field."""
    from openpyxl import load_workbook
    by_id = {i["id"]: i for i in items}
    by_cell = {(i["sheet"], i["row"], i["field"]): i for i in items if i["row"] is not None}
    ws = load_workbook(path, read_only=True)["Review queue"]
    rows = ws.iter_rows(values_only=True)
    head = [str(h or "") for h in next(rows)]
    col = {h: k for k, h in enumerate(head)}
    reviews, problems = [], []
    for n, r in enumerate(rows, 2):
        get = lambda name: "" if col.get(name) is None or r[col[name]] is None else str(r[col[name]]).strip()
        decision = get("Decision").casefold()
        if not decision:
            continue
        pid, row = get("Program ID"), get("Row")
        try:
            item = (by_id.get(item_id(int(float(pid)), get("Field"))) if pid
                    else by_cell.get((get("Sheet"), int(float(row)), get("Field"))) if row else None)
        except ValueError:
            problems.append(f"row {n}: {get('University')} / {get('Field')}: program id {pid!r} or row {row!r} is not a"
                            " number")
            continue
        if item is None:
            problems.append(f"row {n}: {get('University')} / {get('Field')} is no longer in the queue")
            continue
        try:
            reviews.append(make_review(item, decision, get("Value"), get("Source URL"), get("Note"), reviewer))
        except (ValueError, KeyError) as exc:
            problems.append(f"row {n}: {get('University')} / {get('Field')}: {exc}")
    return reviews, problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--export", nargs="?", const="", metavar="FILE",
                    help="write the queue to an Excel file (default <workbook>_field_pipeline/review_queue.xlsx)")
    ap.add_argument("--import", dest="import_", type=Path, metavar="FILE", help="read decisions from a filled export")
    ap.add_argument("--reviewer", default="", help="your name, stored with imported decisions")
    ap.add_argument("--prior", type=Path, help="last year's workbook (default: the one OLD *.xlsx next to it)")
    ap.add_argument("--db", type=Path, help="field_pipeline SQLite with source health checks")
    ap.add_argument("--run", help="only this run's cells (its version folder name, or 'latest'); default every run")
    ap.add_argument("--list-runs", action="store_true", help="list the runs and exit")
    args = ap.parse_args()
    wb = args.xlsx.resolve()
    runs = list_runs(wb)
    if args.list_runs:
        for r in runs:
            print(f"{r['id']:28} {r['label']}")
        return
    run = (runs[0]["id"] if runs else None) if args.run == "latest" else args.run
    items = build_queue(wb, args.prior, args.db, run)
    if args.import_:
        reviews, problems = import_xlsx(args.import_, items, args.reviewer)
        append_reviews(wb, reviews)
        print(f"{len(reviews)} decision(s) -> {reviews_path(wb)}")
        for p in problems:
            print(f"  skipped {p}")
        items = build_queue(wb, args.prior, args.db, run)
    counts = {GROUPS[g]: sum(i["group"] == g for i in items) for g in GROUPS}
    print(f"{len(items)} cell(s) to review: " + ", ".join(f"{g} {n}" for g, n in counts.items() if n))
    if args.export is not None:
        name = f"review_queue_{run}.xlsx" if run else "review_queue.xlsx"
        dest = Path(args.export) if args.export else wb.with_name(f"{wb.stem}_field_pipeline") / name
        print(f"queue -> {export_xlsx(items, dest)}")


if __name__ == "__main__":
    main()
