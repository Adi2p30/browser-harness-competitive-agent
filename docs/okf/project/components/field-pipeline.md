---
type: Component
title: field_pipeline
description: Fills the competitive-analysis workbook one field group at a time, verifying every value against a quoted page.
resource: agent-workspace/field_pipeline/__main__.py
generated: {by: claude-code/sonnet-5, at: '2026-10-27T00:00:00Z'}
status: stable
---

# field_pipeline

`python -m field_pipeline <workbook.xlsx> --sheet "<sheet>"`, run from `agent-workspace/`.
Per row: [discover](/okf/project/stages/discover.md) a start page, then run every blank field
group through [fetch](/okf/project/stages/fetch.md) → [chunking](/okf/project/stages/chunking.md)
→ [retrieve](/okf/project/stages/retrieve.md) → [extract](/okf/project/stages/extract.md) →
[validate](/okf/project/stages/validate.md), fall back to [IPEDS](/okf/project/stages/ipeds.md)
federal data for a few fields, then [write](/okf/project/stages/write.md) a new version of the
workbook. Imports `browser_harness.admin`/`helpers` in-process for local-mode browsing — see
[browser-harness-core](/okf/project/components/browser-harness-core.md).

Once an admin has pulled production's snapshot (`python -m prod.client pull`), the rows are
production's active programs, identified by program id (`--program <id>` picks some), and the
workbook only supplies each sheet's columns (rows not in production are skipped; new programs
come through `prod.client add-program`). A program's blanks are the columns with no current
production fact. Without a snapshot the rows are the workbook's, keyed `sheet:row`. Results,
reviews, recheck rows and the stored page results all follow that key; files from before
program ids map by legacy key (`agent-workspace/field_pipeline/workbook.py:ref_of`).

Every run writes `decisions.csv` (one row per cell decided) and `run.log`
(`agent-workspace/field_pipeline/__main__.py:setup_logging`) in the new version folder; the web
app tails that same log stream live instead of waiting for the run to finish.

Monthly, `python -m field_pipeline.health <workbook.xlsx>` re-checks every stored quote against its live
source page ([health](/okf/project/stages/health.md)). Its `recheck.csv` feeds `--recheck`, which
researches only the flagged cells again. What neither settles goes to the review queue
(`python -m field_pipeline.review`, the web app's Review page), where people's decisions
(`reviews.csv`) override pipeline values in the knowledge base.
