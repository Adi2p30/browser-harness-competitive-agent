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
   BROWSER_USE_API_KEY=<your Browser Use key>   # optional: only used with --browser cloud (billed)
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
| Browser Use cloud browser instead of your Chrome (billed) | `--browser cloud` |
| Local Chrome: briefly show the pipeline's tab so scroll-only counters load | `--foreground` |
| Schools per cloud session (default 2) | `--schools-per-session 3` |
| Cloud session length limit (default 15 min) | `--session-minutes 15` |
| Allow plain HTTP when the browser can't read a page | `--http-fallback` |
| Debugging only: no browser at all | `--browser none` |
| New research for every cell, ignoring cached pages and results (earlier-found Website Links are kept) | `--fresh` |
| Ask the LLM again for schools already done | `--reextract` |
| Also research cells that already have a value | `--overwrite` |
| Skip the Bing search step | `--no-search` |
| Text chunks per LLM call (default 18) | `--chunks 24` |
| Skip the second LLM pass for missing fields | `--no-second-pass` |
| Use a specific prior-year workbook for carried-forward values | `--prior "../OLD 2025-2026.xlsx"` |
| Never carry values forward from last year's workbook | `--no-prior` |
| Don't search for a Website Link on rows without one | `--no-find-links` |
| Skip IPEDS (conferrals, graduate-share proxies) | `--no-ipeds` |
| More detail in the terminal | `-v` |

Each school's log line shows the time elapsed so far and an estimate of the time left; the run ends with the total.

Every web page is opened in a real browser through browser-harness (plain HTTP is only used for PDFs and
sitemap files). By default that is your own Chrome (`--browser local`, free; enable
`chrome://inspect/#remote-debugging` first). The Browser Use cloud browser is used only with `--browser cloud`
(or `--browser auto` when `BROWSER_USE_API_KEY` is set), even if the key is in `.env`. If the browser can't be
reached the run stops instead of quietly using HTTP.

Cloud sessions are batched: a fresh cloud browser is started every `--schools-per-session` schools (default 2) or
before `--session-minutes` (default 15) runs out, and again if a session dies mid-school. The pipeline always
stops the browser at the end (also on errors and Ctrl-C), because cloud browsers bill while open.

Expect about 5 minutes per school with the cloud browser.

## Output

Every run writes a new version folder; nothing from an earlier run is overwritten, and the input workbook is
never modified:

```
EDIT Competitive Analysis Research 2026-2027_field_pipeline/
  runs.csv                       one line per run: version, start, minutes, done/failed/interrupted, options,
                                 cells written, status counts, the exact command
  runs/2026-10-22_183419/        a normal run
  runs/2026-10-23_090102_fresh/  a --fresh run
    EDIT Competitive Analysis Research 2026-2027_field_pipeline.xlsx
                                 copy of the input with the filled cells: each has a comment (status, source
                                 URL, quote) and a hyperlink; cells left blank get a comment saying why
    decisions.csv                one line per researched cell: status, value, source URL, quote, reason
    run.log                      every page fetched and every decision
    llm.jsonl                    every prompt sent to RCAC and its reply
```

Each run starts from the input workbook, so a version holds only what that run filled. `python -m
knowledge_base` combines all versions (the newest run that filled a cell wins; a later blank never erases a value).

Statuses in `decisions.csv`:

| Status | Meaning | In the workbook? |
|---|---|---|
| `found` | Passed every check, 2026-2027 or undated | yes |
| `found-prior` | Passed every check, but for an earlier year (`cycle` column) | yes, yellow (2025-26) / orange (2024-25) |
| `not-required` | Average GMAT / GRE: the program says the test is not required, optional or waivable (quote checked) | yes ("Not required") |
| `computed` | Tuition total = per-credit x credit hours (site gave no total; only when the credits quote says credits/hours) | yes |
| `carried-forward` | Nothing passed this year; value copied from last year's workbook | yes, grey |
| `school-wide` | Class-profile, score or outcome field with no program-specific value: the school's / university's or business school's graduate figure, e.g. the business school's employment report or its U.S. News profile (MBA figures), labelled in the comment (passed every check; undergraduate figures are never used) | yes, purple |
| `ipeds` | Average Conferrals from federal IPEDS completions data (3-year average) | yes |
| `ipeds-proxy` | Female (%) / International (%) nothing else filled: share of the program's IPEDS *graduates*, not the entering class | yes, blue |
| `not-published` | Class-profile, test-score or outcome field: the program's page was read, no class-profile / outcomes page failed to load, and no page states it | "Not published", light grey |
| `not-found` | No fetched page states it, and a relevant page may have been missed (start page or a profile page could not be read, or a non-profile field) | blank + comment |
| `off-cycle` | Value is for a year older than the field allows, or a later year | blank + comment |
| `other-program` | Value is for another program, undergrad, or school-wide where it must be program-specific | blank + comment |
| `bad-quote` | Model's quote is not on the page (paraphrase or invention) | blank + comment |
| `bad-source` | Ranking value not from (or naming) its publisher | blank + comment |
| `bad-value` / `out-of-range` | Value doesn't match its quote, or is implausible (e.g. GPA 35); also a Yes/No the quote doesn't support (a "No" needs a negation, "only", "fully online", or for 3-year Indian degrees a four-year requirement; a "Yes" fails on "not offered / not STEM") and credit hours the page states in units or courses | blank + comment |
| `no-start-link` | Row has no Website Link, none was found by search, and IPEDS has no web address for the university | blank |

### Year fallbacks

Each field tries, in order: 2026-2027 (or an undated value) → 2025-2026 → 2024-2025 → last year's workbook
(`OLD *.xlsx` next to the input, same sheet + university + program). Class-profile, test-score and outcome fields
then fall back to the school's / university's graduate figure (`school-wide`), and Female / International (%) last
to IPEDS graduates (`ipeds-proxy`). How far back a field may go is `max_lag` in
`fields.py`: deadlines never fall back (an old deadline is wrong), tuition, scholarships and deposits go back one
year, everything else (class profile, outcomes, rankings, structure) two. The model returns up to 3 dated
candidates per field and the newest one that passes wins; the second pass also retries fields that only found an
older year. A row without a Website Link takes last year's link, else a search for a `.edu` page naming the
program (Bing, or DuckDuckGo when Bing finds nothing or shows a challenge page; written to the workbook in yellow,
so check it). Links found this way are reused by later runs, including `--fresh` ones. A row without a Program Name
searches for its sheet's category with abbreviations spelled out (`MS HRM` -> "MS human resource management",
`discover.ABBREV`). If no program page is found, the row is still researched on the university's own site (its web
address from the IPEDS directory): the searches and crawl only need the domain. That home page is not written as the
Website Link, and such rows never get `not-published` (the program page was not read).

### U.S. News program profiles

Each row is mapped to its U.S. News page by sheet: online MBA → `usnews.com/education/online-education/<school>/mba`,
other online sheets → `.../business` (the school's online non-MBA business master's), residential sheets → the
business-school page `usnews.com/best-graduate-schools/top-business-schools/<school>`. The online pages give
acceptance rate, GPA, GMAT, work experience, international %, female %, average age, enrollment and rank for free;
these count as program-level values and their cell comment names the profile. Online Economics is not a business
program, so for that sheet the online business profile only gives `school-wide` values. The business-school page lists
the specialty ranks (Finance, Marketing, Business Analytics, Accounting...). Its GMAT, GPA and GRE are behind
the paid U.S. News Compass, so they cannot be read; its free figures (e.g. specialty master's international % and
gender, enrollment, acceptance rate) are school-level, so for non-MBA sheets they are only used as the
`school-wide` fallback. Every row always gets the business-school profile (e.g.
`usnews.com/best-graduate-schools/top-business-schools/clark-university-01109`), whatever else was found, plus
its online profile on online sheets; the profile is offered to the class-stat groups (scores, class, outcomes) and
the U.S. News ranking, and its 3 best excerpts are always shown in those calls. The profile is found from links on U.S. News pages already fetched for the row (the
graduate rankings page links it; online profiles via the school's online-education page), then Bing, then
DuckDuckGo, so a Bing challenge page no longer loses it. `best-colleges` (undergraduate) pages are always rejected.

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
  still at 0 become `[counter not loaded]` so a real-looking 0% is never extracted. Tallies ("0/4") and counters
  in navigation, headers, buttons, forms and cart/compare widgets ("0 Shortlisted") are left alone. If this step
  fails on a page, the page is still read. Some counters only run in
  the visible tab: the cloud browser shows the pipeline's tab for them; in your own Chrome add `--foreground`
  to do the same (a few seconds per such page, then your tab is shown again);
- writes table cells with their column header (`Average GMAT | MSF: 640 | MSBA: 655`) when a table has 3+ columns;
- keeps image alt text and SVG chart labels that contain a number (not icon names such as "down arrow"), and
  `<dt>/<dd>` pairs, as text;
- OCRs large images on profile-like pages when `tesseract` is installed (`brew install tesseract`; optional).

Pages that could not be read (usually a momentary browser timeout) are retried once at the end of each school,
with plain HTTP if the browser still fails.

Pages and results are cached in `agent-workspace/field_pipeline/data/pipeline.sqlite`, so a rerun only does new
work. To start completely fresh, delete that file.

## Review queue

`review.py` gathers what the pipeline couldn't settle so a person can finish it. That covers every cell whose
newest pipeline decision isn't a filled status and that nothing else fills (another run, the workbook, an
earlier review), plus values the monthly source check flagged. Cells are grouped by what to do, most useful
first:

| Group | What it is | Quick decisions |
|---|---|---|
| Rejected value to check | A value was found but a check rejected it; the candidate and its quote are shown | Accept, type a value, leave blank |
| Source changed | The source page no longer has the quote; shows what it says now | Type the new value, keep the current one, leave blank |
| Research by hand | Not on any page the pipeline read | Type a value, leave blank |
| Computed once credit hours are in | Tuition totals | Type a value, leave blank |
| Yes/no not mentioned on the site | Experiential and community extras | Answer No (yes/no questions only), type a value, leave blank |
| Ranking not found | Most likely not ranked | Not ranked, type a value, leave blank |
| Not published: confirm | The program's pages were read and none states it | Leave blank, type a value |

Work through it on the web app's **Review** page. Set your name once and it's stored with each decision. A
decision removes the cell from the queue, and the **Reviewed** tab can undo it. To work in Excel instead, export
the queue, fill the yellow Decision / Value / Source URL / Note columns, and import it back. The web app's page
has buttons for both, or use the command line:

```bash
uv run --with openpyxl --with beautifulsoup4 --with requests --with pypdf \
  python -m field_pipeline.review "../EDIT Competitive Analysis Research 2026-2027.xlsx" --export
```

```bash
uv run --with openpyxl --with beautifulsoup4 --with requests --with pypdf \
  python -m field_pipeline.review "../EDIT Competitive Analysis Research 2026-2027.xlsx" \
  --import "../EDIT Competitive Analysis Research 2026-2027_field_pipeline/review_queue.xlsx" --reviewer "Your Name"
```

Decisions are appended to `<workbook>_field_pipeline/reviews.csv`, which is never rewritten, so it doubles as an
audit log; the newest decision per cell wins. `python -m knowledge_base` uses reviewed values ahead of every
pipeline value, tags them "reviewed", and keeps the source URL and quote, so the monthly check covers them too.
Reviews don't change the workbook: later pipeline runs still research those cells, but the reviewed value keeps
winning in the knowledge base.

## Monthly source health check

`health.py` re-opens every source page behind the knowledge base and checks that each stored quote is still on
it. It makes no RCAC calls, opens about 185 pages, and takes 40–60 minutes. Run it from `agent-workspace/`:

```bash
uv run --with openpyxl --with beautifulsoup4 --with requests --with pypdf \
  python -m field_pipeline.health "../EDIT Competitive Analysis Research 2026-2027.xlsx"
```

The facts it checks are the knowledge base's: the newest pipeline run per cell plus values typed into the
workbook. Each URL is fetched once with the pipeline's browser fetcher, and every fact citing it gets one result:

| Result | Meaning |
|---|---|
| `ok` | The quote is on the page (same matching as validate: case, spacing and punctuation ignored) |
| `moved` | The quote is on the page, but the URL now redirects elsewhere |
| `changed` | The exact quote is gone, but a near copy is there with some words or numbers different (e.g. `$1,250` → `$1,310`, `2026-2027` → `2027-2028`). `snippet` holds what the page says now |
| `missing` | The page reads fine but has neither the quote nor a near copy |
| `dead` | HTTP 404/410, a "page not found" page, or a redirect to the site's home page |
| `unreadable` | Bot wall, timeout, empty page, or a quote that came from image OCR. This says nothing about the fact |
| `live` | A fact with a URL but no quote (typed value, computed total, IPEDS) whose page still opens |

Before `changed` or `missing` is reported, the page is re-read once over plain HTTP. Quotes shorter than four
words are never near-matched, so they are either `ok` or `missing`. Results go to
`<workbook>_field_pipeline/health/<date_time>/`:

- `health.csv`: one line per fact.
- `recheck.csv`: the changed, missing or dead cells.
- `health.log`: the run log.

Every check is also stored in the `health` table of `pipeline.sqlite`, so history builds up month to month. The
refreshed pages replace the cached copies, so the next pipeline run reads current text. Pages checked within
`--stale-days` (default 25) are skipped, which means re-running after a crash picks up where it stopped. Other
options:

- `--links`: also check last year's workbook links (liveness only).
- `--only`, `--sheet` and `--limit`: narrow the check.
- `--browser`: the same choices as the pipeline.

To research the flagged cells again, pass the recheck file to the pipeline. It researches only those cells and
ignores their stored values:

```bash
uv run --with openpyxl --with beautifulsoup4 --with requests --with pypdf \
  python -m field_pipeline "../EDIT Competitive Analysis Research 2026-2027.xlsx" \
  --recheck "../EDIT Competitive Analysis Research 2026-2027_field_pipeline/health/<date_time>/recheck.csv"
```

`python -m knowledge_base` reads the latest check of each fact. The comparison page marks changed, missing and
dead sources with a red "source …" tag, and `kb.sqlite` has the `health`, `health_checked` and `health_now`
columns. The web app's **Source health** page runs a check, lists the flagged values, and has a button that
starts the `--recheck` run.

**Schedule.** This command installs a launchd job that runs the check on the 1st of every month at 9:00 with
`--browser auto`, which uses cloud if `BROWSER_USE_API_KEY` is set and local Chrome otherwise. Change the timing
with `--day` and `--hour`, and the browser with `--browser`:

```bash
uv run --with openpyxl --with beautifulsoup4 --with requests --with pypdf \
  python -m field_pipeline.health "../EDIT Competitive Analysis Research 2026-2027.xlsx" --install-schedule
```

launchd only runs the job while the Mac is awake; a missed run happens at the next wake. Local Chrome also
needs remote debugging allowed. The job's output goes to `health/launchd.log`. Remove it with
`--uninstall-schedule`.

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
     topuniversities.com) and opens the top results there; the school's own "rankings" pages are used too. For QS
     the university's `topuniversities.com/universities/<slug>` page is opened (followed from QS search pages, or
     the slug guessed): it states the QS World University Rank, while QS search/list pages render by script and
     hold no ranks, so they are never excerpt sources.
   - Undergraduate pages, other programs' pages and other schools' sections of the site are skipped. A page under a
     program path must name the whole program (both distinctive words of a two-word name, all but one of a longer
     one; "management" / "business" alone never count), and another professional school's subdomain or section
     (`kentlaw.`, `law.`, `/law/`, medicine, nursing...) is dropped unless the start link is on it.
2. **fetch** (`fetch.py`): browser-harness renders the page and opens accordions/tabs; plain HTTP is the fallback;
   PDFs are read with pypdf.
3. **chunk** (`retrieve.py`): pages -> ~1,500-character chunks; duplicate pages dropped.
4. **retrieve** (`retrieve.py`): BM25 ranks chunks per field group; the top 18 go to the model. A ranking
   publisher's pages are only shown to that publisher's ranking call (and U.S. News profiles to the class-stat
   groups, `fields.visible`): they are long and mention every topic, and once took 40% of the excerpts of other
   calls. The school's own pages are shown to every group.
5. **extract** (`extract.py`): one RCAC call per field group: 16 narrow groups (test scores, class makeup,
   tuition, aid, deadlines, requirements, structure, campus, experiential, community, career, outcomes, basics,
   and one per ranking publisher), so about 16 calls per school. Fields still missing get a **second pass** on the
   next 18 chunks, never one the first pass showed, at most 4 from one page (about 8-12 more calls).
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
  python -m pytest tests/unit/test_field_pipeline.py tests/unit/test_health.py tests/unit/test_review.py -q
```

## Known limits

- Rows without a Website Link and no program page found by search are researched site-wide, which is noisier
  than starting from the program page: fill the link in the workbook when you know it.
- If Browser Use runs out of credits (HTTP 402) the run stops researching, writes everything finished plus the
  stored results of the remaining rows, and stops the browser; rerun later (or without `--browser cloud`) to continue.
- usnews.com is strict with automated browsers; if a profile page can't be opened, U.S. News values come from the
  school quoting its rank. Business-school pages hide most class stats behind U.S. News Compass (paid).
- DuckDuckGo shows a CAPTCHA to the cloud browser, so it is effectively off; Bing works but ignores `site:`,
  which is why queries use the university name instead.
- Class-profile, outcomes and ranking fields are often not published on schools' own pages.
- The row's Program Name steers the model: if it doesn't match the Website Link, values can come from the wrong
  program. Check that the two agree.
