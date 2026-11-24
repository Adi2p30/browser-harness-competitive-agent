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
  + IPEDS      federal completions data: Average Conferrals, and women / international share of graduates as a
               labelled proxy for Female (%) / International (%) cells still blank (no browser, no LLM)
  7 write      a new version folder per run: <workbook>_field_pipeline/runs/<date_time>/ with the filled workbook
               copy (value + source comment + hyperlink), decisions.csv, run.log and llm.jsonl; every run is
               listed in <workbook>_field_pipeline/runs.csv. Earlier versions are never overwritten.
Pages and results live in field_pipeline/data/pipeline.sqlite, so reruns reuse them (--fresh: nothing reused).
--recheck <recheck.csv>: research again only the cells a source health check flagged (field_pipeline/health.py).
"""
import argparse
import csv
import logging
import re
import shlex
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from . import llm
from .discover import Discoverer, foreign_program, program_name, program_words
from .extract import better, extract_group
from .fetch import BrowserUnavailable, Fetcher, clean_url
from .fields import FILLED, plan, usnews_kind
from .ipeds import Ipeds, page_cip
from .retrieve import chunk_pages
from .store import DEFAULT_DB, Store
from .workbook import read_rows, read_values, selection_notes, write

log = logging.getLogger("field_pipeline")
COMPUTED_TOTALS = (("In-State Tuition Total", "In-State Tuition Per Credit"),
                   ("Out-of-State Tuition Total", "Out-of-State Tuition Per Credit"),
                   ("International Tuition Total", "International Tuition Per Credit"))
RETRY = ("not-found", "bad-quote", "bad-value", "other-program", "off-cycle", "found-prior",
         "school-wide")  # second-pass statuses: the next-best chunks may hold a program-specific value
# Cell colours for values that are not 2026-2027 facts: one year back, two years back, last year's workbook,
# IPEDS graduates standing in for the entering class.
FILL = {"2025-2026": "FFF2CC", "2024-2025": "FCE4D6", "carried-forward": "E7E6E6", "ipeds-proxy": "DDEBF7",
        "school-wide": "E4DFEC", "not-published": "F2F2F2"}
NOT_PUBLISHED = "Not published"  # written into cells the program's own pages were read for and do not state
CONFERRALS = re.compile(r"conferral", re.I)
PUBLISHED_GROUPS = ("scores", "class", "outcomes")  # fields a program may simply not publish
PROFILE_URL = re.compile(r"profile|by-the-numbers|facts|statistic|outcome|employment|class-of|cohort", re.I)
FEMALE, INTERNATIONAL = re.compile(r"^(female|women)\s*(\(%\)|%)", re.I), re.compile(r"^international\s*(\(%\)|%)", re.I)


def fill_for(r: dict) -> str | None:
    return FILL.get(r["status"]) or FILL.get(r.get("cycle") or "")


def mark_not_published(store: Store, row, wanted: dict, results: dict, missing: str) -> None:
    """Class-profile, test-score and outcome fields still not found become not-published when the program's own
    page was read and no class-profile / outcomes page was lost to a failed fetch: the site was read and does not
    state them. Otherwise they stay not-found (we may have missed them)."""
    todo = [c for c, f in wanted.items()
            if f.group in PUBLISHED_GROUPS and results.get(c, {"status": missing})["status"] == "not-found"]
    if not todo or not row.start_url:
        return
    start = store.page(clean_url(row.start_url))
    lost = [u for u in store.row_failed(row.key) if PROFILE_URL.search(u)]
    if lost:
        log.info("not marking 'not-published': profile/outcome page(s) could not be read: %s", lost[:3])
    if start is None or start["status"] != "ok" or lost:
        return
    for c in todo:
        results[c] = {**results.get(c, {}), "status": "not-published",
                      "detail": "the program's pages were read and none states it (no class-profile or outcomes "
                                "page was lost to a failed fetch)"}


def apply_ipeds(ip: Ipeds, store: Store, row, wanted: dict, results: dict, conferral_cols: list[str]) -> None:
    """Average Conferrals from IPEDS, and the women / nonresident share of graduates for Female (%) /
    International (%) cells nothing else filled. Uses the CIP code the program's pages state, else the sheet's."""
    proxy_cols = [c for c in wanted if (FEMALE.search(c) or INTERNATIONAL.search(c))
                  and results.get(c, {}).get("status") not in FILLED]
    if not conferral_cols and not proxy_cols:
        return
    stated = page_cip([p["text"] for p in store.row_pages(row.key)])
    got, why = ip.lookup(row.university, row.sheet, row.program, row.start_url, stated)
    if got is None:
        for c in conferral_cols:
            results[c] = {"status": "not-found", "detail": f"IPEDS: {why}"}
        return
    url = f"https://nces.ed.gov/collegenavigator/?id={got.unitid}"
    what = f"IPEDS completions {got.span}, {got.school}, {got.cips}: master's degrees per year {got.totals}"
    for c in conferral_cols:
        results[c] = {"value": str(round(got.average)), "status": "ipeds", "url": url, "quote": None, "detail": what,
                      "cycle": f"IPEDS {got.span}"}
    for c in proxy_cols:
        n = got.women if FEMALE.search(c) else got.nonresident
        if (share := got.share(n)) is not None:
            results[c] = {"value": f"{share:g}%", "status": "ipeds-proxy", "url": url, "quote": None,
                          "detail": f"share of master's GRADUATES ({n} of {sum(got.totals)}), not the entering class; "
                                    f"{what}", "cycle": f"IPEDS {got.span}"}


def read_recheck(path: Path) -> dict[tuple[str, int], set[str]]:
    """{(sheet, row): fields} from a health check's recheck.csv."""
    out: dict[tuple[str, int], set[str]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.setdefault((r["sheet"], int(r["row"])), set()).add(r["field"])
    return out


def prior_record(prior: dict, row) -> dict | None:
    """The row of last year's workbook for the same sheet and university (and program, when both name one)."""
    recs = prior.get((row.sheet.casefold(), row.university.casefold()), [])
    norm = lambda v: " ".join(str(v or "").split()).casefold()
    same = [r for r in recs if not row.program or not r.get("Program Name") or norm(r["Program Name"]) == norm(row.program)]
    return same[0] if len(same) == 1 else None


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
                workers: int, page_text: dict[str, str], usnews: str = "") -> dict[str, dict]:
    """One LLM call per group (in parallel); {column: result}."""
    found = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {g: pool.submit(extract_group, row.university, program, today, g, fs, chunks, k, skip, page_text,
                               usnews)
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
    ap.add_argument("--row", action="append", metavar="SHEET:ROW",
                    help="only this row (repeatable), e.g. 'MS HRM - Residential:10001' for a school added with field_pipeline.schools")
    ap.add_argument("--limit", type=int, help="stop after this many rows")
    ap.add_argument("--browser", choices=("local", "cloud", "auto", "none"), default="local",
                    help="local = your Chrome (default, free), cloud = Browser Use Cloud (needs BROWSER_USE_API_KEY, "
                         "billed), auto = cloud if the key is set, else local; none = plain HTTP (debugging only)")
    ap.add_argument("--session-minutes", type=int, default=15, help="cloud session length limit (default 15)")
    ap.add_argument("--schools-per-session", type=int, default=2,
                    help="start a fresh cloud browser after this many schools (default 2)")
    ap.add_argument("--foreground", action="store_true",
                    help="local Chrome: briefly show the pipeline's tab on pages whose counters only load when visible, "
                         "then switch back to your tab (the cloud browser always does this)")
    ap.add_argument("--http-fallback", action="store_true",
                    help="if the browser cannot read an HTML page, try plain HTTP (off by default)")
    ap.add_argument("--no-search", action="store_true", help="skip Bing search through the browser")
    ap.add_argument("--per-group", type=int, default=4, help="pages opened per field group in round 1 (default 4)")
    ap.add_argument("--reextract", action="store_true", help="ask the LLM again even if results are stored")
    ap.add_argument("--overwrite", action="store_true", help="research cells that already have a value")
    ap.add_argument("--fresh", action="store_true",
                    help="new research for every cell: pages fetched again, no stored results reused, cells that "
                         "already have a value researched too (= --overwrite --reextract + fresh pages); Website Links "
                         "found by earlier runs are kept")
    ap.add_argument("--workers", type=int, default=2, help="concurrent RCAC calls per row (default 2)")
    ap.add_argument("--chunks", type=int, default=18, help="text chunks (~1,500 chars each) per LLM call (default 18)")
    ap.add_argument("--no-second-pass", action="store_true",
                    help="skip the second LLM pass over the next-best chunks for fields still not found")
    ap.add_argument("--prior", type=Path, help="last year's workbook: its values fill cells still blank (carried-forward)"
                    "; default: the one 'OLD *.xlsx' next to the input, if there is exactly one")
    ap.add_argument("--no-prior", action="store_true", help="never carry values forward from last year's workbook")
    ap.add_argument("--no-find-links", action="store_true", help="skip rows without a Website Link instead of searching for one "
                    "(or researching on the university's site from IPEDS)")
    ap.add_argument("--no-ipeds", action="store_true", help="skip IPEDS (Average Conferrals, graduate-share proxies)")
    ap.add_argument("--db", type=Path, help="SQLite path (default field_pipeline/data/pipeline.sqlite)")
    ap.add_argument("--recheck", type=Path,
                    help="recheck.csv from `python -m field_pipeline.health`: research only those cells again, "
                         "ignoring their stored values (the health check already refreshed their pages)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.fresh:
        args.overwrite = args.reextract = True

    src = Path(args.xlsx).resolve()
    started = time.time()
    out_dir = version_dir(src, args.fresh)
    setup_logging(out_dir, args.verbose)
    log.info("version folder: %s", out_dir)
    llm.api_key()  # fail fast without a key
    store = Store(args.db or DEFAULT_DB, fresh_since=started if args.fresh else None)
    fetcher = Fetcher(args.browser, args.session_minutes, args.schools_per_session, args.http_fallback, args.foreground)
    summary, status = {}, "failed"
    try:
        summary = run(args, src, out_dir, store, fetcher)
        status = "done"
    except NothingToDo as exc:
        log.error("STOPPING: %s", exc)
        status = "nothing to do"
    except KeyboardInterrupt:
        status = "interrupted"
        raise
    finally:
        fetcher.close()
        record_run(out_dir, started, status, args, summary)
    if status != "done":
        sys.exit(2)


class NothingToDo(RuntimeError):
    """The filters select no row: stop instead of reporting a successful run that researched nothing."""


def version_dir(src: Path, fresh: bool) -> Path:
    """A new folder for this run's outputs: <workbook>_field_pipeline/runs/<YYYY-MM-DD_HHMMSS>[_fresh]."""
    runs = src.with_name(f"{src.stem}_field_pipeline") / "runs"
    name = datetime.now().strftime("%Y-%m-%d_%H%M%S") + ("_fresh" if fresh else "")
    out, n = runs / name, 2
    while out.exists():  # two runs started in the same second
        out, n = runs / f"{name}-{n}", n + 1
    out.mkdir(parents=True)
    return out


def record_run(out_dir: Path, started: float, status: str, args, summary: dict) -> None:
    """Append this run to runs.csv next to the runs/ folder (the list of versions)."""
    index = out_dir.parent.parent / "runs.csv"
    new = not index.exists()
    with open(index, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["version", "started", "minutes", "status", "fresh", "browser", "sheets", "only", "limit",
                        "rows", "cells_written", "status_counts", "command"])
        w.writerow([out_dir.name, datetime.fromtimestamp(started).isoformat(timespec="seconds"),
                    round((time.time() - started) / 60, 1), status, args.fresh, args.browser,
                    "; ".join(args.sheet or ["all"]), "; ".join(args.only or []), args.limit or "",
                    summary.get("rows", ""), summary.get("written", ""), summary.get("tally", ""),
                    shlex.join(["python", "-m", "field_pipeline", *sys.argv[1:]])])


def run(args, src: Path, out_dir: Path, store: Store, fetcher: Fetcher) -> dict:
    discoverer = Discoverer(store, fetcher, per_group=args.per_group, use_search=not args.no_search)
    today = date.today().isoformat()

    columns, rows = read_rows(str(src), args.sheet)
    olds = sorted(src.parent.glob("OLD *.xlsx"))
    prior_path = None if args.no_prior else args.prior or (olds[0] if len(olds) == 1 else None)
    prior = read_values(str(prior_path)) if prior_path else {}
    log.info("last year's workbook: %s", prior_path or "none")
    ip = None
    if not args.no_ipeds:
        try:
            ip = Ipeds()
        except Exception as exc:  # NCES unreachable on a first run: everything else still works
            log.warning("IPEDS unavailable (%s); Average Conferrals left blank", exc)
    if args.sheet or args.only:
        match, notes = selection_notes(read_rows(str(src))[1] if args.sheet else rows, args.sheet, args.only)
        for note in notes:
            log.warning("%s", note)
        if not match:
            raise NothingToDo("no row matches the --sheet / --only filters: " + "; ".join(notes))
    if args.only:
        rows = [r for r in rows if any(o.casefold() in r.university.casefold() for o in args.only)]
    if args.row:
        rows = [r for r in rows if f"{r.sheet}:{r.row}" in set(args.row)]
        if not rows:
            raise NothingToDo(f"no row {' or '.join(args.row)} (sheet name and row number, e.g. 'MS HRM - Residential:5')")
    recheck = read_recheck(args.recheck) if args.recheck else None
    if recheck is not None:
        rows = [r for r in rows if (r.sheet, r.row) in recheck]
        log.info("recheck: %d cell(s) on %d row(s) from %s", sum(map(len, recheck.values())), len(rows), args.recheck)
    rows = rows[:args.limit] if args.limit else rows
    log.info("%d row(s) on %d sheet(s) -> %s", len(rows), len(columns), out_dir)

    cells, decisions, tally = [], [], Counter()
    stopped = False  # browser gone: remaining rows are written from stored results only, so no earlier cell is lost
    started = time.time()
    for i, row in enumerate(rows, 1):
        elapsed = (time.time() - started) / 60
        eta = f", ETA ~{elapsed / (i - 1) * (len(rows) - i + 1):.0f} min" if i > 1 else ""
        fields, links, unmapped = plan(columns[row.sheet])
        redo = recheck.get((row.sheet, row.row), set()) if recheck is not None else set()
        asked = (lambda c: c in redo) if recheck is not None else (lambda c: args.overwrite or c in row.blanks)
        wanted = {c: f for c, f in fields.items() if asked(c)}
        conferral_cols = [c for c in columns[row.sheet] if ip and c and CONFERRALS.search(c) and asked(c)]
        log.info("=== [%d/%d] %s | %s | %s | %d field(s) | elapsed %.1f min%s", i, len(rows), row.sheet,
                 row.university, row.program or "-", len(wanted) + len(conferral_cols), elapsed, eta)
        if unmapped:
            log.debug("unmapped columns: %s", unmapped)
        if not wanted and not conferral_cols:
            continue
        old = prior_record(prior, row) if prior else None
        if not row.start_url and old and old.get("Website Link"):
            row.start_url = str(old["Website Link"]).strip()
            log.info("Website Link from last year's workbook: %s", row.start_url)
            cells.append((row.sheet, row.row, "Website Link", row.start_url, f"from {prior_path.name}", row.start_url))
        if not row.start_url and (earlier_link := store.start_url(row.key)):
            row.start_url = earlier_link
            log.info("Website Link from an earlier run: %s", row.start_url)
            cells.append((row.sheet, row.row, "Website Link", row.start_url,
                          f"field-pipeline: found by web search in an earlier run, please check it is the right program",
                          row.start_url, "FFF2CC"))
        name = program_name(row.program, row.sheet)  # what to search for: abbreviations spelled out
        begun = False
        if not row.start_url and not args.no_find_links and not args.no_search and not stopped:
            fetcher.begin_school()
            begun = True
            try:
                row.start_url = discoverer.find_start(row.university, name) or ""
            except BrowserUnavailable as exc:
                log.error("STOPPING: %s. Finished schools are saved; rerun later to continue from here.", exc)
                stopped = True
            if row.start_url:
                cells.append((row.sheet, row.row, "Website Link", row.start_url,
                              f"field-pipeline {today}: found by web search, please check it is the right program",
                              row.start_url, "FFF2CC"))
        # Without a program page, research still runs on the university's own site: searches and crawl only need
        # its domain. Not-published is then never concluded (mark_not_published needs the program page).
        anchor = row.start_url
        if not anchor and ip and not args.no_find_links and (anchor := ip.homepage(row.university) or ""):
            log.info("no Website Link: researching from the university's site %s (IPEDS directory)", anchor)
        if not anchor:
            log.warning("no Website Link and no university site -> no web research for this row")
        missing = "not-found" if anchor else "no-start-link"

        groups = sorted({f.group for f in wanted.values()})
        usnews = usnews_kind(row.sheet, row.program)
        stored = {} if args.reextract else {c: r for c, r in store.results(row.key).items() if c not in redo}
        todo = [] if stopped or not anchor else [g for g in groups if any(c not in stored for c, f in wanted.items() if f.group == g)]
        results = {c: r for c, r in stored.items() if c in wanted}
        if todo:
            if not begun:
                fetcher.begin_school()
            try:
                pages = discoverer.run(row.key, anchor, name, todo, row.university, row.sheet, usnews,
                                       homepage=not row.start_url)
            except BrowserUnavailable as exc:
                hint = (" -- Browser Use returned 402 Payment Required: the account is out of credits"
                        if "402" in str(exc) else "")
                log.error("STOPPING: %s%s. Finished schools are saved; rerun later to continue from here.", exc, hint)
                stopped = True
                results = {c: r for c, r in store.results(row.key).items() if c in wanted and c not in redo}
            else:
                words = program_words(name)
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
                found = extract_all(row, program, today, group_fields, chunks, args.chunks, 0, args.workers, page_text,
                                    usnews)
                if not args.no_second_pass:
                    retry = {g: {c: f for c, f in fs.items() if found[c]["status"] in RETRY}
                             for g, fs in group_fields.items()}
                    retry = {g: fs for g, fs in retry.items() if fs}
                    if retry:
                        log.info("second pass: %d field(s) in %d group(s) on the next-best chunks",
                                 sum(map(len, retry.values())), len(retry))
                        again = extract_all(row, program, today, retry, chunks, args.chunks, args.chunks, args.workers,
                                            page_text, usnews)
                        for c, r in again.items():
                            if r["status"] != "llm-error" and (found[c]["status"] == "llm-error" or better(r, found[c])):
                                r["detail"] = (r.get("detail") or "") + " [second pass]"
                                found[c] = r
                earlier = {c: r for c, r in store.results(row.key).items() if c not in redo}  # a flagged value is stale
                for c, r in found.items():
                    # a value that passed every check in an earlier run is kept unless this run found a better one
                    if (e := earlier.get(c)) and e["status"] in FILLED and r["status"] != e["status"] and better(e, r):
                        e["detail"] = ((e.get("detail") or "").replace(" [kept from earlier run]", "")
                                       + " [kept from earlier run]")
                        r = e
                    store.save_result(row.key, c, wanted[c].group, r)
                    results[c] = r

        # derived: total = per-credit x credit hours, only when the site states no total
        credits = results.get("Credit Hours", {})
        for total, per in COMPUTED_TOTALS:
            r_per = results.get(per, {})
            stated = results.get(total, {})
            newer = stated.get("status") != "found-prior" or (r_per.get("cycle") or "") > (stated.get("cycle") or "")
            if (total in wanted and stated.get("status") != "found" and newer
                    and r_per.get("status") in ("found", "found-prior") and credits.get("status") in ("found", "found-prior")
                    and money(r_per["value"]) and money(credits["value"])
                    and re.search(r"credit|hour", credits.get("quote") or "", re.I)):  # never units x per-credit
                value = round(money(r_per["value"]) * money(credits["value"]))
                results[total] = {"value": f"${value:,}", "status": "computed", "url": r_per.get("url"),
                                  "quote": None, "detail": f"{r_per['value']} x {credits['value']} credit hours",
                                  "cycle": min(r_per.get("cycle") or "2026-2027", credits.get("cycle") or "2026-2027")}

        # last resort: last year's researched value for cells still blank (never for fields without a fallback year)
        for c, f in wanted.items():
            r = results.get(c, {"status": missing})
            # last year's program value beats this year's school-wide fallback
            if (old and (r["status"] not in FILLED or r["status"] == "school-wide") and f.max_lag >= 1
                    and not re.fullmatch(r"[-\u2013\u2014?.\s]*", str(old.get(c) or ""))):  # blank or a "-" placeholder
                this_year = r["status"] + (f" {r['value']}" if r["status"] == "school-wide" else "")
                results[c] = {"value": old[c], "status": "carried-forward", "url": None, "quote": None,
                              "detail": f"value from {prior_path.name} row {old['_row']} (this year: {this_year})",
                              "cycle": "2025-2026 workbook"}

        if ip:
            apply_ipeds(ip, store, row, wanted, results, conferral_cols)
        mark_not_published(store, row, wanted, results, missing)

        for c in [*wanted, *conferral_cols]:
            r = results.get(c, {"status": missing})
            tally[r["status"]] += 1
            decisions.append((row, c, r))
            if r["status"] in FILLED:
                cycle = r.get("cycle") or "2026-2027"
                note = (f"{r['status']} | {cycle} | field-pipeline {today}\n"
                        + (f"{r['detail']}\n" if r.get("detail") and r.get("quote") else "")
                        + f"{r.get('url') or ''}\n"
                        + (f"\"{r['quote']}\"" if r.get("quote") else r.get("detail") or ""))
                cells.append((row.sheet, row.row, c, r["value"], note, r.get("url"), fill_for(r)))
            elif r["status"] == "not-published":
                cells.append((row.sheet, row.row, c, NOT_PUBLISHED, f"not-published | field-pipeline {today}\n{r['detail']}",
                              None, FILL["not-published"]))
            else:
                note = f"{r['status']}" + (f": {r['detail']}" if r.get("detail") else "") + \
                       (f"\nbest candidate: {r['value']}\n{r.get('url') or ''}\n\"{r.get('quote') or ''}\""
                        if r.get("value") else "")
                cells.append((row.sheet, row.row, c, None, f"field-pipeline {today}: {note}", None))
        for link_col, pair in links.items():
            r = results.get(pair, {})
            if (pair in redo or (recheck is None and (args.overwrite or link_col in row.blanks))) \
                    and r.get("status") in ("found", "found-prior") and r.get("url"):
                cells.append((row.sheet, row.row, link_col, r["url"], f"page where '{pair}' was found", r["url"]))

    dest = out_dir / f"{src.stem}_field_pipeline{src.suffix}"
    written = write(str(src), str(dest), cells)
    with open(out_dir / "decisions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sheet", "row", "university", "program", "field", "status", "cycle", "value", "source_url", "quote",
                    "detail"])
        for row, c, r in decisions:
            w.writerow([row.sheet, row.row, row.university, row.program, c, r.get("status"), r.get("cycle"),
                        r.get("value"), r.get("url"), r.get("quote"), r.get("detail")])
    total = sum(tally.values())
    findable = total - tally["not-published"]  # cells the program publishes at all
    log.info("DONE: %d cells written -> %s | %.1f min for %d row(s)", written, dest, (time.time() - started) / 60,
             len(rows))
    filled = sum(tally[s] for s in FILLED)
    log.info("status counts: %s | fill rate %.0f%% of all cells, %.0f%% of cells not marked not-published "
             "(2026-2027 only: %.0f%%)", dict(tally), 100 * filled / total if total else 0,
             100 * filled / findable if findable else 0,
             100 * (tally["found"] + tally["computed"]) / total if total else 0)
    log.info("browser pages: %d ok, %d failed | cloud sessions: %d | searches: %s, disabled: %s", fetcher.browser_ok,
             fetcher.browser_fail, fetcher.sessions, discoverer.searcher.stats, sorted(discoverer.searcher.disabled) or "none")
    print(f"\n{written} cells -> {dest}\ndecisions -> {out_dir / 'decisions.csv'}\n{dict(tally)}"
          f"\nelapsed: {(time.time() - started) / 60:.1f} min for {len(rows)} row(s)")
    return {"rows": len(rows), "written": written, "tally": dict(tally)}


if __name__ == "__main__":
    main()
