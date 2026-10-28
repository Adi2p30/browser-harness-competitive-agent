---
type: Pipeline Stage
title: Discover
description: Find a program's start page, its own links, its sitemap, and (via the browser) a Bing search — no LLM.
resource: agent-workspace/field_pipeline/discover.py
generated: {by: claude-code/sonnet-5, at: '2026-10-27T00:00:00Z'}
status: stable
---

# Discover

`Discoverer.find_start` (no stored Website Link) then `Discoverer.run`: candidate pages per
field group, ranked by link text and URL shape. Logger `field_pipeline.discover`.
