# `vn` — VegaNotes terminal CLI

A tiny shell helper for VegaNotes — a thin wrapper around the existing REST
API for people who live in their terminal.

```bash
vn task T-123 status=done priority=P1 eta=2026-W18
vn list --owner kushwanth --status open
vn note new --project ww16 --title 'standup notes'
vn whoami
```

Implements [issue #38](https://github.com/intelprasada/nsaddaga.PcoreFitScripts/issues/38).

## Install

`vn` is a normal pip-installable package with **no third-party dependencies**
(stdlib only).

**One-liner (recommended):** the bundled `install.sh` works from anywhere
and picks the right Python automatically:

```bash
/path/to/VegaNotes/tools/vn/install.sh
# Options: --no-editable | --system | --python python3.11 | --uninstall
```

**Or run pip yourself** from the repo root:

```bash
# Use Python 3.9+ — DO NOT use the system `pip` if it's bound to Python <3.9.
# On Intel hosts where /usr/bin/pip is Python 3.6, run pip via python3 instead:
python3 -m pip install --user -e VegaNotes/tools/vn
```

That puts a `vn` command in `~/.local/bin` (drop `--user` if you're in a
virtualenv). Confirm with `vn --version`. If `vn` isn't on `PATH`, add
`~/.local/bin` to it.

> The package requires Python ≥ 3.9. The bundled `setup.py` shim is there
> only so editable installs work on older pip versions; the canonical
> metadata lives in `pyproject.toml`.

> An `npm` shim is **not** shipped in v0.1 — the issue offered the choice and
> Python won out for parity with the backend. A trivial `npm` wrapper that
> shells out to `vn` can be added later if needed.

## Authentication

Lookup order (first hit wins):

1. Environment variables — `VEGANOTES_URL`, `VEGANOTES_USER`, `VEGANOTES_PASS`.
2. `~/.veganotes/credentials` — INI file:

   ```ini
   [default]
   url = http://localhost:8000
   user = alice
   password = s3cret
   ```

   Use `--profile prod` (or `VEGANOTES_PROFILE=prod`) to pick a section other
   than `[default]`.

`chmod 600 ~/.veganotes/credentials` is recommended.

## Commands

### `vn task <ID> [key=value ...]`

Patch a task. With no attrs, fetches and prints the task. Accepted keys map
straight to `PATCH /api/tasks/{id}`:

| key                 | maps to    | notes                          |
| ------------------- | ---------- | ------------------------------ |
| `status`            | `status`   | e.g. `done`, `wip`             |
| `priority`          | `priority` | e.g. `P0`, `P1`                |
| `eta`               | `eta`      | e.g. `2026-W18`, `ww17`        |
| `owner` / `owners`  | `owners`   | comma-separated → list         |
| `feature` / `features` | `features` | comma-separated → list      |
| `note` / `add-note` | `add_note` | append a single note line      |

```bash
vn task T-123 status=done priority=P1 eta=2026-W18
vn task T-456 owners=alice,bob feature=login add-note='shipped behind flag'
```

### `vn list [filters]`

```bash
vn list --owner kushwanth --status open
vn list --project gfc --priority P0,P1 --hide-done
vn list -q "carveout" --limit 20
```

Outputs a human-readable table; pass `--json` for raw API output.

### `vn note new --project <p> --title <t>`

Creates `<project>/<slug>.md` on the server (slug derived from the title).
Use `--path` to override the path or `--body` to supply markdown.

```bash
vn note new --project ww16 --title 'standup notes'
vn note new --project ww16 --title 'incident' --body "$(cat draft.md)"
```

### `vn whoami`

Prints the authenticated user (and `(admin)` if applicable).

## Global flags

- `--profile NAME`  — credentials profile (default `default`).
- `--json`          — emit raw JSON instead of the table view.
- `--version`       — print version and exit.

## Tests

```bash
cd VegaNotes/tools/vn
pip install -e .[dev]
pytest
```
