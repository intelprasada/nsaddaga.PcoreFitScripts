#!/usr/bin/env bash
# dev-start.sh — start backend + frontend dev servers for VegaNotes
# Usage: ./scripts/dev-start.sh [--backend-only | --frontend-only | --restart]
#
#   (no args)        Start both backend and frontend (errors if already running)
#   --backend-only   Start backend only
#   --frontend-only  Start frontend only
#   --restart        Kill any existing backend/frontend session and relaunch both

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$REPO_ROOT/.devdata"
VENV="${VENV_PATH:-/tmp/vega-venv}"
BACKEND_LOG=/tmp/vega-backend.log
FRONTEND_LOG=/tmp/vega-frontend.log
PID_FILE=/tmp/vega-pids

kill_old() {
  local pids

  # Kill by saved PIDs first (fastest, avoids false matches).
  if [[ -f "$PID_FILE" ]]; then
    while IFS= read -r pid; do
      [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
    done < "$PID_FILE"
    rm -f "$PID_FILE"
  fi

  # Fallback: pattern-match in case pids file is stale.
  pids=$(pgrep -f "uvicorn app.main:app" 2>/dev/null || true)
  for pid in $pids; do kill "$pid" 2>/dev/null || true; done
  pids=$(pgrep -f "vite" 2>/dev/null || true)
  for pid in $pids; do kill "$pid" 2>/dev/null || true; done

  sleep 1
  echo "  ✓ Stopped existing processes."
}

start_backend() {
  echo "▶ Starting backend (data: $DATA_DIR)..."
  source "$VENV/bin/activate"
  cd "$REPO_ROOT/backend"
  VEGANOTES_DATA_DIR="$DATA_DIR" setsid uvicorn app.main:app \
    --port 8000 --log-level warning > "$BACKEND_LOG" 2>&1 &
  local bpid=$!
  echo "$bpid" >> "$PID_FILE"
  echo "  PID=$bpid"
}

start_frontend() {
  echo "▶ Starting frontend..."
  cd "$REPO_ROOT/frontend"
  # Clear stale Vite cache to avoid stale module graph errors
  rm -rf node_modules/.vite
  setsid npx vite --host > "$FRONTEND_LOG" 2>&1 &
  local fpid=$!
  echo "$fpid" >> "$PID_FILE"
  echo "  PID=$fpid"
}

MODE="${1:-}"

case "$MODE" in
  --restart)
    echo "↺  Restarting VegaNotes..."
    kill_old
    start_backend
    start_frontend
    ;;
  --backend-only)
    start_backend
    ;;
  --frontend-only)
    start_frontend
    ;;
  *)
    start_backend
    start_frontend
    ;;
esac

echo ""
echo "Backend log : $BACKEND_LOG"
echo "Frontend log: $FRONTEND_LOG"
echo "Done. Wait a few seconds, then open http://localhost:5173"
