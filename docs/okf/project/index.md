---
title: Project
---

# Purdue Competitive Analysis

Fills the "Competitive Analysis Research" workbook — one row per competitor program — by
browsing each school's own pages, asking an LLM for values, and keeping only what it can
quote back from the page. See [`components/`](/okf/project/components/index.md) for what runs
it ([`repository.md`](/okf/project/components/repository.md) for the whole checkout's layout
in one table) and [`stages/`](/okf/project/stages/index.md) for how one row is filled.

## Run it

```bash
./run-webapp
```

Open the printed `http://127.0.0.1:5000`, add a program and/or school (dropdowns, plus
free-write for a school not in the workbook), and start a run — more than one can run at once.
Every step streams to the page live. See [`components/webapp.md`](/okf/project/components/webapp.md).
