---
type: Component
title: prod API
description: The webapp's /api routes over the production database (export, publish, requests, programs; admin key) and the public /request form.
resource: webapp/api.py
generated: {by: claude-code/opus-5.5, at: '2026-11-24T00:00:00Z'}
status: draft
---

# prod API

`webapp/api.py` is a Flask blueprint that `app.py` registers. It serves
[prod.db](prod-db.md) over JSON. Each request opens its own connection.

Config is read from the environment on every request:

- `PROD_DB`: the database path. Without it, `/api/*` and `/request` answer 503 and the rest of
  the app still works.
- `PROD_BACKUP_DIR`: where publishes back up first. The default is `<PROD_DB dir>/backups`.
- `KNOWLEDGE_DIR`: the knowledge bundle to rebuild. The default is `docs/okf/knowledge`.
- `PROD_TEMPLATE`: the template workbook. The default is the repo root's
  `EDIT Competitive Analysis Research*.xlsx`, found the way `app.py` finds it.

Admin routes need `Authorization: Bearer <key>` (see `python -m prod.admins`). A missing, bad
or revoked key gets 401. Every error under `/api` is `{"error": "..."}`.

- `GET /api/export`: the snapshot.
- `POST /api/publish` `{"changes", "note"}`: runs `apply_changes` as the key's admin. Bad input
  gets 400 and writes nothing. When anything applied, it rebuilds the knowledge bundle with
  `knowledge_base.from_db.write_from_db(conn, template, knowledge_dir)`. The response says
  `"rebuilt": true` or `false`. A failed rebuild is logged and never fails the publish.
- `GET /api/requests?status=`, `POST /api/requests/<id>/claim` (409 names who holds it),
  `POST /api/requests/<id>/status` `{"status", "admin_note"?, "program_id"?}`.
- `POST /api/programs` `{"sheet", "university", "school"?, "program"?, "link"?, "request_id"?}`
  returns 201 `{"id"}`. With `request_id`, that request becomes done.

`/request` is public. Anyone can ask for a school to be added. Sheet names come from the
template workbook when it can be read. A filled hidden `website` field is accepted and dropped.
Each IP gets 5 submissions an hour, counted in memory. Behind a reverse proxy, the app must see
the client's address, not the proxy's.
