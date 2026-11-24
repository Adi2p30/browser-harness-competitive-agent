"""Serve webapp/app.py with waitress behind an HTTPS reverse proxy. See deploy/README.md.

    uv run --no-project --with-requirements deploy/requirements.txt python deploy/serve.py

PROD_HOST and PROD_PORT pick the address (default 127.0.0.1:8000). One process: the request limiter and the rebuild
lock in webapp/api.py live in memory.
"""
import ipaddress
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THREADS = 4
OFF = ("", "0", "false", "no", "off")
ADMIN_ROUTES = ("/run", "/health", "/review")  # no login; public mode must remove them


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def config(env=os.environ) -> dict:
    """waitress settings from the environment. Raises SystemExit naming the problem when PROD_DB is unset, Flask
    debug is on, or X-Forwarded-For would be trusted on a non-loopback address."""
    if not env.get("PROD_DB", "").strip():
        raise SystemExit("serve: PROD_DB is not set")
    if env.get("FLASK_DEBUG", "").strip().lower() not in OFF:
        raise SystemExit("serve: FLASK_DEBUG is on; the debugger must never be served")
    try:
        port = int(env.get("PROD_PORT") or 8000)
        proxies = int(env.get("PROD_TRUSTED_PROXIES") or 0)
    except ValueError:
        raise SystemExit("serve: PROD_PORT and PROD_TRUSTED_PROXIES must be whole numbers") from None
    host = env.get("PROD_HOST") or "127.0.0.1"
    if proxies > 0 and not _loopback(host):
        raise SystemExit(f"serve: PROD_TRUSTED_PROXIES is set but PROD_HOST {host} is not loopback; clients could "
                         "reach waitress directly and forge X-Forwarded-For")
    # waitress drops X-Forwarded-For by default; webapp/api.py reads it only with PROD_TRUSTED_PROXIES set.
    return {"host": host, "port": port, "threads": THREADS, "url_scheme": "https",
            "clear_untrusted_proxy_headers": False}


def check_public(app) -> None:
    """Raises SystemExit while the app still has a login-free admin route."""
    admin = sorted({r.rule for r in app.url_map.iter_rules() if r.rule.startswith(ADMIN_ROUTES)})
    if admin:
        raise SystemExit("serve: the app still serves admin routes without a login (" + ", ".join(admin) + "). "
                         "It must honour APP_MODE=public (Step 8) before it is hosted.")


def load_app():
    """webapp/app.py's app, imported in public mode. Raises SystemExit if debug is on or admin routes remain."""
    os.environ["APP_MODE"] = "public"
    sys.path.insert(0, str(ROOT / "webapp"))
    from app import app
    if app.debug:
        raise SystemExit("serve: the app has debug on; the debugger must never be served")
    check_public(app)
    return app


def main() -> None:
    settings = config()
    app = load_app()
    from waitress import serve
    serve(app, **settings)


if __name__ == "__main__":
    main()
