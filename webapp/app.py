"""The Flask app, in one of two modes picked by APP_MODE when the app is created (create_app). See
docs/okf/project/components/webapp.md.

- admin (the default; local, 127.0.0.1 only): start a field_pipeline run and watch it live, the review queue, source
  health, the knowledge pages, and the production pages in prod_pages.py. Every form carries a CSRF token.
- public (hosted; deploy/serve.py sets it): the knowledge pages, the request form and admin-key API (api.py), and
  /download.xlsx. No run, review, health or /admin route exists.

    uv run --with flask --with openpyxl --with beautifulsoup4 --with requests --with pypdf \\
        --with pyyaml python webapp/app.py
"""
import csv
import hmac
import io
import logging
import os
import secrets
import sqlite3
import sys
import tempfile
import threading
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
AGENT_WORKSPACE = ROOT / "agent-workspace"
sys.path.insert(0, str(AGENT_WORKSPACE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import (Blueprint, Flask, Response, abort, current_app, redirect, render_template, request,  # noqa: E402
                   send_file, session, stream_with_context, url_for)
from markupsafe import Markup  # noqa: E402

import api  # noqa: E402
import mdlite  # noqa: E402
from runner import PHASE_LABELS, Run, start_health, start_run, stop_run  # noqa: E402
from field_pipeline import llm, review  # noqa: E402
from field_pipeline.store import DEFAULT_DB  # noqa: E402
from field_pipeline.workbook import load_snapshot, read_rows, selection_notes, snapshot_path  # noqa: E402
from knowledge_base.okf_format import read_concept  # noqa: E402

log = logging.getLogger(__name__)
KNOWLEDGE = ROOT / "docs" / "okf" / "knowledge"
MODES = ("admin", "public")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
NAV = {"admin": [("admin.index", "Run"), ("admin.review_index", "Review"), ("admin.health_index", "Source health"),
                 ("prod.requests_inbox", "Requests"), ("prod.publish", "Publish"), ("knowledge.index", "Knowledge")],
       "public": [("knowledge.index", "Knowledge"), ("api.request_form", "Request a school"),
                  ("site.download", "Download the workbook")]}
admin = Blueprint("admin", __name__)  # run, review, health: admin mode only
knowledge = Blueprint("knowledge", __name__)  # both modes
site = Blueprint("site", __name__)  # public mode only
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
    """(sheet names, distinct university names) for the run form's dropdowns: production's programs once a
    snapshot is pulled, else the workbook's rows (as the pipeline picks them)."""
    if not xlsx:
        return [], []
    cols, rows = read_rows(str(xlsx), snapshot=load_snapshot(xlsx))
    return sorted(cols), sorted({r.university for r in rows if r.university})


def _rcac_key_missing() -> str | None:
    """field_pipeline's own fail-fast check (agent-workspace/field_pipeline/llm.py:api_key),
    run before launching so a missing key shows on the form instead of inside a failed run."""
    try:
        llm.api_key()
        return None
    except RuntimeError as exc:
        return str(exc)


@admin.get("/")
def index():
    xlsx = _xlsx()
    sheets, universities = _workbook_options(xlsx)
    return render_template("index.html", sheets=sheets, universities=universities, xlsx=xlsx,
                           rcac_warning=_rcac_key_missing(), running=_running(),
                           runs=sorted(_runs.values(), key=lambda r: -r.started_at))


@admin.post("/run")
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
        match, notes = selection_notes(read_rows(str(xlsx), snapshot=load_snapshot(xlsx))[1], sheets_chosen, only)
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
    return redirect(url_for("admin.run_view", run_id=run.id))


@admin.get("/run/<run_id>")
def run_view(run_id):
    run = _runs.get(run_id)
    if not run:
        abort(404)
    return render_template("run.html", run=run, phase_labels=PHASE_LABELS,
                           decisions=run.decisions() if run.status != "running" else [])


@admin.get("/run/<run_id>/events")
def run_events(run_id):
    run = _runs.get(run_id)
    if not run:
        abort(404)
    last_seq = int(request.headers.get("Last-Event-ID") or request.args.get("last") or 0)
    return Response(stream_with_context(run.stream(last_seq)), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@admin.post("/run/<run_id>/stop")
def run_stop(run_id):
    run = _runs.get(run_id)
    if not run:
        abort(404)
    stop_run(run)
    return redirect(url_for("admin.run_view", run_id=run_id))


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


@admin.get("/health")
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


@admin.post("/health/run")
def health_start():
    xlsx = _xlsx()
    if not xlsx:
        abort(400, "no 'EDIT Competitive Analysis Research*.xlsx' workbook found in the repo root")
    if _running():  # the check writes the shared page cache and health table: not alongside a pipeline run
        abort(409, "a run is going; start the source check when it finishes")
    run = start_health(xlsx, AGENT_WORKSPACE, browser=request.form.get("browser", "local"),
                       links="links" in request.form, stale_days=0 if "all" in request.form else None)
    _runs[run.id] = run
    return redirect(url_for("admin.run_view", run_id=run.id))


@admin.post("/health/recheck")
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
    return redirect(url_for("admin.run_view", run_id=run.id))


_queue_cache: dict = {"key": None, "by_run": {}}
_queue_lock = threading.Lock()


def _queue(xlsx: Path, run: str | None) -> dict:
    """The review queue for one run (None = every run), rebuilt only when its inputs change (runs, the workbook,
    reviews, health checks)."""
    base = xlsx.with_name(f"{xlsx.stem}_field_pipeline")
    inputs = [xlsx, review.reviews_path(xlsx), snapshot_path(xlsx), DEFAULT_DB,
              *sorted(base.glob("runs/*/decisions.csv"))]
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


@admin.get("/review")
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
    reviewed = sorted(({**r, "id": review.item_id(*key)} for key, r in result["reviews"].items()
                       if run == "all" or key in result["cells"]), key=lambda r: r["at"], reverse=True)
    group = request.args.get("group") or next((g for g in review.GROUPS if counts[g]), "candidate")
    return render_template("review.html", xlsx=xlsx, groups=review.GROUPS, choices=review.CHOICES, group=group,
                           counts=counts, items=[i for i in shown if i["group"] == group], total=len(shown),
                           sheets=sorted({i["sheet"] for i in items}), sheet=sheet, q=request.args.get("q", ""),
                           reviewed=reviewed, reviewer=request.cookies.get("reviewer", ""), run=run, runs=runs,
                           filled_elsewhere=result["filled_elsewhere"],
                           msg=request.args.get("msg", ""), error=request.args.get("error", ""))


@admin.post("/review/decide")
def review_decide():
    xlsx = _xlsx()
    item = (next((i for i in _queue(xlsx, _scope(_run_choice(xlsx)[0]))["items"] if i["id"] == request.form.get("id")),
                 None) if xlsx else None)
    if item is None:
        return redirect(url_for("admin.review_index", **_review_args(), error="That cell is no longer in the queue."))
    try:
        row = review.make_review(item, request.form.get("decision", ""), request.form.get("value", ""),
                                 request.form.get("source_url", ""), request.form.get("note", ""),
                                 request.cookies.get("reviewer", ""))
    except ValueError as exc:
        return redirect(url_for("admin.review_index", **_review_args(), error=f"{item['university']} / {item['field']}: {exc}")
                        + f"#{item['id']}")
    review.append_reviews(xlsx, [row])
    return redirect(url_for("admin.review_index", **_review_args(),
                            msg=f"Saved {item['field']} for {item['university']}: {row['decision']}"))


@admin.post("/review/undo")
def review_undo():
    xlsx = _xlsx()
    done = review.load_reviews(xlsx, load_snapshot(xlsx)) if xlsx else {}
    rv = next((r for key, r in done.items() if review.item_id(*key) == request.form.get("id")), None)
    if rv:
        review.append_reviews(xlsx, [review.undo_review(rv, request.cookies.get("reviewer", ""))])
    return redirect(url_for("admin.review_index", **_review_args(), group="reviewed",
                            msg=f"{rv['field']} for {rv['university']} is back in the queue" if rv else ""))


@admin.post("/review/name")
def review_name():
    resp = redirect(url_for("admin.review_index", **_review_args()))
    resp.set_cookie("reviewer", request.form.get("reviewer", "").strip()[:60], max_age=365 * 86400, samesite="Lax")
    return resp


@admin.get("/review/export")
def review_export():
    xlsx = _xlsx()
    if not xlsx:
        abort(400, "no workbook")
    run = _run_choice(xlsx)[0]
    name = "review_queue.xlsx" if run == "all" else f"review_queue_{run}.xlsx"
    dest = xlsx.with_name(f"{xlsx.stem}_field_pipeline") / name
    review.export_xlsx(_queue(xlsx, _scope(run))["items"], dest)
    return send_file(dest, as_attachment=True, download_name=name)


@admin.post("/review/import")
def review_import():
    xlsx = _xlsx()
    upload = request.files.get("file")
    if not xlsx or not upload or not upload.filename:
        return redirect(url_for("admin.review_index", **_review_args(), error="Choose the filled-in review_queue.xlsx first."))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "import.xlsx"
        upload.save(path)
        try:
            rows, problems = review.import_xlsx(path, _queue(xlsx, _scope(_run_choice(xlsx)[0]))["items"],
                                                request.cookies.get("reviewer", ""))
        except Exception as exc:  # not an export of the queue
            return redirect(url_for("admin.review_index", **_review_args(), error=f"Could not read that file: {exc}"))
    review.append_reviews(xlsx, rows)
    note = f"Imported {len(rows)} decision(s)." + (f" Skipped {len(problems)}: " + "; ".join(problems[:5])
                                                     if problems else "")
    return redirect(url_for("admin.review_index", **_review_args(), msg=note))


def knowledge_dir() -> Path:
    """KNOWLEDGE_DIR (the bundle api.py rebuilds after each publish), else <repo>/docs/okf/knowledge. ValueError if
    it doesn't end in docs/okf/knowledge, the only place write_from_db writes."""
    path = Path(os.environ.get("KNOWLEDGE_DIR") or KNOWLEDGE).resolve()
    if path.parts[-3:] != ("docs", "okf", "knowledge"):
        raise ValueError(f"KNOWLEDGE_DIR must end in docs/okf/knowledge: {path}")
    return path


@knowledge.get("/knowledge", endpoint="index")
def knowledge_index():
    folder = current_app.config["KNOWLEDGE_DIR"]
    if not (folder / "index.md").is_file():
        return render_template("knowledge.html", built=False)
    frontmatter, body = read_concept(folder / "index.md")
    title = frontmatter.get("title", "Knowledge")
    return render_template("knowledge.html", built=True, title=title,
                           body_html=mdlite.render(mdlite.drop_title(body, title)))


@knowledge.get("/knowledge/<path:rel>", endpoint="page")
def knowledge_page(rel):
    folder = current_app.config["KNOWLEDGE_DIR"]
    path = (folder / rel).resolve()
    if folder not in path.parents or not path.is_file():
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


@site.get("/")
def home():
    return redirect(url_for("knowledge.index"))


_export: dict = {"key": None, "data": b""}
_export_lock = threading.Lock()


@site.get("/download.xlsx")
def download():
    """prod.exporter's filled workbook for PROD_DB and PROD_TEMPLATE, built in a temp folder and kept in memory
    until the database version or the template changes."""
    db_path, template = api._db_path(), api._template()
    if db_path is None or template is None:
        abort(503, "The workbook download isn't set up on this server.")
    from prod import db, exporter
    try:
        conn = db.connect(db_path, readonly=True)
        try:
            version = db.version(conn)
        finally:
            conn.close()
        key = (str(db_path), version, str(template), template.stat().st_mtime_ns)
        with _export_lock:  # one build at a time; later requests reuse it
            if _export["key"] != key:
                with tempfile.TemporaryDirectory() as tmp:
                    out = Path(tmp) / "export.xlsx"
                    exporter.export_workbook(db_path, template, out)
                    _export.update(key=key, data=out.read_bytes())
            data = _export["data"]
    except (sqlite3.Error, OSError, ValueError):
        log.warning("workbook export failed", exc_info=True)
        abort(503, "The workbook can't be built right now.")
    return send_file(io.BytesIO(data), as_attachment=True,
                     download_name=f"Purdue competitive analysis v{version}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --- admin mode: local host only, and a CSRF token on every form ---

def _local_only():
    """Refuse a Host that isn't this machine: a DNS-rebinding page would otherwise be same-origin with the app."""
    if urlsplit(f"//{request.host}").hostname not in LOCAL_HOSTS:
        abort(400, "the admin app answers only on 127.0.0.1 or localhost")


def csrf_token() -> str:
    """This browser session's token (in the signed session cookie), made on first use."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def csrf_field() -> Markup:
    return Markup('<input type="hidden" name="csrf_token" value="{}">').format(csrf_token())


def _origin(url: str) -> tuple:
    u = urlsplit(url)
    return u.scheme, u.netloc


def _check_csrf():
    """A state-changing request must come from this app's own page: Origin (or Referer) names this host, and the
    form's csrf_token (or an X-CSRF-Token header) equals the session's. The bearer-key API is exempt."""
    if request.method in ("GET", "HEAD", "OPTIONS") or request.blueprint == "api":
        return
    sent_from = request.headers.get("Origin") or request.headers.get("Referer")
    if sent_from and _origin(sent_from) != _origin(request.host_url):
        abort(403, "cross-site request refused")
    token, sent = session.get("csrf", ""), request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not token or not hmac.compare_digest(sent.encode(), token.encode()):
        abort(403, "missing or expired form token: reload the page and try again")


def create_app(mode: str) -> Flask:
    """The app for `mode` ("admin" or "public"); only that mode's routes are registered."""
    if mode not in MODES:
        raise ValueError(f"APP_MODE must be one of {MODES}, not {mode!r}")
    app = Flask(__name__)
    app.config.update(MODE=mode, KNOWLEDGE_DIR=knowledge_dir())
    app.register_blueprint(knowledge)
    app.register_blueprint(api.bp)
    if mode == "public":
        app.register_blueprint(site)
    else:
        import prod_pages
        app.register_blueprint(admin)
        app.register_blueprint(prod_pages.bp)
        app.secret_key = secrets.token_bytes(32)  # per process: a restart only means reloading open pages
        app.config.update(SESSION_COOKIE_NAME="admin_session", SESSION_COOKIE_SAMESITE="Strict",
                          WORKBOOK=lambda: _xlsx())
        app.before_request(_local_only)
        app.before_request(_check_csrf)
        app.jinja_env.globals["csrf_field"] = csrf_field

    @app.context_processor
    def _nav():
        return {"mode": mode, "nav": [(endpoint, label) for endpoint, label in NAV[mode]
                                      if endpoint in app.view_functions]}

    return app


app = create_app(os.environ.get("APP_MODE") or "admin")

if __name__ == "__main__":
    xlsx = _xlsx()
    print(f"workbook: {xlsx or '(none found in repo root)'}")
    app.run(host="127.0.0.1", port=5000)
