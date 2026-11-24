# Hosting the site

`webapp/app.py` served by waitress (`deploy/serve.py`) behind an HTTPS reverse proxy, as one
systemd service. The examples use the account `prodsite`, the checkout `/srv/prod-site/repo`,
the data in `/srv/prod-site/data` and `/srv/prod-site/backups`, and the env file
`/etc/prod-public.env`. Change them to suit.

> **Not public yet.** `serve.py` sets `APP_MODE=public`, but the app ignores it until the public
> mode step lands. Until then the site still serves `/run`, `/health` and `/review`, which have no
> login. Do not expose it to the internet before that step.

**One process only.** The request limiter and the rebuild lock live in memory. Never run two
copies, and never put it under a multi-worker server (gunicorn `-w`, several instances behind
the proxy). `serve.py` uses threads in one process.

## 1. Prerequisites

Linux with systemd, Python 3.11+, [uv](https://docs.astral.sh/uv/), git, nginx or Apache 2.4
with a TLS certificate for the host name, and outbound access to pypi.org (uv fetches the
packages on first start). `sqlite3` is optional; `backup.sh` falls back to Python.

## 2. Clone

```bash
sudo useradd --system --create-home prodsite
sudo mkdir -p /srv/prod-site/data /srv/prod-site/backups && sudo chown -R prodsite: /srv/prod-site
sudo -u prodsite git clone https://github.com/Adi2p30/browser-harness-competitive-agent /srv/prod-site/repo
sudo -u prodsite mkdir -p /srv/prod-site/repo/docs/okf/knowledge
```

## 3. Env file

`/etc/prod-public.env`, owned by `prodsite`, mode 600 (`sudo chown prodsite: … && sudo chmod 600 …`):

```bash
# The production database. Required: serve.py refuses to start without it.
PROD_DB=/srv/prod-site/data/prod.sqlite
# Publishes back up here first (newest 50 kept); backup.sh writes nightly/ under it.
PROD_BACKUP_DIR=/srv/prod-site/backups
# The knowledge pages /knowledge serves, rebuilt after each publish. Must be <repo>/docs/okf/knowledge.
KNOWLEDGE_DIR=/srv/prod-site/repo/docs/okf/knowledge
# The template workbook (sheet names, column order). Default: the repo root's EDIT Competitive Analysis Research*.xlsx.
PROD_TEMPLATE="/srv/prod-site/repo/EDIT Competitive Analysis Research 2026-2027.xlsx"
# One reverse proxy in front: take the client address from X-Forwarded-For.
PROD_TRUSTED_PROXIES=1
# Where waitress listens. Default 127.0.0.1:8000; the proxy configs assume it.
#PROD_HOST=127.0.0.1
#PROD_PORT=8000
```

Keep values that contain spaces in double quotes: cron sources this file with the shell.

## 4. First-time data

The commands below run from `agent-workspace/` with this shorthand:

```bash
PY="uv run --no-project --with flask --with openpyxl --with beautifulsoup4 --with requests --with pypdf --with pyyaml python"
```

Load the workbook into a new database. The importer also reads the pipeline runs and
`reviews.csv` next to the workbook, so run it on the admin laptop that has them:

```bash
$PY -m prod.importer "../EDIT Competitive Analysis Research 2026-2027.xlsx" prod.sqlite --by <your name>
scp prod.sqlite <server>:/srv/prod-site/data/prod.sqlite   # then chown prodsite: on the server
```

Copy `prod.sqlite-wal` too if one is left next to it. Running the same importer on the server
works as well, but it sees only the workbook and last year's file.

Build the knowledge pages once (afterwards each publish rebuilds them), on the server as `prodsite`:

```bash
set -a; . /etc/prod-public.env; set +a
$PY -c 'import os; from knowledge_base.from_db import write_from_db; write_from_db(os.environ["PROD_DB"], os.environ["PROD_TEMPLATE"], knowledge_dir=os.environ["KNOWLEDGE_DIR"])'
```

## 5. Admin keys

On the server as `prodsite`, one per admin:

```bash
$PY -m prod.admins add /srv/prod-site/data/prod.sqlite <name>     # prints the key once
$PY -m prod.admins list /srv/prod-site/data/prod.sqlite
$PY -m prod.admins rotate /srv/prod-site/data/prod.sqlite <name>  # lost or leaked key
$PY -m prod.admins revoke /srv/prod-site/data/prod.sqlite <name>
```

Hand each key to its admin in person or through a password manager share, never by email or
chat. The server keeps only its hash.

## 6. Service, proxy and HTTPS

```bash
sudo cp /srv/prod-site/repo/deploy/prod-public.service /etc/systemd/system/
sudoedit /etc/systemd/system/prod-public.service   # fill every @PLACEHOLDER@
sudo systemctl daemon-reload && sudo systemctl enable --now prod-public
journalctl -u prod-public -f                       # first start downloads packages
curl -s http://127.0.0.1:8000/api/export           # 401 {"error": ...} means it is up
```

Then copy `deploy/nginx.conf.example` (or `deploy/apache.conf.example`) into the proxy's
config, fill in `@SERVER_NAME@` and the certificate paths, test it (`nginx -t` or
`apachectl configtest`) and reload. Both forward to `127.0.0.1:8000`, set `X-Forwarded-For`
(hence `PROD_TRUSTED_PROXIES=1`), cap request bodies at 64 KB and allow 32 MB and 300 s for
`/api/publish`. Keep port 8000 closed to the outside.

## 7. Nightly backup

`deploy/backup.sh` copies the live database to `$PROD_BACKUP_DIR/nightly/prod-YYYYmmdd.sqlite`,
keeps the newest 30 and prints nothing unless it fails. `crontab -e` as `prodsite`:

```cron
15 3 * * * set -a; . /etc/prod-public.env; set +a; /srv/prod-site/repo/deploy/backup.sh
```

Copy the backups off the server too.

## 8. Restore

Pick a nightly copy or a pre-publish one (`$PROD_BACKUP_DIR/prod-v<version>-<time>.sqlite`), then:

```bash
sudo systemctl stop prod-public
sqlite3 <backup> 'PRAGMA integrity_check'          # expect: ok
cd /srv/prod-site/data && R=replaced-$(date +%Y%m%d-%H%M%S) && sudo -u prodsite mkdir "$R"
sudo -u prodsite mv prod.sqlite* "$R"/             # the database with its -wal and -shm files
sudo -u prodsite cp <backup> prod.sqlite
sudo systemctl start prod-public
```

Rebuild the knowledge pages as in step 4. Admins then `pull` before their next publish.

## 9. Update the code

```bash
sudo -u prodsite git -C /srv/prod-site/repo pull --ff-only
sudo systemctl restart prod-public
```

Packages are not pinned, so a restart may pick up newer releases.

## 10. Admin laptops

In the repo-root `.env` (or the environment):

```bash
PROD_URL=https://<server name>
PROD_ADMIN_KEY=<the key from step 5>
```

Then `$PY -m prod.client pull|diff|publish|requests …` from `agent-workspace/`; see
`docs/okf/project/components/prod-client.md`.
