---
type: Component
title: prod.client
description: The admin's command-line client for the production server; pull a snapshot, diff local research against it, publish, and handle school requests.
resource: agent-workspace/prod/client.py
generated: {by: claude-code/opus-5.5, at: '2026-11-24T00:00:00Z'}
status: draft
---

# prod.client

Run it from `agent-workspace/`. It needs `PROD_URL` and `PROD_ADMIN_KEY`, set in the
environment or in the repo-root `.env`. It never prints the key.

```bash
python -m prod.client pull "../EDIT Competitive Analysis Research 2026-2027.xlsx"
python -m prod.client diff "../EDIT ….xlsx"
python -m prod.client publish "../EDIT ….xlsx" --note "fall run"
```

- `pull` saves the server's snapshot to `<workbook>_field_pipeline/prod-snapshot.json`.
- `diff` builds the local knowledge base the way `python -m knowledge_base` does and compares
  it with the saved snapshot. Lines read `+` for a new fact, `~` for a changed one and `-` for
  a fact marked not published. Programs with no production match are listed after the diff.
- `publish` pulls a fresh snapshot, shows the diff, and asks before posting (`--yes` skips the
  question). It then saves the snapshot again.

Only pipeline values (statuses in `FILLED`) and review decisions are published. A review
decision of "not-published" publishes that status with no value. Values typed into the
workbook and last year's values came in with the import and are never published from here.
Programs match by `legacy_key` (`sheet:row`).

A change applies only if the server still holds the value your snapshot showed. Otherwise it
is a conflict: the client prints both values, who changed the field and when, and exits 2.
Pull, check the field, and publish again.

School requests: `requests [--status new]`, `claim <id>`, `done <id> [--program ID] [--note]`
and `reject <id> [--note]`. `add-program --sheet S --university U [--school --program --link
--request ID]` creates a program and marks the request done. Pull before the next diff.

Exit codes: 0 ok, 1 error, 2 conflicts. The diff code is in `agent-workspace/prod/diff.py`.
