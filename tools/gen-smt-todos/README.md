# gen-smt-todos — SMT / JNC TODO Scanner

Scans RTL and ICF files under `core/fe/rtl` and `core/msid/rtl` for comment
lines that contain **both** a TODO/FIXME marker and an SMT/JNC/Thread-1
keyword.  Outputs a TSV and a summary text file.

## Quick start

Run from the model root (any `MT1_enab*` workspace root):

```tcsh
python3 tools/gen-smt-todos/gen_smt_todos.py
```

or via the `bin/` wrapper:

```tcsh
bin/gen-smt-todos
```

## Outputs

Both files are written to the **current working directory**:

| File | Description |
|------|-------------|
| `smt_jnc_todos.tsv` | TSV with columns: `tree`, `unit`, `path`, `line`, `text` |
| `smt_jnc_todos_summary.txt` | Per-file match counts |

## Keywords matched

A line is included when **both** of the following groups appear in the same
comment:

| Group | Keywords |
|-------|---------|
| Action marker | `TODO`, `FIXME` |
| Thread / SMT marker | `SMT`, `JNC`, `T1`, `Thread 1`, `thread 1`, `MT mode`, `multithread`, `multi-thread` |

## File types scanned

`*.vs`, `*.sv`, `*.svh`, `*.v`, `*.icf`, `*.hier` under `core/fe/rtl` and
`core/msid/rtl` (relative to the current working directory).

## Requirements

Python 3.8+ — stdlib only; no external packages needed.
