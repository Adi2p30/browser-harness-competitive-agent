"""Read program rows from the workbook and write results to a copy (formatting kept, only target cells touched).

Schools added by a maintainer (add_school, field_pipeline.schools) live in "<workbook>.added_schools.csv" next to the workbook, never in
the workbook file itself. read_rows merges them into their sheet as rows numbered from ADDED_FIRST_ROW (10001, far
past any sheet's own rows, and fixed for good), so runs, reviews and the knowledge base treat them like any other
row. write() puts an added school into the output copy as a real row under the sheet's last school.
"""
import csv
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import PatternFill

AUTHOR = "field-pipeline"


@dataclass
class Row:
    sheet: str
    row: int              # 1-based Excel row
    university: str
    program: str
    start_url: str
    blanks: list[str]     # columns that are empty in the workbook

    @property
    def key(self) -> str:
        return f"{self.sheet}::{self.row}::{self.university}::{self.program}"


def _blank(v) -> bool:
    return v is None or str(v).strip() in ("", "nan", "None")


def headers(ws) -> list[str]:
    return [" ".join(str(c.value).split()) if c.value is not None else None for c in ws[1]]


ADDED_FIRST_ROW = 10001
ADDED_COLUMNS = ["id", "sheet", "university", "school", "program", "website_link", "added_by", "added_at", "removed_at"]
SCHOOL_COLUMN = re.compile(r"school\s*/\s*college( name)?", re.I)
PROGRAM_COLUMN = re.compile(r"program name", re.I)
_added_lock = threading.Lock()


def added_path(workbook) -> Path:
    workbook = Path(workbook)
    return workbook.with_name(f"{workbook.stem}.added_schools.csv")


def load_added(workbook, include_removed: bool = False) -> list[dict]:
    """Added schools, oldest first, each with its fixed "row" number."""
    path = added_path(workbook)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        out = [{**e, "row": ADDED_FIRST_ROW + int(e["id"]) - 1} for e in csv.DictReader(f)]
    return out if include_removed else [e for e in out if not e.get("removed_at")]


def id_values(head: list, entry: dict) -> dict[str, str]:
    """{header: value} of an added school's identifying columns on a sheet with this header."""
    out = {}
    for i, h in enumerate(head):
        if not h:
            continue
        if i == 0:
            out[h] = entry["university"]
        elif SCHOOL_COLUMN.fullmatch(h) and entry.get("school"):
            out[h] = entry["school"]
        elif PROGRAM_COLUMN.fullmatch(h) and entry.get("program"):
            out[h] = entry["program"]
        elif h == "Website Link" and entry.get("website_link"):
            out[h] = entry["website_link"]
    return out


def _norm(s) -> str:
    return " ".join(str(s or "").split()).casefold()


def add_school(workbook, sheet: str, university: str, school: str = "", program: str = "", website_link: str = "",
               added_by: str = "") -> dict:
    """Add a school to a program sheet (in the side file, not the workbook); ValueError when it can't be added."""
    university, school, program = (" ".join(str(v or "").split()) for v in (university, school, program))
    website_link = (website_link or "").strip()
    if not university:
        raise ValueError("type the university's name")
    if website_link and not website_link.startswith(("http://", "https://")):
        raise ValueError("the website link must be a web address (http/https)")
    cols, rows = read_rows(str(workbook), [sheet])
    if sheet not in cols:
        raise ValueError(f"no program sheet named '{sheet}'")
    university = university.upper()  # the sheets write universities in capitals
    for r in rows:
        if _norm(r.university) == _norm(university) and _norm(r.program) == _norm(program):
            where = f"row {r.row}" if r.row < ADDED_FIRST_ROW else "the added schools"
            raise ValueError(f"{university}{' (' + program + ')' if program else ''} is already on {sheet} ({where})")
    with _added_lock:
        entries = load_added(workbook, include_removed=True)
        entry = {"id": str(len(entries) + 1), "sheet": sheet, "university": university, "school": school,
                 "program": program, "website_link": website_link, "added_by": (added_by or "").strip(),
                 "added_at": datetime.now().isoformat(timespec="seconds"), "removed_at": ""}
        path = added_path(workbook)
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, ADDED_COLUMNS)
            if new:
                w.writeheader()
            w.writerow(entry)
    return {**entry, "row": ADDED_FIRST_ROW + int(entry["id"]) - 1}


def remove_school(workbook, school_id: str) -> dict | None:
    """Mark an added school removed (its row number stays reserved, its run results stay in the run folders)."""
    with _added_lock:
        entries = load_added(workbook, include_removed=True)
        hit = next((e for e in entries if e["id"] == str(school_id) and not e.get("removed_at")), None)
        if hit is None:
            return None
        hit["removed_at"] = datetime.now().isoformat(timespec="seconds")
        with open(added_path(workbook), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, ADDED_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(entries)
    return hit


def read_rows(path: str, sheets: list[str] | None = None) -> tuple[dict[str, list[str]], list[Row]]:
    added = load_added(path)
    wb = load_workbook(path, read_only=True)
    cols, rows = {}, []
    for ws in wb.worksheets:
        if sheets and ws.title not in sheets:
            continue
        head = headers(ws)
        if "Website Link" not in head:
            continue
        cols[ws.title] = head
        web = head.index("Website Link")
        prog = next((i for i, h in enumerate(head) if h and re.fullmatch(r"program name", h, re.I)), 2)
        for r, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            values = list(values) + [None] * (len(head) - len(values))
            if _blank(values[0]):
                continue
            rows.append(Row(ws.title, r, str(values[0]).strip(),
                            "" if _blank(values[prog]) else str(values[prog]).strip(),
                            "" if _blank(values[web]) else str(values[web]).strip(),
                            [h for h, v in zip(head, values) if h and _blank(v)]))
        for e in (e for e in added if e["sheet"] == ws.title):
            given = id_values(head, e)
            rows.append(Row(ws.title, e["row"], e["university"], e.get("program") or "", e.get("website_link") or "",
                            [h for h in head if h and h not in given]))
    wb.close()
    return cols, rows


def _last_school_row(ws) -> int:
    """The last row with a university name (column A); formatting alone doesn't count."""
    for r in range(ws.max_row, 1, -1):
        if not _blank(ws.cell(row=r, column=1).value):
            return r
    return 1


def selection_notes(rows: list[Row], sheets: list[str] | None, only: list[str] | None) -> tuple[bool, list[str]]:
    """(does any row match, what doesn't) for a run's --sheet / --only filters, matched as the pipeline matches them
    (exact sheet name; school text contained in the university, any case). `rows` must be every row of every sheet,
    so a school can be pointed to the sheets it is on: the web app lists every school of every sheet."""
    names = {r.sheet for r in rows}
    in_sheets = [r for r in rows if not sheets or r.sheet in sheets]
    notes = [f"no rows on sheet '{s}'" for s in sheets or [] if s not in names]
    for o in only or []:
        if any(o.casefold() in r.university.casefold() for r in in_sheets):
            continue
        where = sorted({r.sheet for r in rows if o.casefold() in r.university.casefold()})
        notes.append(f"{o} is not on {' or '.join(sheets) if sheets else 'any sheet'}"
                     + (f" (it is on: {', '.join(where)})" if where else ""))
    match = any(not only or any(o.casefold() in r.university.casefold() for o in only) for r in in_sheets)
    return match, notes


def read_values(path: str) -> dict[tuple[str, str], list[dict]]:
    """Every program row of a workbook as {column: value}, keyed by (sheet, university) casefolded."""
    wb = load_workbook(path, read_only=True)
    out: dict[tuple[str, str], list[dict]] = {}
    for ws in wb.worksheets:
        head = headers(ws)
        if "Website Link" not in head:
            continue
        for r, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if values and not _blank(values[0]):
                rec = {h: v for h, v in zip(head, values) if h and not _blank(v)}
                rec["_row"] = r
                out.setdefault((ws.title.casefold(), str(values[0]).strip().casefold()), []).append(rec)
    wb.close()
    return out


def write(src: str, dest: str, cells: list[tuple]) -> int:
    """cells: (sheet, row, column, value or None, comment, hyperlink[, fill hex]). None value = comment only."""
    wb = load_workbook(src)
    added = {(e["sheet"], e["row"]): e for e in load_added(src)}
    placed: dict[tuple[str, int], int] = {}
    n = 0
    for sheet, row, col, value, note, link, *fill in cells:
        ws = wb[sheet]
        head = headers(ws)
        if (sheet, row) in added:  # an added school: a real row under the sheet's last school, the first time
            if (sheet, row) not in placed:
                taken = [r for (s, _), r in placed.items() if s == sheet]
                last = max(taken) if taken else _last_school_row(ws)
                placed[(sheet, row)] = last + 1
                for h, v in id_values(head, added[(sheet, row)]).items():
                    ws.cell(row=last + 1, column=head.index(h) + 1).value = v
            row = placed[(sheet, row)]
        cell = ws.cell(row=row, column=head.index(col) + 1)
        if value is not None:
            cell.value = value
            n += 1
        cell.comment = Comment(note[:1500], AUTHOR, width=420, height=180)
        if link and value is not None:
            cell.hyperlink = link
        if fill and fill[0] and value is not None:
            cell.fill = PatternFill("solid", start_color=fill[0], end_color=fill[0])
    wb.save(dest)
    return n
