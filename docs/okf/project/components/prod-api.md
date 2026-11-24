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

The hosted app must run in public mode (Step 8). Until then, `app.py` also serves `/run`,
`/health` and `/review`, which have no login, so do not expose it as is.

Config is read from the environment on every request:

- `PROD_DB`: the database path. Without it, `/api/*` and `/request` answer 503 and the rest of
  the app still works.
- `PROD_BACKUP_DIR`: where publishes back up first. The default is `<PROD_DB dir>/backups`.
- `KNOWLEDGE_DIR`: the knowledge bundle to rebuild. It must be the folder the site's
  `/knowledge` pages read, `<repo>/docs/okf/knowledge`, which is the default.
  `write_from_db` refuses a path that doesn't end in `docs/okf/knowledge`.
- `PROD_TEMPLATE`: the template workbook. The default is the repo root's
  `EDIT Competitive Analysis Research*.xlsx`, found the way `app.py` finds it.
- `PROD_TRUSTED_PROXIES`: how many reverse proxies sit in front of the app. The default is 0,
  and then `X-Forwarded-For` is ignored. With N, the client is the Nth address from the right
  of `X-Forwarded-For`, as `ProxyFix(x_for=N)` reads it.

The rate limiter and the rebuild lock live in the process, so run a single worker.

Admin routes need `Authorization: Bearer <key>` (see `python -m prod.admins`). A missing, bad
or revoked key gets 401. Every error under `/api` is `{"error": "..."}`. An id too big for
SQLite gets 400, or 404 in a path.

- `GET /api/export`: the snapshot.
- `POST /api/publish` `{"changes", "note"}`: runs `apply_changes` as the key's admin. Bad input,
  including a `note` that isn't a string, gets 400 and writes nothing. An empty `changes`
  returns the current version without a backup. When anything applied, it rebuilds the
  knowledge bundle after the commit with
  `knowledge_base.from_db.write_from_db(PROD_DB, template, knowledge_dir=...)`. The response says
  `"rebuilt": true` or `false`. Without a template workbook the rebuild is skipped. A skipped or
  failed rebuild is logged and never fails the publish.
- `GET /api/requests?status=`, `POST /api/requests/<id>/claim` (409 names who holds it),
  `POST /api/requests/<id>/status` `{"status", "admin_note"?, "program_id"?}`. An unknown
  `program_id` gets 400.
- `POST /api/programs` `{"sheet", "university", "school"?, "program"?, "link"?, "request_id"?}`
  returns 201 `{"id"}`. With `request_id`, that request becomes done in the same transaction.

`/request` is public. Anyone can ask for a school to be added. Sheet names come from the
template workbook when it can be read. A body over 16 KB gets 413. A filled hidden `website`
field is accepted and dropped. Control and format characters (such as U+202E) become spaces,
and a `link` that doesn't start with `http://` or `https://` is stored empty. Each client gets 5
submissions an hour, counted in memory. IPv6 clients are counted by their /64, and IPv4-mapped
addresses (`::ffff:a.b.c.d`) by their IPv4 address. Stale entries are swept once more
than 10,000 addresses are tracked.
