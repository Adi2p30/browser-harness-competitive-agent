---
type: Pipeline Stage
title: Fetch
description: Open each candidate page through browser-harness (accordions/tabs expanded) or plain HTTP; read PDFs; cache pages in SQLite.
resource: agent-workspace/field_pipeline/fetch.py
generated: {by: claude-code/sonnet-5, at: '2026-10-27T00:00:00Z'}
status: stable
---

# Fetch

`Fetcher` wraps `browser_harness.admin`/`.helpers` (local Chrome, Browser Use Cloud, or plain
HTTP per `--browser`). Pages are cached in `field_pipeline/data/pipeline.sqlite`, so reruns
reuse them unless `--fresh`. Logger `field_pipeline.fetch` (Bing search: `field_pipeline.search`).
