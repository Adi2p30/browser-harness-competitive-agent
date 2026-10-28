---
type: Pipeline Stage
title: IPEDS
description: Federal completions data fills Average Conferrals and a labelled women/international graduate-share proxy for cells still blank. No browser, no LLM.
resource: agent-workspace/field_pipeline/ipeds.py
generated: {by: claude-code/sonnet-5, at: '2026-10-27T00:00:00Z'}
status: stable
---

# IPEDS

`apply_ipeds` (`agent-workspace/field_pipeline/__main__.py`), backed by `Ipeds`
(`agent-workspace/field_pipeline/ipeds.py`). Runs after [extract](/okf/project/stages/extract.md)
and [validate](/okf/project/stages/validate.md), only for fields those left blank. Logger
`field_pipeline.ipeds`.
