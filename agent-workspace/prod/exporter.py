"""Export the production database as a filled copy of the template workbook.

  python -m prod.exporter <prod.sqlite> <template workbook> <out.xlsx>

A program with legacy_key "sheet:row" (row below 10001) fills that row of the template, through
field_pipeline.workbook.write; the row's university must match the program's, or nothing is written (ValueError).
Every other active program (an added school, rows 10001 and up, or one with no legacy_key) goes in a new row
inserted right under the sheet's last real school (note and caption rows, which build() skips, don't count), in
legacy-row then id order, with its university, school, program and link. Each cell gets its fact value, a comment
"status · source_url" plus the quote, and a hyperlink to source_url. A fact equal to what the template cell
already holds is not written, so typed numbers keep their type and format. The template file is never written.
"""
import argparse
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.comments import Comment

from field_pipeline.workbook import (ADDED_FIRST_ROW, AUTHOR, _blank, _norm, headers, id_values, legacy_row, read_rows,
                                     write)
from knowledge_base.__main__ import ID_COLUMNS, JUNK, current_values

from . import db
from .values import to_text

QUOTE_CHARS = 300


def note(f: dict) -> str:
    """Cell comment: "status · source_url", then the quote, shortened."""
    head = " · ".join(x for x in (f["status"], f["source_url"]) if x)
    q = f["quote"] or ""
    return head + (f'\n"{q[:QUOTE_CHARS]}…"' if len(q) > QUOTE_CHARS else f'\n"{q}"' if q else "")


def last_real_school(ws) -> int:
    """The last row whose column A is a school, not a note or caption row (build()'s JUNK rule); 1 if none."""
    for r in range(ws.max_row, 1, -1):
        v = ws.cell(row=r, column=1).value
        if not _blank(v) and not JUNK.fullmatch(str(v).strip()):
            return r
    return 1


def export_workbook(db_path: Path, template: Path, out: Path) -> int:
    """Write a filled copy of template to out and return the number of cells written."""
    if Path(out).resolve() == Path(template).resolve():
        raise ValueError("out must not be the template workbook")
    cols, _ = read_rows(str(template))
    typed = current_values(str(template))  # {(sheet, row): {column: value}} already in the template
    conn = db.connect(db_path, readonly=True)
    try:
        snap = db.snapshot(conn)
    finally:
        conn.close()
    facts: dict[int, list[dict]] = {}
    for f in snap["facts"]:
        if f["value"] is not None:
            facts.setdefault(f["program_id"], []).append(f)

    def cells(p: dict, row: int, held: dict) -> list[tuple]:
        head = cols[p["sheet"]]  # identifying columns come from the program itself, never from a fact
        return [(p["sheet"], row, f["field"], f["value"], note(f), f["source_url"] or None)
                for f in facts.get(p["id"], []) if f["field"] in head and not ID_COLUMNS.match(f["field"])
                and to_text(held.get(f["field"])) != f["value"]]

    in_place, extra = [], []
    for p in snap["programs"]:
        if not p["active"] or p["sheet"] not in cols:
            continue
        row = legacy_row(p["legacy_key"])
        if row is not None and row < ADDED_FIRST_ROW:
            held = typed.get((p["sheet"], row), {})
            there = held.get(cols[p["sheet"]][0])
            if _norm(there) != _norm(p["university"]):
                raise ValueError(f"{p['sheet']} row {row} is {there!r} in the template, not {p['university']!r}"
                                 f" (program {p['id']}, legacy_key {p['legacy_key']!r})")
            in_place += cells(p, row, held)
        else:
            extra.append((row is None, row or 0, p["id"], p))
    n = write(str(template), str(out), in_place)
    if not extra:
        return n
    wb = load_workbook(out)
    per_sheet: dict[str, list[dict]] = {}
    for *_, p in sorted(extra, key=lambda e: e[:3]):
        per_sheet.setdefault(p["sheet"], []).append(p)
    for sheet, progs in per_sheet.items():  # new rows inserted right under the last real school
        ws = wb[sheet]
        head = headers(ws)
        first = last_real_school(ws) + 1
        ws.insert_rows(first, len(progs))
        for r in ws.iter_rows(min_row=first + len(progs)):  # openpyxl doesn't move a hyperlink's anchor
            for cell in r:
                if cell.hyperlink:
                    cell.hyperlink.ref = cell.coordinate
        for row, p in enumerate(progs, start=first):
            entry = {"university": p["university"], "school": p["school"], "program": p["program"],
                     "website_link": p["link"]}
            for h, v in id_values(head, entry).items():
                ws.cell(row=row, column=head.index(h) + 1).value = v
            for *_, col, value, text, link in cells(p, row, {}):  # as write() fills a cell, without a fill
                cell = ws.cell(row=row, column=head.index(col) + 1)
                cell.value, cell.comment = value, Comment(text[:1500], AUTHOR, width=420, height=180)
                if link:
                    cell.hyperlink = link
                n += 1
    wb.save(out)
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", type=Path)
    ap.add_argument("template", type=Path)
    ap.add_argument("out", type=Path)
    args = ap.parse_args()
    n = export_workbook(args.db, args.template, args.out)
    print(f"{n} cells -> {args.out}")


if __name__ == "__main__":
    main()
