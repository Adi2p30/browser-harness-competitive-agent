"""The production database over HTTP: the admin API under /api (Bearer admin key) and the public school request
form at /request. See docs/okf/project/components/prod-api.md.

Config is read from the environment on every request: PROD_DB (required; without it these routes answer 503),
PROD_BACKUP_DIR (default <PROD_DB dir>/backups), KNOWLEDGE_DIR (the folder /knowledge serves; default
docs/okf/knowledge), PROD_TEMPLATE (the template workbook; default the repo root's EDIT Competitive Analysis
Research*.xlsx, as app.py finds it) and PROD_TRUSTED_PROXIES (reverse proxies in front of the app, default 0).
The rate limiter and the rebuild lock live in this process: run a single worker.
"""
import ipaddress
import logging
import os
import sys
import threading
import time
import unicodedata
from functools import wraps
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-workspace"))

from flask import Blueprint, abort, g, jsonify, redirect, render_template, request, url_for  # noqa: E402
from werkzeug.exceptions import HTTPException  # noqa: E402

from field_pipeline.workbook import read_rows  # noqa: E402
from prod import db  # noqa: E402
from prod.values import to_text  # noqa: E402

log = logging.getLogger(__name__)
bp = Blueprint("api", __name__)

RATE_LIMIT, RATE_WINDOW = 5, 3600  # public request submissions per IP per hour
MAX_HIT_KEYS = 10_000  # sweep stale IPs from the limiter past this many
MAX_FORM_BYTES = 16 * 1024
SQLITE_MAX_INT = 2**63 - 1
_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()
_rebuild_lock = threading.Lock()


def _db_path() -> Path | None:
    return Path(os.environ["PROD_DB"]) if os.environ.get("PROD_DB") else None


def _backup_dir() -> Path:
    return Path(os.environ.get("PROD_BACKUP_DIR") or _db_path().parent / "backups")


def _knowledge_dir() -> Path | None:
    """KNOWLEDGE_DIR (it must end in docs/okf/knowledge), or None for write_from_db's default."""
    return Path(os.environ["KNOWLEDGE_DIR"]) if os.environ.get("KNOWLEDGE_DIR") else None


def _template() -> Path | None:
    """The template workbook: PROD_TEMPLATE if set (None when that file is missing), else app.py's _xlsx() rule."""
    if os.environ.get("PROD_TEMPLATE"):
        path = Path(os.environ["PROD_TEMPLATE"])
        return path if path.exists() else None
    return min(ROOT.glob("EDIT Competitive Analysis Research*.xlsx"), key=lambda p: (len(p.name), p.name), default=None)


def reset_rate_limit() -> None:
    with _hits_lock:
        _hits.clear()


def _error(message: str, code: int):
    return jsonify(error=message), code


@bp.before_request
def _need_db():
    if _db_path() is None:
        return _error("PROD_DB not configured", 503)


def _conn():
    """This request's connection, opened on first use and closed at teardown."""
    if "prod_conn" not in g:
        g.prod_conn = db.connect(_db_path())
    return g.prod_conn


@bp.teardown_request
def _close(_exc):
    conn = g.pop("prod_conn", None)
    if conn is not None:
        conn.close()


@bp.app_errorhandler(HTTPException)
def _json_errors(exc: HTTPException):
    """{"error": ...} for every HTTP error under /api, including unknown routes; elsewhere Flask's default page."""
    if request.path.startswith("/api/"):
        return _error(exc.description, exc.code)
    return exc


def admin_required(view):
    """Accept `Authorization: Bearer <key>` for an active admin and put their name in g.admin; 401 otherwise."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        scheme, _, key = request.headers.get("Authorization", "").partition(" ")
        name = db.check_key(_conn(), key.strip()) if scheme.lower() == "bearer" else None
        if not name:
            return _error("missing, invalid or revoked admin key", 401)
        g.admin = name
        return view(*args, **kwargs)
    return wrapper


def _body() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _opt_text(body: dict, key: str) -> str:
    """An optional string field ("" when absent); ValueError if it is not a string."""
    v = body.get(key)
    if v is not None and not isinstance(v, str):
        raise ValueError(f"{key} must be a string")
    return to_text(v) or ""


def _is_id(v) -> bool:
    """An integer SQLite can store (bool and oversized ints are not)."""
    return isinstance(v, int) and not isinstance(v, bool) and -SQLITE_MAX_INT - 1 <= v <= SQLITE_MAX_INT


def _request_row(request_id: int):
    if not _is_id(request_id):
        return None
    return _conn().execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()


def _program_exists(program_id: int) -> bool:
    return _conn().execute("SELECT 1 FROM programs WHERE id=?", (program_id,)).fetchone() is not None


@bp.get("/api/export")
@admin_required
def export():
    return jsonify(db.snapshot(_conn()))


@bp.post("/api/publish")
@admin_required
def publish():
    body = _body()
    changes, note = body.get("changes"), body.get("note", "")
    if not isinstance(changes, list) or not isinstance(note, str):
        return _error("expected {\"changes\": [...], \"note\": \"\"}", 400)
    if not changes:  # nothing to publish: no backup, no new version
        return jsonify(version=db.version(_conn()), applied=[], conflicts=[])
    try:
        result = db.apply_changes(_conn(), changes, g.admin, note, backup_dir=_backup_dir())
    except ValueError as exc:
        return _error(str(exc), 400)
    if result["applied"]:
        result["rebuilt"] = _rebuild()
    return jsonify(result)


def _rebuild() -> bool:
    """Rewrite the knowledge bundle from the database. A failure (Step 3's module missing included) is logged and
    never fails the publish: the facts are already committed."""
    template = _template()
    if template is None:
        log.warning("knowledge bundle not rebuilt after publish: no template workbook")
        return False
    try:
        from knowledge_base.from_db import write_from_db
        with _rebuild_lock:  # apply_changes has committed; write_from_db opens the file read-only itself
            write_from_db(_db_path(), template, knowledge_dir=_knowledge_dir())
        return True
    except Exception:
        log.warning("knowledge bundle rebuild after publish failed", exc_info=True)
        return False


@bp.get("/api/requests")
@admin_required
def requests_list():
    status = request.args.get("status") or None
    if status is not None and status not in db.REQUEST_STATUSES:
        return _error(f"status must be one of {db.REQUEST_STATUSES}", 400)
    return jsonify(requests=db.list_requests(_conn(), status))


@bp.post("/api/requests/<int:request_id>/claim")
@admin_required
def request_claim(request_id):
    if _request_row(request_id) is None:
        return _error(f"unknown request {request_id}", 404)
    if db.claim_request(_conn(), request_id, g.admin):
        return jsonify(claimed=True)
    row = _request_row(request_id)
    return _error(f"claimed by {row['claimed_by']}" if row["status"] == "claimed" else f"request is {row['status']}",
                  409)


@bp.post("/api/requests/<int:request_id>/status")
@admin_required
def request_status(request_id):
    body = _body()
    status, note, program_id = body.get("status"), body.get("admin_note"), body.get("program_id")
    if status not in db.REQUEST_STATUSES:
        return _error(f"status must be one of {db.REQUEST_STATUSES}", 400)
    if note is not None and not isinstance(note, str) or program_id is not None and not _is_id(program_id):
        return _error("admin_note must be a string and program_id an integer", 400)
    if _request_row(request_id) is None:
        return _error(f"unknown request {request_id}", 404)
    if program_id is not None and not _program_exists(program_id):
        return _error(f"unknown program {program_id}", 400)
    db.set_request_status(_conn(), request_id, status, admin_note=note, program_id=program_id)
    return jsonify(ok=True)


@bp.post("/api/programs")
@admin_required
def program_create():
    body = _body()
    try:
        fields = {k: _opt_text(body, k) for k in ("sheet", "university", "school", "program", "link")}
    except ValueError as exc:
        return _error(str(exc), 400)
    if not fields["sheet"] or not fields["university"]:
        return _error("sheet and university are required", 400)
    request_id = body.get("request_id")
    if request_id is not None and not _is_id(request_id):
        return _error("request_id must be an integer", 400)
    with db._tx(_conn()):  # the program and its request's "done" land together or not at all
        if request_id is not None:
            row = _request_row(request_id)
            if row is None:
                return _error(f"unknown request {request_id}", 404)
            if row["status"] not in ("new", "claimed"):  # already done (a double submit) or rejected
                return _error(f"request {request_id} is {row['status']}", 409)
        program_id = db.create_program(_conn(), **fields, added_by=g.admin)
        if request_id is not None:
            db.set_request_status(_conn(), request_id, "done", program_id=program_id)
    return jsonify(id=program_id), 201


# --- public: ask for a school to be added ---

def _sheets() -> list[str]:
    template = _template()
    if not template:
        return []
    try:
        return sorted(read_rows(str(template))[0])
    except Exception:
        log.warning("could not read sheet names from %s", template, exc_info=True)
        return []


def _client_key() -> str:
    """The rate-limit key for this client. X-Forwarded-For is trusted only with PROD_TRUSTED_PROXIES=N (N>0): the
    client is then the Nth address from the right, as ProxyFix(x_for=N) reads it. IPv6 clients are keyed by /64."""
    try:
        trusted = int(os.environ.get("PROD_TRUSTED_PROXIES") or 0)
    except ValueError:
        trusted = 0
    ip = request.remote_addr or ""
    if trusted > 0 and request.headers.get("X-Forwarded-For"):
        route = request.access_route
        if len(route) >= trusted:
            ip = route[-trusted]
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6 and addr.ipv4_mapped:  # ::ffff:a.b.c.d on a dual-stack bind is an IPv4 client
        addr = addr.ipv4_mapped
    return str(ipaddress.ip_network(f"{addr}/64", strict=False)) if addr.version == 6 else str(addr)


def _over_limit(ip: str) -> bool:
    """Record a submission from `ip`; True if it already made RATE_LIMIT in the last RATE_WINDOW seconds."""
    now = time.time()
    with _hits_lock:
        if len(_hits) > MAX_HIT_KEYS:
            for stale in [k for k, ts in _hits.items() if not ts or now - ts[-1] >= RATE_WINDOW]:
                del _hits[stale]
        recent = [t for t in _hits.get(ip, []) if now - t < RATE_WINDOW]
        if len(recent) >= RATE_LIMIT:
            _hits[ip] = recent
            return True
        _hits[ip] = recent + [now]
        return False


@bp.get("/request")
def request_form():
    return render_template("request.html", sheets=_sheets(), sent=request.args.get("sent") == "1", form={})


def _clean(v: str) -> str:
    """A public form value: control and format characters (U+202E and the like) become spaces, then
    knowledge_base's text form."""
    return to_text("".join(" " if unicodedata.category(c) in ("Cc", "Cf") else c for c in v[:2000])) or ""


@bp.post("/request")
def request_submit():
    request.max_content_length = MAX_FORM_BYTES  # a body that slips past the check below fails when parsed
    if (request.content_length or 0) > MAX_FORM_BYTES:
        abort(413)
    if _over_limit(_client_key()):
        return render_template("request.html", sheets=_sheets(), sent=False, form={},
                               error="Too many requests from your address; try again in an hour."), 429
    if request.form.get("website"):  # honeypot: a bot filled the hidden field
        return redirect(url_for("api.request_form", sent=1))
    form = {k: _clean(request.form.get(k, "")) for k in ("sheet", "university", "program", "link", "note")}
    if not form["link"].lower().startswith(("http://", "https://")):
        form["link"] = ""
    if not form["sheet"] or not form["university"]:
        return render_template("request.html", sheets=_sheets(), sent=False, form=form,
                               error="Program and university are required."), 400
    db.add_request(_conn(), **form)
    return redirect(url_for("api.request_form", sent=1))
