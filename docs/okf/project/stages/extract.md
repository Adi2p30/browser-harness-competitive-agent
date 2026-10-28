---
type: Pipeline Stage
title: Extract
description: One Purdue RCAC LLM call per field group, given only its retrieved chunks, returning a value and a verbatim quote.
resource: agent-workspace/field_pipeline/extract.py
generated: {by: claude-code/sonnet-5, at: '2026-10-27T00:00:00Z'}
status: stable
---

# Extract

`extract_group` (`agent-workspace/field_pipeline/extract.py`) calls the RCAC-hosted model
(`field_pipeline/llm.py`, `RCAC_MODEL` env var, default `qwen3.8:27b`) in parallel across field
groups (`--workers`). A second pass re-asks on the next-best chunks for fields still unresolved
unless `--no-second-pass`. Logger `field_pipeline.extract` (LLM I/O: `field_pipeline.llm`).
