# Developer Guide

This guide explains how to add a new tool to the **core-tools** repository.

## Prerequisites

- Python 3.8+ (for Python tools)
- Perl 5.20+ (for Perl tools)
- `make`, `bash`, `shellcheck`, `flake8`, `pytest`, `prove`

## Repository Layout

```
core-tools/
├── bin/          # Thin entry-point wrappers (add to PATH)
├── tools/        # One subdirectory per tool
├── lib/          # Shared libraries (python/, perl/, shell/)
├── configs/      # Configuration templates
├── docs/         # Extended documentation
├── release/      # Release automation
└── tests/        # Repo-wide integration tests
```

## Adding a New Tool

### 1. Create the tool directory

```bash
TOOL=my-tool
mkdir -p tools/${TOOL}/tests
```

### 2. Write the main script

- **Python**: `tools/${TOOL}/${TOOL//-/_}.py`
- **Perl**: `tools/${TOOL}/${TOOL//-/_}.pl`

Import shared utilities from `lib/python/common_utils.py` or `lib/perl/CommonUtils.pm`.

### 3. Add a bin wrapper

Create `bin/${TOOL}` (executable) that delegates to the main script.

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python "${REPO_ROOT}/tools/${TOOL}/${TOOL//-/_}.py" "$@"
```

```bash
chmod +x bin/${TOOL}
```

### 4. Write tests

Place unit tests in `tools/${TOOL}/tests/`:
- **Python**: `test_${TOOL//-/_}.py` (pytest)
- **Perl**: `test_${TOOL//-/_}.t` (TAP / prove)

Run tests:
```bash
make test-tool-a   # or the equivalent for your new tool
```

### 5. List Python dependencies

Add a `tools/${TOOL}/requirements.txt` if the tool has Python dependencies.

### 6. Write a tool README

Document usage, options, and examples in `tools/${TOOL}/README.md`.

### 7. Update the root README

Add an entry to the **Tool Catalog** table in the root `README.md`.

### 8. Update CHANGELOG.md

Add an entry under `[Unreleased]`.
