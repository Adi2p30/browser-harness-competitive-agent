---
title: Components
---

# Components

- [webapp](/okf/project/components/webapp.md) — Flask app; starts a run, streams its steps, browses the knowledge bundle
- [field_pipeline](/okf/project/components/field-pipeline.md) — fills the workbook; see [stages](/okf/project/stages/index.md)
- [prod-db](/okf/project/components/prod-db.md) — the production database: programs with permanent ids, sourced facts, history, publishes, requests, admin keys
  - [prod-importer](/okf/project/components/prod-importer.md) — one-time load of the workbook, runs, reviews and last year into the database
  - [prod-exporter](/okf/project/components/prod-exporter.md) — knowledge pages and the Excel workbook built from the database
  - [prod-api](/okf/project/components/prod-api.md) — the hosted server's admin-key API and the public school-request form
  - [prod-client](/okf/project/components/prod-client.md) — the admin's pull / diff / publish and request commands
- [knowledge_base](/okf/project/components/knowledge-base.md) — workbook + pipeline results → report + OKF bundle
- [browser-harness-core](/okf/project/components/browser-harness-core.md) — the CDP layer field_pipeline browses through (one table of modules, not a concept per module)
- [repository](/okf/project/components/repository.md) — the whole checkout's top-level layout, one table
