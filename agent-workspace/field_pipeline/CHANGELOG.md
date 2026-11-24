# field_pipeline change log

What changed to improve the pipeline, one entry per commit, newest first.

## 2026-11-24: A public site and a local admin app

The web app now runs in one of two modes. The hosted site (`APP_MODE=public`, set by `deploy/serve.py`) serves only
the knowledge pages, the school-request form, the admin-key API and `/download.xlsx`; the pages that start research
runs or change reviews don't exist there, so the hosting kit's start-up check now passes. The download is rebuilt
from the database alone, taking only the workbook's column headers: no hidden sheets, staff comments, blanked
values or inactive schools from the template reach it, and on the real data all 2,178 published values are in it.
The admin app (`./run-webapp.sh`, localhost only, no debug) keeps Run, Review and Health and adds a Requests inbox
(claim, add as program, reject), a Publish page that shows the diff and re-checks it before posting, Add program and
Pull. Every admin form needs a CSRF token, requests from other host names are refused, and a school request that is
already done or rejected can't become a second program. Reverse-proxy configs must allow `/download.xlsx`.

## 2026-11-24: Programs identified by production id, and a hosting kit

Once an admin has pulled the production snapshot, the pipeline works on production programs by their permanent id
instead of by sheet and Excel row, so reordering or renaming workbook rows no longer detaches results, reviews or
health checks, and programs added through the site (which have no workbook row) can be researched and published.
`--program <id>` replaces `--row`, and `field_pipeline.schools` is gone in favour of `prod.client add-program`.
decisions.csv, reviews.csv, recheck.csv and health.csv gain a `program_id` column at the end; existing reviews.csv
and runs.csv files get the new header once, through a temp file and rename with a `.pre-program-id` backup, and
every existing row is kept byte for byte. Old rows map to programs by their sheet and row only when the university
still matches. The first run with a snapshot moves pipeline.sqlite's stored results to the new keys (there is no
rollback, so older checkouts must not share that file). Without a snapshot the pipeline behaves as before. On the
real data the knowledge base, review queue and health check are identical in both modes and a fresh import shows no
changes to publish. Separately, `deploy/` adds what the hosted site needs: a waitress entry point that refuses to
start while admin-only pages exist, a hardened systemd unit running from a hash-checked virtualenv, nightly
backups, allowlisting nginx and Apache examples, and a step-by-step runbook.

## 2026-11-24: Import, export, a server API and an admin client for the production database

The production database can now be filled, served and published to. `python -m prod.importer` loads the workbook,
every run's decisions, the review decisions and last year's workbook into an empty database as version 1, in the same
order of precedence the knowledge base uses. On the real data that is 313 programs, 80 last-year-only programs kept
but hidden, and 4,411 values, each matching the knowledge base exactly. The hosted app gains an admin-key API
(`webapp/api.py`): export a snapshot, publish changes with per-value conflict checks, work through school requests
and add programs. It also gains a public "request a school" form with a size cap, a per-visitor rate limit and
cleaned input. After each publish the server rebuilds the knowledge pages from the database
(`knowledge_base.from_db`), and `python -m prod.exporter` writes the database back into the workbook layout for
anyone who wants a spreadsheet. Admins use `python -m prod.client` to pull, see what would change, and publish after
confirming. Only pipeline and reviewed values are published, never typed-in or last-year ones. A fresh import
followed by a diff shows no changes. Rows are matched by sheet and row and checked against the university's name,
so a reordered workbook can't publish to the wrong school. Programs created through the API are matched by sheet,
university and program.

## 2026-11-24: A production database to replace the workbook as the source of truth

The workbook identifies a school by its sheet and row number, so sorting or inserting rows detaches every run, review
and source quote from the school it belongs to, and nothing records who changed a value or what it was before. The new
`agent-workspace/prod/` package is the start of a production database that the hosted site will own: every program
gets a permanent id (keeping its old "sheet:row" as `legacy_key`), each fact keeps its value, status, cycle, source,
quote and origin, and every change is kept in a history table. Admins will publish batches of changes; each change
names the value it started from and is applied only if that value is still current, so several admins can publish at
once and only real clashes come back as conflicts, with who changed the value and when. Each publish is one
transaction after a backup (the newest 50 are kept). Admin keys are stored only as hashes and managed with
`python -m prod.admins add|list|revoke|rotate`. Nothing uses the database yet; the importer, exporter, server API and
admin client follow.

## 2026-11-24: Maintainers can add schools that aren't in the workbook

A competitor missing from the workbook could not be researched at all, and typing its name into the web app's school
filter only matched existing rows. Maintainers now add one from the command line with `python -m field_pipeline.schools`
(`add`, `list`, `remove`). Added schools are kept in `<workbook>.added_schools.csv` beside the workbook, never in the
workbook itself, and every run, review and knowledge-base build reads them as rows numbered from 10001; output copies
place them under the sheet's last school. A new `--row SHEET:ROW` flag researches just one row, which `add` prints for
the new school. The web app's school filter now says that it only matches the workbook and that new schools are added
by a maintainer. This is a stopgap until the production database gives every program a permanent id.

## 2026-11-19: Review queue by run

The review queue used to merge every run, taking the newest result for each cell, so a reviewer couldn't work
through what one run left behind. It is now built per run: the Review page opens on the newest run and has a run
picker, labelled from runs.csv with date, programs and row count, plus an "All runs" option that keeps the old
merged view. A run's queue holds the cells that run left unfilled, and under "Source changed" only values that came
from that run. Cells another run or the workbook has filled since are left out and counted on the page. Filters,
decisions, undo and the Excel round trip all stay on the selected run, and the export is named after it. On the
command line, `field_pipeline.review` gains `--run` (a version folder or `latest`) and `--list-runs`.
`review.queue()` returns the items with those counts, and `build_queue()` keeps returning just the items.

## 2026-11-19: Stop runs whose sheet and school filters match nothing

A run for a school that isn't on any of the selected sheets used to finish as "done" with 0 rows and 0 cells,
without saying why. For example, Carnegie Mellon picked with 1yr MBA and MS Accounting, where it has no row.
This is easy to do from the web app, whose school list holds every sheet's schools. The pipeline now checks the
--sheet and --only filters before starting. It names each school that isn't on the chosen sheets, and the sheets
it is on, and stops with exit code 2 and status "nothing to do" in runs.csv when no row matches. The web app's run
form refuses that combination with the same explanation, while a selection where at least one school matches
still starts. Both use the new `workbook.selection_notes()`.

## 2026-11-12: Wider retrieval, cleaner sources, source health checks and a review queue

**Extraction.** Each LLM call now gets 18 chunks instead of 10. Ranking-publisher pages (U.S. News, QS,
Best-Masters) are only visible to their own ranking group, apart from U.S. News profiles, which also feed scores,
class profile and outcomes. As a result, third-party text dropped from about 40% to 5% of what the other groups
see. The second pass skips every chunk the first pass saw, the pinned ones included, and takes at most 4 chunks
per page. Business-school-wide figures, including a U.S. News business-school profile's "other" values, count as
school-wide instead of being rejected as another program's.

**Discovery.** Program-name abbreviations (HRM, MSGSCM, GSCM, BAIM) are spelled out before searching. Other
programs' degree pages are dropped only when they name the other program in full, and other professional schools
(law, medicine and so on) are always foreign. A home-page start link no longer exempts the whole site. QS profiles
are found from QS search results or a guessed slug. Rows with no Website Link are now researched from the
university's home page in the IPEDS directory instead of being skipped. This took BYU from 0 to 19 of 43 fields.

**Output.** Profile, score and outcome fields that the program's pages were read for and don't state are written
as "Not published", in grey. The run log reports the fill rate both over all cells and over the cells that aren't
marked not-published. On the same four MS HRM schools, filled cells went from 79 to 104 (46% to 60%).

**Source health check** (`health.py`). This monthly job re-opens every source page in the knowledge base and
checks each stored quote. It reports ok, moved, changed (with what the page says now), missing, dead or
unreadable, and re-reads a page over plain HTTP before flagging it. Results are stored in a `health` table, and
the knowledge base marks stale facts. The job writes a `recheck.csv`, and a new `--recheck` flag researches only
those cells again. `--install-schedule` installs a monthly launchd job. Quote matching moved into
`validate.quote_on_page`, so validate and the health check can't drift apart, and OCR text is now marked so a
quote from an image is never reported as gone.

**Review queue** (`review.py`). Every cell the pipeline couldn't settle, plus values whose source changed, is
grouped by the action it needs: check a rejected candidate, research by hand, a derived total, a yes/no the site
never mentions, an unranked program, or not published. People decide each cell on the web app's Review page, or
by exporting the queue to Excel and importing it back. Decisions are appended to `reviews.csv` and override
pipeline values in the knowledge base.
