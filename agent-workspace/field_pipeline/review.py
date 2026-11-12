"""Review queue: every cell the pipeline left unfilled (or whose source changed), grouped by what a person needs to
do, plus a log of the decisions people make on them.

    uv run --with openpyxl --with beautifulsoup4 --with requests --with pypdf \\
        python -m field_pipeline.review "../EDIT Competitive Analysis Research 2026-2027.xlsx" --export

(run from agent-workspace/). The web app's Review page works on the same queue; --export / --import round-trip
it through an Excel file for people who would rather work in a spreadsheet.

A cell is in the queue when the newest pipeline decision for it is not a filled status, nothing else fills it
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
returns a cell to the queue). knowledge_base uses reviewed values ahead of every pipeline value.
"""
import argparse
import csv
import re
import threading
from datetime import datetime
from pathlib import Path

from .fields import FILLED, field_for

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
                  "reviewer", "at"]
_lock = threading.Lock()


def reviews_path(workbook: Path) -> Path:
    return workbook.with_name(f"{workbook.stem}_field_pipeline") / "reviews.csv"


def load_reviews(workbook: Path) -> dict[tuple[str, int, str], dict]:
    """{(sheet, row, field): newest decision}; cells whose newest decision is `undo` are left out."""
    path = reviews_path(workbook)
    out: dict[tuple[str, int, str], dict] = {}
    if not path.exists():
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r["sheet"], int(r["row"]), r["field"])
            if r["decision"] == "undo":
                out.pop(key, None)
            else:
                out[key] = r
    return out


def append_reviews(workbook: Path, rows: list[dict]) -> None:
    path = reviews_path(workbook)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, REVIEW_COLUMNS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerows(rows)


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


def item_id(sheet: str, row: int, field: str) -> str:
    return f"{sheet}|{row}|{field}"


def build_queue(workbook: Path, prior: Path | None = None, db: Path | None = None) -> list[dict]:
    """The review queue, most useful groups first, then by sheet, school and field."""
    from knowledge_base.__main__ import attach_health, build, versions  # knowledge_base imports field_pipeline
    from .store import DEFAULT_DB
    workbook = Path(workbook)
    if prior is None:
        olds = sorted(workbook.parent.glob("OLD *.xlsx"))
        prior = olds[0] if len(olds) == 1 else None
    files = versions(workbook)
    kb = build(str(workbook), str(prior) if prior else None, files)
    attach_health(kb, db or DEFAULT_DB)
    programs = {p["id"]: p for p in kb["programs"]}
    facts = {}
    for f in kb["facts"]:
        p = programs[f["p"]]
        if p.get("row"):
            facts[(p["sheet"], p["row"], f["f"])] = (f, p)
    by_row = {(p["sheet"], p["row"]): p for p in kb["programs"] if p.get("row")}

    reviewed = load_reviews(workbook)
    latest: dict[tuple[str, int, str], dict] = {}  # newest pipeline decision per cell
    for path in files:
        with open(path, newline="", encoding="utf-8") as fh:
            for d in csv.DictReader(fh):
                latest[(d["sheet"], int(d["row"]), d["field"])] = {**d, "run": path.parent.name}

    items = []
    for key, d in latest.items():
        fact, p = facts.get(key, (None, by_row.get(key[:2])))
        if d["status"] in FILLED or p is None or key in reviewed or (fact and fact["s"] != "prior-year"):
            continue  # filled by this run, another run, the workbook or a review
        last = fact if fact and fact["s"] == "prior-year" else None
        items.append({"group": group_of(d["field"], d["status"], d.get("value") or ""), "status": d["status"],
                      "value": d.get("value") or "", "quote": d.get("quote") or "", "url": d.get("source_url") or "",
                      "detail": d.get("detail") or "", "why": why(d["status"], d.get("detail") or ""),
                      "run": d["run"], "snippet": "",
                      "last_year": last["v"] if last else "", "last_year_url": last["u"] if last else ""}
                     | _where(p, key))
    for key, (fact, p) in facts.items():  # values whose source the monthly check flagged
        if fact.get("h") in ("changed", "missing", "dead") and fact["s"] != "prior-year":
            items.append({"group": "stale", "status": fact["s"], "value": fact["v"], "quote": fact.get("q") or "",
                          "url": fact.get("u") or "", "detail": f"source {fact['h']} (checked {fact.get('ht')})",
                          "why": {"changed": "the source page now says something different",
                                  "missing": "the source page no longer has the quote",
                                  "dead": "the source page is gone"}[fact["h"]] + f" (checked {fact.get('ht')})",
                          "snippet": fact.get("hn") or "", "run": "", "last_year": "", "last_year_url": ""}
                         | _where(p, key))
    order = list(GROUPS)
    items.sort(key=lambda i: (order.index(i["group"]), i["sheet"], i["university"], i["row"], i["field"]))
    return items


def _where(p: dict, key: tuple) -> dict:
    f = field_for(key[2])
    return {"id": item_id(*key), "sheet": key[0], "row": key[1], "field": key[2], "university": p["university"],
            "yesno": bool((f and f.kind == "yesno") or YESNO_QUESTION.search(key[2])),
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
    return {"sheet": item["sheet"], "row": item["row"], "university": item["university"], "program": item["program"],
            "field": item["field"], "decision": decision, "value": value, "source_url": source_url, "quote": quote,
            "note": note, "reviewer": (reviewer or "").strip(), "at": datetime.now().isoformat(timespec="seconds")}


def undo_review(review: dict, reviewer: str = "") -> dict:
    return {**{k: review[k] for k in ("sheet", "row", "university", "program", "field")}, "decision": "undo",
            "value": "", "source_url": "", "quote": "", "note": "", "reviewer": reviewer,
            "at": datetime.now().isoformat(timespec="seconds")}


# Excel round trip ---------------------------------------------------------------------------------------------------
EXPORT_COLUMNS = [("Group", 22), ("Sheet", 18), ("Row", 6), ("University", 26), ("Program", 22), ("Field", 26),
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
        ws.append([GROUPS[i["group"]], i["sheet"], i["row"], i["university"], i["program"], i["field"], i["status"],
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
    """(reviews to append, problems) from a filled-in export. Rows match queue items by sheet, row and field."""
    from openpyxl import load_workbook
    by_id = {i["id"]: i for i in items}
    ws = load_workbook(path, read_only=True)["Review queue"]
    rows = ws.iter_rows(values_only=True)
    head = [str(h or "") for h in next(rows)]
    col = {h: k for k, h in enumerate(head)}
    reviews, problems = [], []
    for n, r in enumerate(rows, 2):
        get = lambda name: "" if r[col[name]] is None else str(r[col[name]]).strip()
        decision = get("Decision").casefold()
        if not decision:
            continue
        item = by_id.get(item_id(get("Sheet"), int(float(get("Row") or 0)), get("Field")))
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
    args = ap.parse_args()
    wb = args.xlsx.resolve()
    items = build_queue(wb, args.prior, args.db)
    if args.import_:
        reviews, problems = import_xlsx(args.import_, items, args.reviewer)
        append_reviews(wb, reviews)
        print(f"{len(reviews)} decision(s) -> {reviews_path(wb)}")
        for p in problems:
            print(f"  skipped {p}")
        items = build_queue(wb, args.prior, args.db)
    counts = {GROUPS[g]: sum(i["group"] == g for i in items) for g in GROUPS}
    print(f"{len(items)} cell(s) to review: " + ", ".join(f"{g} {n}" for g, n in counts.items() if n))
    if args.export is not None:
        dest = Path(args.export) if args.export else wb.with_name(f"{wb.stem}_field_pipeline") / "review_queue.xlsx"
        print(f"queue -> {export_xlsx(items, dest)}")


if __name__ == "__main__":
    main()
