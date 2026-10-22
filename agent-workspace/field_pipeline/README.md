# field_pipeline

Fills the competitive-analysis workbook one **field group** at a time: it finds a program's pages, picks the
text relevant to each group of columns, asks Purdue's RCAC model (`qwen3.8:27b`; set `RCAC_MODEL` to change it) for the values, and keeps a
value only if code can verify its quote on the page.

## One-time setup

1. Install `uv` (runs Python 3.11+ with the right packages):
   ```bash
   brew install uv
   ```
2. Create `.env` in the repo root (it is git-ignored) with your keys:
   ```
   RCAC_API_KEY=<your RCAC GenAI Studio key>
   BROWSER_USE_API_KEY=<your Browser Use key>   # optional: cloud browser for sites that block plain requests
   ```
3. Make sure the workbook is in the repo root: `EDIT Competitive Analysis Research 2026-2027.xlsx`.

## Run it

From `agent-workspace/`:

```bash
cd agent-workspace
uv run --with openpyxl --with beautifulsoup4 --with requests --with pypdf \
  python -m field_pipeline "../EDIT Competitive Analysis Research 2026-2027.xlsx" --sheet "Online MS Economics"
```

Useful variations:

| Goal | Add |
|---|---|
| Try one or two schools first | `--only "BOSTON COLLEGE" --only DEPAUL` |
| First N rows only | `--limit 3` |
| Several sheets | `--sheet "Online MS Economics" --sheet "Online MSGSCM"` |
| Every sheet | leave out `--sheet` |
| Your own Chrome instead of the cloud | `--browser local` (enable `chrome://inspect/#remote-debugging` first) |
| Schools per cloud session (default 2) | `--schools-per-session 3` |
| Cloud session length limit (default 15 min) | `--session-minutes 15` |
| Allow plain HTTP when the browser can't read a page | `--http-fallback` |
| Debugging only: no browser at all | `--browser none` |
| Ask the LLM again for schools already done | `--reextract` |
| Also research cells that already have a value | `--overwrite` |
| Skip the Bing search step | `--no-search` |
| Text chunks per LLM call (default 10) | `--chunks 12` |
| Skip the second LLM pass for missing fields | `--no-second-pass` |
| Use a specific prior-year workbook for carried-forward values | `--prior "../OLD 2025-2026.xlsx"` |
| Never carry values forward from last year's workbook | `--no-prior` |
| Don't search for a Website Link on rows without one | `--no-find-links` |
| Skip IPEDS (conferrals, graduate-share proxies) | `--no-ipeds` |
| More detail in the terminal | `-v` |

Every web page is opened in a real browser through browser-harness (plain HTTP is only used for PDFs and
sitemap files). `--browser auto` (the default) uses the Browser Use cloud browser when `BROWSER_USE_API_KEY` is
set, else your local Chrome. If the browser can't be reached the run stops instead of quietly using HTTP.

Cloud sessions are batched: a fresh cloud browser is started every `--schools-per-session` schools (default 2) or
before `--session-minutes` (default 15) runs out, and again if a session dies mid-school. The pipeline always
stops the browser at the end (also on errors and Ctrl-C), because cloud browsers bill while open.

Expect about 5 minutes per school with the cloud browser.

## Output

Next to the input workbook:

- `EDIT Competitive Analysis Research 2026-2027_field_pipeline.xlsx` — a copy with the filled cells. Each filled
  cell has a comment (status, source URL, quote) and a hyperlink to the source. Cells left blank get a comment
  saying why. The original workbook is never modified.
- `EDIT Competitive Analysis Research 2026-2027_field_pipeline/`
  - `decisions.csv` — one line per researched cell: status, value, source URL, quote, reason. Review this first.
  - `run.log` — every page fetched and every decision.
  - `llm.jsonl` — every prompt sent to RCAC and its reply.

Statuses in `decisions.csv`:

| Status | Meaning | In the workbook? |
|---|---|---|
| `found` | Passed every check, 2026-2027 or undated | yes |
| `found-prior` | Passed every check, but for an earlier year (`cycle` column) | yes, yellow (2025-26) / orange (2024-25) |
| `computed` | Tuition total = per-credit x credit hours (site gave no total) | yes |
| `carried-forward` | Nothing passed this year; value copied from last year's workbook | yes, grey |
| `ipeds` | Average Conferrals from federal IPEDS completions data (3-year average) | yes |
| `ipeds-proxy` | Female (%) / International (%) nothing else filled: share of the program's IPEDS *graduates*, not the entering class | yes, blue |
| `not-found` | No fetched page states it | blank + comment |
| `off-cycle` | Value is for a year older than the field allows, or a later year | blank + comment |
| `other-program` | Value is for another program, undergrad, or school-wide where it must be program-specific | blank + comment |
| `bad-quote` | Model's quote is not on the page (paraphrase or invention) | blank + comment |
| `bad-source` | Ranking value not from (or naming) its publisher | blank + comment |
| `bad-value` / `out-of-range` | Value doesn't match its quote, or is implausible (e.g. GPA 35) | blank + comment |
| `no-start-link` | Row has no Website Link | blank |

### Year fallbacks

Each field tries, in order: 2026-2027 (or an undated value) → 2025-2026 → 2024-2025 → last year's workbook
(`OLD *.xlsx` next to the input, same sheet + university + program). How far back a field may go is `max_lag` in
`fields.py`: deadlines never fall back (an old deadline is wrong), tuition, scholarships and deposits go back one
year, everything else (class profile, outcomes, rankings, structure) two. The model returns up to 3 dated
candidates per field and the newest one that passes wins; the second pass also retries fields that only found an
older year. A row without a Website Link takes last year's link, else a Bing search for a `.edu` page naming the
program (written to the workbook in yellow, so check it).

### U.S. News program profiles

Each row is mapped to its U.S. News page by sheet: online MBA → `usnews.com/education/online-education/<school>/mba`,
other online sheets → `.../business` (the school's online non-MBA business master's), residential sheets → the
business-school page `usnews.com/best-graduate-schools/top-business-schools/<school>`. The online pages give
acceptance rate, GPA, GMAT, work experience, international %, female %, average age, enrollment and rank for free;
these count as program-level values and their cell comment names the profile. The business-school page lists
the specialty ranks (Finance, Marketing, Business Analytics, Accounting...); its free class stats are MBA figures,
so they are only used for MBA sheets. `best-colleges` (undergraduate) pages are always rejected.

### IPEDS (conferrals and graduate-share proxies)

`ipeds.py` reads the NCES IPEDS Completions files (master's degrees per school and CIP code; downloaded once,
about 25 MB, into `field_pipeline/data/ipeds/`). No browser and no LLM:

- **Average Conferrals (past 3 years)** = mean master's degrees awarded over the last 3 published years.
- **Female (%) / International (%)**, only when nothing else filled them: women / nonresident share of those
  graduates, labelled `ipeds-proxy` with a blue fill. Graduates are not the entering class, so treat these as
  estimates.

The school is matched by its web domain and name ("PURDUE UNIVERSITY - WEST LAFAYETTE" → "Purdue
University-Main Campus"). Systems that report online programs separately (Penn State World Campus, ASU Digital
Immersion) get that unit on online sheets. The CIP code comes from the program's own pages when they state it
(STEM programs do, for OPT: "CIP Code 45.0603"), else from the sheet (`CIP` in `ipeds.py`; Master of Business &
Technology has none). All of a school's programs under one code are counted together (online + on-campus, or
several STEM master's sharing 52.1301); the cell comment names the code and the yearly counts.

### Class-profile pages

Class stats often hide in animated counters, multi-column tables and infographics. The fetcher:

- sets "by the numbers" counters to the target in their data attribute; if some still read 0 it scrolls the
  page (with a forced render, since the pipeline's tab is in the background) and reads them again; counters
  still at 0 become `[counter not loaded]` so a real-looking 0% is never extracted;
- writes table cells with their column header (`Average GMAT | MSF: 640 | MSBA: 655`) when a table has 3+ columns;
- keeps image alt text, SVG chart labels and `<dt>/<dd>` pairs as text;
- OCRs large images on profile-like pages when `tesseract` is installed (`brew install tesseract`; optional).

Pages and results are cached in `agent-workspace/field_pipeline/data/pipeline.sqlite`, so a rerun only does new
work. To start completely fresh, delete that file.

## How it works

```
workbook -> 1 discover -> 2 fetch -> 3 chunk -> 4 retrieve -> 5 extract -> 6 validate -> 7 write
```

1. **discover** (`discover.py`, `search.py`, no LLM):
   - **search** for each field group, in the browser: Bing with "<university> <program> <topic>" (results off the
     school's site are dropped), DuckDuckGo if Bing gives nothing, and the school's own site-search box. The top 3
     hits per group are opened. Engines that show a CAPTCHA are switched off for the run (never solved).
   - **crawl** outward from the start link and the found pages, plus the school's sitemap; URLs are scored per
     field group and each group gets its own best pages.
   - **rankings**: each ranking column searches its publisher (usnews.com, best-masters.com,
     topuniversities.com) and opens the top results there; the school's own "rankings" pages are used too.
   - Undergraduate pages, other programs' pages and other schools' sections of the site are skipped.
2. **fetch** (`fetch.py`): browser-harness renders the page and opens accordions/tabs; plain HTTP is the fallback;
   PDFs are read with pypdf.
3. **chunk** (`retrieve.py`): pages -> ~1,500-character chunks; duplicate pages dropped.
4. **retrieve** (`retrieve.py`): BM25 ranks chunks per field group; the top 10 go to the model.
5. **extract** (`extract.py`): one RCAC call per field group: 16 narrow groups (test scores, class makeup,
   tuition, aid, deadlines, requirements, structure, campus, experiential, community, career, outcomes, basics,
   and one per ranking publisher), so about 16 calls per school. Fields still missing get a **second pass** on the
   next 10 chunks (about 8-12 more calls).
6. **validate** (`validate.py`): quote must be on the page, value's numbers in the quote, value in range,
   academic year within the field's `max_lag`, right program; ranking values must come from (or name) their
   publisher.
7. **write** (`workbook.py`, `__main__.py`): fallback tiers, cell colours and the `cycle` column.

Columns, their groups, definitions and valid ranges are in `fields.py`. To support a new column, add a `Field`
line there.

## Tests

From the repo root:

```bash
uv run --with pytest --with openpyxl --with beautifulsoup4 --with requests --with pypdf \
  python -m pytest tests/unit/test_field_pipeline.py -q
```

## Known limits

- Rows without a Website Link are skipped (fill the link in the workbook).
- If Browser Use runs out of credits (HTTP 402) the run stops researching, writes everything finished plus the
  stored results of the remaining rows, and stops the browser; rerun later (or with `--browser local`) to continue.
- usnews.com is strict with automated browsers; if a profile page can't be opened, U.S. News values come from the
  school quoting its rank. Business-school pages hide most class stats behind U.S. News Compass (paid).
- DuckDuckGo shows a CAPTCHA to the cloud browser, so it is effectively off; Bing works but ignores `site:`,
  which is why queries use the university name instead.
- Class-profile, outcomes and ranking fields are often not published on schools' own pages.
- The row's Program Name steers the model: if it doesn't match the Website Link, values can come from the wrong
  program. Check that the two agree.
