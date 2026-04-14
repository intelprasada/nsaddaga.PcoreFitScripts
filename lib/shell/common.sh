#!/usr/bin/env bash
# common.sh – Shared shell utility functions for core-tools

# Print an informational message with a timestamp
log_info() {
    echo "[INFO]  $(date -u '+%Y-%m-%dT%H:%M:%SZ') $*"
}

# Print an error message and exit with status 1
die() {
    echo "[ERROR] $(date -u '+%Y-%m-%dT%H:%M:%SZ') $*" >&2
    exit 1
}

# Require a command to be available on PATH
require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}
