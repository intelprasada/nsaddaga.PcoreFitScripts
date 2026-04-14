#!/usr/bin/env bash
# tests/test_integration.sh – Repo-wide integration tests
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../lib/shell/common.sh
source "${REPO_ROOT}/lib/shell/common.sh"

PASS=0
FAIL=0

pass() { log_info "PASS: $1"; ((PASS++)) || true; }
fail() { log_info "FAIL: $1"; ((FAIL++)) || true; }

# ---------------------------------------------------------------------------
# 1. Directory structure checks
# ---------------------------------------------------------------------------
for dir in bin tools/tool-a tools/tool-b lib/python lib/perl lib/shell configs docs release tests; do
    if [ -d "${REPO_ROOT}/${dir}" ]; then
        pass "Directory exists: ${dir}"
    else
        fail "Directory missing: ${dir}"
    fi
done

# ---------------------------------------------------------------------------
# 2. Key file checks
# ---------------------------------------------------------------------------
for file in VERSION CHANGELOG.md Makefile .gitignore \
            bin/tool-a bin/tool-b \
            tools/tool-a/tool_a.py tools/tool-a/requirements.txt \
            tools/tool-b/tool_b.pl \
            lib/python/common_utils.py lib/perl/CommonUtils.pm lib/shell/common.sh \
            configs/defaults.yaml docs/developer-guide.md docs/release-process.md \
            release/build.sh release/deploy.sh; do
    if [ -f "${REPO_ROOT}/${file}" ]; then
        pass "File exists: ${file}"
    else
        fail "File missing: ${file}"
    fi
done

# ---------------------------------------------------------------------------
# 3. bin wrappers are executable
# ---------------------------------------------------------------------------
for wrapper in bin/tool-a bin/tool-b; do
    if [ -x "${REPO_ROOT}/${wrapper}" ]; then
        pass "Executable: ${wrapper}"
    else
        fail "Not executable: ${wrapper}"
    fi
done

# ---------------------------------------------------------------------------
# 4. tool-a smoke test (requires Python + pyyaml)
# ---------------------------------------------------------------------------
if command -v python >/dev/null 2>&1; then
    output=$(python "${REPO_ROOT}/tools/tool-a/tool_a.py" hello 2>&1)
    if echo "${output}" | grep -q 'HELLO'; then
        pass "tool-a smoke test"
    else
        fail "tool-a smoke test (output: ${output})"
    fi
else
    log_info "SKIP: Python not found – skipping tool-a smoke test"
fi

# ---------------------------------------------------------------------------
# 5. tool-b smoke test (requires Perl)
# ---------------------------------------------------------------------------
if command -v perl >/dev/null 2>&1; then
    output=$(perl "${REPO_ROOT}/tools/tool-b/tool_b.pl" hello 2>&1)
    if echo "${output}" | grep -q 'HELLO'; then
        pass "tool-b smoke test"
    else
        fail "tool-b smoke test (output: ${output})"
    fi
else
    log_info "SKIP: Perl not found – skipping tool-b smoke test"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
log_info "Integration test results: ${PASS} passed, ${FAIL} failed."
[ "${FAIL}" -eq 0 ] || exit 1
