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

Every run writes `decisions.csv` (one row per cell decided) and `run.log`
(`agent-workspace/field_pipeline/__main__.py:setup_logging`) in the new version folder; the web
app tails that same log stream live instead of waiting for the run to finish.
