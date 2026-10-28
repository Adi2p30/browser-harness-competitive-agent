---
type: Component
title: webapp
description: Flask app that starts a field_pipeline run as a subprocess, streams its log lines live over SSE, and browses the OKF knowledge bundle.
resource: webapp/app.py
generated: {by: claude-code/sonnet-5, at: '2026-10-27T00:00:00Z'}
status: stable
---

# webapp

`uv run --with flask --with openpyxl --with beautifulsoup4 --with requests --with pypdf --with pyyaml python webapp/app.py`

- `/` — pick sheet(s) and school(s) from dropdowns populated from the workbook (each adds a
  removable chip; schools also take free-written names not in the workbook), row limit,
  overwrite/fresh, browser mode; starts a run. Warns, without starting a run, if
  `RCAC_API_KEY` isn't set (`field_pipeline.llm.api_key`, checked before launching).
- `/run/<id>` — the live step view: one card per row (`field_pipeline`'s own
  `=== [i/n] sheet | university | program` log line), each line tagged by stage from its
  Python logger name (`field_pipeline.discover` → Discover, `.fetch`/`.search` → Fetch,
  `.extract`/`.llm` → Extract, `.ipeds` → IPEDS). Streamed from `webapp/runner.py`, which
  launches `python -m field_pipeline` as a subprocess (`cwd=agent-workspace`) and tails its
  log; stopping a run sends it `SIGINT`, the same signal `field_pipeline.__main__.main()`
  already catches to save partial results before exiting.
- `/knowledge` — renders `docs/okf/knowledge/` (built by `knowledge_base`'s OKF export) as
  cards: one per program × field group, value/status/cycle, with a link to its source quote.

One run at a time: `field_pipeline` owns one browser session and one SQLite store per process.
