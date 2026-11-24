---
type: Component
title: prod.importer
description: One-time load of the workbook's knowledge base into an empty production database as version 1.
resource: agent-workspace/prod/importer.py
generated: {by: claude-code/opus-5.5, at: '2026-11-24T00:00:00Z'}
status: draft
---

# prod.importer

`python -m prod.importer <workbook.xlsx> <prod.sqlite> [--prior OLD.xlsx] [--by NAME]`, run
from `agent-workspace/`. It builds the knowledge base the way `python -m knowledge_base` does.
That means every pipeline version, `reviews.csv`, and the one `OLD *.xlsx` next to the
workbook unless `--prior` names another. The winning value of each (program, field) becomes
one fact. The programs are always the workbook's rows, even when a `prod-snapshot.json` sits
next to it: the import is what assigns the program ids.

- Programs: `legacy_key` is `sheet:row`. Schools only in last year's workbook are imported
  with `active=0` and no key. `link` is the program URL, and `added_by` is `--by` (default
  `import`).
- Facts: the value goes through `prod.values.to_text`, and blanks are skipped. Review values
  pass through `to_text` like every other value. `status` is the
  knowledge-base status unchanged. `origin` maps from it: reviewed → review, a pipeline status
  → pipeline, entered → workbook, prior-year → prior-year. `run` is the pipeline version folder
  for pipeline facts. `reviewed_by` is the reviewer for reviewed facts. Empty cycle, source and
  quote are stored as NULL.
- Everything is written in one transaction, with a `publishes` row for version 1. No
  `fact_history` rows are written; history starts with the first publish.
- A database that already has programs or publishes is refused (exit 1).

`import_workbook(workbook, db_path, prior=None, by="import")` does the same and returns the
summary it prints.
