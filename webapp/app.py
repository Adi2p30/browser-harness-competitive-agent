"""Flask app: start a field_pipeline run, watch every step live, browse the OKF knowledge
bundle. See docs/okf/project/components/webapp.md.

    uv run --with flask --with openpyxl --with beautifulsoup4 --with requests --with pypdf \\
        --with pyyaml python webapp/app.py
"""
import csv
import sys
import tempfile
import threading
import uuid
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_WORKSPACE = ROOT / "agent-workspace"
sys.path.insert(0, str(AGENT_WORKSPACE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import (Flask, Response, abort, redirect, render_template, request, send_file,  # noqa: E402
                   stream_with_context, url_for)

import mdlite  # noqa: E402
from runner import PHASE_LABELS, Run, start_health, start_run, stop_run  # noqa: E402
from field_pipeline import llm, review  # noqa: E402
from field_pipeline.store import DEFAULT_DB  # noqa: E402
from field_pipeline.workbook import read_rows, selection_notes  # noqa: E402
from knowledge_base.okf_format import read_concept  # noqa: E402

app = Flask(__name__)

KNOWLEDGE = ROOT / "docs" / "okf" / "knowledge"
_runs: dict[str, Run] = {}


def _xlsx() -> Path | None:
    """The workbook itself, not a copy next to it (the pipeline's "<workbook>_field_pipeline.xlsx", a "_filled"
    export): the shortest matching name."""
    return min(ROOT.glob("EDIT Competitive Analysis Research*.xlsx"), key=lambda p: (len(p.name), p.name), default=None)


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
    sheets_chosen = request.form.getlist("sheet")
    only = [s.strip() for s in request.form.getlist("only") if s.strip()]
    if only:  # the school list holds every sheet's schools: a school may not be on the programs picked
        match, notes = selection_notes(read_rows(str(xlsx))[1], sheets_chosen, only)
        if not match:
            sheets, universities = _workbook_options(xlsx)
            return render_template("index.html", sheets=sheets, universities=universities, xlsx=xlsx,
                                   form_error="Nothing to research: " + "; ".join(notes) + ".", running=_running(),
                                   runs=sorted(_runs.values(), key=lambda r: -r.started_at)), 400
    limit = request.form.get("limit", "").strip()
    run = start_run(
        xlsx, AGENT_WORKSPACE,
        sheets=sheets_chosen,
        only=only,
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


def _health_dirs(xlsx: Path | None) -> list[Path]:
    """Source health check folders (field_pipeline.health), newest first."""
    if not xlsx:
        return []
    base = xlsx.with_name(f"{xlsx.stem}_field_pipeline") / "health"
    return sorted((d for d in base.glob("*") if (d / "health.csv").exists()), reverse=True)


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@app.get("/health")
def health_index():
    xlsx = _xlsx()
    dirs = _health_dirs(xlsx)
    chosen = next((d for d in dirs if d.name == request.args.get("check")), dirs[0] if dirs else None)
    rows = _read_csv(chosen / "health.csv") if chosen else []
    order = {"changed": 0, "missing": 1, "dead": 2, "unreadable": 3}
    flagged = sorted((r for r in rows if r["result"] in order), key=lambda r: (order[r["result"]], r["university"]))
    return render_template("health.html", xlsx=xlsx, checks=dirs, chosen=chosen, rows=rows, flagged=flagged,
                           counts=Counter(r["result"] for r in rows), running=_running(),
                           recheck=len(_read_csv(chosen / "recheck.csv")) if chosen else 0,
                           rcac_warning=_rcac_key_missing(), runs=sorted(_runs.values(), key=lambda r: -r.started_at))


@app.post("/health/run")
def health_start():
    xlsx = _xlsx()
    if not xlsx:
        abort(400, "no 'EDIT Competitive Analysis Research*.xlsx' workbook found in the repo root")
    if _running():  # the check writes the shared page cache and health table: not alongside a pipeline run
        abort(409, "a run is going; start the source check when it finishes")
    run = start_health(xlsx, AGENT_WORKSPACE, browser=request.form.get("browser", "local"),
                       links="links" in request.form, stale_days=0 if "all" in request.form else None)
    _runs[run.id] = run
    return redirect(url_for("run_view", run_id=run.id))


@app.post("/health/recheck")
def health_recheck():
    xlsx = _xlsx()
    chosen = next((d for d in _health_dirs(xlsx) if d.name == request.form.get("check")), None)
    if not xlsx or not chosen or not _read_csv(chosen / "recheck.csv"):
        abort(400, "no flagged cells to research again")
    if warning := _rcac_key_missing():
        abort(400, warning)
    run = start_run(xlsx, AGENT_WORKSPACE, sheets=[], only=[], limit=None, overwrite=False, fresh=False,
                    browser=request.form.get("browser", "local"), db=_db_for_new_run(),
                    recheck=chosen / "recheck.csv")
    _runs[run.id] = run
    return redirect(url_for("run_view", run_id=run.id))


_queue_cache: dict = {"key": None, "by_run": {}}
_queue_lock = threading.Lock()


def _queue(xlsx: Path, run: str | None) -> dict:
    """The review queue for one run (None = every run), rebuilt only when its inputs change (runs, the workbook,
    reviews, health checks)."""
    base = xlsx.with_name(f"{xlsx.stem}_field_pipeline")
    inputs = [xlsx, review.reviews_path(xlsx), DEFAULT_DB, *sorted(base.glob("runs/*/decisions.csv"))]
    key = tuple((str(p), p.stat().st_mtime if p.exists() else 0) for p in inputs)
    with _queue_lock:
        if _queue_cache["key"] != key:
            _queue_cache.update(key=key, by_run={})
        if run not in _queue_cache["by_run"]:
            _queue_cache["by_run"][run] = review.queue(xlsx, run=run)
        return _queue_cache["by_run"][run]


def _run_choice(xlsx: Path) -> tuple[str, list[dict]]:
    """("all" or a run id, the runs newest first); the newest run when none is asked for."""
    runs = review.list_runs(xlsx)
    src = request.form if request.method == "POST" else request.args
    asked = src.get("run", "")
    if asked == "all" or asked in {r["id"] for r in runs}:
        return asked, runs
    return (runs[0]["id"] if runs else "all"), runs


def _scope(choice: str) -> str | None:
    return None if choice == "all" else choice


def _review_args() -> dict:
    """The filters to come back to after a decision."""
    src = request.form if request.method == "POST" else request.args
    return {k: src.get(k) for k in ("run", "group", "sheet", "q") if src.get(k)}


@app.get("/review")
def review_index():
    xlsx = _xlsx()
    if not xlsx:
        return render_template("review.html", xlsx=None)
    run, runs = _run_choice(xlsx)
    result = _queue(xlsx, _scope(run))
    items = result["items"]
    sheet, q = request.args.get("sheet", ""), request.args.get("q", "").strip().casefold()
    shown = [i for i in items if (not sheet or i["sheet"] == sheet) and (not q or q in i["university"].casefold())]
    counts = Counter(i["group"] for i in shown)
    reviewed = sorted((r for key, r in review.load_reviews(xlsx).items() if run == "all" or key in result["cells"]),
                      key=lambda r: r["at"], reverse=True)
    group = request.args.get("group") or next((g for g in review.GROUPS if counts[g]), "candidate")
    return render_template("review.html", xlsx=xlsx, groups=review.GROUPS, choices=review.CHOICES, group=group,
                           counts=counts, items=[i for i in shown if i["group"] == group], total=len(shown),
                           sheets=sorted({i["sheet"] for i in items}), sheet=sheet, q=request.args.get("q", ""),
                           reviewed=reviewed, reviewer=request.cookies.get("reviewer", ""), run=run, runs=runs,
                           filled_elsewhere=result["filled_elsewhere"],
                           msg=request.args.get("msg", ""), error=request.args.get("error", ""))


@app.post("/review/decide")
def review_decide():
    xlsx = _xlsx()
    item = (next((i for i in _queue(xlsx, _scope(_run_choice(xlsx)[0]))["items"] if i["id"] == request.form.get("id")),
                 None) if xlsx else None)
    if item is None:
        return redirect(url_for("review_index", **_review_args(), error="That cell is no longer in the queue."))
    try:
        row = review.make_review(item, request.form.get("decision", ""), request.form.get("value", ""),
                                 request.form.get("source_url", ""), request.form.get("note", ""),
                                 request.cookies.get("reviewer", ""))
    except ValueError as exc:
        return redirect(url_for("review_index", **_review_args(), error=f"{item['university']} / {item['field']}: {exc}")
                        + f"#{item['id']}")
    review.append_reviews(xlsx, [row])
    return redirect(url_for("review_index", **_review_args(),
                            msg=f"Saved {item['field']} for {item['university']}: {row['decision']}"))


@app.post("/review/undo")
def review_undo():
    xlsx = _xlsx()
    done = review.load_reviews(xlsx) if xlsx else {}
    sheet, row, field = (request.form.get("id") or "||").split("|", 2)
    rv = done.get((sheet, int(row or 0), field))
    if rv:
        review.append_reviews(xlsx, [review.undo_review(rv, request.cookies.get("reviewer", ""))])
    return redirect(url_for("review_index", **_review_args(), group="reviewed",
                            msg=f"{field} for {rv['university']} is back in the queue" if rv else ""))


@app.post("/review/name")
def review_name():
    resp = redirect(url_for("review_index", **_review_args()))
    resp.set_cookie("reviewer", request.form.get("reviewer", "").strip()[:60], max_age=365 * 86400, samesite="Lax")
    return resp


@app.get("/review/export")
def review_export():
    xlsx = _xlsx()
    if not xlsx:
        abort(400, "no workbook")
    run = _run_choice(xlsx)[0]
    name = "review_queue.xlsx" if run == "all" else f"review_queue_{run}.xlsx"
    dest = xlsx.with_name(f"{xlsx.stem}_field_pipeline") / name
    review.export_xlsx(_queue(xlsx, _scope(run))["items"], dest)
    return send_file(dest, as_attachment=True, download_name=name)


@app.post("/review/import")
def review_import():
    xlsx = _xlsx()
    upload = request.files.get("file")
    if not xlsx or not upload or not upload.filename:
        return redirect(url_for("review_index", **_review_args(), error="Choose the filled-in review_queue.xlsx first."))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "import.xlsx"
        upload.save(path)
        try:
            rows, problems = review.import_xlsx(path, _queue(xlsx, _scope(_run_choice(xlsx)[0]))["items"],
                                                request.cookies.get("reviewer", ""))
        except Exception as exc:  # not an export of the queue
            return redirect(url_for("review_index", **_review_args(), error=f"Could not read that file: {exc}"))
    review.append_reviews(xlsx, rows)
    note = f"Imported {len(rows)} decision(s)." + (f" Skipped {len(problems)}: " + "; ".join(problems[:5])
                                                     if problems else "")
    return redirect(url_for("review_index", **_review_args(), msg=note))


@app.get("/knowledge")
def knowledge_index():
    if not KNOWLEDGE.exists():
        return render_template("knowledge.html", built=False)
    frontmatter, body = read_concept(KNOWLEDGE / "index.md")
    title = frontmatter.get("title", "Knowledge")
    return render_template("knowledge.html", built=True, title=title,
                           body_html=mdlite.render(mdlite.drop_title(body, title)))


@app.get("/knowledge/<path:rel>")
def knowledge_page(rel):
    path = (KNOWLEDGE / rel).resolve()
    if KNOWLEDGE not in path.parents or not path.exists():
        abort(404)
    frontmatter, body = read_concept(path)
    title = frontmatter.get("title", rel)
    return render_template("knowledge_concept.html", frontmatter=frontmatter,
                           body_html=mdlite.render(mdlite.drop_title(body, title)), title=title,
                           updated=_day(frontmatter.get("generated", {}).get("at")),
                           stale_after=_day(frontmatter.get("stale_after")))


def _day(value) -> str:
    """'2026-10-30T01:44:03Z' -> '2026-10-30' (frontmatter dates, for display)."""
    return str(value)[:10] if value else ""


if __name__ == "__main__":
    xlsx = _xlsx()
    print(f"workbook: {xlsx or '(none found in repo root)'}")
    app.run(debug=True, port=5000)
