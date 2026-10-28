#!/usr/bin/env bash
# Starts the Purdue Competitive Analysis web app (webapp/app.py).
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPS=(--with flask --with openpyxl --with beautifulsoup4 --with requests --with pypdf --with pyyaml)

if command -v uv >/dev/null 2>&1; then
  exec uv --directory "$ROOT" run "${DEPS[@]}" python webapp/app.py "$@"
fi

echo "run-webapp: uv not found on PATH; install it (https://docs.astral.sh/uv/) or run:" >&2
echo "  pip install flask openpyxl beautifulsoup4 requests pypdf pyyaml && python webapp/app.py" >&2
exit 1
