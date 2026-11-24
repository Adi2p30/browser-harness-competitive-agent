"""Knowledge base from the production database (prod.db) instead of the workbook and run folders.

build_from_db returns the same dict as knowledge_base.__main__.build(), so okf_export.write_bundle works on it
unchanged. The database stores no column order, so each sheet's fields (order, group, kind) come from the template
workbook's headers, exactly as build() derives them. Only active programs are included; program ids are database ids.
"""
import re
from collections import Counter
from datetime import date
from pathlib import Path

from field_pipeline.fields import SKIP, field_for, link_target
from field_pipeline.validate import NUMERIC, to_number
from field_pipeline.workbook import read_rows

from prod import db

from .__main__ import GROUP_LABELS, ID_COLUMNS, words
from .okf_export import write_bundle

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE = Path("docs", "okf", "knowledge")  # where write_bundle writes under a repo root


def sheet_fields(head: list) -> list[dict]:
    """[{name, group, kind}] of a sheet's header: build()'s rule (ID and link columns dropped, grouped, workbook
    order within each group)."""
    fields = []
    for c in head:
        if not c or c.startswith("Unnamed") or ID_COLUMNS.match(c) or link_target(c, head):
            continue
        f = field_for(c)
        label = GROUP_LABELS.get(f.group if f else "other", "Other")
        fields.append({"name": c, "group": label, "kind": f.kind if f else ("number" if SKIP.search(c) else "text")})
    order = list(dict.fromkeys(GROUP_LABELS.values()))
    fields.sort(key=lambda f: order.index(f["group"]))
    return fields


def legacy_row(key: str | None) -> int | None:
    """The workbook row of a legacy_key "sheet:row", else None."""
    if not key or not (m := re.search(r":(\d+)$", key)):
        return None
    return int(m.group(1))


def _fact(pid: int, name: str, kind: str, f: dict) -> dict:
    """A KB fact, with n and percent handling as build()'s add()."""
    v = f["value"]
    n = to_number(v, kind) if kind in NUMERIC else None
    if kind == "percent" and n is not None and n <= 1 and re.fullmatch(r"[\d.]+", v):
        n = round(n * 100, 1)  # Excel stores 28% as 0.28
        v = f"{n:g}%"
    return {"p": pid, "f": name, "n": n, "v": v, "c": f["cycle"] or "", "s": f["status"],
            "u": f["source_url"] or "", "q": f["quote"] or ""}


def build_from_db(conn, workbook: Path) -> dict:
    """{"built", "sheets", "programs", "facts"} from the database's current snapshot."""
    return build_from_snapshot(db.snapshot(conn), workbook)


def build_from_snapshot(snap: dict, workbook: Path) -> dict:
    """build_from_db for a db.snapshot() already read (one transaction, so its "version" labels it exactly).
    Programs on a sheet the template doesn't have, and facts for a column it doesn't have, are left out."""
    cols, _ = read_rows(str(workbook))
    by_program: dict[int, dict[str, dict]] = {}
    for f in snap["facts"]:
        by_program.setdefault(f["program_id"], {})[f["field"]] = f
    sheets, programs, facts = [], [], []
    for sheet, head in cols.items():
        fields = sheet_fields(head)
        sheets.append({"name": sheet, "fields": fields})
        mine = [(legacy_row(p["legacy_key"]), p) for p in snap["programs"] if p["active"] and p["sheet"] == sheet]
        for row, p in sorted(mine, key=lambda rp: (rp[0] is None, rp[0] or 0, rp[1]["id"])):
            programs.append({"id": p["id"], "sheet": sheet, "row": row, "university": p["university"],
                             "program": p["program"] or "", "school": p["school"] or "", "url": p["link"] or "",
                             "purdue": words(p["university"]) in (["purdue", "university"],
                                                                  ["purdue", "university", "west", "lafayette"]),
                             "lastYearOnly": False})
            stored = by_program.get(p["id"], {})
            facts += [_fact(p["id"], f["name"], f["kind"], stored[f["name"]]) for f in fields
                      if stored.get(f["name"]) and stored[f["name"]]["value"]]
    return {"built": date.today().isoformat(), "sheets": sheets, "programs": programs, "facts": facts}


def write_from_db(db_path: Path, workbook: Path, repo_root: Path = REPO_ROOT, knowledge_dir: Path | None = None) -> Path:
    """Rebuild the OKF knowledge bundle from the database (opened read-only) and return its folder.
    write_bundle always writes to <repo root>/docs/okf/knowledge, so knowledge_dir must end in docs/okf/knowledge;
    its repo root is then used instead of repo_root."""
    if knowledge_dir is not None:
        knowledge_dir = Path(knowledge_dir).resolve()
        if knowledge_dir.parts[-3:] != BUNDLE.parts:
            raise ValueError(f"knowledge_dir must end in {BUNDLE} (write_bundle's fixed location): {knowledge_dir}")
        repo_root = knowledge_dir.parents[2]
    conn = db.connect(db_path, readonly=True)
    try:
        snap = db.snapshot(conn)
    finally:
        conn.close()
    kb, v = build_from_snapshot(snap, workbook), snap["version"]
    by = Counter(f["s"] for f in kb["facts"])
    return write_bundle(kb, Path(repo_root), f"prod v{v}: {len(kb['programs'])} programs, {len(kb['facts'])} facts {dict(by)}")
