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
# Prefer the project-local venv (backend/.venv); fall back to legacy
# /tmp/vega-venv only if it exists. Override with VENV_PATH=... if needed.
# Hard-coding /tmp was unreliable on shared dev hosts (issue #202).
if [[ -n "${VENV_PATH:-}" ]]; then
  VENV="$VENV_PATH"
elif [[ -x "$REPO_ROOT/backend/.venv/bin/uvicorn" ]]; then
  VENV="$REPO_ROOT/backend/.venv"
elif [[ -x "/tmp/vega-venv/bin/uvicorn" ]]; then
  VENV="/tmp/vega-venv"
else
  echo "✗ No usable venv found. Looked at: backend/.venv, /tmp/vega-venv." >&2
  echo "  Set VENV_PATH=/path/to/venv or run: python -m venv backend/.venv && backend/.venv/bin/pip install -e backend" >&2
  exit 1
fi
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
  echo "▶ Starting backend (data: $DATA_DIR, venv: $VENV)..."
  if [[ ! -x "$VENV/bin/uvicorn" ]]; then
    echo "  ✗ $VENV/bin/uvicorn missing or not executable. Aborting." >&2
    exit 1
  fi
  cd "$REPO_ROOT/backend"
  # Run uvicorn directly from the venv (no `source activate`) so a broken
  # site-packages can't poison this shell. Redirect stdin so setsid+nohup
  # don't trip the Node/Python ResetStdio assertion (issue #202).
  VEGANOTES_DATA_DIR="$DATA_DIR" setsid "$VENV/bin/uvicorn" app.main:app \
    --port 8000 --log-level warning > "$BACKEND_LOG" 2>&1 < /dev/null &
  local bpid=$!
  echo "$bpid" >> "$PID_FILE"
  echo "  PID=$bpid"

  # Health-check: poll /healthz for up to 15s. Surface failures loudly
  # instead of letting the script claim success while the backend is dead.
  local i=0
  while (( i < 15 )); do
    sleep 1
    if curl -fsS -o /dev/null --max-time 1 http://localhost:8000/healthz 2>/dev/null; then
      echo "  ✓ Backend healthy on :8000"
      return 0
    fi
    if ! kill -0 "$bpid" 2>/dev/null; then
      echo "  ✗ Backend process $bpid died. Last 20 lines of $BACKEND_LOG:" >&2
      tail -20 "$BACKEND_LOG" >&2
      exit 1
    fi
    i=$((i+1))
  done
  echo "  ✗ Backend didn't become healthy within 15s. See $BACKEND_LOG." >&2
  exit 1
}

start_frontend() {
  echo "▶ Starting frontend..."
  cd "$REPO_ROOT/frontend"
  # Clear stale Vite cache to avoid stale module graph errors
  rm -rf node_modules/.vite
  # Redirect stdin so Node doesn't crash with a ResetStdio assertion when
  # the parent shell exits or doesn't have a TTY (issue #202).
  setsid npx vite --host > "$FRONTEND_LOG" 2>&1 < /dev/null &
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
