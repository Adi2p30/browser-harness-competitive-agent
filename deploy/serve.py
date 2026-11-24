"""Serve webapp/app.py with waitress behind an HTTPS reverse proxy. See deploy/README.md.

    uv run --no-project --with waitress --with flask --with openpyxl --with beautifulsoup4 --with requests \\
        --with pypdf --with pyyaml python deploy/serve.py

PROD_HOST and PROD_PORT pick the address (default 127.0.0.1:8000). One process: the request limiter and the rebuild
lock in webapp/api.py live in memory.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THREADS = 4
OFF = ("", "0", "false", "no", "off")


def config(env=os.environ) -> dict:
    """waitress settings from the environment. Raises SystemExit when PROD_DB is unset or Flask debug is on."""
    if not env.get("PROD_DB", "").strip():
        raise SystemExit("serve: PROD_DB is not set")
    if env.get("FLASK_DEBUG", "").strip().lower() not in OFF:
        raise SystemExit("serve: FLASK_DEBUG is on; the debugger must never be served")
    try:
        port = int(env.get("PROD_PORT") or 8000)
    except ValueError:
        raise SystemExit(f"serve: PROD_PORT is not a number: {env['PROD_PORT']!r}") from None
    # waitress drops X-Forwarded-For by default; webapp/api.py reads it only with PROD_TRUSTED_PROXIES set.
    return {"host": env.get("PROD_HOST") or "127.0.0.1", "port": port, "threads": THREADS,
            "url_scheme": "https", "clear_untrusted_proxy_headers": False}


def load_app():
    """webapp/app.py's app, imported in public mode. Raises SystemExit if debug is on."""
    os.environ["APP_MODE"] = "public"
    sys.path.insert(0, str(ROOT / "webapp"))
    from app import app
    if app.debug:
        raise SystemExit("serve: the app has debug on; the debugger must never be served")
    return app


def main() -> None:
    settings = config()
    app = load_app()
    from waitress import serve
    serve(app, **settings)


if __name__ == "__main__":
    main()
