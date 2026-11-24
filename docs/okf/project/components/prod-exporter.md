---
type: Component
title: prod.exporter
description: Builds the knowledge bundle and a filled copy of the template workbook from the production database.
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
  to `okf_export.write_bundle`. `write_bundle` always writes to
  `<root>/docs/okf/knowledge`, so a `knowledge_dir` must end in that path.
- `python -m prod.exporter <prod.sqlite> <template.xlsx> <out.xlsx>`, run from
  `agent-workspace/`, writes a filled copy (`export_workbook` returns the number of cells
  written). A program with `legacy_key` `sheet:row` below 10001 fills that row through
  `field_pipeline.workbook.write`. Every other active program goes in a new row under the
  sheet's last school, written after `write()` returns: added schools (rows 10001 and up)
  first, then programs with no `legacy_key`. Each cell's comment is
  `status · source_url` plus the quote, cut to 300 characters, and the cell links to
  `source_url`. There is no fill. Facts for identifying columns are skipped, and the
  template is never written.
