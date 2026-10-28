---
type: Component
title: Repository layout
description: Top-level layout of this checkout, as one table — the rest of docs/okf/project/ goes one level deeper on the pieces that make up the Purdue tool.
resource: .
generated: {by: claude-code/sonnet-5, at: '2026-10-28T00:00:00Z'}
status: stable
---

# Repository layout

| Path | What it is |
|---|---|
| `README.md` | entry point: what this tool does, how to run it |
| `run-webapp` | launches [webapp](/okf/project/components/webapp.md) via `uv` |
| `webapp/` | the Flask app — see [webapp](/okf/project/components/webapp.md) |
| `agent-workspace/field_pipeline/` | fills the workbook — see [field_pipeline](/okf/project/components/field-pipeline.md), [stages](/okf/project/stages/index.md) |
| `agent-workspace/knowledge_base/` | report + OKF export — see [knowledge_base](/okf/project/components/knowledge-base.md) |
| `agent-workspace/agent_helpers.py`, `domain-skills/` | generic browser-harness agent workspace (unused by this tool so far) |
| `src/browser_harness/` | the vendored harness core — see [browser-harness-core](/okf/project/components/browser-harness-core.md) (one table there, not per-module concepts) |
| `AGENTS.md` | map of `src/browser_harness/` for agents working on the harness core |
| `CLAUDE.md` | points Claude Code at `AGENTS.md` |
| `SKILL.md`, `install.md` | the harness core's own usage/setup docs |
| `CONTRIBUTING.md`, `LICENSE` | dev workflow notes; MIT |
| `pyproject.toml` | packaging/dependencies for the harness core (`browser-harness` package) |
| `.env.example` / `.env` | `RCAC_API_KEY`, `BROWSER_USE_API_KEY` (`.env` is gitignored) |
| `EDIT Competitive Analysis Research *.xlsx` | the workbook field_pipeline fills |
| `OLD *.xlsx` | last year's workbook, carried forward where this year's is still blank |
| `docs/okf/` | this OKF bundle — [project](/okf/project/index.md) (hand-written) + [knowledge](/okf/knowledge/index.md) (generated) |
| `tests/`, `webapp/tests/` | unit tests (`uv run --with pytest python -m pytest tests/unit webapp/tests -q`) |
