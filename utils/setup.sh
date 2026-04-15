#!/usr/bin/env bash
# setup.sh — Clone core-tools and wire its aliases into the user's shell.
#
# Supports both csh/tcsh (.aliases, .cshrc) and bash/zsh (.bash_aliases, .bashrc, .zshrc).
# Auto-detects the shell flavour from the target aliases file.
#
# For csh/tcsh, adds to your aliases file:
#   setenv CORE_TOOLS_DIR /path/to/core-tools
#   source $CORE_TOOLS_DIR/aliases.csh
#
# For bash/zsh, adds:
#   source "/path/to/core-tools/aliases.sh"
#
# Aliases defined:
#   is             →  bin/interfacespec
#   sc             →  bin/supercsv
#   st             →  bin/supertracker
#   email          →  bin/email-sender
#
# Usage:
#   bash setup.sh                         # clone to ~/core-tools, auto-detect alias file
#   bash setup.sh --dir /my/path          # clone to a custom directory
#   bash setup.sh --aliases ~/.aliases    # target a specific aliases file
#   bash setup.sh --dir /my/path --aliases ~/.aliases
#
# Idempotent: safe to run multiple times — already-present steps are skipped.

set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────
REPO_URL="https://github.com/intelprasada/nsaddaga.PcoreFitScripts.git"
DEFAULT_CLONE_DIR="${HOME}/core-tools"
TOOL_ALIASES=(is sc st email)

# ─── Colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; RESET='\033[0m'
info()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
skip()  { echo -e "${YELLOW}[SKIP]${RESET}  $*"; }
warn()  { echo -e "${RED}[WARN]${RESET}  $*"; }

# ─── Argument parsing ─────────────────────────────────────────────────────────
CLONE_DIR=""
ALIASES_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)      CLONE_DIR="${2:-}";    shift 2 ;;
        --aliases)  ALIASES_FILE="${2:-}"; shift 2 ;;
        -h|--help)
            sed -n '2,22p' "$0" | sed 's/^# *//'
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

[[ -z "$CLONE_DIR" ]] && CLONE_DIR="$DEFAULT_CLONE_DIR"

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
info "Repository : ${REPO_URL}"
info "Clone dir  : ${CLONE_DIR}"
info "Alias file : ${ALIASES_FILE}"
echo

# ─── Step 1: Clone the repository ────────────────────────────────────────────
if [[ -d "${CLONE_DIR}/.git" ]]; then
    EXISTING_REMOTE=$(git -C "${CLONE_DIR}" remote get-url origin 2>/dev/null || true)
    if [[ "$EXISTING_REMOTE" == "$REPO_URL" ]]; then
        skip "Repo already cloned at ${CLONE_DIR} — skipping clone."
    else
        echo "ERROR: ${CLONE_DIR} exists but points to a different remote:" >&2
        echo "       ${EXISTING_REMOTE}" >&2
        echo "       Use --dir to specify a different location." >&2
        exit 1
    fi
elif [[ -e "${CLONE_DIR}" ]]; then
    echo "ERROR: ${CLONE_DIR} exists but is not a git repository." >&2
    echo "       Use --dir to specify a different location." >&2
    exit 1
else
    info "Cloning ${REPO_URL} → ${CLONE_DIR} ..."
    git clone "${REPO_URL}" "${CLONE_DIR}"
    ok "Cloned successfully."
fi

# ─── Step 2: Ensure aliases file exists ──────────────────────────────────────
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

# ─── Step 3: Warn about conflicting existing aliases ─────────────────────────
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

# ─── Step 4: Add source block (once) ─────────────────────────────────────────
MARKER="CORE_TOOLS_DIR"   # present in both csh and bash blocks

if grep -q "${MARKER}" "${ALIASES_FILE}" 2>/dev/null; then
    skip "core-tools already configured in ${ALIASES_FILE} — skipping."
else
    if is_csh "${ALIASES_FILE}"; then
        # csh/tcsh format
        info "Detected csh/tcsh aliases file — writing csh source block."
        printf '\n# core-tools (is / sc / st / email)\nsetenv CORE_TOOLS_DIR "%s"\nsource $CORE_TOOLS_DIR/aliases.csh\n' \
            "${CLONE_DIR}" >> "${ALIASES_FILE}"
        RELOAD_CMD="source ${ALIASES_FILE}"
    else
        # bash/zsh format
        info "Detected bash/zsh aliases file — writing bash source block."
        printf '\n# core-tools (is / sc / st / email)\nexport CORE_TOOLS_DIR="%s"\nsource "$CORE_TOOLS_DIR/aliases.sh"\n' \
            "${CLONE_DIR}" >> "${ALIASES_FILE}"
        RELOAD_CMD="source ${ALIASES_FILE}"
    fi
    ok "Added to ${ALIASES_FILE}."
fi

# ─── Done ─────────────────────────────────────────────────────────────────────
echo
echo -e "${GREEN}Setup complete.${RESET} Activate now with:"
echo
echo "    ${RELOAD_CMD}"
echo
