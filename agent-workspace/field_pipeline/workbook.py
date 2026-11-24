"""The programs a run works on, and writing results to a copy of the workbook (formatting kept, only target cells
touched).

With the admin's production snapshot (`python -m prod.client pull` saves it to
<workbook>_field_pipeline/prod-snapshot.json) the programs are production's active ones, each identified by its
permanent program id; the workbook is then only the template for each sheet's columns. Without one they are the
workbook's rows, identified by "sheet:row" (a production program's legacy_key). Every results file, review and
knowledge-base build refers to a program by that `ref`.

Schools a maintainer added before production existed live in "<workbook>.added_schools.csv" next to the workbook.
read_rows merges them into their sheet as rows numbered from ADDED_FIRST_ROW (10001, fixed for good), and write()
puts one into the output copy as a real row under the sheet's last school. New programs are added with
`python -m prod.client add-program`.
"""
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import PatternFill

AUTHOR = "field-pipeline"


@dataclass
class Row:
    sheet: str
    row: int | None       # 1-based Excel row in the workbook; None for a production program not in it
    university: str
    program: str
    start_url: str
    blanks: list[str]     # columns to research: empty in the workbook, or with no current production fact
    program_id: int | None = None  # production id (snapshot runs only)
    school: str = ""      # from production (snapshot runs only)

    @property
    def key(self) -> str:
        """The row's key in pipeline.sqlite."""
        if self.program_id is not None:
            return f"p{self.program_id}"
        return f"{self.sheet}::{self.row}::{self.university}::{self.program}"

    @property
    def ref(self) -> int | str:
        """The program id with a snapshot, else "sheet:row"."""
        return self.program_id if self.program_id is not None else f"{self.sheet}:{self.row}"


def _blank(v) -> bool:
    return v is None or str(v).strip() in ("", "nan", "None")


def headers(ws) -> list[str]:
    return [" ".join(str(c.value).split()) if c.value is not None else None for c in ws[1]]


def snapshot_path(workbook) -> Path:
    workbook = Path(workbook)
    return workbook.with_name(f"{workbook.stem}_field_pipeline") / "prod-snapshot.json"


def load_snapshot(workbook) -> dict | None:
    """The admin's local copy of production (prod.client pull), or None before the first pull."""
    path = snapshot_path(workbook)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def legacy_row(key: str | None) -> int | None:
    """The workbook row of a legacy_key "sheet:row", else None."""
    if not key or not (m := re.search(r":(\d+)$", key)):
        return None
    return int(m.group(1))


def legacy_ids(snapshot: dict | None) -> dict[str, int] | None:
    """{legacy_key: program id} of a snapshot; None without one."""
    if snapshot is None:
        return None
    return {p["legacy_key"]: p["id"] for p in snapshot["programs"] if p.get("legacy_key")}


def ref_of(d: dict, legacy: dict[str, int] | None) -> int | str | None:
    """The ref (see Row.ref) of a decisions / reviews / recheck row or a knowledge-base program. With a snapshot
    (legacy = legacy_ids(snapshot)) that is its program_id; a row written before program ids maps by its legacy key
    "sheet:row", None when no program has it."""
    key = f"{d['sheet']}:{'' if d.get('row') is None else d['row']}"
    if legacy is None:
        return key
    return int(d["program_id"]) if d.get("program_id") not in (None, "") else legacy.get(key)


def append_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    """Append rows to a CSV log, first giving a log started before `columns` grew the new columns (blank in its old
    rows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            old = None if r.fieldnames == columns else list(r)
        if old is not None:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, columns, extrasaction="ignore")
                w.writeheader()
                w.writerows(old)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, columns, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


ADDED_FIRST_ROW = 10001
SCHOOL_COLUMN = re.compile(r"school\s*/\s*college( name)?", re.I)
PROGRAM_COLUMN = re.compile(r"program name", re.I)


def added_path(workbook) -> Path:
    workbook = Path(workbook)
    return workbook.with_name(f"{workbook.stem}.added_schools.csv")


def load_added(workbook) -> list[dict]:
    """Added schools not removed, oldest first, each with its fixed "row" number."""
    path = added_path(workbook)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        out = [{**e, "row": ADDED_FIRST_ROW + int(e["id"]) - 1} for e in csv.DictReader(f)]
    return [e for e in out if not e.get("removed_at")]


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


def read_rows(path: str, sheets: list[str] | None = None,
              snapshot: dict | None = None) -> tuple[dict[str, list[str]], list[Row]]:
    """({sheet: column headers}, programs) of the workbook's program sheets: the workbook's rows, or with a snapshot
    production's active programs on those sheets (see snapshot_rows)."""
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
        mine = []
        for r, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            values = list(values) + [None] * (len(head) - len(values))
            if _blank(values[0]):
                continue
            mine.append(Row(ws.title, r, str(values[0]).strip(),
                            "" if _blank(values[prog]) else str(values[prog]).strip(),
                            "" if _blank(values[web]) else str(values[web]).strip(),
                            [h for h, v in zip(head, values) if h and _blank(v)]))
        for e in (e for e in added if e["sheet"] == ws.title):
            given = id_values(head, e)
            mine.append(Row(ws.title, e["row"], e["university"], e.get("program") or "", e.get("website_link") or "",
                            [h for h in head if h and h not in given]))
        rows += mine if snapshot is None else snapshot_rows(snapshot, ws.title, head, mine)
    wb.close()
    return cols, rows


def snapshot_rows(snapshot: dict, sheet: str, head: list, template: list[Row]) -> list[Row]:
    """The sheet's active production programs, in legacy-row then id order. A program's row is its legacy row when
    the workbook's row there is the same university (else None: it isn't in the workbook). Its blanks are the
    columns with no current fact (any origin but prior-year), less the identifying columns it has."""
    at = {r.row: r.university for r in template}
    have: dict[int, set[str]] = {}
    for f in snapshot["facts"]:
        if f["origin"] != "prior-year":
            have.setdefault(f["program_id"], set()).add(f["field"])
    mine = [p for p in snapshot["programs"] if p["active"] and p["sheet"] == sheet]
    out = []
    for p in sorted(mine, key=lambda p: (legacy_row(p["legacy_key"]) is None, legacy_row(p["legacy_key"]) or 0,
                                         p["id"])):
        row = legacy_row(p["legacy_key"])
        if row is not None and _norm(at.get(row)) != _norm(p["university"]):
            row = None
        filled = have.get(p["id"], set()) | set(id_values(head, {
            "university": p["university"], "school": p["school"], "program": p["program"], "website_link": p["link"]}))
        out.append(Row(sheet, row, p["university"], p["program"] or "", p["link"] or "",
                       [h for h in head if h and h not in filled], p["id"], p["school"] or ""))
    return out


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
