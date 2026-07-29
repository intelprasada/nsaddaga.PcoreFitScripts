#!/usr/bin/env bash
# prod-start.sh — start VegaNotes from the prod git worktree
# Companion to dev-start.sh but points at the sibling `core-tools-prod`
# worktree (pinned to the `prod` git branch).
#
# Usage: ./scripts/prod-start.sh [--backend-only | --frontend-only | --restart | --install | --sync]
#
#   (no args)        Start backend + frontend from the prod worktree
#   --backend-only   Start backend only
#   --frontend-only  Start frontend only (uses vite preview off a built dist)
#   --restart        Kill existing prod session and relaunch
#   --install        Install/refresh deps in the prod worktree (venv + npm)
#   --sync           git fetch + fast-forward prod worktree to origin/prod
#
# Ports: prod owns the shared team URL (:5173/:8000); dev moved to :4173/:8100.
#   Backend:  8000  (dev uses 8100)
#   Frontend: 5173  (vite preview; dev uses 4173)

set -euo pipefail

# ─── paths ──────────────────────────────────────────────────────────────
DEV_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# core-tools-prod is the sibling worktree of core-tools
CORE_TOOLS_ROOT="$(cd "$DEV_REPO_ROOT/.." && pwd)"                    # …/core-tools
PROD_CORE_ROOT="${PROD_CORE_ROOT:-$(cd "$CORE_TOOLS_ROOT/.." && pwd)/core-tools-prod}"
PROD_ROOT="$PROD_CORE_ROOT/VegaNotes"
DATA_DIR="${VEGANOTES_PROD_DATA_DIR:-$PROD_ROOT/.proddata}"
VENV="${PROD_VENV_PATH:-$PROD_ROOT/backend/.venv}"

BACKEND_PORT="${VEGANOTES_PROD_BACKEND_PORT:-8000}"
FRONTEND_PORT="${VEGANOTES_PROD_FRONTEND_PORT:-5173}"

BACKEND_LOG=/tmp/vega-prod-backend.log
FRONTEND_LOG=/tmp/vega-prod-frontend.log
PID_FILE=/tmp/vega-prod-pids

# ─── sanity: prod worktree must exist ───────────────────────────────────
if [[ ! -d "$PROD_ROOT" ]]; then
  echo "✗ Prod worktree not found at $PROD_ROOT" >&2
  echo "  Create it with:" >&2
  echo "    cd $CORE_TOOLS_ROOT && git worktree add ../core-tools-prod prod" >&2
  exit 1
fi

# ─── helpers ────────────────────────────────────────────────────────────
kill_old() {
  if [[ -f "$PID_FILE" ]]; then
    while IFS= read -r pid; do
      [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
    done < "$PID_FILE"
    rm -f "$PID_FILE"
  fi
  # Fallback: match by port occupancy
  local pids
  pids=$(lsof -t -i ":$BACKEND_PORT" 2>/dev/null || true)
  for pid in $pids; do kill "$pid" 2>/dev/null || true; done
  pids=$(lsof -t -i ":$FRONTEND_PORT" 2>/dev/null || true)
  for pid in $pids; do kill "$pid" 2>/dev/null || true; done
  sleep 1
  echo "  ✓ Stopped existing prod processes."
}

do_sync() {
  echo "▶ Syncing prod worktree to origin/prod..."
  cd "$PROD_CORE_ROOT"
  git fetch origin prod --quiet
  local remote_sha local_sha
  remote_sha="$(git rev-parse origin/prod)"
  local_sha="$(git rev-parse HEAD)"
  if [[ "$remote_sha" == "$local_sha" ]]; then
    echo "  ✓ Already at $local_sha"
    return 0
  fi
  if ! git merge-base --is-ancestor "$local_sha" "$remote_sha"; then
    echo "  ✗ Local prod ($local_sha) is not an ancestor of origin/prod ($remote_sha)." >&2
    echo "    Refusing to force-update. Inspect the worktree manually." >&2
    exit 1
  fi
  git reset --hard "$remote_sha"
  echo "  ✓ Fast-forwarded to $remote_sha"
}

do_install() {
  echo "▶ Installing/refreshing prod deps..."

  # Python venv
  if [[ ! -x "$VENV/bin/uvicorn" ]]; then
    echo "  · Creating venv at $VENV"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -e "$PROD_ROOT/backend"
  else
    echo "  · Refreshing backend package"
    "$VENV/bin/pip" install --quiet -e "$PROD_ROOT/backend"
  fi

  # Node deps
  echo "  · Running npm install (prod worktree)"
  (cd "$PROD_ROOT/frontend" && npm install --silent)

  # Prod build
  echo "  · Building frontend (vite build)"
  (cd "$PROD_ROOT/frontend" && npx vite build)
  echo "  ✓ Install complete"
}

start_backend() {
  if [[ ! -x "$VENV/bin/uvicorn" ]]; then
    echo "✗ $VENV/bin/uvicorn missing. Run './scripts/prod-start.sh --install' first." >&2
    exit 1
  fi
  echo "▶ Starting prod backend (data: $DATA_DIR, venv: $VENV, port: $BACKEND_PORT)..."
  mkdir -p "$DATA_DIR"
  cd "$PROD_ROOT/backend"
  : "${VEGANOTES_PHONEBOOK_SCRAPER_ENABLED:=true}"
  : "${VEGANOTES_PHONEBOOK_DEFAULT_ANCHOR:=${USER:-}}"
  export VEGANOTES_PHONEBOOK_SCRAPER_ENABLED VEGANOTES_PHONEBOOK_DEFAULT_ANCHOR
  VEGANOTES_DATA_DIR="$DATA_DIR" setsid "$VENV/bin/uvicorn" app.main:app \
    --port "$BACKEND_PORT" --log-level warning > "$BACKEND_LOG" 2>&1 < /dev/null &
  local bpid=$!
  echo "$bpid" >> "$PID_FILE"
  echo "  PID=$bpid"

  local i=0
  while (( i < 15 )); do
    sleep 1
    if curl -fsS -o /dev/null --max-time 1 "http://localhost:$BACKEND_PORT/healthz" 2>/dev/null; then
      echo "  ✓ Prod backend healthy on :$BACKEND_PORT"
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
  if [[ ! -d "$PROD_ROOT/frontend/dist" ]]; then
    echo "✗ $PROD_ROOT/frontend/dist missing. Run './scripts/prod-start.sh --install' to build." >&2
    exit 1
  fi
  echo "▶ Starting prod frontend (vite preview :$FRONTEND_PORT → API :$BACKEND_PORT)..."
  cd "$PROD_ROOT/frontend"
  # VEGA_BACKEND_PORT points the preview server's /api proxy at the prod
  # backend (not whatever is on :8000). Without it the built bundle's API
  # calls would hit the wrong instance once dev/prod share a host.
  VEGA_BACKEND_PORT="$BACKEND_PORT" VEGA_FRONTEND_PORT="$FRONTEND_PORT" \
    setsid npx vite preview --host --port "$FRONTEND_PORT" > "$FRONTEND_LOG" 2>&1 < /dev/null &
  local fpid=$!
  echo "$fpid" >> "$PID_FILE"
  echo "  PID=$fpid"
}

MODE="${1:-}"

case "$MODE" in
  --sync)      do_sync ;;
  --install)   do_install ;;
  --restart)
    echo "↺  Restarting prod VegaNotes..."
    kill_old
    start_backend
    start_frontend
    ;;
  --backend-only)  start_backend ;;
  --frontend-only) start_frontend ;;
  "")
    start_backend
    start_frontend
    ;;
  *)
    echo "Unknown option: $MODE" >&2
    grep -E "^#   " "${BASH_SOURCE[0]}" >&2
    exit 2
    ;;
esac

echo ""
echo "Prod worktree : $PROD_ROOT"
echo "Backend log   : $BACKEND_LOG"
echo "Frontend log  : $FRONTEND_LOG"
echo "Open          : http://localhost:$FRONTEND_PORT   (API: http://localhost:$BACKEND_PORT)"
