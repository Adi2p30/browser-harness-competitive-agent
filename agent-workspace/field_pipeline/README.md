# field_pipeline

Fills the competitive-analysis workbook one **field group** at a time: it finds a program's pages, picks the
text relevant to each group of columns, asks Purdue's RCAC model (`gpt-oss:120b`) for the values, and keeps a
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
| `found` | Passed every check | yes |
| `computed` | Tuition total = per-credit x credit hours (site gave no total) | yes |
| `not-found` | No fetched page states it | blank + comment |
| `off-cycle` | Value is for a year other than 2026-2027 | blank + comment |
| `other-program` | Value is for another program, undergrad, or school-wide where it must be program-specific | blank + comment |
| `bad-quote` | Model's quote is not on the page (paraphrase or invention) | blank + comment |
| `bad-value` / `out-of-range` | Value doesn't match its quote, or is implausible (e.g. GPA 35) | blank + comment |
| `no-start-link` | Row has no Website Link | blank |

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
   - Undergraduate pages, other programs' pages and other schools' sections of the site are skipped.
2. **fetch** (`fetch.py`): browser-harness renders the page and opens accordions/tabs; plain HTTP is the fallback;
   PDFs are read with pypdf.
3. **chunk** (`retrieve.py`): pages -> ~1,500-character chunks; duplicate pages dropped.
4. **retrieve** (`retrieve.py`): BM25 ranks chunks per field group; the top 6 go to the model.
5. **extract** (`extract.py`): one RCAC call per field group (about 8 per school, not one per cell).
6. **validate** (`validate.py`): quote must be on the page, value's numbers in the quote, value in range,
   2026-2027 cycle only, right program.
7. **write** (`workbook.py`, `__main__.py`).

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
- DuckDuckGo shows a CAPTCHA to the cloud browser, so it is effectively off; Bing works but ignores `site:`,
  which is why queries use the university name instead.
- Class-profile, outcomes and ranking fields are often not published on schools' own pages.
- The row's Program Name steers the model: if it doesn't match the Website Link, values can come from the wrong
  program. Check that the two agree.
