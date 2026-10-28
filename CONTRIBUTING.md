# Contributing

Bug fixes, documentation changes, and helper improvements are welcome. Keep changes small and
focused — see `AGENTS.md`'s Contributing section.

## Development

From a checkout, use `./browser-harness` to run the current working tree without
activating a virtual environment or depending on the globally installed command:

```bash
./browser-harness <<'PY'
print(page_info())
PY
```

Agent-facing documentation should use `browser-harness`. The `./browser-harness`
launcher is only for testing a local checkout.

For the Purdue competitive-analysis tool built on top (`agent-workspace/field_pipeline`,
`knowledge_base`, `webapp/`), see `README.md` and `docs/okf/project/index.md`.
