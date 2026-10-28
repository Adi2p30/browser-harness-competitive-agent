# Purdue Competitive Analysis

Fills Purdue's competitive-analysis workbook — one row per competitor program — by browsing
each school's own pages, asking an LLM for values, and keeping only what it can quote back
from the page. A Flask web app starts a run and shows every step live; a knowledge-base export
turns the results into a comparison report and an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
bundle.

See [`docs/okf/project/index.md`](docs/okf/project/index.md) for how the pieces fit together.

## Run the web app

```bash
./run-webapp
```

Open the printed `http://127.0.0.1:5000`. Pick a sheet (and optionally a school filter, row
limit, or browser mode), start a run, and watch it discover, fetch, extract, and validate each
field live. **A local-mode run drives your real Chrome** (`--browser local`, the default) —
`./browser-harness --doctor` first if it can't attach.

## Build the knowledge base (report + OKF bundle)

```bash
cd agent-workspace
uv run --with openpyxl --with beautifulsoup4 --with requests --with pypdf --with pyyaml \
  python -m knowledge_base "../EDIT Competitive Analysis Research 2026-2027.xlsx"
```

Writes `<workbook>_kb/competitive_analysis.html` (open in any browser) and
`docs/okf/knowledge/` (browse it from the web app's **Knowledge** page, or any OKF-aware tool).

## Run field_pipeline directly (CLI)

See [`agent-workspace/field_pipeline/README.md`](agent-workspace/field_pipeline/README.md) for
every flag; the web app's run form covers the common ones.

## The underlying browser harness

`field_pipeline` drives the browser through the vendored, general-purpose
[browser-harness](https://github.com/browser-use/browser-harness) project — kept intact rather
than reimplemented. `install.md` connects it to your browser; `SKILL.md` documents its helpers;
`AGENTS.md` is the map of `src/browser_harness/`. `./browser-harness --doctor` diagnoses
install/daemon/browser state; `./browser-harness --reload` restarts the daemon after a code
change.

## Tests

```bash
uv run --with pytest python -m pytest tests/unit -q
uv run --with pytest --with pyyaml python -m pytest webapp/tests -q
```

(`--with openpyxl --with beautifulsoup4 --with pyyaml` additionally enables the
`field_pipeline`/`knowledge_base`/OKF tests, which otherwise skip.)
