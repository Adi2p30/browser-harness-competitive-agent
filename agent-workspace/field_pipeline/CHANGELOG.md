# field_pipeline change log

What changed to improve the pipeline, one entry per commit, newest first.

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
