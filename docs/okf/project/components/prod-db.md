---
type: Component
title: prod.db
description: The production SQLite database; one published fact per (program, field), versioned publishes with per-fact conflict checks, school requests and admin keys.
resource: agent-workspace/prod/db.py
generated: {by: claude-code/opus-5.5, at: '2026-11-24T00:00:00Z'}
status: draft
---

# prod.db

`agent-workspace/prod/db.py` is stdlib `sqlite3` (WAL mode, foreign keys on, autocommit).
Tables:

- `programs`: one row per competitor program. `legacy_key` is its old workbook identity,
  `sheet:row`.
- `facts`: one row per (program, field), where `field` is the sheet's column header. `origin`
  says where the value came from: pipeline, review, workbook or prior-year.
- `fact_history`: every change to a fact, with who made it and when.
- `publishes`: one row per version. The current version is the newest row, or 0.
- `requests`: schools people asked to add (new → claimed → done/rejected).
- `admins`: one key per admin, stored as its sha256 hash.

`apply_changes` publishes a batch in one `BEGIN IMMEDIATE` transaction. Each change carries the
`old` (value, status) it was based on. It applies only if the stored fact still matches;
otherwise it comes back as a conflict, with the current fact and who last changed it. The
rest of the batch still applies. The version goes up by one only when something applied, and
`backup_dir` keeps a copy of the database from before the publish.

Admin keys: `python -m prod.admins add|list|revoke <db> [name]`, run from `agent-workspace/`.
`add` prints the key once; store it as `PROD_ADMIN_KEY` in `.env`.
