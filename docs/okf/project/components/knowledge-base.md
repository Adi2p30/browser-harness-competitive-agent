---
type: Component
title: knowledge_base
description: Turns the workbook + field_pipeline's results into a comparison report and an OKF concept bundle. No web or LLM calls.
resource: agent-workspace/knowledge_base/__main__.py
generated: {by: claude-code/sonnet-5, at: '2026-10-27T00:00:00Z'}
status: stable
---

# knowledge_base

`python -m knowledge_base <workbook.xlsx>`, run from `agent-workspace/`. Reads every
`field_pipeline` run's `decisions.csv`, the workbook's own typed cells, and last year's
workbook, in that priority order, into one fact per (program, field). Writes:

- `<workbook>_kb/kb.sqlite` and `competitive_analysis.html` (unchanged, pre-existing)
- `docs/okf/knowledge/` — the same facts as an [OKF](/okf/index.md) concept bundle
  (`agent-workspace/knowledge_base/okf_export.py:write_bundle`): one `Program` concept per
  program — every field it has, in one table — plus an `Attested Computation` concept per
  derived tuition total that a computed field's row links to. One link per program; `--no-okf`
  skips this. The web app's Knowledge page reads this bundle.
