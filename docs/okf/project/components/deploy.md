---
type: Component
title: deploy
description: The hosting kit; waitress in one process behind an HTTPS reverse proxy, a systemd unit, pinned packages, nightly SQLite backups and the runbook.
resource: deploy/
generated: {by: claude-code/opus-5.5, at: '2026-11-24T00:00:00Z'}
status: draft
---

# deploy

`deploy/` hosts the [webapp](webapp.md) and its [prod API](prod-api.md) on any Linux server
with Python 3.11+, systemd and a reverse proxy doing HTTPS. `deploy/README.md` is the runbook.

- `serve.py` serves `webapp/app.py` with waitress on `PROD_HOST:PROD_PORT` (default
  `127.0.0.1:8000`), 4 threads in one process. It sets `APP_MODE=public` before importing the
  app. It refuses to start in four cases: `PROD_DB` is unset, Flask debug is on,
  `PROD_TRUSTED_PROXIES` is set with a non-loopback host, or the app still has a `/run`,
  `/health` or `/review` route. It keeps `X-Forwarded-For`, which waitress drops by default, so
  the API can apply `PROD_TRUSTED_PROXIES`.
- `requirements.in` lists the packages; `requirements.txt` pins them with hashes
  (`uv pip compile --generate-hashes`). The deploy user installs them into a venv with
  `uv pip sync --require-hashes`, which refuses a download whose hash doesn't match.
- `prod-public.service` is the systemd unit template. It runs `serve.py` with the venv's
  Python. The venv and the checkout are read-only to the service. Only the database folder, the
  backup folder and `docs/okf/knowledge` are writable.
- `backup.sh` copies the live database to `$PROD_BACKUP_DIR/nightly/prod-YYYYmmdd.sqlite` with
  SQLite's online backup and keeps 30. It prints nothing on success.
- `nginx.conf.example` and `apache.conf.example` proxy HTTPS to port 8000. They forward only
  `/api/`, `/request`, `/knowledge` and `/static/`, and set `X-Forwarded-For`. Request bodies
  are capped at 64 KB, except `/api/publish`, which gets 32 MB and 300 s.

The app must run as a single process because the API's rate limiter and rebuild lock live in
memory. Until the app honours `APP_MODE=public` (Step 8), `serve.py` refuses to start.
