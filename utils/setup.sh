#!/usr/bin/env bash
# setup.sh — Register core-tools aliases and add bin/ to PATH.
#
# Run this once from inside your cloned repo:
#   bash utils/setup.sh
#   bash utils/setup.sh --aliases ~/.aliases
#
# Writes into your aliases file (csh/tcsh):
#   setenv CORE_TOOLS_DIR /path/to/core-tools
#   source $CORE_TOOLS_DIR/utils/aliases.csh
#   setenv PATH ${CORE_TOOLS_DIR}/bin:${PATH}
#
# Or for bash/zsh:
#   export CORE_TOOLS_DIR="/path/to/core-tools"
#   source "$CORE_TOOLS_DIR/utils/aliases.sh"
#   export PATH="$CORE_TOOLS_DIR/bin:$PATH"
#
# Aliases defined:
#   is     ->  bin/interfacespec
#   sc     ->  bin/supercsv
#   st     ->  bin/supertracker
#   email  ->  bin/email-sender
#
# Idempotent: safe to run multiple times — already-present steps are skipped.

set -euo pipefail

# Resolve repo root from this script's own location (utils/../)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TOOL_ALIASES=(is sc st email)

# ─── Colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; RESET='\033[0m'
info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
skip()  { echo -e "${YELLOW}[SKIP]${RESET}  $*"; }
warn()  { echo -e "${RED}[WARN]${RESET}  $*"; }

# ─── Argument parsing ─────────────────────────────────────────────────────────
ALIASES_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --aliases)  ALIASES_FILE="${2:-}"; shift 2 ;;
        -h|--help)
            sed -n '2,20p' "$0" | sed 's/^# *//'
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ─── Detect aliases file ──────────────────────────────────────────────────────
detect_aliases_file() {
    # Prefer dedicated aliases files; fall back to shell rc
    if   [[ -f "${HOME}/.aliases" ]];       then echo "${HOME}/.aliases"
    elif [[ -f "${HOME}/.bash_aliases" ]];  then echo "${HOME}/.bash_aliases"
    elif [[ "${SHELL:-}" == */zsh ]] && [[ -f "${HOME}/.zshrc" ]]; then echo "${HOME}/.zshrc"
    elif [[ -f "${HOME}/.bashrc" ]];        then echo "${HOME}/.bashrc"
    else echo "${HOME}/.bash_aliases"
    fi
}

[[ -z "$ALIASES_FILE" ]] && ALIASES_FILE="$(detect_aliases_file)"

# Detect csh/tcsh by looking for csh-specific syntax in the file
is_csh() {
    grep -qE "^setenv |^alias [a-zA-Z_-]+ '" "$1" 2>/dev/null
}

# ─── Header ───────────────────────────────────────────────────────────────────
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  core-tools setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo
info "Repo root  : ${REPO_ROOT}"
info "Alias file : ${ALIASES_FILE}"
echo

# ─── Step 1: Ensure aliases file exists ──────────────────────────────────────
if [[ ! -f "${ALIASES_FILE}" ]]; then
    touch "${ALIASES_FILE}"
    info "Created ${ALIASES_FILE}"
fi

# Wire ~/.bash_aliases into ~/.bashrc if needed (bash only)
if [[ "${ALIASES_FILE}" == "${HOME}/.bash_aliases" && -f "${HOME}/.bashrc" ]]; then
    if ! grep -q '\.bash_aliases' "${HOME}/.bashrc"; then
        printf '\n# Source alias definitions\nif [ -f ~/.bash_aliases ]; then\n    . ~/.bash_aliases\nfi\n' \
            >> "${HOME}/.bashrc"
        info "Wired ~/.bash_aliases into ~/.bashrc"
    fi
fi

# ─── Step 2: Warn about conflicting existing aliases ─────────────────────────
echo
CONFLICTS=()
for name in "${TOOL_ALIASES[@]}"; do
    if grep -qE "^alias[[:space:]]+${name}([[:space:]]|=)" "${ALIASES_FILE}" 2>/dev/null; then
        CONFLICTS+=("$name")
    fi
done

if [[ ${#CONFLICTS[@]} -gt 0 ]]; then
    warn "The following aliases are already defined in ${ALIASES_FILE}:"
    for name in "${CONFLICTS[@]}"; do
        echo "    $(grep -E "^alias[[:space:]]+${name}([[:space:]]|=)" "${ALIASES_FILE}")"
    done
    echo
    warn "These will be shadowed by the core-tools definitions once sourced."
    warn "Consider removing or commenting them out to avoid conflicts."
    echo
fi

# ─── Step 3: Fix exec permissions on bin/ tools ──────────────────────────────
echo
info "Checking exec permissions on bin/ tools ..."
FIXED=0
MISSING=0
for f in "${REPO_ROOT}/bin"/*; do
    [[ -f "$f" ]] || continue
    if [[ ! -x "$f" ]]; then
        chmod +x "$f"
        ok "Fixed: $(basename "$f") — set executable"
        (( FIXED++ )) || true
    else
        (( MISSING++ )) || true
    fi
done
if [[ $FIXED -eq 0 ]]; then
    skip "All bin/ tools already have exec permissions."
fi
echo

# ─── Step 4: Install Python dependencies ─────────────────────────────────────
# Maps PyPI package names to their Python import module names (where they differ)
_pkg_to_module() {
    case "$(echo "$1" | tr '[:upper:]' '[:lower:]')" in
        pyyaml)       echo yaml ;;
        pillow)       echo PIL ;;
        scikit-learn) echo sklearn ;;
        opencv-python) echo cv2 ;;
        beautifulsoup4) echo bs4 ;;
        *)            echo "${1,,}" | tr '-' '_' ;;
    esac
}

# Returns 0 if every real package in a requirements file is already importable
_reqs_importable() {
    local req="$1"
    while IFS= read -r line; do
        local pkg mod
        pkg="$(echo "$line" | sed 's/[><=!;[:space:]\[].*//')"
        mod="$(_pkg_to_module "$pkg")"
        "$PY3" -c "import ${mod}" 2>/dev/null || return 1
    done < <(grep -vE '^\s*#|^\s*$' "$req")
    return 0
}

echo
info "Installing Python dependencies for all tools ..."
PY3="$(command -v python3 2>/dev/null || true)"
if [[ -z "$PY3" ]]; then
    warn "python3 not found — skipping dependency install."
else
    for req in "${REPO_ROOT}/tools"/*/requirements.txt; do
        tool_name="$(basename "$(dirname "$req")")"
        pkgs="$(grep -vE '^\s*#|^\s*$' "$req" 2>/dev/null || true)"
        if [[ -z "$pkgs" ]]; then
            skip "${tool_name}: no external Python dependencies."
            continue
        fi
        info "${tool_name}: checking dependencies ..."
        if "$PY3" -m pip install --user -q --disable-pip-version-check -r "$req" 2>/dev/null; then
            ok "${tool_name}: dependencies satisfied."
        else
            # pip failed (network/proxy timeout) — check if packages are already importable
            if _reqs_importable "$req"; then
                skip "${tool_name}: packages already importable — pip skipped (no network needed)."
            else
                warn "${tool_name}: pip install failed AND packages are not importable."
                warn "  If behind a proxy, retry with:"
                warn "    pip install --user -r ${req} \\"
                warn "      --proxy \"\${HTTPS_PROXY:-\${https_proxy}}\" \\"
                warn "      --trusted-host pypi.org --trusted-host files.pythonhosted.org"
            fi
        fi
    done
fi
echo

# ─── Step 5: Configure shell aliases ─────────────────────────────────────────
MARKER="CORE_TOOLS_DIR"   # present in both csh and bash blocks

if grep -q "${MARKER}" "${ALIASES_FILE}" 2>/dev/null; then
    skip "core-tools already configured in ${ALIASES_FILE} — skipping."
else
    if is_csh "${ALIASES_FILE}"; then
        # csh/tcsh format
        info "Detected csh/tcsh aliases file — writing csh source block."
        printf '\n# core-tools (is / sc / st / email)\nsetenv CORE_TOOLS_DIR "%s"\nsource $CORE_TOOLS_DIR/utils/aliases.csh\nsetenv PATH ${CORE_TOOLS_DIR}/bin:${PATH}\n' \
            "${REPO_ROOT}" >> "${ALIASES_FILE}"
        RELOAD_CMD="source ${ALIASES_FILE}"
    else
        # bash/zsh format
        info "Detected bash/zsh aliases file — writing bash source block."
        printf '\n# core-tools (is / sc / st / email)\nexport CORE_TOOLS_DIR="%s"\nsource "$CORE_TOOLS_DIR/utils/aliases.sh"\nexport PATH="$CORE_TOOLS_DIR/bin:$PATH"\n' \
            "${REPO_ROOT}" >> "${ALIASES_FILE}"
        RELOAD_CMD="source ${ALIASES_FILE}"
    fi
    ok "Added to ${ALIASES_FILE}."
fi

# ─── Step 6: Smoke-test tools ─────────────────────────────────────────────────
echo
info "Smoke-testing tools via bin/ wrappers (--help) ..."
SMOKE_TOOLS=(interfacespec supercsv supertracker email-sender)
SMOKE_FAILED=0
for tool in "${SMOKE_TOOLS[@]}"; do
    wrapper="${REPO_ROOT}/bin/${tool}"
    if [[ ! -f "$wrapper" ]]; then
        warn "${tool}: bin wrapper not found — skipping."
        (( SMOKE_FAILED++ )) || true
        continue
    fi
    if bash "$wrapper" --help >/dev/null 2>&1; then
        ok "${tool}: imports and --help passed."
    else
        warn "${tool}: smoke test FAILED — run 'bash ${wrapper} --help' to debug."
        (( SMOKE_FAILED++ )) || true
    fi
done
if [[ $SMOKE_FAILED -eq 0 ]]; then
    ok "All tools passed smoke test."
fi
echo

# ─── Done ─────────────────────────────────────────────────────────────────────
echo
echo -e "${GREEN}Setup complete.${RESET} Activate now with:"
echo
echo "    ${RELOAD_CMD}"
echo
