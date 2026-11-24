#!/usr/bin/env bash
# Nightly online backup of PROD_DB to $PROD_BACKUP_DIR/nightly/prod-YYYYmmdd.sqlite, keeping the newest 30.
# Safe while the site runs. Prints nothing on success, so cron mails only failures. See deploy/README.md.
set -euo pipefail

: "${PROD_DB:?PROD_DB is not set}"
[[ -f "$PROD_DB" ]] || { echo "backup: no database at $PROD_DB" >&2; exit 1; }
DIR="${PROD_BACKUP_DIR:-$(dirname -- "$PROD_DB")/backups}/nightly"
KEEP=30
DEST="$DIR/prod-$(date +%Y%m%d).sqlite"
TMP="$DIR/.prod-$$.tmp"

mkdir -p -- "$DIR"
trap 'rm -f -- "$TMP"' EXIT
# SQLite's online backup API; the paths go in as arguments, never into a command string.
python3 -c 'import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()' "$PROD_DB" "$TMP"
mv -f -- "$TMP" "$DEST"

# Names sort by date: delete all but the newest $KEEP.
shopt -s nullglob
backups=("$DIR"/prod-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].sqlite)
for ((i = 0; i < ${#backups[@]} - KEEP; i++)); do
  rm -f -- "${backups[i]}"
done
