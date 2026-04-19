#!/usr/bin/env bash
# Run backend + frontend in dev mode.
set -euo pipefail
cd "$(dirname "$0")/.."

export VEGANOTES_DATA_DIR="${VEGANOTES_DATA_DIR:-$PWD/.devdata}"
export VEGANOTES_SERVE_STATIC=false
mkdir -p "$VEGANOTES_DATA_DIR/notes"

(cd backend && uvicorn app.main:app --reload --port 8000) &
BACK=$!
(cd frontend && pnpm dev) &
FRONT=$!

trap "kill $BACK $FRONT 2>/dev/null || true" EXIT
wait
