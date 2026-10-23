#!/usr/bin/env bash
# Build frontend + backend, then start the API (serves web/dist at :8000).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

echo "==> Backend: Python venv + dependencies"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "==> Backend: verify app import"
PYTHONPATH=src python -c "from adapters.http.app import app; print(f'    FastAPI {app.title} v{app.version}')"

echo "==> Frontend: npm install + production build"
if [[ ! -d web/node_modules ]]; then
  (cd web && npm install)
else
  (cd web && npm install --prefer-offline --no-audit --no-fund)
fi
(cd web && npm run build)

if [[ ! -f web/dist/index.html ]]; then
  echo "error: web/dist/index.html missing after build" >&2
  exit 1
fi

# Free the port if a previous uvicorn is still bound.
if command -v lsof >/dev/null 2>&1; then
  OLD_PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${OLD_PIDS}" ]]; then
    echo "==> Stopping process(es) on :$PORT ($OLD_PIDS)"
    # shellcheck disable=SC2086
    kill $OLD_PIDS 2>/dev/null || true
    sleep 1
  fi
fi

echo
echo "Build complete. Starting API on http://${HOST}:${PORT}/"
echo "  Debug mode: http://${HOST}:${PORT}/?debug=1"
echo "  Stop with Ctrl+C"
echo

export PYTHONPATH=src
exec uvicorn adapters.http.app:app --reload --host "$HOST" --port "$PORT"
