"""Flask app: start a field_pipeline run, watch every step live, browse the OKF knowledge
bundle. See docs/okf/project/components/webapp.md.

    uv run --with flask --with openpyxl --with beautifulsoup4 --with requests --with pypdf \\
        --with pyyaml python webapp/app.py
"""
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_WORKSPACE = ROOT / "agent-workspace"
sys.path.insert(0, str(AGENT_WORKSPACE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, Response, abort, redirect, render_template, request, stream_with_context, url_for  # noqa: E402

import mdlite  # noqa: E402
from runner import PHASE_LABELS, Run, start_run, stop_run  # noqa: E402
from field_pipeline import llm  # noqa: E402
from field_pipeline.workbook import read_rows  # noqa: E402
from knowledge_base.okf_format import read_concept  # noqa: E402

app = Flask(__name__)

KNOWLEDGE = ROOT / "docs" / "okf" / "knowledge"
_runs: dict[str, Run] = {}


def _xlsx() -> Path | None:
    return next(ROOT.glob("EDIT Competitive Analysis Research*.xlsx"), None)


def _running() -> list[Run]:
    return [r for r in _runs.values() if r.status == "running"]


def _db_for_new_run() -> Path | None:
    """field_pipeline's default SQLite cache isn't safe for two processes writing at once.
    One run at a time still gets it (so reruns keep reusing cached pages/results, as before);
    any run started while another is already going gets its own isolated cache instead."""
    if _running():
        return AGENT_WORKSPACE / "field_pipeline" / "data" / f"pipeline-{uuid.uuid4().hex[:8]}.sqlite"
    return None


def _workbook_options(xlsx: Path | None) -> tuple[list[str], list[str]]:
    """(sheet names, distinct university names) for the run form's dropdowns."""
    if not xlsx:
        return [], []
    cols, rows = read_rows(str(xlsx))
    return sorted(cols), sorted({r.university for r in rows if r.university})


def _rcac_key_missing() -> str | None:
    """field_pipeline's own fail-fast check (agent-workspace/field_pipeline/llm.py:api_key),
    run before launching so a missing key shows on the form instead of inside a failed run."""
    try:
        llm.api_key()
        return None
    except RuntimeError as exc:
        return str(exc)


@app.get("/")
def index():
    xlsx = _xlsx()
    sheets, universities = _workbook_options(xlsx)
    return render_template("index.html", sheets=sheets, universities=universities, xlsx=xlsx,
                           rcac_warning=_rcac_key_missing(), running=_running(),
                           runs=sorted(_runs.values(), key=lambda r: -r.started_at))


@app.post("/run")
def run_start():
    xlsx = _xlsx()
    if not xlsx:
        abort(400, "no 'EDIT Competitive Analysis Research*.xlsx' workbook found in the repo root")
    if warning := _rcac_key_missing():
        sheets, universities = _workbook_options(xlsx)
        return render_template("index.html", sheets=sheets, universities=universities, xlsx=xlsx,
                               rcac_warning=warning, running=_running(),
                               runs=sorted(_runs.values(), key=lambda r: -r.started_at))
    limit = request.form.get("limit", "").strip()
    run = start_run(
        xlsx, AGENT_WORKSPACE,
        sheets=request.form.getlist("sheet"),
        only=[s.strip() for s in request.form.getlist("only") if s.strip()],
        limit=int(limit) if limit else None,
        overwrite="overwrite" in request.form,
        fresh="fresh" in request.form,
        browser=request.form.get("browser", "local"),
        db=_db_for_new_run(),
    )
    _runs[run.id] = run
    return redirect(url_for("run_view", run_id=run.id))


@app.get("/run/<run_id>")
def run_view(run_id):
    run = _runs.get(run_id)
    if not run:
        abort(404)
    return render_template("run.html", run=run, phase_labels=PHASE_LABELS,
                           decisions=run.decisions() if run.status != "running" else [])


@app.get("/run/<run_id>/events")
def run_events(run_id):
    run = _runs.get(run_id)
    if not run:
        abort(404)
    last_seq = int(request.headers.get("Last-Event-ID") or request.args.get("last") or 0)
    return Response(stream_with_context(run.stream(last_seq)), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/run/<run_id>/stop")
def run_stop(run_id):
    run = _runs.get(run_id)
    if not run:
        abort(404)
    stop_run(run)
    return redirect(url_for("run_view", run_id=run_id))


@app.get("/knowledge")
def knowledge_index():
    if not KNOWLEDGE.exists():
        return render_template("knowledge.html", built=False)
    frontmatter, body = read_concept(KNOWLEDGE / "index.md")
    return render_template("knowledge.html", built=True, title=frontmatter.get("title", "Knowledge"),
                           body_html=mdlite.render(body))


@app.get("/knowledge/<path:rel>")
def knowledge_page(rel):
    path = (KNOWLEDGE / rel).resolve()
    if KNOWLEDGE not in path.parents or not path.exists():
        abort(404)
    frontmatter, body = read_concept(path)
    return render_template("knowledge_concept.html", frontmatter=frontmatter,
                           body_html=mdlite.render(body), title=frontmatter.get("title", rel))


if __name__ == "__main__":
    xlsx = _xlsx()
    print(f"workbook: {xlsx or '(none found in repo root)'}")
    app.run(debug=True, port=5000)
