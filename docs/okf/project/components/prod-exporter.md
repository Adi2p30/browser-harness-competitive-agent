---
type: Component
title: prod.exporter
description: Builds the knowledge bundle and a workbook laid out like the template from the production database.
resource: agent-workspace/prod/exporter.py
generated: {by: claude-code/opus-5.5, at: '2026-11-24T00:00:00Z'}
status: draft
---

# prod.exporter

Two read-only views of the production database. Both take the column layout from the template
workbook, since the database stores no column order.

- `knowledge_base.from_db.build_from_db(conn, workbook)` returns the same dict as
  `knowledge_base.build()`, with fields derived from the template's headers by the same rule.
  It includes only active programs. Program ids are database ids, and `row` comes from
  `legacy_key`. `write_from_db(db, workbook, repo_root, knowledge_dir=None)` passes that dict
  to `okf_export.write_bundle`, and labels the log line with the version of the same snapshot
  the dict was built from. `write_bundle` always writes to
  `<root>/docs/okf/knowledge`, so a `knowledge_dir` must end in that path.
- `python -m prod.exporter <prod.sqlite> <template.xlsx> <out.xlsx>`, run from
  `agent-workspace/`, writes the database as a workbook (`export_workbook` returns the number of
  fact cells written; the public site serves it as `/download.xlsx`). It is a clean rebuild, not
  an overlay of the template: only the visible program sheets' header rows are kept (header
  values and formatting, column widths, frozen panes). No template data row, comment, hidden
  sheet or other sheet is copied, so values and notes typed into the template never reach it.
  Each sheet gets one row per active program, by legacy row (`legacy_key` `sheet:row`), then
  programs with no `legacy_key`, then id. The identifying columns come from the program record;
  each fact with a value fills its column with a comment `status · source_url` plus the quote
  (cut to 300 characters) and a link to `source_url`. A fact with no value (not published) leaves
  its cell blank. The template is never written.
