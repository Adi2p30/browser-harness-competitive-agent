"""The admin app's production pages (admin mode only): the requests inbox, publish, add a program, and pull. They
reach the server through prod.client, with PROD_URL and PROD_ADMIN_KEY from the environment or the repo-root .env.
See docs/okf/project/components/prod-client.md.
"""
import hashlib
import hmac
import json
from pathlib import Path

from flask import Blueprint, current_app, redirect, render_template, request, url_for

from field_pipeline.workbook import read_rows
from prod.client import Client, ClientError, save_snapshot, setting

bp = Blueprint("prod", __name__, url_prefix="/admin")
PROGRAM_FIELDS = ("sheet", "university", "school", "program", "link")


def _client() -> Client:
    """ClientError when PROD_URL or PROD_ADMIN_KEY is missing or unusable. PROD_SESSION in the app config stands
    in for requests.Session in tests."""
    return Client(setting("PROD_URL"), setting("PROD_ADMIN_KEY"), current_app.config.get("PROD_SESSION"))


def _workbook() -> Path | None:
    return current_app.config["WORKBOOK"]()


def _pull(client: Client) -> str:
    """Save a fresh snapshot next to the workbook, as `prod.client pull` does; a sentence saying how it went."""
    xlsx = _workbook()
    if xlsx is None:
        return "No workbook in the repo root, so no snapshot was saved."
    try:
        snap = client.export()
    except ClientError as e:
        return f"Couldn't pull a fresh snapshot ({e}); press Pull a fresh snapshot to try again."
    save_snapshot(xlsx, snap)
    n = len(snap["programs"])
    return f"Pulled a fresh snapshot: version {snap['version']}, {n} program{'s' if n != 1 else ''}."


def _research_hint(program_id: int) -> str:
    return f" Research it with: python -m field_pipeline <workbook> --program {program_id}"


@bp.post("/pull")
def pull():
    back = request.form.get("back")
    try:
        msg = _pull(_client())
    except ClientError as e:
        msg = str(e)
    return redirect(url_for("prod.publish" if back == "publish" else "prod.requests_inbox", msg=msg))


# --- requests inbox ---

@bp.get("/requests")
def requests_inbox():
    status = request.args.get("status", "")
    page = {"status": status, "msg": request.args.get("msg", ""), "error": request.args.get("error", "")}
    try:
        c = _client()
    except ClientError as e:
        return render_template("admin/requests.html", rows=None, unconfigured=str(e), **page)
    try:
        rows = c.requests(status or None)
    except ClientError as e:
        return render_template("admin/requests.html", rows=None, unreachable=str(e), **page)
    return render_template("admin/requests.html", rows=rows[::-1], **page)


def _act(request_id: int, action):
    """Run action(client) and come back to the inbox with its message, or the server's error."""
    status = request.form.get("status") or None
    try:
        msg = action(_client())
    except ClientError as e:
        return redirect(url_for("prod.requests_inbox", status=status, error=f"Request #{request_id}: {e}"))
    return redirect(url_for("prod.requests_inbox", status=status, msg=msg))


@bp.post("/requests/<int:request_id>/claim")
def request_claim(request_id):
    def claim(c: Client) -> str:
        c.claim(request_id)
        return f"You claimed request #{request_id}."
    return _act(request_id, claim)


@bp.post("/requests/<int:request_id>/add")
def request_add(request_id):
    def add(c: Client) -> str:
        req = next((r for r in c.requests() if r["id"] == request_id), None)
        if req is None:
            raise ClientError("no such request on the server")
        if req["status"] not in ("new", "claimed"):  # a double submit, or a rejected request
            raise ClientError(f"it is already {req['status']}, so no program was added")
        pid = c.add_program(sheet=req["sheet"], university=req["university"], program=req.get("program") or None,
                            link=req.get("link") or None, request_id=request_id)
        return f"Added program {pid} for request #{request_id}; the request is done. {_pull(c)}{_research_hint(pid)}"
    return _act(request_id, add)


@bp.post("/requests/<int:request_id>/reject")
def request_reject(request_id):
    def reject(c: Client) -> str:
        c.set_status(request_id, "rejected", request.form.get("note", "").strip() or None)
        return f"Rejected request #{request_id}."
    return _act(request_id, reject)


# --- add a program ---

@bp.route("/programs/new", methods=["GET", "POST"])
def program_new():
    xlsx = _workbook()
    sheets = sorted(read_rows(str(xlsx))[0]) if xlsx else []
    form = {k: request.form.get(k, "").strip() for k in PROGRAM_FIELDS}
    if request.method == "GET":
        return render_template("admin/program.html", sheets=sheets, form=form, msg=request.args.get("msg", ""))
    if not form["sheet"] or not form["university"]:
        return render_template("admin/program.html", sheets=sheets, form=form,
                               error="Program sheet and university are required."), 400
    try:
        c = _client()
        pid = c.add_program(**{k: v or None for k, v in form.items()})
    except ClientError as e:
        return render_template("admin/program.html", sheets=sheets, form=form, error=str(e)), 502
    return redirect(url_for("prod.program_new", msg=f"Added program {pid}: {form['sheet']} · {form['university']}. "
                                                    f"{_pull(c)}{_research_hint(pid)}"))


# --- publish ---

def digest(changes: list[dict]) -> str:
    """The hash of a change list, carried by the confirm form so the publish posts exactly what was shown."""
    return hashlib.sha256(json.dumps(changes, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _plan(c: Client, xlsx: Path) -> tuple[dict, list[dict], list[dict]]:
    """(a fresh snapshot, the changes to publish against it, unmapped local programs)."""
    from prod.diff import compare  # builds the knowledge base: the heavy imports wait until a publish
    snap = c.export()
    changes, unmapped = compare(xlsx, snap)
    return snap, changes, unmapped


def _names(snap: dict) -> dict:
    return {p["id"]: f"{p['sheet']} · {p['university']}" for p in snap["programs"]}


def _shown(fact: dict | None) -> str:
    if fact is None:
        return ""
    return fact["value"] if fact.get("value") is not None else f"({fact['status']})"


def change_rows(changes: list[dict], snap: dict) -> list[dict]:
    """One row per change, by program: what format_diff prints, as a table."""
    names = _names(snap)
    rows = []
    for c in changes:
        new, old = c["new"], c["old"]
        kind = ("not published" if new["status"] == "not-published" and new["value"] is None
                else "new" if old is None else "changed")
        rows.append({"program": names.get(c["program_id"], f"program {c['program_id']}"), "field": c["field"],
                     "kind": kind, "old": _shown(old), "new": _shown(new), "status": new["status"],
                     "source": new.get("source_url") or "", "origin": new.get("origin") or ""})
    return sorted(rows, key=lambda r: r["program"])


def conflict_rows(conflicts: list[dict], snap: dict) -> list[dict]:
    names = _names(snap)
    return [{"program": names.get(x["change"]["program_id"], f"program {x['change']['program_id']}"),
             "field": x["change"]["field"], "theirs": _shown(x["current"]) or "no fact",
             "by": (x["current"] or {}).get("by") or "", "at": (x["current"] or {}).get("at") or "",
             "yours": _shown(x["change"]["new"])} for x in conflicts]


def _diff_page(snap: dict, changes: list[dict], unmapped: list[dict], code: int = 200, **extra):
    return render_template("admin/publish.html", snap=snap, rows=change_rows(changes, snap), unmapped=unmapped,
                           digest=digest(changes), count=len(changes), msg=request.args.get("msg", ""),
                           **extra), code


@bp.get("/publish")
def publish():
    xlsx = _workbook()
    if xlsx is None:
        return render_template("admin/publish.html", unavailable="No EDIT Competitive Analysis Research*.xlsx "
                                                                "workbook in the repo root.")
    try:
        snap, changes, unmapped = _plan(_client(), xlsx)
    except ClientError as e:
        return render_template("admin/publish.html", unavailable=str(e))
    return _diff_page(snap, changes, unmapped)


@bp.post("/publish")
def publish_confirm():
    """Recompute the changes now; post them only if they hash to what the admin confirmed."""
    xlsx = _workbook()
    if xlsx is None:
        return redirect(url_for("prod.publish"))
    try:
        c = _client()
        snap, changes, unmapped = _plan(c, xlsx)
    except ClientError as e:
        return render_template("admin/publish.html", unavailable=str(e)), 502
    if not hmac.compare_digest(digest(changes).encode(), request.form.get("digest", "").encode()):
        return _diff_page(snap, changes, unmapped, 409,
                          stale="The changes are no longer the ones you confirmed: someone published, or the local "
                                "research changed. Nothing was published. Check them again below.")
    if not changes:
        return _diff_page(snap, changes, unmapped)
    try:
        res = c.publish(changes, request.form.get("note", "").strip())
    except ClientError as e:
        return _diff_page(snap, changes, unmapped, 502, error=f"Not published: {e}")
    return render_template("admin/published.html", res=res, applied=change_rows(res["applied"], snap),
                           conflicts=conflict_rows(res["conflicts"], snap), pulled=_pull(c))
