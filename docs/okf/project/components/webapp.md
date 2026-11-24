---
type: Component
title: webapp
description: Flask app in two modes; admin (local) runs and reviews the research and publishes it, public (hosted) serves the knowledge pages, the request form, the API and the workbook download.
resource: webapp/app.py
generated: {by: claude-code/opus-5.5, at: '2026-11-24T00:00:00Z'}
status: stable
---

# webapp

`./run-webapp.sh` (or `uv run --with flask --with openpyxl --with beautifulsoup4 --with requests --with pypdf
--with pyyaml python webapp/app.py`) starts the admin app on `127.0.0.1:5000`, never with debug on.

`app.py` builds the app with `create_app(mode)`, reading `APP_MODE` once at import (`app = create_app(...)`). Each
mode registers only its own blueprints, so a route of the other mode doesn't exist (404).

- **public** (hosted, set by `deploy/serve.py`): `/` redirects to `/knowledge`; the knowledge pages;
  `/request` and `/api/*` ([prod API](prod-api.md)); `/download.xlsx`, the production database (`PROD_DB`)
  exported into the template workbook (`PROD_TEMPLATE`) by `prod.exporter`. The export is built in a temp folder and
  kept in memory until the database version or the template changes, so nothing is written in the repo. 503 when
  either is missing. No `/run`, `/health`, `/review` or `/admin` route is registered.
- **admin** (the default): everything below, plus `/request` and `/api/*`. It answers only to a `Host` of
  `127.0.0.1`, `localhost` or `::1` (against DNS rebinding). Every POST outside `/api` and `/request` needs the
  session's CSRF token (a hidden `csrf_token` field or an `X-CSRF-Token` header; the session cookie is signed with
  a key made at startup, so restarting means reloading open pages), and an `Origin` or `Referer` naming this host
  when the browser sends one. Otherwise 403.

The nav lists only the mode's pages. The knowledge pages read `KNOWLEDGE_DIR` (the folder the API rebuilds after a
publish), default `<repo>/docs/okf/knowledge`; the app refuses to start when it doesn't end in `docs/okf/knowledge`.

Admin pages:

- `/` — pick sheet(s) and school(s) from dropdowns populated from the programs a run would
  research (production's, once a snapshot is pulled, else the workbook's rows; each adds a
  removable chip; a typed school name only matches those university names, e.g.
  "carnegie" — new programs are added on `/admin/programs/new` or with `prod.client add-program`), row limit,
  overwrite/fresh, browser mode; starts a run. Warns, without starting a run, if
  `RCAC_API_KEY` isn't set (`field_pipeline.llm.api_key`, checked before launching).
- `/run/<id>` — the live step view: one card per row (`field_pipeline`'s own
  `=== [i/n] sheet | university | program` log line), each line tagged by stage from its
  Python logger name (`field_pipeline.discover` → Discover, `.fetch`/`.search` → Fetch,
  `.extract`/`.llm` → Extract, `.ipeds` → IPEDS). Streamed from `webapp/runner.py`, which
  launches `python -m field_pipeline` as a subprocess (`cwd=agent-workspace`) and tails its
  log; stopping a run sends it `SIGINT`, the same signal `field_pipeline.__main__.main()`
  already catches to save partial results before exiting.
- `/review` — the review queue (`field_pipeline.review`) for one run at a time (`?run=`, newest by default, or
  `all`): every unfilled or stale cell of that run, grouped by what a
  person needs to do, with one-click decisions (accept the rejected candidate, Not ranked, No, leave blank) or
  a typed value and source. Decisions append to `<workbook>_field_pipeline/reviews.csv`; a Reviewed tab undoes
  them; the queue also round-trips through an Excel export/import. A cell's id is `<program id>|<field>`
  (`<sheet>:<row>|<field>` before a snapshot is pulled). The reviewer's name is a cookie.
- `/health` — the latest source health check (`field_pipeline.health`): counts per result, a table of
  changed / missing / dead / unreadable values with the stored quote and what the page says now. Buttons
  start a check (blocked while a run is going: both write the page cache) or a `--recheck` pipeline run on
  the flagged cells. Health log lines show under the "Source check" stage.
- `/knowledge` — renders the knowledge bundle (built by `knowledge_base`'s OKF export, or rebuilt from the
  database after a publish) as cards: one per program × field group, value/status/cycle, with a link to its
  source quote.
- `/admin/requests`, `/admin/publish`, `/admin/programs/new`, `/admin/pull` — the production server's pages
  (`webapp/prod_pages.py`), described in [prod.client](prod-client.md).

One run at a time: `field_pipeline` owns one browser session and one SQLite store per process.
