"""Field-retrieval pipeline: fill a competitive-analysis workbook one field group at a time.

    uv run --with openpyxl --with beautifulsoup4 --with requests --with pypdf \\
        python -m field_pipeline "../EDIT Competitive Analysis Research 2026-2027.xlsx" --sheet "Online MS Economics"

(run from agent-workspace/). Per row:
  1 discover   start link + its links + sitemap (+ Bing via the browser) -> candidate pages per field group, no LLM
  2 fetch      browser-harness (accordions/tabs expanded) or plain HTTP; PDFs read; pages cached in SQLite
  3 index      pages -> ~1,500-char chunks
  4 retrieve   BM25 top chunks per field group
  5 extract    one RCAC call per field group with only those chunks -> value + verbatim quote
  6 validate   quote on page, digits in quote, range, 2026-2027 cycle (code, not the model)
  7 write      <workbook>_field_pipeline.xlsx (value + source comment + hyperlink) and <...>_decisions.csv
Pages and results live in field_pipeline/data/pipeline.sqlite, so reruns reuse them.
"""
import argparse
import csv
import logging
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from . import llm
from .discover import Discoverer, foreign_program, program_words
from .extract import extract_group
from .fetch import BrowserUnavailable, Fetcher
from .fields import plan
from .retrieve import chunk_pages
from .store import Store
from .workbook import read_rows, write

log = logging.getLogger("field_pipeline")
COMPUTED_TOTALS = (("In-State Tuition Total", "In-State Tuition Per Credit"),
                   ("Out-of-State Tuition Total", "Out-of-State Tuition Per Credit"),
                   ("International Tuition Total", "International Tuition Per Credit"))


def setup_logging(out_dir: Path, verbose: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h, level in ((logging.FileHandler(out_dir / "run.log", encoding="utf-8"), logging.DEBUG),
                     (logging.StreamHandler(), logging.DEBUG if verbose else logging.INFO)):
        h.setLevel(level)
        h.setFormatter(fmt)
        root.addHandler(h)
    for noisy in ("urllib3", "websockets", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    llm.LOG_PATH = out_dir / "llm.jsonl"


def money(v: str) -> float | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", v or "")
    return float(m.group().replace(",", "")) if m else None


def extract_all(row, program: str, today: str, group_fields: dict, chunks: list, k: int, skip: int,
                workers: int, page_text: dict[str, str]) -> dict[str, dict]:
    """One LLM call per group (in parallel); {column: result}."""
    found = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {g: pool.submit(extract_group, row.university, program, today, g, fs, chunks, k, skip, page_text)
                for g, fs in group_fields.items()}
        for g, job in jobs.items():
            try:
                found.update(job.result())
            except RuntimeError as exc:
                log.error("group %s failed: %s", g, exc)
                found.update({c: {"status": "llm-error", "detail": str(exc)} for c in group_fields[g]})
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx")
    ap.add_argument("--sheet", action="append", help="sheet to fill (repeatable); default: every program sheet")
    ap.add_argument("--only", action="append", help="only rows whose university contains this text (repeatable)")
    ap.add_argument("--limit", type=int, help="stop after this many rows")
    ap.add_argument("--browser", choices=("auto", "cloud", "local", "none"), default="auto",
                    help="cloud = Browser Use Cloud (needs BROWSER_USE_API_KEY), local = your Chrome, none = plain HTTP "
                         "(debugging only); auto = cloud if the key is set, else local")
    ap.add_argument("--session-minutes", type=int, default=15, help="cloud session length limit (default 15)")
    ap.add_argument("--schools-per-session", type=int, default=2,
                    help="start a fresh cloud browser after this many schools (default 2)")
    ap.add_argument("--http-fallback", action="store_true",
                    help="if the browser cannot read an HTML page, try plain HTTP (off by default)")
    ap.add_argument("--no-search", action="store_true", help="skip Bing search through the browser")
    ap.add_argument("--per-group", type=int, default=4, help="pages opened per field group in round 1 (default 4)")
    ap.add_argument("--reextract", action="store_true", help="ask the LLM again even if results are stored")
    ap.add_argument("--overwrite", action="store_true", help="research cells that already have a value")
    ap.add_argument("--workers", type=int, default=2, help="concurrent RCAC calls per row (default 2)")
    ap.add_argument("--chunks", type=int, default=10, help="text chunks (~1,500 chars each) per LLM call (default 10)")
    ap.add_argument("--no-second-pass", action="store_true",
                    help="skip the second LLM pass over the next-best chunks for fields still not found")
    ap.add_argument("--db", type=Path, help="SQLite path (default field_pipeline/data/pipeline.sqlite)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    src = Path(args.xlsx).resolve()
    out_dir = src.with_name(f"{src.stem}_field_pipeline")
    setup_logging(out_dir, args.verbose)
    llm.api_key()  # fail fast without a key
    store = Store(args.db) if args.db else Store()
    fetcher = Fetcher(args.browser, args.session_minutes, args.schools_per_session, args.http_fallback)
    try:
        run(args, src, out_dir, store, fetcher)
    finally:
        fetcher.close()


def run(args, src: Path, out_dir: Path, store: Store, fetcher: Fetcher) -> None:
    discoverer = Discoverer(store, fetcher, per_group=args.per_group, use_search=not args.no_search)
    today = date.today().isoformat()

    columns, rows = read_rows(str(src), args.sheet)
    if args.only:
        rows = [r for r in rows if any(o.casefold() in r.university.casefold() for o in args.only)]
    rows = rows[:args.limit] if args.limit else rows
    log.info("%d row(s) on %d sheet(s) -> %s", len(rows), len(columns), out_dir)

    cells, decisions, tally = [], [], Counter()
    for i, row in enumerate(rows, 1):
        fields, links, unmapped = plan(columns[row.sheet])
        wanted = {c: f for c, f in fields.items() if args.overwrite or c in row.blanks}
        log.info("=== [%d/%d] %s | %s | %s | %d field(s)", i, len(rows), row.sheet, row.university,
                 row.program or "-", len(wanted))
        if unmapped:
            log.debug("unmapped columns: %s", unmapped)
        if not wanted:
            continue
        if not row.start_url:
            log.warning("no Website Link -> skipped")
            for c in wanted:
                decisions.append((row, c, {"status": "no-start-link"}))
            continue

        groups = sorted({f.group for f in wanted.values()})
        stored = {} if args.reextract else store.results(row.key)
        todo = [g for g in groups if any(c not in stored for c, f in wanted.items() if f.group == g)]
        results = {c: r for c, r in stored.items() if c in wanted}
        if todo:
            fetcher.begin_school()
            try:
                pages = discoverer.run(row.key, row.start_url, row.program or row.sheet, todo, row.university)
            except BrowserUnavailable as exc:
                hint = (" -- Browser Use returned 402 Payment Required: the account is out of credits"
                        if "402" in str(exc) else "")
                log.error("STOPPING: %s%s. Finished schools are saved; rerun later to continue from here.", exc, hint)
                break
            words = program_words(row.program or row.sheet)
            dropped = [p["url"] for p in pages if foreign_program(p["url"], words, row.start_url)]
            pages = [p for p in pages if p["url"] not in dropped]
            if dropped:
                log.info("ignored %d page(s) of other programs: %s", len(dropped), dropped[:5])
            chunks = chunk_pages(pages)
            log.info("%d readable page(s) (%d pdf) -> %d chunks", len(pages),
                     sum(p["kind"] == "pdf" for p in pages), len(chunks))
            program = f"{row.program or row.sheet} (workbook sheet '{row.sheet}')"
            group_fields = {g: {c: f for c, f in wanted.items() if f.group == g} for g in todo}
            page_text = {p["url"]: p["text"] for p in pages}
            found = extract_all(row, program, today, group_fields, chunks, args.chunks, 0, args.workers, page_text)
            if not args.no_second_pass:
                retry = {g: {c: f for c, f in fs.items() if found[c]["status"] in ("not-found", "bad-quote")}
                         for g, fs in group_fields.items()}
                retry = {g: fs for g, fs in retry.items() if fs}
                if retry:
                    log.info("second pass: %d field(s) in %d group(s) on the next-best chunks",
                             sum(map(len, retry.values())), len(retry))
                    again = extract_all(row, program, today, retry, chunks, args.chunks, args.chunks, args.workers,
                                        page_text)
                    for c, r in again.items():
                        if r["status"] not in ("not-found", "llm-error"):
                            r["detail"] = (r.get("detail") or "") + " [second pass]"
                            found[c] = r
            for c, r in found.items():
                store.save_result(row.key, c, wanted[c].group, r)
                results[c] = r

        # derived: total = per-credit x credit hours, only when the site states no total
        credits = results.get("Credit Hours", {})
        for total, per in COMPUTED_TOTALS:
            r_per = results.get(per, {})
            if (total in wanted and results.get(total, {}).get("status") != "found" and r_per.get("status") == "found"
                    and credits.get("status") == "found" and money(r_per["value"]) and money(credits["value"])):
                value = round(money(r_per["value"]) * money(credits["value"]))
                results[total] = {"value": f"${value:,}", "status": "computed", "url": r_per.get("url"),
                                  "quote": None, "detail": f"{r_per['value']} x {credits['value']} credit hours"}

        for c in wanted:
            r = results.get(c, {"status": "not-found"})
            tally[r["status"]] += 1
            decisions.append((row, c, r))
            if r["status"] in ("found", "computed"):
                note = (f"{r['status']} | 2026-2027 | field-pipeline {today}\n{r.get('url') or ''}\n"
                        + (f"\"{r['quote']}\"" if r.get("quote") else r.get("detail") or ""))
                cells.append((row.sheet, row.row, c, r["value"], note, r.get("url")))
            else:
                note = f"{r['status']}" + (f": {r['detail']}" if r.get("detail") else "") + \
                       (f"\nbest candidate: {r['value']}\n{r.get('url') or ''}\n\"{r.get('quote') or ''}\""
                        if r.get("value") else "")
                cells.append((row.sheet, row.row, c, None, f"field-pipeline {today}: {note}", None))
        for link_col, pair in links.items():
            r = results.get(pair, {})
            if (args.overwrite or link_col in row.blanks) and r.get("status") == "found" and r.get("url"):
                cells.append((row.sheet, row.row, link_col, r["url"], f"page where '{pair}' was found", r["url"]))

    dest = src.with_name(f"{src.stem}_field_pipeline{src.suffix}")
    written = write(str(src), str(dest), cells)
    with open(out_dir / "decisions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sheet", "row", "university", "program", "field", "status", "value", "source_url", "quote", "detail"])
        for row, c, r in decisions:
            w.writerow([row.sheet, row.row, row.university, row.program, c, r.get("status"), r.get("value"),
                        r.get("url"), r.get("quote"), r.get("detail")])
    by_group = defaultdict(Counter)
    for row, c, r in decisions:
        by_group[c][r["status"]] += 1
    total = sum(tally.values())
    log.info("DONE: %d cells written -> %s", written, dest)
    log.info("status counts: %s | fill rate %.0f%%", dict(tally),
             100 * (tally["found"] + tally["computed"]) / total if total else 0)
    log.info("browser pages: %d ok, %d failed | cloud sessions: %d | searches: %s, disabled: %s", fetcher.browser_ok,
             fetcher.browser_fail, fetcher.sessions, discoverer.searcher.stats, sorted(discoverer.searcher.disabled) or "none")
    print(f"\n{written} cells -> {dest}\ndecisions -> {out_dir / 'decisions.csv'}\n{dict(tally)}")


if __name__ == "__main__":
    main()
