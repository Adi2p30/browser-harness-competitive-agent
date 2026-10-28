"""Writes the knowledge base out as an Open Knowledge Format (OKF v0.2) bundle at
<repo>/docs/okf/knowledge/: one `Program` concept per program (e.g. "Online MS Economics") —
one link per program — and inside it one table with a row per college and a column per field,
so the whole cross-college comparison for that program is in one place. Each value cell links
to its source URL/quote (or, for a derived field, to its shared `Attested Computation` concept
— the three tuition totals field_pipeline computes itself, `COMPUTED_TOTALS` in
field_pipeline/__main__.py). `docs/okf/index.md` and `docs/okf/project/` are hand-written and
never touched here.
"""
import re
from datetime import date, datetime, timezone
from pathlib import Path

from .okf_format import write_concept

PRODUCER = "knowledge_base/okf_export"
# (concept slug, the per-credit field it was derived from) for each field_pipeline COMPUTED_TOTALS pair
COMPUTATIONS = {
    "In-State Tuition Total": ("in-state-tuition-total", "In-State Tuition Per Credit"),
    "Out-of-State Tuition Total": ("out-of-state-tuition-total", "Out-of-State Tuition Per Credit"),
    "International Tuition Total": ("international-tuition-total", "International Tuition Per Credit"),
}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).casefold()).strip("-") or "x"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lifecycle(cycle: str) -> tuple[str, str | None]:
    """A fact from the current research cycle (2026-2027) is stable until next cycle's refresh;
    anything carried forward from an earlier cycle is a draft, stale once that cycle is two years old."""
    year = re.search(r"(20\d\d)", cycle or "")
    if not year:
        return "stable", None
    stale_after = f"{int(year.group(1)) + 2}-10-01T00:00:00Z"
    return ("stable", stale_after) if str(cycle).startswith("2026") else ("draft", stale_after)


def _field_order(kb: dict) -> dict[tuple[str, str], int]:
    """A field's position in its sheet's own (group-then-name) order, from knowledge_base's
    `build()` — reused here so one program's table reads in the same group order as the report."""
    return {(s["name"], f["name"]): i for s in kb["sheets"] for i, f in enumerate(s["fields"])}


def _write_computations(bundle: Path) -> None:
    body = "# Computations\n\n" + "\n".join(
        f"- [{total}](/knowledge/computations/{cslug}.md)" for total, (cslug, _) in COMPUTATIONS.items())
    write_concept(bundle / "computations" / "index.md", {"title": "Computations"}, body)
    for total, (cslug, per) in COMPUTATIONS.items():
        body = (
            "# Computation\n\n"
            "```python\n"
            f"round(money(r_per['value']) * money(credits['value']))  # {per} x Credit Hours\n"
            "```\n\n"
            "Used only when the program's own pages state no total for this field; see "
            "`COMPUTED_TOTALS` in `agent-workspace/field_pipeline/__main__.py`.\n"
        )
        write_concept(bundle / "computations" / f"{cslug}.md", {
            "type": "Attested Computation",
            "title": total,
            "description": f"{total}, derived from {per} and Credit Hours when the site itself states no total.",
            "runtime": "python",
            "parameters": [
                {"name": "per_credit", "type": "number", "required": True},
                {"name": "credit_hours", "type": "number", "required": True},
            ],
            "executor": {"resource": "agent-workspace/field_pipeline/__main__.py#COMPUTED_TOTALS",
                         "receipt": ["value"]},
            "status": "stable",
            "generated": {"by": PRODUCER, "at": _now()},
        }, body)


def _cell(f: dict, field_name: str) -> str:
    """A table cell: the value, linked to its source quote (or, for a derived field, to the
    shared computation it came from); escaped so a stray '|' in a quote can't break the row."""
    value = str(f.get("v") or "").replace("|", "\\|")
    if not value:
        return ""
    if f.get("u"):
        return f"[{value}]({f['u']})"
    if f["s"] == "computed" and field_name in COMPUTATIONS:
        return f"[{value}](/knowledge/computations/{COMPUTATIONS[field_name][0]}.md)"
    return value


def _write_sheet_table(bundle: Path, sheet_slug: str, sheet_name: str, programs: dict[int, dict],
                       pids: list[int], by_program: dict[int, list[dict]],
                       order: dict[tuple[str, str], int]) -> None:
    """One row per college, one column per field — the whole program's cross-college comparison
    in one table, rather than a separate page per college."""
    field_names = sorted({f["f"] for pid in pids for f in by_program.get(pid, [])},
                         key=lambda n: order.get((sheet_name, n), 999))
    sources, seen = [], {}
    rows = []
    for pid in sorted(pids, key=lambda pid: programs[pid]["university"]):
        program = programs[pid]
        by_field = {f["f"]: f for f in by_program.get(pid, [])}
        label = program["university"]
        if program["program"] and program["program"].casefold() != sheet_name.casefold():
            label += f" ({program['program']})"
        cells = []
        for name in field_names:
            f = by_field.get(name)
            if f and f.get("u") and f["u"] not in seen:
                sources.append({"id": f"s{len(sources) + 1}", "resource": f["u"],
                                "title": (f.get("q") or "")[:140] or name})
                seen[f["u"]] = sources[-1]["id"]
            cells.append(_cell(f, name) if f else "")
        rows.append([label.replace("|", "\\|"), *cells])
    cycles = [f["c"] for pid in pids for f in by_program.get(pid, []) if f.get("c")]
    status, stale_after = _lifecycle(max(cycles) if cycles else "")
    frontmatter = {
        "type": "Program",
        "title": sheet_name,
        "description": f"Cross-college comparison for {sheet_name}: one row per college, one column per field.",
        "tags": [slug(sheet_name)],
        "status": status,
        "generated": {"by": PRODUCER, "at": _now()},
    }
    if stale_after:
        frontmatter["stale_after"] = stale_after
    if sources:
        frontmatter["sources"] = sources
    header = "| College | " + " | ".join(field_names) + " |\n" + "|---" * (len(field_names) + 1) + "|\n"
    body = f"# {sheet_name}\n\n" + header + "\n".join("| " + " | ".join(r) + " |" for r in rows)
    write_concept(bundle / f"{sheet_slug}.md", frontmatter, body)


def write_bundle(kb: dict, repo_root: Path, run_summary: str) -> Path:
    bundle = repo_root / "docs" / "okf" / "knowledge"
    order = _field_order(kb)
    programs = {p["id"]: p for p in kb["programs"]}
    by_program: dict[int, list[dict]] = {}
    for fact in kb["facts"]:
        by_program.setdefault(fact["p"], []).append(fact)

    _write_computations(bundle)

    by_sheet: dict[str, list[int]] = {}
    for pid, program in programs.items():
        by_sheet.setdefault(program["sheet"], []).append(pid)

    sheet_names: dict[str, str] = {}
    for sheet_name, pids in by_sheet.items():
        sheet_slug = slug(sheet_name)
        sheet_names[sheet_slug] = sheet_name
        _write_sheet_table(bundle, sheet_slug, sheet_name, programs, pids, by_program, order)

    body = "# Knowledge\n\n" + "\n".join(
        f"- [{sheet_names[s]}](/knowledge/{s}.md)" for s in sorted(sheet_names, key=sheet_names.get)) \
        + "\n- [Computations](/knowledge/computations/index.md)"
    write_concept(bundle / "index.md", {"title": "Knowledge"}, body)

    _append_log(bundle, run_summary)
    return bundle


def _append_log(bundle: Path, run_summary: str) -> None:
    log_path = bundle / "log.md"
    heading = f"## {date.today().isoformat()}"
    entry = f"- {datetime.now().strftime('%H:%M:%S')} {run_summary}\n"
    text = log_path.read_text(encoding="utf-8") if log_path.exists() else "# Log\n\n"
    idx = text.rfind("## ")
    if idx != -1 and text[idx:idx + len(heading)] == heading:
        text = text.rstrip("\n") + "\n" + entry
    else:
        text = text.rstrip("\n") + "\n\n" + heading + "\n\n" + entry
    log_path.write_text(text, encoding="utf-8")
