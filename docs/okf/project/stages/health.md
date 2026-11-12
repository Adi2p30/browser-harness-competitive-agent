---
type: Pipeline Stage
title: Health
description: Monthly check that every stored quote is still on its source page; flags changed, missing and dead sources for re-research. No LLM.
resource: agent-workspace/field_pipeline/health.py
generated: {by: claude-code/opus-5.5, at: '2026-11-06T00:00:00Z'}
status: draft
---

# Health

`python -m field_pipeline.health <workbook>` takes the knowledge base's facts that have a source URL
(`knowledge_base.build`). It fetches each URL once with the pipeline's `Fetcher` and judges every fact on it as
`ok`, `moved`, `changed`, `missing`, `dead`, `unreadable` or `live`. Quote matching is `validate.quote_on_page`.
A `changed` result means a near copy is on the page (word-level similarity ≥ 0.8). Checks are stored in the
`health` table of `pipeline.sqlite` and read by `knowledge_base.attach_health`. `recheck.csv` feeds
`python -m field_pipeline --recheck`. `--install-schedule` installs a monthly launchd job. Logger
`field_pipeline.health`.
