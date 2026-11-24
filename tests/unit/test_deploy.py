"""deploy/: serve.py's startup checks, backup.sh, and the placeholders in the service and proxy files.
Nothing here starts a server or binds a port."""
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
sys.path.insert(0, str(DEPLOY))

import serve  # noqa: E402

PLACEHOLDER = re.compile(r"@[A-Z_]+@")


def test_serve_refuses_without_prod_db():
    for env in ({}, {"PROD_DB": "  "}):
        with pytest.raises(SystemExit, match="PROD_DB"):
            serve.config(env)


def test_serve_refuses_debug():
    with pytest.raises(SystemExit, match="FLASK_DEBUG"):
        serve.config({"PROD_DB": "prod.sqlite", "FLASK_DEBUG": "1"})
    assert serve.config({"PROD_DB": "prod.sqlite", "FLASK_DEBUG": "0"})


def test_serve_config():
    cfg = serve.config({"PROD_DB": "prod.sqlite"})
    assert (cfg["host"], cfg["port"]) == ("127.0.0.1", 8000)
    assert cfg["threads"] > 1
    assert cfg["clear_untrusted_proxy_headers"] is False  # the app reads X-Forwarded-For itself
    cfg = serve.config({"PROD_DB": "prod.sqlite", "PROD_HOST": "::1", "PROD_PORT": "9000"})
    assert (cfg["host"], cfg["port"]) == ("::1", 9000)
    with pytest.raises(SystemExit, match="PROD_PORT"):
        serve.config({"PROD_DB": "prod.sqlite", "PROD_PORT": "http"})


def test_serve_config_is_valid_for_waitress():
    adjustments = pytest.importorskip("waitress.adjustments")
    adjustments.Adjustments(**serve.config({"PROD_DB": "prod.sqlite"}))


def test_load_app_sets_public_mode(monkeypatch):
    pytest.importorskip("flask")
    monkeypatch.delenv("APP_MODE", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    app = serve.load_app()
    assert serve.os.environ["APP_MODE"] == "public"
    assert not app.debug


def _live_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    return conn  # left open: the backup runs against a database in use


def _run_backup(tmp_path: Path, path_env: str | None = None) -> Path:
    db = tmp_path / "prod.sqlite"
    conn = _live_db(db)
    nightly = tmp_path / "backups" / "nightly"
    nightly.mkdir(parents=True)
    old = [nightly / f"prod-2020{m:02d}{d:02d}.sqlite" for m in (1, 2) for d in range(1, 19)]  # 36
    for p in old:
        p.write_bytes(b"")
    env = {"PROD_DB": str(db), "PROD_BACKUP_DIR": str(tmp_path / "backups"),
           "PATH": path_env or "/usr/bin:/bin:/usr/local/bin"}
    res = subprocess.run([shutil.which("bash"), str(DEPLOY / "backup.sh")], env=env, capture_output=True,
                         text=True, timeout=60)
    conn.close()
    assert (res.returncode, res.stdout, res.stderr) == (0, "", "")
    kept = sorted(p.name for p in nightly.iterdir())
    assert len(kept) == 30 and not [n for n in kept if n.startswith(".")]
    assert set(kept) & {p.name for p in old[:7]} == set()  # the 7 oldest of 37 pruned
    new = nightly / kept[-1]
    assert re.fullmatch(r"prod-\d{8}\.sqlite", new.name) and new not in old
    copy = sqlite3.connect(new)
    assert copy.execute("SELECT x FROM t").fetchall() == [(42,)]
    assert copy.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    copy.close()
    return new


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_backup_copies_and_prunes(tmp_path):
    _run_backup(tmp_path)


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_backup_without_sqlite3_cli(tmp_path):
    """A PATH holding only what backup.sh needs, with python3 but no sqlite3."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("date", "dirname", "mkdir", "mv", "rm"):
        if not shutil.which(tool):
            pytest.skip(f"no {tool}")
        (bin_dir / tool).symlink_to(shutil.which(tool))
    python3 = bin_dir / "python3"
    python3.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python3.chmod(0o755)
    run = tmp_path / "run"
    run.mkdir()
    _run_backup(run, str(bin_dir))


def test_service_template():
    text = (DEPLOY / "prod-public.service").read_text()
    for needle in ("User=@USER@", "WorkingDirectory=@REPO@", "EnvironmentFile=@ENV_FILE@", "Restart=on-failure",
                   "NoNewPrivileges=true", "ProtectSystem=strict",
                   "ReadWritePaths=@DB_DIR@ @BACKUP_DIR@ @REPO@/docs/okf/knowledge", "python deploy/serve.py"):
        assert needle in text
    lines = text.splitlines()
    explained = {m for line in lines if line.startswith("#") for m in PLACEHOLDER.findall(line)}
    used = {m for line in lines if not line.startswith("#") for m in PLACEHOLDER.findall(line)}
    assert used and used <= explained


@pytest.mark.parametrize("name, limit", [("nginx.conf.example", "client_max_body_size 64k;"),
                                         ("apache.conf.example", "LimitRequestBody 65536")])
def test_proxy_examples(name, limit):
    text = (DEPLOY / name).read_text()
    for needle in ("@SERVER_NAME@", "http://127.0.0.1:8000", "X-Forwarded-For", "PROD_TRUSTED_PROXIES=1", limit, "443"):
        assert needle in text
