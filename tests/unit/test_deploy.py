"""deploy/: serve.py's startup checks, backup.sh, the pinned requirements, and the service and proxy files.
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
DB = {"PROD_DB": "prod.sqlite"}


def test_serve_refuses_without_prod_db():
    for env in ({}, {"PROD_DB": "  "}):
        with pytest.raises(SystemExit, match="PROD_DB"):
            serve.config(env)


def test_serve_refuses_debug():
    with pytest.raises(SystemExit, match="FLASK_DEBUG"):
        serve.config({**DB, "FLASK_DEBUG": "1"})
    assert serve.config({**DB, "FLASK_DEBUG": "0"})


def test_serve_config():
    cfg = serve.config(DB)
    assert (cfg["host"], cfg["port"]) == ("127.0.0.1", 8000)
    assert cfg["threads"] > 1
    assert cfg["clear_untrusted_proxy_headers"] is False  # the app reads X-Forwarded-For itself
    cfg = serve.config({**DB, "PROD_HOST": "::1", "PROD_PORT": "9000"})
    assert (cfg["host"], cfg["port"]) == ("::1", 9000)
    for bad in ({"PROD_PORT": "http"}, {"PROD_TRUSTED_PROXIES": "one"}):
        with pytest.raises(SystemExit, match="whole numbers"):
            serve.config({**DB, **bad})


def test_serve_trusts_proxies_only_on_loopback():
    for host in ("127.0.0.1", "::1", "localhost"):
        assert serve.config({**DB, "PROD_TRUSTED_PROXIES": "1", "PROD_HOST": host})
    for host in ("0.0.0.0", "10.1.2.3", "example.edu"):
        with pytest.raises(SystemExit, match="not loopback"):
            serve.config({**DB, "PROD_TRUSTED_PROXIES": "1", "PROD_HOST": host})
    assert serve.config({**DB, "PROD_HOST": "0.0.0.0"})  # no proxies trusted: X-Forwarded-For is ignored


def test_serve_config_is_valid_for_waitress():
    adjustments = pytest.importorskip("waitress.adjustments")
    adjustments.Adjustments(**serve.config(DB))


def test_check_public_refuses_admin_routes():
    flask = pytest.importorskip("flask")
    for rule in ("/run", "/run/<run_id>", "/health", "/review/export"):
        app = flask.Flask("t")
        app.add_url_rule("/knowledge", "k", lambda: "")
        app.add_url_rule(rule, "admin", lambda: "")
        with pytest.raises(SystemExit, match="APP_MODE=public"):
            serve.check_public(app)
    app = flask.Flask("t")
    app.add_url_rule("/knowledge", "k", lambda: "")
    app.add_url_rule("/request", "r", lambda: "")
    serve.check_public(app)


def test_load_app_sets_public_mode_and_checks_it(monkeypatch):
    """Refuses today (the app ignores APP_MODE); once public mode lands it returns an app without admin routes."""
    pytest.importorskip("flask")
    monkeypatch.setenv("APP_MODE", "")
    monkeypatch.setattr(sys, "path", list(sys.path))
    try:
        app = serve.load_app()
    except SystemExit as e:
        assert "APP_MODE=public" in str(e)
    else:
        assert not app.debug
        assert not [r for r in app.url_map.iter_rules() if r.rule.startswith(serve.ADMIN_ROUTES)]
    assert serve.os.environ["APP_MODE"] == "public"


@pytest.mark.skipif(not shutil.which("bash") or not shutil.which("python3"), reason="needs bash and python3")
def test_backup_copies_and_prunes(tmp_path):
    root = tmp_path / "it's here"  # a quote in the path must not reach a command string
    root.mkdir()
    db = root / "prod.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()  # left open: the backup runs against a database in use
    nightly = root / "backups" / "nightly"
    nightly.mkdir(parents=True)
    old = [nightly / f"prod-2020{m:02d}{d:02d}.sqlite" for m in (1, 2) for d in range(1, 19)]  # 36
    for p in old:
        p.write_bytes(b"")
    env = {"PROD_DB": str(db), "PROD_BACKUP_DIR": str(root / "backups"), "PATH": "/usr/bin:/bin:/usr/local/bin"}
    res = subprocess.run([shutil.which("bash"), str(DEPLOY / "backup.sh")], env=env, capture_output=True, text=True,
                         timeout=60)
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


def test_requirements_pinned_with_hashes():
    text = (DEPLOY / "requirements.txt").read_text()
    pinned = {m.lower() for m in re.findall(r"^([A-Za-z0-9_.-]+)==\S+", text, re.M)}
    assert {"flask", "waitress", "openpyxl", "beautifulsoup4", "requests", "pypdf", "pyyaml"} <= pinned
    for block in re.split(r"\n(?=\S)", text):
        if re.match(r"[A-Za-z0-9_.-]+==", block):
            assert "--hash=sha256:" in block, block.splitlines()[0]


@pytest.mark.skipif(not shutil.which("uv"), reason="needs uv")
def test_require_hashes_rejects_a_bad_hash(tmp_path):
    """The README's `uv pip sync --require-hashes`, on one small package: the real hashes install, a corrupted one
    is refused. Skipped when pypi can't be reached."""
    text = (DEPLOY / "requirements.txt").read_text()
    block = next(b for b in re.split(r"\n(?=\S)", text) if b.startswith("blinker=="))
    good, bad = tmp_path / "good.txt", tmp_path / "bad.txt"
    good.write_text(block + "\n")
    bad.write_text(re.sub(r"sha256:[0-9a-f]{64}", "sha256:" + "0" * 64, block) + "\n")
    env = {**serve.os.environ, "UV_CACHE_DIR": str(tmp_path / "cache"), "UV_NO_CONFIG": "1"}

    def sync(name: str, reqs: Path) -> subprocess.CompletedProcess:
        venv = tmp_path / name
        subprocess.run(["uv", "venv", "-q", "--python", sys.executable, str(venv)], env=env, check=True, timeout=60)
        return subprocess.run(["uv", "pip", "sync", "--require-hashes", "--python", str(venv / "bin" / "python"),
                               str(reqs)], env=env, capture_output=True, text=True, timeout=120)

    ok = sync("good", good)
    if ok.returncode:
        pytest.skip(f"offline or pypi unreachable: {ok.stderr.strip()[-200:]}")
    res = sync("bad", bad)
    assert res.returncode != 0 and "Hash mismatch" in res.stderr


def test_service_template():
    text = (DEPLOY / "prod-public.service").read_text()
    for needle in ("User=@USER@", "WorkingDirectory=@REPO@", "EnvironmentFile=@ENV_FILE@", "Restart=on-failure",
                   "StartLimitIntervalSec=300", "StartLimitBurst=5", "UMask=0027",
                   "NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=true",
                   "ReadWritePaths=@DB_DIR@ @BACKUP_DIR@ @REPO@/docs/okf/knowledge",
                   "ExecStart=@VENV@/bin/python @REPO@/deploy/serve.py"):
        assert needle in text
    assert "uv run" not in text and "UV_CACHE_DIR" not in text and "CacheDirectory" not in text
    assert "@VENV@" not in text.split("ReadWritePaths=")[1].splitlines()[0]  # the venv stays read-only
    lines = text.splitlines()
    explained = {m for line in lines if line.startswith("#") for m in PLACEHOLDER.findall(line)}
    used = {m for line in lines if not line.startswith("#") for m in PLACEHOLDER.findall(line)}
    assert used and used <= explained


@pytest.mark.parametrize("name, needles", [
    ("nginx.conf.example", ("client_max_body_size 64k;", "https://@SERVER_NAME@$request_uri", "return 404;",
                            "location /api/", "location = /request", "location /knowledge/", "location /static/")),
    ("apache.conf.example", ("LimitRequestBody 65536", "ProxyRequests Off", "Require all denied",
                             "api/|request$|knowledge(/|$)|static/")),
])
def test_proxy_examples(name, needles):
    text = (DEPLOY / name).read_text()
    for needle in ("@SERVER_NAME@", "http://127.0.0.1:8000", "X-Forwarded-For", "PROD_TRUSTED_PROXIES=1", "443",
                   *needles):
        assert needle in text
