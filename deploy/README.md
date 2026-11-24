# Hosting the site

`webapp/app.py` served by waitress (`deploy/serve.py`) behind an HTTPS reverse proxy, as one
systemd service. The examples use the service account `prodsite`, the checkout
`/srv/prod-site/repo`, the venv `/srv/prod-site/venv`, the data in `/srv/prod-site/data` and
`/srv/prod-site/backups`, and the env file `/etc/prod-public.env`. "You" are the deploy user:
an account with sudo, not `prodsite`. Change the paths to suit, but keep them out of `/home`:
the service runs with `ProtectHome=true`.

**Public mode only.** `serve.py` sets `APP_MODE=public`, so the hosted app serves the knowledge
pages, `/request`, `/api/*`, `/download.xlsx` and static files, and has no `/run`, `/health`,
`/review` or `/admin` route. `serve.py` refuses to start if one is registered. Admins research and
publish from the admin app on their own laptops (step 11). The proxy examples also forward only
`/api/`, `/request`, `/knowledge`, `/download.xlsx` and `/static/`.

**One process only.** The request limiter and the rebuild lock live in memory. Never run two
copies, and never put it under a multi-worker server (gunicorn `-w`, several instances behind
the proxy). `serve.py` uses threads in one process.

## 1. Prerequisites

Linux with systemd, Python 3.11+ and `sqlite3` installed system-wide, git, nginx or Apache 2.4
with a TLS certificate for the host name, outbound access to pypi.org, and
[uv](https://docs.astral.sh/uv/) for the deploy user.

## 2. Clone

```bash
sudo useradd --system --create-home prodsite
sudo install -d -o "$USER" /srv/prod-site                     # yours: the venv goes here
sudo install -d -o prodsite -g prodsite -m 750 /srv/prod-site/data /srv/prod-site/backups
sudo install -d -o prodsite -g prodsite /srv/prod-site/repo
sudo -u prodsite git clone https://github.com/Adi2p30/browser-harness-competitive-agent /srv/prod-site/repo
sudo -u prodsite mkdir -p /srv/prod-site/repo/docs/okf/knowledge
```

## 3. Packages

As yourself, not `prodsite`, so the service can't change its own code:

```bash
uv venv --python 3.11 --no-managed-python /srv/prod-site/venv
uv pip sync --require-hashes --python /srv/prod-site/venv/bin/python /srv/prod-site/repo/deploy/requirements.txt
```

`deploy/requirements.txt` pins every package with its hashes, and `--require-hashes` refuses a
download that doesn't match. `--no-managed-python` builds the venv on the system's Python:
a uv-managed one lives under your home, which the service can't see. Use `--python 3.12` (or
newer) if that is the system's version.

## 4. Env file

`/etc/prod-public.env`, owned `root:prodsite`, mode 640
(`sudo chown root:prodsite /etc/prod-public.env && sudo chmod 640 /etc/prod-public.env`):

```bash
# The production database. Required: serve.py refuses to start without it.
PROD_DB=/srv/prod-site/data/prod.sqlite
# Publishes back up here first (newest 50 kept); backup.sh writes nightly/ under it.
PROD_BACKUP_DIR=/srv/prod-site/backups
# The knowledge pages /knowledge serves, rebuilt after each publish. Must be <repo>/docs/okf/knowledge.
KNOWLEDGE_DIR=/srv/prod-site/repo/docs/okf/knowledge
# The template workbook (sheet names, column order). Default: the repo root's EDIT Competitive Analysis Research*.xlsx.
PROD_TEMPLATE="/srv/prod-site/repo/EDIT Competitive Analysis Research 2026-2027.xlsx"
# One reverse proxy in front: take the client address from X-Forwarded-For. serve.py then insists on a loopback PROD_HOST.
PROD_TRUSTED_PROXIES=1
# Where waitress listens. Default 127.0.0.1:8000; the proxy configs assume it.
#PROD_HOST=127.0.0.1
#PROD_PORT=8000
```

Keep values that contain spaces in double quotes: cron sources this file with the shell.

## 5. First-time data

The commands in steps 5 and 6 run from `agent-workspace/` with a `$PY` shorthand. On a laptop
it is `PY="uv run --no-project --with-requirements ../deploy/requirements.txt python"`. On the
server, `data/` and `backups/` are mode 750, so open a shell as `prodsite` first:

```bash
sudo -u prodsite -s
cd /srv/prod-site/repo/agent-workspace && PY=/srv/prod-site/venv/bin/python
```

The first import must run on an admin laptop that has the research. The server's clone has the
workbooks, but not the pipeline runs or `reviews.csv` in `<workbook>_field_pipeline/`, which git
ignores. An import there would hold only the workbook's typed values and last year's.

```bash
$PY -m prod.importer "../EDIT Competitive Analysis Research 2026-2027.xlsx" prod.sqlite --by <your name>
sqlite3 prod.sqlite 'PRAGMA wal_checkpoint(TRUNCATE)'   # fold the WAL into the file
scp prod.sqlite <server>:/tmp/
```

Without the `sqlite3` CLI, checkpoint with
`python3 -c "import sqlite3; sqlite3.connect('prod.sqlite').execute('PRAGMA wal_checkpoint(TRUNCATE)')"`.
Then on the server:

```bash
sudo install -o prodsite -g prodsite -m 600 /tmp/prod.sqlite /srv/prod-site/data/ && rm /tmp/prod.sqlite
```

Build the knowledge pages once (afterwards each publish rebuilds them), in the server's `prodsite` shell:

```bash
set -a; . /etc/prod-public.env; set +a
$PY -c 'import os; from knowledge_base.from_db import write_from_db; write_from_db(os.environ["PROD_DB"], os.environ["PROD_TEMPLATE"], knowledge_dir=os.environ["KNOWLEDGE_DIR"])'
```

## 6. Admin keys

In the server's `prodsite` shell, one per admin:

```bash
$PY -m prod.admins add /srv/prod-site/data/prod.sqlite <name>     # prints the key once
$PY -m prod.admins list /srv/prod-site/data/prod.sqlite
$PY -m prod.admins rotate /srv/prod-site/data/prod.sqlite <name>  # lost or leaked key
$PY -m prod.admins revoke /srv/prod-site/data/prod.sqlite <name>
```

Hand each key to its admin in person or through a password manager share, never by email or
chat. The server keeps only its hash.

## 7. Service, proxy and HTTPS

```bash
sudo cp /srv/prod-site/repo/deploy/prod-public.service /etc/systemd/system/
sudoedit /etc/systemd/system/prod-public.service   # fill every @PLACEHOLDER@
sudo systemctl daemon-reload && sudo systemctl enable --now prod-public
journalctl -u prod-public -f
curl -s http://127.0.0.1:8000/api/export           # 401 {"error": ...} means it is up
```

Then copy `deploy/nginx.conf.example` (or `deploy/apache.conf.example`) into the proxy's
config, fill in `@SERVER_NAME@` and the certificate paths, test it (`nginx -t` or
`apachectl configtest`) and reload. Both forward only `/api/`, `/request`, `/knowledge`,
`/download.xlsx` (the workbook, built from the database) and `/static/` to `127.0.0.1:8000`, send `/` to `/knowledge` and refuse everything else. They set
`X-Forwarded-For` (hence `PROD_TRUSTED_PROXIES=1`), cap request bodies at 64 KB, and allow
32 MB and 300 s for `/api/publish`. Keep port 8000 closed to the outside.

## 8. Nightly backup

`deploy/backup.sh` copies the live database to `$PROD_BACKUP_DIR/nightly/prod-YYYYmmdd.sqlite`,
keeps the newest 30 and prints nothing unless it fails. Add it with `sudo -u prodsite crontab -e`:

```cron
15 3 * * * set -a; . /etc/prod-public.env; set +a; /srv/prod-site/repo/deploy/backup.sh
```

Copy the backups off the server too.

## 9. Restore

Pick a nightly copy or a pre-publish one (`prod-v<version>-<time>.sqlite`).
Everything since that backup is lost: publishes, school requests, and admin key changes.

```bash
sudo -u prodsite ls /srv/prod-site/backups /srv/prod-site/backups/nightly
sudo systemctl stop prod-public
sudo -u prodsite sqlite3 'file:/srv/prod-site/backups/nightly/prod-YYYYmmdd.sqlite?immutable=1' 'PRAGMA integrity_check'   # expect: ok
# Move the database (with its -wal and -shm files) aside and copy the backup in, as prodsite in one shell.
sudo -u prodsite sh -ec 'cd /srv/prod-site/data; R=replaced-$(date +%Y%m%d-%H%M%S); mkdir "$R"; mv prod.sqlite* "$R"/; cp "$1" prod.sqlite' _ /srv/prod-site/backups/nightly/prod-YYYYmmdd.sqlite
sudo systemctl start prod-public
```

Then, in a `prodsite` shell as in step 5, run
`$PY -m prod.admins list /srv/prod-site/data/prod.sqlite`. Revoke or rotate again any key that
was revoked or rotated after the backup, because the restore brought the old key back. Rebuild
the knowledge pages as in step 5. Admins `pull` before their next publish.

## 10. Update

```bash
sudo -u prodsite git -C /srv/prod-site/repo pull --ff-only
uv pip sync --require-hashes --python /srv/prod-site/venv/bin/python /srv/prod-site/repo/deploy/requirements.txt  # as yourself
sudo systemctl restart prod-public
```

The sync does nothing unless `deploy/requirements.txt` changed. To change packages, in a
checkout with pypi access: edit `deploy/requirements.in` (the direct packages), recompile, and
review `git diff deploy/requirements.txt` (every version jump and every new package) before
committing. Then update the server as above.

```bash
uv pip compile deploy/requirements.in --universal --python-version 3.11 --generate-hashes --upgrade -o deploy/requirements.txt
```

Leave out `--upgrade` to keep the current pins and only add or drop what `requirements.in` changed.

## 11. Admin laptops

In the repo-root `.env` (or the environment):

```bash
PROD_URL=https://<server name>
PROD_ADMIN_KEY=<the key from step 6>
```

Then start the admin app with `./run-webapp.sh` (it listens on `127.0.0.1:5000` only): its
Requests and Publish pages work the server's requests, add programs and publish. Or run
`$PY -m prod.client pull|diff|publish|requests …` from `agent-workspace/`, with the laptop `$PY`
from step 5. See `docs/okf/project/components/prod-client.md`.
