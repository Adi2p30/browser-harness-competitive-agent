---
type: Pipeline Stage
title: Retrieve
description: BM25-rank chunks per field group, so each LLM call sees only the most relevant text.
resource: agent-workspace/field_pipeline/retrieve.py
generated: {by: claude-code/sonnet-5, at: '2026-10-27T00:00:00Z'}
status: stable
---

# Retrieve

Top-`k` chunks per field group (`--chunks`, default 18), BM25 over the chunked pages from
[chunking](/okf/project/stages/chunking.md). Ranking-publisher pages are visible only to their own ranking group
(U.S. News profiles also to the class-stat groups): `fields.visible`, applied in `extract_group`.
