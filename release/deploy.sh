#!/usr/bin/env bash
# release/deploy.sh – Copy the release tarball to the shared install location
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../lib/shell/common.sh
source "${REPO_ROOT}/lib/shell/common.sh"

VERSION="${1:-$(cat "${REPO_ROOT}/VERSION")}"
ARCHIVE_NAME="core-tools-v${VERSION}.tar.gz"
ARCHIVE_PATH="${SCRIPT_DIR}/${ARCHIVE_NAME}"

# Configure the shared install location here (or export INSTALL_DIR from your environment)
INSTALL_DIR="${INSTALL_DIR:-/opt/core-tools}"

[ -f "${ARCHIVE_PATH}" ] || die "Release tarball not found: ${ARCHIVE_PATH}. Run 'make release' first."

log_info "Deploying ${ARCHIVE_NAME} to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
tar -xzf "${ARCHIVE_PATH}" -C "${INSTALL_DIR}"
log_info "Deployment complete."
