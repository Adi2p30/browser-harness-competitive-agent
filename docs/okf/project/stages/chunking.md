---
type: Pipeline Stage
title: Chunking
description: Split fetched pages into roughly 1,500-character chunks for retrieval (the pipeline docstring's "index" step).
resource: agent-workspace/field_pipeline/retrieve.py
generated: {by: claude-code/sonnet-5, at: '2026-10-27T00:00:00Z'}
status: stable
---

# Chunking

`chunk_pages` (`agent-workspace/field_pipeline/retrieve.py`). Named "chunking" here, not
`index`, because `index.md` is a reserved OKF filename.
