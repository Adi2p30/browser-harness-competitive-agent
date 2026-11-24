"""Admin client for the production server: pull a snapshot, diff the local research against it, publish, and work
through school requests. Run from agent-workspace/:

  python -m prod.client pull "../EDIT Competitive Analysis Research 2026-2027.xlsx"
  python -m prod.client diff <workbook>
  python -m prod.client publish <workbook> [--note TEXT] [--yes]
  python -m prod.client requests [--status new]
  python -m prod.client claim <id>
  python -m prod.client done <id> [--program ID] [--note TEXT]
  python -m prod.client reject <id> [--note TEXT]
  python -m prod.client add-program --sheet S --university U [--school --program --link --request ID]

PROD_URL and PROD_ADMIN_KEY come from the environment or the repo-root .env. Exit codes: 0 ok, 1 error,
3 publish conflicts.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests

TIMEOUT = 60
PUBLISH_TIMEOUT = 300  # a publish backs up the database and rebuilds the knowledge bundle
CONFLICTS = 3
ENV = Path(__file__).resolve().parents[2] / ".env"
LOCAL_HOSTS = ("localhost", "127.0.0.1")


class ClientError(Exception):
    """A failed request: the server's {"error"} text, or why the server could not be reached."""


class Client:
    def __init__(self, base_url: str, key: str, session=None):
        if not key or any(c.isspace() or not c.isprintable() for c in key):
            raise ClientError("PROD_ADMIN_KEY is empty or contains whitespace or a control character")
        url = urlsplit(base_url)
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in LOCAL_HOSTS):
            raise ClientError(f"PROD_URL must be https (http only for {' or '.join(LOCAL_HOSTS)}): {base_url}")
        self.base, self.key = base_url.rstrip("/"), key
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {key}"})

    def _error(self, msg: str) -> ClientError:
        return ClientError(msg.replace(self.key, "<key>"))

    def _call(self, method: str, path: str, body: dict | None = None, params: dict | None = None,
              timeout: int = TIMEOUT) -> dict:
        try:
            r = self.session.request(method, self.base + path, json=body, params=params, timeout=timeout)
        except requests.RequestException as e:
            raise self._error(f"{method} {path}: {e}") from None
        try:
            data = r.json()
        except ValueError:
            data = None
        if not 200 <= r.status_code < 300:
            msg = data.get("error") if isinstance(data, dict) else None
            raise self._error(f"{method} {path}: {r.status_code} {msg or r.text[:200]}")
        if not isinstance(data, dict):
            raise self._error(f"{method} {path}: {r.status_code} but the reply is not a JSON object: {r.text[:200]}")
        return data

    def export(self) -> dict:
        return self._call("GET", "/api/export")

    def publish(self, changes: list[dict], note: str = "") -> dict:
        return self._call("POST", "/api/publish", {"changes": changes, "note": note}, timeout=PUBLISH_TIMEOUT)

    def requests(self, status: str | None = None) -> list[dict]:
        return self._call("GET", "/api/requests", params={"status": status} if status else None)["requests"]

    def claim(self, id: int) -> dict:
        return self._call("POST", f"/api/requests/{id}/claim")

    def set_status(self, id: int, status: str, admin_note: str | None = None, program_id: int | None = None) -> dict:
        body = {"status": status, "admin_note": admin_note, "program_id": program_id}
        return self._call("POST", f"/api/requests/{id}/status", {k: v for k, v in body.items() if v is not None})

    def add_program(self, **fields) -> int:
        return self._call("POST", "/api/programs", {k: v for k, v in fields.items() if v is not None})["id"]


def setting(name: str) -> str:
    """An environment variable, else its line in the repo-root .env (as field_pipeline.llm.api_key reads it);
    surrounding whitespace and quotes are dropped."""
    if not os.environ.get(name, "").strip() and ENV.is_file():
        for line in ENV.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name and v.strip():
                os.environ[name] = v.strip().strip("\"'")
    if not (value := os.environ.get(name, "").strip()):
        raise ClientError(f"{name} missing: set it or add it to .env in the repo root")
    return value


def snapshot_path(workbook: Path) -> Path:
    return workbook.with_name(f"{workbook.stem}_field_pipeline") / "prod-snapshot.json"


def save_snapshot(workbook: Path, snap: dict) -> Path:
    path = snapshot_path(workbook)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return path


def pull(client: Client, workbook: Path) -> dict:
    snap = client.export()
    path = save_snapshot(workbook, snap)
    print(f"version {snap['version']}: {len(snap['programs'])} programs, {len(snap['facts'])} facts -> {path}")
    return snap


def confirm(question: str) -> bool:
    """Yes only for y/yes; EOF and Ctrl-C mean no."""
    try:
        return input(question).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def print_conflicts(conflicts: list[dict], snap: dict) -> None:
    names = {p["id"]: f"{p['sheet']} · {p['university']}" for p in snap["programs"]}
    shown = lambda f: "no fact" if f is None else f"{f['value']} ({f['status']})"
    for c in conflicts:
        ch, cur = c["change"], c["current"]
        who = f" by {cur.get('by')} at {cur.get('at')}" if cur else ""
        print(f"  conflict {names.get(ch['program_id'], ch['program_id'])} / {ch['field']}:"
              f" theirs {shown(cur)}{who}; yours {shown(ch['new'])}")


def run(args, client: Client) -> int:
    from .diff import compare, format_diff  # builds the KB: only these commands need the pipeline's imports

    if args.cmd == "pull":
        pull(client, args.workbook)
    elif args.cmd == "diff":
        path = snapshot_path(args.workbook)
        if not path.exists():
            raise ClientError(f"no snapshot at {path}: run `python -m prod.client pull {args.workbook}` first")
        snap = json.loads(path.read_text(encoding="utf-8"))
        changes, unmapped = compare(args.workbook, snap)
        print(f"against version {snap['version']}")
        print(format_diff(changes, snap))
        for p in unmapped:
            print(f"not in production: {p['sheet']} · {p['university']} (row {p['row']}): {p['reason']}")
    elif args.cmd == "publish":
        snap = pull(client, args.workbook)
        changes, _ = compare(args.workbook, snap)
        if not changes:
            print("Nothing to publish.")
            return 0
        print(format_diff(changes, snap))
        if not args.yes and not confirm(f"Publish {len(changes)} changes? [y/N] "):
            print("Not published.")
            return 0
        res = client.publish(changes, args.note)
        print(f"version {res['version']}: {len(res['applied'])} applied, {len(res['conflicts'])} conflict(s)")
        print_conflicts(res["conflicts"], snap)
        try:
            pull(client, args.workbook)
        except ClientError:
            print(f"published (version {res['version']}); couldn't refresh the snapshot, run pull")
        return CONFLICTS if res["conflicts"] else 0
    return 0


def main(argv: list[str] | None = None, session=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m prod.client", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pull", "diff", "publish"):
        p = sub.add_parser(name)
        p.add_argument("workbook", type=Path)
        if name == "publish":
            p.add_argument("--note", default="")
            p.add_argument("--yes", action="store_true", help="publish without asking")
    sub.add_parser("requests").add_argument("--status", choices=("new", "claimed", "done", "rejected"))
    sub.add_parser("claim").add_argument("id", type=int)
    p = sub.add_parser("done")
    p.add_argument("id", type=int)
    p.add_argument("--program", type=int, help="the production program id the request became")
    p.add_argument("--note")
    p = sub.add_parser("reject")
    p.add_argument("id", type=int)
    p.add_argument("--note")
    p = sub.add_parser("add-program")
    p.add_argument("--sheet", required=True)
    p.add_argument("--university", required=True)
    for opt in ("--school", "--program", "--link"):
        p.add_argument(opt)
    p.add_argument("--request", type=int, help="the request this program fulfils (it is marked done)")
    args = ap.parse_args(argv)
    try:
        client = Client(setting("PROD_URL"), setting("PROD_ADMIN_KEY"), session)
        if args.cmd == "requests":
            for r in client.requests(args.status):
                print(f"#{r['id']} [{r['status']}{' by ' + r['claimed_by'] if r.get('claimed_by') else ''}]"
                      f" {r['sheet']} · {r['university']} · {r.get('program') or ''} {r.get('link') or ''}"
                      f"{' - ' + r['note'] if r.get('note') else ''}")
        elif args.cmd == "claim":
            client.claim(args.id)
            print(f"claimed request {args.id}")
        elif args.cmd in ("done", "reject"):
            status = "done" if args.cmd == "done" else "rejected"
            client.set_status(args.id, status, args.note, getattr(args, "program", None))
            print(f"request {args.id}: {status}")
        elif args.cmd == "add-program":
            pid = client.add_program(sheet=args.sheet, university=args.university, school=args.school,
                                     program=args.program, link=args.link, request_id=args.request)
            print(f"added program {pid}. Add the school to your workbook with the same sheet, university and program"
                  " name so the diff can match it, then run `python -m prod.client pull <workbook>`.")
        else:
            return run(args, client)
    except ClientError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
