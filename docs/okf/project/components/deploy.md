---
type: Component
title: deploy
description: The hosting kit; waitress in one process behind an HTTPS reverse proxy, a systemd unit, nightly SQLite backups and the runbook.
resource: deploy/
generated: {by: claude-code/opus-5.5, at: '2026-11-24T00:00:00Z'}
status: draft
---

# deploy

`deploy/` hosts the [webapp](webapp.md) and its [prod API](prod-api.md) on any Linux server
with Python 3.11+, systemd and a reverse proxy doing HTTPS. `deploy/README.md` is the runbook.

- `serve.py` serves `webapp/app.py` with waitress on `PROD_HOST:PROD_PORT` (default
  `127.0.0.1:8000`), 4 threads in one process. It refuses to start without `PROD_DB` or with
  Flask debug on. It sets `APP_MODE=public` before importing the app. It keeps
  `X-Forwarded-For`, which waitress drops by default, so the API can apply
  `PROD_TRUSTED_PROXIES`.
- `prod-public.service` is the systemd unit template. It runs `serve.py` through
  `uv run --no-project`, so the checkout stays read-only. Only the database folder, the backup
  folder and `docs/okf/knowledge` are writable.
- `backup.sh` copies the live database to `$PROD_BACKUP_DIR/nightly/prod-YYYYmmdd.sqlite` with
  SQLite's online backup and keeps 30. It prints nothing on success.
- `nginx.conf.example` and `apache.conf.example` proxy HTTPS to port 8000 and set
  `X-Forwarded-For`. They cap request bodies at 64 KB, except `/api/publish`, which gets
  32 MB and 300 s.

The app must run as a single process because the API's rate limiter and rebuild lock live in
memory. Until the app honours `APP_MODE=public`, it still serves `/run`, `/health` and
`/review` without a login, so the hosted site must not be public yet.
