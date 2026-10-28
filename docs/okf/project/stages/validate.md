---
type: Pipeline Stage
title: Validate
description: Code, not the model, checks the quote is really on the page, digits match, the value is in range, and the cycle is 2026-2027.
resource: agent-workspace/field_pipeline/validate.py
generated: {by: claude-code/sonnet-5, at: '2026-10-27T00:00:00Z'}
status: stable
---

# Validate

`agent-workspace/field_pipeline/validate.py`. A value that fails any check is never written —
the field stays at its prior status (`not-found`, `bad-quote`, etc.) instead.
