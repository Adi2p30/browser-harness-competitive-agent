"""Source health check: re-open every source page behind the knowledge base and confirm each stored quote is still
on it. Meant to run monthly; no LLM calls.

    uv run --with openpyxl --with beautifulsoup4 --with requests --with pypdf \\
        python -m field_pipeline.health "../EDIT Competitive Analysis Research 2026-2027.xlsx"

(run from agent-workspace/). The facts are the knowledge base's (knowledge_base.build: the newest pipeline run per
cell, values typed into the workbook, last year's workbook). Each source URL is fetched once with the pipeline's
Fetcher (browser, accordions expanded, PDFs read) and every fact citing it is judged:

  ok          the quote is on the page (same matching as validate: case, spacing and punctuation ignored)
  moved       the quote is on the page, but the URL now redirects elsewhere (final_url)
  changed     the exact quote is gone, but a near copy is there with some words/numbers different: the value has
              probably been updated; snippet = what the page says now
  missing     the page reads fine but has neither the quote nor a near copy
  dead        404/410, a "page not found" page, or a redirect to the site's home page
  unreadable  bot wall, timeout, empty page, or a quote that came from image OCR: says nothing about the fact
  live        (facts with a URL but no quote: typed values, computed totals, IPEDS) the page still opens

changed / missing are re-checked once over plain HTTP before being reported. Writes, per run,
<workbook>_field_pipeline/health/<date_time>/: health.csv (every fact), recheck.csv (changed / missing / dead cells
for `python -m field_pipeline ... --recheck recheck.csv`) and health.log. Every check is also stored in the
`health` table of pipeline.sqlite, which knowledge_base reads to badge stale facts; refreshed pages replace the
cached copies, so the next pipeline run reads current text.
"""
import argparse
import csv
import hashlib
import logging
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

import requests

from .fetch import HEADERS, OCR_MARK, BrowserUnavailable, Fetcher, clean_url, site
from .store import DEFAULT_DB, Store
from .validate import quote_on_page, squash
from .workbook import load_snapshot

log = logging.getLogger("field_pipeline.health")

NEAR = 0.8            # word-level similarity for "changed"
MIN_NEAR_WORDS = 4    # shorter quotes ("$1,250") are too short to recognise a near copy of
STALE_DAYS = 25       # URLs checked more recently than this are skipped (a re-run after a crash resumes)
FLAGGED = ("changed", "missing", "dead")
# cells a --recheck run may research again: values this year's pipeline or a person put there (last year's
# workbook values are not in this year's cells, so a normal run already researches them)
RECHECK = {"found", "found-prior", "school-wide", "not-required", "computed", "ipeds", "ipeds-proxy", "entered"}
NOT_FOUND_PAGE = re.compile(r"\b(404|page not found|page cannot be found|page can't be found|page could not be found|"
                            r"page (you requested |you are looking for )?(does not|doesn't|no longer) exist|"
                            r"page has (been )?(moved|removed))\b", re.I)
WORD = re.compile(r"\w+")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
LABEL = "edu.purdue.competitive-analysis.health"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def quote_key(quote: str | None) -> str:
    """Stable key for a quote (squashed, hashed); "" for facts without one."""
    return hashlib.sha1(squash(quote).encode()).hexdigest()[:16] if quote and squash(quote) else ""


def same_url(a: str, b: str) -> bool:
    """Same page, ignoring scheme, www., a trailing slash and the query string's tracking parameters."""
    norm = lambda u: (urlparse(u).netloc.casefold().removeprefix("www."), urlparse(u).path.rstrip("/"),
                      urlparse(clean_url(u)).query)
    return norm(a) == norm(b)


def dead_reason(page: dict, url: str) -> str:
    """Why the page is gone, or "" if it is not."""
    if page.get("code") in (404, 410):
        return f"HTTP {page['code']}"
    head = f"{page.get('title') or ''} {(page.get('text') or '')[:300]}"
    if page.get("text") is not None and NOT_FOUND_PAGE.search(head):
        return "the page says it was not found"
    final = page.get("final_url") or url
    if urlparse(url).path.strip("/") and not urlparse(final).path.strip("/") and not urlparse(final).query:
        return f"redirects to the home page {final}"
    return ""


def dom_text(text: str) -> str:
    """The page text without OCR'd image text (fetch appends it after OCR_MARK; older pages: '[image text' lines)."""
    cut = [i for i in (text.find(f"\n{OCR_MARK}\n"), text.find("\n[image text")) if i >= 0]
    return text[:min(cut)] if cut else text


def from_ocr(quote: str, old_text: str | None) -> bool:
    """The stored copy of the page had the quote only in its OCR'd image text."""
    return bool(old_text) and quote_on_page(quote, old_text) and not quote_on_page(quote, dom_text(old_text))


def best_window(q: list[str], toks: list[str]) -> tuple[float, int, int]:
    """(similarity, start, end) of the run of page words most like the quote's words. Windows start where a
    non-numeric quote word lines up, and may be one word shorter or longer than the quote."""
    anchors = {}
    for j, w in enumerate(q):
        if not w.isdigit():
            anchors.setdefault(w, []).append(j)
    starts = sorted({max(0, i - j) for i, t in enumerate(toks) for j in anchors.get(t, ())})[:5000]
    best = (0.0, 0, 0)
    for s in starts:
        for n in (len(q) - 1, len(q), len(q) + 1):
            window = toks[s:s + n]
            if not window:
                continue
            ratio = SequenceMatcher(None, q, window, autojunk=False).ratio()
            if ratio > best[0]:
                best = (ratio, s, s + len(window))
    return best


def near_match(quote: str, text: str) -> tuple[float, str]:
    """(similarity, what the page says now) for the parts of the quote ("..."-split) that are not on the page.
    The weakest part decides; (0, "") when a missing part is too short to match."""
    spans = [(m.group().casefold(), m.start(), m.end()) for m in WORD.finditer(text)]
    toks, key = [t for t, _, _ in spans], squash(text)
    scores, snippets = [], []
    for piece in re.split(r"\.\.\.|…", quote or ""):
        if not squash(piece) or squash(piece) in key:
            continue
        q = [w.casefold() for w in WORD.findall(piece)]
        if len(q) < MIN_NEAR_WORDS:
            return 0.0, ""
        ratio, a, b = best_window(q, toks)
        if not ratio:
            return 0.0, ""
        scores.append(ratio)
        snippets.append(text[spans[a][1]:spans[b - 1][2]])
    return (min(scores), " … ".join(snippets)) if scores else (0.0, "")


def number_change(quote: str, snippet: str) -> str:
    """'now 1,310 (was 1,250)' when the numbers differ."""
    old, new = NUMBER.findall(quote), NUMBER.findall(snippet)
    gone, came = [n for n in old if n not in new], [n for n in new if n not in old]
    return f"now {', '.join(came)} (was {', '.join(gone)})" if gone or came else "wording changed"


def judge(quote: str, page: dict, url: str, old_text: str | None = None) -> dict:
    """Result for one quoted fact on a freshly fetched page: {result, final_url, snippet, score, detail}."""
    final = page.get("final_url") or url
    text = page.get("text") or ""
    out = {"final_url": final, "snippet": "", "score": None, "detail": ""}
    if text and quote_on_page(quote, text):
        moved = not same_url(url, final)
        return {**out, "result": "moved" if moved else "ok", "detail": f"now at {final}" if moved else ""}
    if why := dead_reason(page, url):
        return {**out, "result": "dead", "detail": why}
    if page.get("status") != "ok":
        return {**out, "result": "unreadable", "detail": f"page {page.get('status') or 'unreadable'}"
                + (f" (HTTP {page['code']})" if page.get("code") else "")}
    if from_ocr(quote, old_text):
        return {**out, "result": "unreadable", "detail": "the quote came from image OCR, which varies between reads"}
    score, snippet = near_match(quote, text)
    if score >= NEAR:
        return {**out, "result": "changed", "snippet": snippet, "score": round(score, 2),
                "detail": number_change(quote, snippet)}
    return {**out, "result": "missing", "score": round(score, 2) if score else None,
            "detail": "the page no longer has the quote or anything close to it"}


def judge_link(page: dict, url: str) -> dict:
    """Result for a fact with a source URL but no quote: does the page still open?"""
    final = page.get("final_url") or url
    out = {"final_url": final, "snippet": "", "score": None, "detail": ""}
    if why := dead_reason(page, url):
        return {**out, "result": "dead", "detail": why}
    if page.get("status") != "ok":
        return {**out, "result": "unreadable", "detail": f"page {page.get('status') or 'unreadable'}"}
    if not same_url(url, final):
        return {**out, "result": "moved", "detail": f"now at {final}"}
    return {**out, "result": "live"}


def probe(url: str) -> int | None:
    """HTTP status code of the URL (redirects followed); None when the request fails."""
    try:
        with requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True, stream=True) as resp:
            return resp.status_code
    except requests.RequestException:
        return None


def collect(workbook: Path, prior: Path | None, links: bool, sheets: list[str] | None = None,
            only: list[str] | None = None) -> list[dict]:
    """Facts with a source URL, from the knowledge base. Last year's workbook links only with `links`."""
    from knowledge_base.__main__ import build, versions  # knowledge_base imports field_pipeline, not the reverse
    kb = build(str(workbook), str(prior) if prior else None, versions(workbook), snapshot=load_snapshot(workbook))
    programs = {p["id"]: p for p in kb["programs"]}
    out = []
    for f in kb["facts"]:
        p = programs[f["p"]]
        if not f.get("u") or not str(f["u"]).startswith(("http://", "https://")):
            continue
        if f["s"] == "prior-year" and not links:
            continue
        if sheets and p["sheet"] not in sheets:
            continue
        if only and not any(o.casefold() in p["university"].casefold() for o in only):
            continue
        out.append({"program_id": p["program_id"], "sheet": p["sheet"], "row": p["row"],
                    "university": p["university"], "program": p["program"],
                    "field": f["f"], "status": f["s"], "value": f["v"], "url": clean_url(str(f["u"])),
                    "quote": f.get("q") or ""})
    return out


def check_url(url: str, facts: list[dict], fetcher: Fetcher, store: Store, run: str) -> list[dict]:
    """Fetch one source page and judge every fact citing it."""
    old = store.page(url)
    page = fetcher.fetch(url, http_ok=True)
    page["code"] = probe(url)
    if page.get("status") == "ok" and not dead_reason(page, url):
        store.save_page(page)  # the pipeline's next run reads the current text
    alt = None
    rows = []
    for f in facts:
        r = judge(f["quote"], page, url, old and old.get("text")) if f["quote"] else judge_link(page, url)
        if r["result"] in ("changed", "missing"):  # browser copy may lack text the server sends: retry over HTTP
            alt = alt if alt is not None else (fetcher._via_http(url, html_ok=True) or {})
            if alt.get("text") and quote_on_page(f["quote"], alt["text"]):
                r = {**r, "result": "ok", "snippet": "", "score": None,
                     "detail": "on the page over plain HTTP (the browser copy lacked it)"}
        rows.append({**f, **r, "quote_key": quote_key(f["quote"]), "checked_at": time.time(), "run": run})
    return rows


def out_dir(workbook: Path) -> Path:
    base = workbook.with_name(f"{workbook.stem}_field_pipeline") / "health"
    out, n = base / datetime.now().strftime("%Y-%m-%d_%H%M%S"), 2
    while out.exists():
        out, n = base / f"{out.name}-{n}", n + 1
    out.mkdir(parents=True)
    return out


def setup_logging(out: Path, verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h, level in ((logging.FileHandler(out / "health.log", encoding="utf-8"), logging.DEBUG),
                     (logging.StreamHandler(), logging.DEBUG if verbose else logging.INFO)):
        h.setLevel(level)
        h.setFormatter(fmt)
        root.addHandler(h)
    for noisy in ("urllib3", "websockets", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.ERROR)


HEALTH_COLUMNS = ["program_id", "sheet", "row", "university", "program", "field", "status", "value", "result", "url",
                  "final_url", "quote", "snippet", "score", "detail"]
RECHECK_COLUMNS = ["program_id", "sheet", "row", "university", "program", "field", "result", "url"]


def write_outputs(out: Path, rows: list[dict]) -> list[dict]:
    """health.csv (every fact checked) and recheck.csv (cells to research again); returns the recheck rows."""
    with open(out / "health.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, HEALTH_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    recheck = [r for r in rows if r["result"] in FLAGGED and r["status"] in RECHECK
               and (r.get("program_id") is not None or r.get("row"))]  # not schools only in last year's workbook
    with open(out / "recheck.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, RECHECK_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(recheck)
    return recheck


def run(args) -> dict:
    wb = Path(args.xlsx).resolve()
    out = out_dir(wb)
    setup_logging(out, args.verbose)
    log.info("version folder: %s", out)  # the web app's runner picks the output folder up from this line
    olds = sorted(wb.parent.glob("OLD *.xlsx"))
    prior = args.prior or (olds[0] if len(olds) == 1 else None)
    facts = collect(wb, prior, args.links, args.sheet, args.only)
    by_url: dict[str, list[dict]] = {}
    for f in facts:
        by_url.setdefault(f["url"], []).append(f)
    store = Store(args.db or DEFAULT_DB)
    cutoff = time.time() - args.stale_days * 86400
    recent = [u for u in by_url if (store.last_checked(u) or 0) >= cutoff] if args.stale_days else []
    todo = sorted((u for u in by_url if u not in recent), key=lambda u: (site(u), u))  # one site at a time
    todo = todo[:args.limit] if args.limit else todo
    log.info("%d fact(s) with a source (%d quoted) on %d page(s); %d checked in the last %d days skipped; "
             "checking %d", len(facts), sum(bool(f["quote"]) for f in facts), len(by_url), len(recent),
             args.stale_days, len(todo))
    fetcher = Fetcher(args.browser, args.session_minutes, args.schools_per_session, foreground=args.foreground)
    rows, last_site, stopped = [], None, ""
    started = time.time()
    try:
        for i, url in enumerate(todo, 1):
            if site(url) != last_site:
                fetcher.begin_school()  # cloud mode: rotates the session every few sites
                last_site = site(url)
            got = check_url(url, by_url[url], fetcher, store, out.name)
            store.save_health(got)
            rows += got
            tally = Counter(r["result"] for r in got)
            log.info("[%d/%d] %s: %s", i, len(todo), url, ", ".join(f"{n} {k}" for k, n in tally.items()))
            for r in got:
                if r["result"] in FLAGGED:
                    log.info("  %s | %s | %s: %s %s", r["university"], r["sheet"], r["field"], r["result"],
                             r["detail"] + (f" -- now: \"{r['snippet'][:160]}\"" if r["snippet"] else ""))
    except BrowserUnavailable as exc:
        stopped = f"browser unavailable: {exc}"
        log.error("STOPPING: %s. Checked pages are saved; rerun to continue (recently checked pages are skipped).",
                  exc)
    except KeyboardInterrupt:
        stopped = "interrupted"
        log.warning("interrupted; writing what was checked")
    finally:
        fetcher.close()
        recheck = write_outputs(out, rows)
    tally = Counter(r["result"] for r in rows)
    schools = Counter(r["university"] for r in rows if r["result"] in FLAGGED)
    log.info("DONE: %d page(s), %d fact(s) in %.1f min | %s", len({r["url"] for r in rows}), len(rows),
             (time.time() - started) / 60, dict(tally))
    if schools:
        log.info("flagged per school: %s", dict(schools.most_common()))
    log.info("%d cell(s) to research again -> %s", len(recheck), out / "recheck.csv")
    if recheck:
        log.info("re-research them with: python -m field_pipeline %s --recheck %s",
                 subprocess.list2cmdline([str(wb)]), subprocess.list2cmdline([str(out / "recheck.csv")]))
    return {"tally": dict(tally), "recheck": len(recheck), "out": out, "stopped": stopped}


def install_schedule(wb: Path, browser: str, day: int, hour: int) -> Path:
    """A launchd agent that runs this check on `day` of every month at `hour`:00 (the Mac must be awake)."""
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv not found on PATH; install it or add it to PATH first")
    workspace = Path(__file__).resolve().parents[1]
    logs = wb.with_name(f"{wb.stem}_field_pipeline") / "health"
    logs.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [uv, "run", "--with", "openpyxl", "--with", "beautifulsoup4", "--with", "requests",
                             "--with", "pypdf", "python", "-m", "field_pipeline.health", str(wb), "--browser", browser],
        "WorkingDirectory": str(workspace),
        "StartCalendarInterval": {"Day": day, "Hour": hour, "Minute": 0},
        "EnvironmentVariables": {"PATH": f"{Path(uv).parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        "StandardOutPath": str(logs / "launchd.log"),
        "StandardErrorPath": str(logs / "launchd.log"),
    }
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(PLIST)], capture_output=True)  # replace an older version
    with open(PLIST, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], check=True)
    return PLIST


def uninstall_schedule() -> bool:
    if not PLIST.exists():
        return False
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST)], capture_output=True)
    PLIST.unlink()
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx")
    ap.add_argument("--sheet", action="append", help="only facts on this sheet (repeatable)")
    ap.add_argument("--only", action="append", help="only rows whose university contains this text (repeatable)")
    ap.add_argument("--limit", type=int, help="check at most this many pages")
    ap.add_argument("--links", action="store_true", help="also check last year's workbook links (no quotes: liveness)")
    ap.add_argument("--stale-days", type=int, default=STALE_DAYS,
                    help=f"skip pages checked within this many days (default {STALE_DAYS}; 0 = check everything)")
    ap.add_argument("--prior", type=Path, help="last year's workbook (default: the one OLD *.xlsx next to it)")
    ap.add_argument("--browser", choices=("local", "cloud", "auto", "none"), default="local",
                    help="local Chrome (default), Browser Use cloud, auto (cloud if BROWSER_USE_API_KEY), none (HTTP)")
    ap.add_argument("--session-minutes", type=int, default=15)
    ap.add_argument("--schools-per-session", type=int, default=8, help="cloud: sites per browser session (default 8)")
    ap.add_argument("--foreground", action="store_true", help="local Chrome: show the checker's tab")
    ap.add_argument("--db", type=Path, help="SQLite path (default field_pipeline/data/pipeline.sqlite)")
    ap.add_argument("--install-schedule", action="store_true",
                    help="install a monthly launchd job for this workbook (uses --browser; default auto) and exit")
    ap.add_argument("--uninstall-schedule", action="store_true", help="remove the monthly launchd job and exit")
    ap.add_argument("--day", type=int, default=1, help="schedule: day of the month (default 1)")
    ap.add_argument("--hour", type=int, default=9, help="schedule: hour, 24-hour clock (default 9)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.uninstall_schedule:
        print(f"removed {PLIST}" if uninstall_schedule() else "no schedule installed")
        return
    if args.install_schedule:
        browser = args.browser if "--browser" in sys.argv else "auto"
        path = install_schedule(Path(args.xlsx).resolve(), browser, args.day, args.hour)
        print(f"installed {path}: day {args.day} of every month at {args.hour}:00, --browser {browser}")
        return
    summary = run(args)
    print(f"\n{summary['tally']}\n{summary['recheck']} cell(s) to research again -> {summary['out'] / 'recheck.csv'}")
    if summary["stopped"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
