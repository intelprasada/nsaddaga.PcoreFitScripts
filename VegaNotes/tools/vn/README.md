# `vn` — VegaNotes terminal CLI

A tiny shell helper for VegaNotes — a thin wrapper around the existing
REST API for people who live in their terminal. Stdlib only, no third-
party dependencies.

```bash
vn task T-123 status=done priority=P1 eta=2026-W18
vn list --owner kushwanth --status open
vn list -w '@area=fit-val' -w 'eta>=ww18' --sort eta:desc
vn note new --project ww16 --title 'standup notes'
vn whoami
```

Implements [issue #38](https://github.com/intelprasada/nsaddaga.PcoreFitScripts/issues/38)
and the generic-attribute search follow-up
([#94](https://github.com/intelprasada/nsaddaga.PcoreFitScripts/issues/94)).

## Install

**One-liner (recommended):** the bundled `install.sh` works from
anywhere and picks the right Python automatically.

```bash
/path/to/VegaNotes/tools/vn/install.sh
# Options: --no-editable | --system | --python python3.11 | --uninstall
```

**Or run pip yourself** from the repo root. Use Python ≥ 3.9 — on Intel
hosts where `/usr/bin/pip` is bound to Python 3.6, invoke pip via
`python3 -m pip`:

```bash
python3 -m pip install --user -e VegaNotes/tools/vn
```

That puts a `vn` command in `~/.local/bin` (drop `--user` if you're in a
virtualenv). Confirm with `vn --version`. If `vn` isn't on `PATH`, add
`~/.local/bin` to it.

> The package requires Python ≥ 3.9. The bundled `setup.py` shim is
> there only so editable installs work on older pip versions; the
> canonical metadata lives in `pyproject.toml`.

## Authentication

Lookup order (first hit wins):

1. Environment variables — `VEGANOTES_URL`, `VEGANOTES_USER`,
   `VEGANOTES_PASS`.
2. `~/.veganotes/credentials` — INI file:

   ```ini
   [default]
   url = http://localhost:8000
   user = alice
   password = s3cret

   [prod]
   url = https://veganotes.example.com
   user = alice
   password = otherone
   ```

   Pick a section other than `[default]` with `--profile prod` (or
   `VEGANOTES_PROFILE=prod`).

`chmod 600 ~/.veganotes/credentials` is recommended.

## Commands

`vn` always takes a subcommand first:

```
vn [GLOBAL FLAGS] <task|list|query|note|whoami> [...]
```

> **Heads-up:** every operation lives under a subcommand. `vn --project
> ww18 --title foo` will not work — you need `vn note new --project
> ww18 --title foo`.

### `vn task <ID> [key=value ...]`

Patch a task. With no `key=value` pairs, fetches and prints the task
instead. Accepted keys map straight to `PATCH /api/tasks/{id}`:

| key                    | maps to    | notes                          |
| ---------------------- | ---------- | ------------------------------ |
| `status`               | `status`   | e.g. `done`, `wip`             |
| `priority`             | `priority` | e.g. `P0`, `P1`                |
| `eta`                  | `eta`      | e.g. `2026-W18`, `ww17`        |
| `owner` / `owners`     | `owners`   | comma-separated → list         |
| `feature` / `features` | `features` | comma-separated → list         |
| `note` / `add-note`    | `add_note` | append a single note line      |
| `notes`                | `notes`    | overwrite the notes block      |

```bash
vn task T-123                                   # fetch + print
vn task T-123 status=done priority=P1 eta=2026-W18
vn task T-456 owners=alice,bob feature=login add-note='shipped behind flag'
```

`<ID>` can be either the integer PK from `vn list` or a `T-XXXXXX` ref.

### `vn list [filters]`  (alias: `vn query`)

```bash
vn list                                         # everything you can see
vn list --owner kushwanth --status open
vn list --project gfc --priority P0,P1 --hide-done
vn list -q "carveout" --limit 20
```

Outputs an aligned text table by default; pick another shape with
`--format`:

| `--format` | description                                              |
| ---------- | -------------------------------------------------------- |
| `table`    | aligned columns, the default                             |
| `json`     | the raw API envelope (same as `--json`)                  |
| `jsonl`    | one task per line — scriptable                           |
| `csv`      | header row + data rows                                   |
| `ids`      | one `T-XXXXXX` (or int PK) per line — pipes into `xargs` |

Pick + reorder columns with `--columns id,priority,eta,title`; bucket
the table by any field with `--group-by area`.

#### Query language (`-w` / `--where`)

`vn list` (and the `vn query` alias) accept repeatable `--where`
clauses that compile to `/api/tasks` parameters. Grammar: `key OP value`
(clauses are AND-ed; repeating the same `@key` ORs values for that key).

| Symbolic operators   | Word operators          |
| -------------------- | ----------------------- |
| `=`, `!=`            | `in`, `not in`, `like`  |
| `>=`, `<=`, `>`, `<` |                         |

* **Bare keys** address fixed columns: `owner`, `project`, `feature`,
  `priority`, `status`, `eta`, `q`, `kind`. Only `=` / `!=` / `in` are
  allowed; `!=` compiles to `not_*`.
* **`@key`** addresses any other `#tag` attribute (e.g. `@area`,
  `@risk`, `@team`). All operators allowed; range ops require a
  `value_norm`-populated key (today: `eta`, `priority`).
* `eta>=ww18` / `eta<=2026-05-01` route to the dedicated date-typed
  `eta_after` / `eta_before` params; `eta=ww17` (bucket equality)
  routes through the generic attr path.

```bash
vn list -w 'owner=alice' -w '@area=fit-val' -w 'eta>=ww18' --sort eta:desc
vn list -w 'priority in P0,P1' -w 'project!=internal' --limit 50
vn list -w '@risk=high' --format ids | xargs -n1 -I{} vn task {} status=wip
```

Other handy flags (mostly thin convenience over the same wire params):
`--sort`, `--limit`, `--offset`, `--not-owner`, `--not-project`,
`--not-feature`, `--not-status`, `--not-priority`, `--kind`.

### `vn note new --project <p> --title <t>`

Creates a new markdown note on the server. **`note` is the parent
command and `new` is the subcommand** — both are required.

| flag        | required | description                                                 |
| ----------- | -------- | ----------------------------------------------------------- |
| `--project` | yes      | folder under the notes root, e.g. `ww18`                    |
| `--title`   | yes      | note title; becomes the file slug if `--path` is not given  |
| `--path`    |          | explicit relative path (overrides `<project>/<slug>.md`)    |
| `--body`    |          | markdown body; defaults to a `# title` heading + timestamp  |

```bash
# Default path — ww18/test-note-thru-cli.md, body is a title heading
vn note new --project ww18 --title 'Test Note Thru CLI'

# Explicit path + body from a file
vn note new --project ww18 --title 'incident' \
            --path ww18/incidents/2026-04-25.md \
            --body "$(cat draft.md)"
```

The slug uses the same lowercase-kebab rule as the rest of VegaNotes
(`'Test Note Thru CLI'` → `test-note-thru-cli`).

### `vn whoami`

Prints the authenticated user (and `(admin)` if applicable).

## Global flags

These come **before** the subcommand:

```bash
vn --profile prod list                 # ✅
vn --json list                         # ✅
vn list --json                         # ❌ argparse rejects: --json is global
```

| flag            | description                                                  |
| --------------- | ------------------------------------------------------------ |
| `--profile NAME`| credentials profile (default: `VEGANOTES_PROFILE` or `default`) |
| `--json`        | emit raw JSON instead of the table view (alias of `--format json`) |
| `--version`     | print version and exit                                       |
| `-h`, `--help`  | help text — also works as `vn <subcommand> --help`           |

## Common workflows

```bash
# Bulk-promote every "in progress" P1 task in your area to "done"
vn list -w 'priority=P1' -w 'status=wip' -w '@area=fit-val' --format ids \
  | xargs -n1 -I{} vn task {} status=done

# Export a CSV for a stakeholder
vn list -w 'project=gfc' -w 'eta>=ww17' --format csv \
        --columns id,owner,priority,eta,title > gfc-roadmap.csv

# Quick "what's blocked across my projects" check
vn list -w 'status=blocked' --group-by project --columns id,owner,eta,title
```

## Tests

```bash
cd VegaNotes/tools/vn
python3 -m pip install --user -e '.[dev]'
pytest
```

(57 tests across `tests/test_cli.py` and `tests/test_query.py`; pure
unit tests — no server required, the HTTP client is monkey-patched.)
