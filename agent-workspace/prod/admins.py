"""Manage admin keys in the production database (run from agent-workspace/):

    python -m prod.admins add <db> <name>      # new admin, or reissue a revoked one; prints the key once
    python -m prod.admins rotate <db> <name>   # replace an admin's key; prints the new key once
    python -m prod.admins list <db>
    python -m prod.admins revoke <db> <name>

Only `add` creates the database file.
"""
import argparse
import sys
from pathlib import Path

from prod import db


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd, named in (("add", True), ("rotate", True), ("list", False), ("revoke", True)):
        p = sub.add_parser(cmd)
        p.add_argument("db", type=Path)
        if named:
            p.add_argument("name")
    args = ap.parse_args(argv)
    if args.cmd != "add" and not args.db.is_file():
        print(f"no database at {args.db}", file=sys.stderr)
        return 1
    conn = db.connect(args.db)
    if args.cmd in ("add", "rotate"):
        try:
            key = (db.add_admin if args.cmd == "add" else db.rotate_admin)(conn, args.name)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        print(key)
        print(f"Key for {args.name}: store it as PROD_ADMIN_KEY in .env; it can't be shown again.", file=sys.stderr)
    elif args.cmd == "list":
        for a in db.list_admins(conn):
            print(f"{a['name']}\tcreated {a['created_at']}" + (f"\trevoked {a['revoked_at']}" if a["revoked_at"] else ""))
    elif not db.revoke_admin(conn, args.name):
        print(f"no active admin named {args.name!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
