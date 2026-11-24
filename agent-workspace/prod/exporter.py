"""Export the production database as a workbook laid out like the template.

  python -m prod.exporter <prod.sqlite> <template workbook> <out.xlsx>

A clean rebuild, not an overlay: from the template only the visible program sheets' header rows are kept (header
values and formatting, column widths, frozen panes). No template data row, comment, hidden sheet or other sheet is
copied, so nothing typed into the template reaches the export. Each sheet then gets one row per active program,
ordered by legacy row (from legacy_key "sheet:row"), programs without one last, then by id: its identifying columns
from the program record, and every fact with a value in its column, with a comment "status · source_url" plus the
quote and a hyperlink to source_url. A fact without a value (not-published) leaves its cell blank. The template file
is never written.
"""
import argparse
from copy import copy
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment

from field_pipeline.workbook import AUTHOR, headers, id_values, legacy_row
from knowledge_base.__main__ import ID_COLUMNS

from . import db

QUOTE_CHARS = 300


def note(f: dict) -> str:
    """Cell comment: "status · source_url", then the quote, shortened."""
    head = " · ".join(x for x in (f["status"], f["source_url"]) if x)
    q = f["quote"] or ""
    return head + (f'\n"{q[:QUOTE_CHARS]}…"' if len(q) > QUOTE_CHARS else f'\n"{q}"' if q else "")


def _copy_header(src, dest) -> None:
    """The header row (values and formatting, no comments), column widths, row height and frozen panes."""
    for cell in src[1]:
        new = dest.cell(row=1, column=cell.column, value=cell.value)
        if cell.has_style:
            new.font, new.fill, new.border = copy(cell.font), copy(cell.fill), copy(cell.border)
            new.alignment, new.number_format = copy(cell.alignment), cell.number_format
    for key, dim in src.column_dimensions.items():
        dest.column_dimensions[key].width = dim.width
    if src.row_dimensions[1].height:
        dest.row_dimensions[1].height = src.row_dimensions[1].height
    dest.freeze_panes = src.freeze_panes


def export_workbook(db_path: Path, template: Path, out: Path) -> int:
    """Write the export to out and return the number of fact cells written."""
    if Path(out).resolve() == Path(template).resolve():
        raise ValueError("out must not be the template workbook")
    conn = db.connect(db_path, readonly=True)
    try:
        snap = db.snapshot(conn)
    finally:
        conn.close()
    return write_snapshot(snap, template, out)


def write_snapshot(snap: dict, template: Path, out: Path) -> int:
    """export_workbook for a db.snapshot() already read."""
    facts: dict[int, list[dict]] = {}
    for f in snap["facts"]:
        if f["value"] is not None:
            facts.setdefault(f["program_id"], []).append(f)
    src = load_workbook(template)
    wb = Workbook()
    wb.remove(wb.active)
    n = 0
    for ws in src.worksheets:
        head = headers(ws)
        if ws.sheet_state != "visible" or "Website Link" not in head:
            continue
        dest = wb.create_sheet(ws.title)
        _copy_header(ws, dest)
        cols = {h: i + 1 for i, h in enumerate(head) if h}
        progs = sorted((p for p in snap["programs"] if p["active"] and p["sheet"] == ws.title),
                       key=lambda p: (legacy_row(p["legacy_key"]) is None, legacy_row(p["legacy_key"]) or 0, p["id"]))
        for row, p in enumerate(progs, start=2):
            entry = {"university": p["university"], "school": p["school"], "program": p["program"],
                     "website_link": p["link"]}
            for h, v in id_values(head, entry).items():
                dest.cell(row=row, column=cols[h], value=v)
            for f in facts.get(p["id"], []):
                if f["field"] not in cols or ID_COLUMNS.match(f["field"]):
                    continue  # identifying columns come from the program itself, never from a fact
                cell = dest.cell(row=row, column=cols[f["field"]], value=f["value"])
                cell.comment = Comment(note(f)[:1500], AUTHOR, width=420, height=180)
                if f["source_url"]:
                    cell.hyperlink = f["source_url"]
                n += 1
    src.close()
    if not wb.worksheets:
        wb.create_sheet("Programs")
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
