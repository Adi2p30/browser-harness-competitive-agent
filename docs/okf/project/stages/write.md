---
type: Pipeline Stage
title: Write
description: Writes a new, never-overwritten version of the workbook plus decisions.csv, run.log, and llm.jsonl.
resource: agent-workspace/field_pipeline/workbook.py
generated: {by: claude-code/sonnet-5, at: '2026-10-27T00:00:00Z'}
status: stable
---

# Write

`workbook.write` saves the filled copy (value, source comment, hyperlink) into
`<workbook>_field_pipeline/runs/<date_time>/`; every run is listed in that folder's parent
`runs.csv`. `knowledge_base` (see [component](/okf/project/components/knowledge-base.md)) later
turns every run's `decisions.csv` into the [knowledge](/okf/knowledge/index.md) bundle.
