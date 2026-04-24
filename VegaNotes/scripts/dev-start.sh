#!/usr/bin/env bash
# dev-start.sh — start backend + frontend dev servers for VegaNotes
# Usage: ./scripts/dev-start.sh [--backend-only | --frontend-only]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$REPO_ROOT/.devdata"
VENV="${VENV_PATH:-/tmp/vega-venv}"
BACKEND_LOG=/tmp/vega-backend.log
FRONTEND_LOG=/tmp/vega-frontend.log

kill_old() {
  local pids
  pids=$(pgrep -f "uvicorn app.main:app" 2>/dev/null || true)
  for pid in $pids; do kill "$pid" 2>/dev/null || true; done
  pids=$(pgrep -f "vite.*VegaNotes" 2>/dev/null || true)
  for pid in $pids; do kill "$pid" 2>/dev/null || true; done
  sleep 1
}

start_backend() {
  echo "▶ Starting backend (data: $DATA_DIR)..."
  source "$VENV/bin/activate"
  cd "$REPO_ROOT/backend"
  VEGANOTES_DATA_DIR="$DATA_DIR" setsid uvicorn app.main:app \
    --port 8000 --log-level warning > "$BACKEND_LOG" 2>&1 &
  echo "  PID=$!"
}

start_frontend() {
  echo "▶ Starting frontend..."
  cd "$REPO_ROOT/frontend"
  # Clear stale Vite cache to avoid stale module graph errors
  rm -rf node_modules/.vite
  setsid npx vite > "$FRONTEND_LOG" 2>&1 &
  echo "  PID=$!"
}

MODE="${1:-}"
kill_old

case "$MODE" in
  --backend-only)  start_backend ;;
  --frontend-only) start_frontend ;;
  *)               start_backend; start_frontend ;;
esac

echo ""
echo "Backend log : $BACKEND_LOG"
echo "Frontend log: $FRONTEND_LOG"
echo "Done. Wait a few seconds, then open http://localhost:5173"
