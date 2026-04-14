#!/usr/bin/env bash
# release/build.sh – Package the repository into a versioned tarball
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../lib/shell/common.sh
source "${REPO_ROOT}/lib/shell/common.sh"

VERSION="${1:-$(cat "${REPO_ROOT}/VERSION")}"
ARCHIVE_NAME="core-tools-v${VERSION}.tar.gz"
ARCHIVE_PATH="${SCRIPT_DIR}/${ARCHIVE_NAME}"

log_info "Building release tarball for version ${VERSION}..."

cd "${REPO_ROOT}"
tar --exclude='.git' \
    --exclude='release/*.tar.gz' \
    --exclude='release/*.zip' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -czf "${ARCHIVE_PATH}" \
    .

log_info "Release tarball created: ${ARCHIVE_PATH}"
