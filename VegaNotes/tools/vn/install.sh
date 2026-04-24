#!/usr/bin/env bash
# install.sh — install the `vn` CLI from this checkout, runnable from anywhere.
#
# Usage:
#   ./install.sh                 # editable install into --user site-packages
#   ./install.sh --no-editable   # regular (non-editable) install
#   ./install.sh --system        # install into the active env (no --user)
#   ./install.sh --python python3.11
#   ./install.sh --uninstall
#
# The script resolves its own location, so it works no matter where you cd to:
#   bash /abs/path/to/VegaNotes/tools/vn/install.sh
#   curl ... | bash    # NOT supported — needs the local checkout

set -euo pipefail

# Resolve the directory containing this script, even through symlinks.
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
PKG_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"

PYTHON="${PYTHON:-python3}"
EDITABLE=1
USER_FLAG="--user"
UNINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-editable) EDITABLE=0; shift ;;
    --system)      USER_FLAG="";  shift ;;
    --python)      PYTHON="$2";   shift 2 ;;
    --uninstall)   UNINSTALL=1;   shift ;;
    -h|--help)
      sed -n '2,15p' "$SOURCE" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "install.sh: unknown option: $1" >&2; exit 2 ;;
  esac
done

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "install.sh: python interpreter not found: $PYTHON" >&2
  echo "Pass --python /path/to/python3.11 (or set \$PYTHON)." >&2
  exit 1
fi

# Sanity-check Python version (need >= 3.9 to match pyproject.toml).
"$PYTHON" - <<'PY' || { echo "install.sh: $PYTHON is too old (need >= 3.9)" >&2; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY

# In a virtualenv, --user is invalid — fall back to a system-of-the-env install.
IN_VENV="$("$PYTHON" -c 'import sys; print(1 if sys.prefix != sys.base_prefix else 0)')"
if [[ "$IN_VENV" == "1" && -n "$USER_FLAG" ]]; then
  echo "install.sh: virtualenv detected — installing into it (ignoring --user)."
  USER_FLAG=""
fi

if [[ "$UNINSTALL" == "1" ]]; then
  echo "install.sh: uninstalling veganotes-vn via $PYTHON"
  exec "$PYTHON" -m pip uninstall -y veganotes-vn
fi

PIP_ARGS=()
[[ -n "$USER_FLAG" ]] && PIP_ARGS+=("$USER_FLAG")
[[ "$EDITABLE" == "1" ]] && PIP_ARGS+=("-e")
PIP_ARGS+=("$PKG_DIR")

echo "install.sh: $PYTHON -m pip install ${PIP_ARGS[*]}"
"$PYTHON" -m pip install "${PIP_ARGS[@]}"

# Locate the installed `vn` script and warn if its directory isn't on PATH.
VN_BIN="$("$PYTHON" - <<'PY'
import shutil, sysconfig, os
# Try user scheme first, then the active env's scripts dir.
candidates = []
try:
    candidates.append(sysconfig.get_path("scripts", "posix_user"))
except Exception:
    pass
candidates.append(sysconfig.get_path("scripts"))
for d in candidates:
    if d and os.path.isfile(os.path.join(d, "vn")):
        print(os.path.join(d, "vn"))
        break
else:
    print(shutil.which("vn") or "")
PY
)"

echo
if [[ -n "$VN_BIN" ]]; then
  echo "install.sh: installed -> $VN_BIN"
  BIN_DIR="$(dirname "$VN_BIN")"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "install.sh: NOTE — $BIN_DIR is not on \$PATH. Add it, e.g.:"
       echo "             export PATH=\"$BIN_DIR:\$PATH\"" ;;
  esac
  echo
  "$VN_BIN" --version || true
else
  echo "install.sh: pip succeeded but the 'vn' script could not be located." >&2
  exit 1
fi
