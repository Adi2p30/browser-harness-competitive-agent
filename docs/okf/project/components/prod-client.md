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
environment or in the repo-root `.env`. `PROD_URL` must be https, except for `localhost` or
`127.0.0.1`. A key with whitespace or control characters is refused, and the key is never
printed.

```bash
python -m prod.client pull "../EDIT Competitive Analysis Research 2026-2027.xlsx"
python -m prod.client diff "../EDIT ….xlsx"
python -m prod.client publish "../EDIT ….xlsx" --note "fall run"
```

- `pull` saves the server's snapshot to `<workbook>_field_pipeline/prod-snapshot.json`.
- `diff` builds the local knowledge base the way `python -m knowledge_base` does and compares
  it with the saved snapshot. Lines read `+` for a new fact, `~` for a changed one and `-` for
  a fact marked not published. Local programs with no production match are listed after the
  diff, with the reason.
- `publish` pulls a fresh snapshot, shows the diff, and asks before posting. `--yes` skips the
  question; Enter, EOF or Ctrl-C at the question means no. It then saves a fresh snapshot. If
  that refresh fails, the publish still stands: run `pull`.

Only pipeline values (statuses in `FILLED`) and review decisions are published. A review
decision of "not-published" publishes that status with no value, as the importer writes it.
Values typed into the workbook and last year's values came in with the import and are never
published from here. A fact counts as changed when its value, status, cycle, source or quote
differs; a different run or reviewer alone does not.

Rejecting a value in review does not yet retract it once it is published. The diff only adds
and changes facts. The one removal it sends is a "not-published" decision, so use that one to
take a value down.

Programs match by `legacy_key` (`sheet:row`) when the university names also agree; a row
whose production program names another university is left unmatched. A program created with
`add-program` has no `legacy_key`. It matches the local row with the same sheet, university
and program name (case and spacing ignored), and only when exactly one active program fits.

A change applies only if the server still holds the value your snapshot showed. Otherwise it
is a conflict: the client prints both values, who changed the field and when, and exits 3.
Pull, check the field, and publish again.

School requests: `requests [--status new]`, `claim <id>`, `done <id> [--program ID] [--note]`
and `reject <id> [--note]`. `add-program --sheet S --university U [--school --program --link
--request ID]` creates a program and marks the request done. Add the school to the workbook
with the same sheet, university and program name, then pull before the next diff.

Exit codes: 0 ok, 1 error, 3 conflicts. The diff code is in `agent-workspace/prod/diff.py`.
