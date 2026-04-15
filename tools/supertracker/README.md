# supertracker — CTE Tracker File Viewer

A specialised viewer for **CTE tracker `.elog` files** (pipe-delimited with
repeating 3-line header blocks throughout the file).  Strips the repeated
headers and loads the data into the same `FilteredTable` widget used by
SuperCSV, giving full filtering, sorting, column-hiding, email, and export
capabilities.

Plain CSV / TSV / PSV files are also supported and are delegated to the
SuperCSV rendering path.

## Dependencies

`supertracker` builds on top of `supercsv`'s shared widget layer
(`FilteredTable`, `FontManager`, `ThemeManager`, `SuperCSV`).  The
`bin/supertracker` wrapper automatically adds `tools/supercsv/` to
`PYTHONPATH`, so no manual setup is needed.

External: `pandas>=1.3` (same requirement as supercsv).

## Quick start

```tcsh
bin/supertracker path/to/file.elog.gz
bin/supertracker                          # opens file-picker dialog
```

## CLI options

| Option | Default | Description |
|--------|---------|-------------|
| `[files …]` | — | One or more `.elog`, `.elog.gz`, `.csv`, `.tsv`, `.psv` files |
| `--delimiter CHAR` | auto | Force column separator (`\t`, `\|`, `,`, …) |
| `--header-row N[:k]` | `0` | Header block spec — 0-based start row; optional block height |
| `--num-cols N` | auto | Override column count |
| `--drop-bad-lines` | off | Drop rows with wrong column count instead of padding |

## Elog format

CTE tracker logs contain a repeating pattern:

```
====================...==    ← separator (≥20 '=' characters)
col1 | col2 | col3          ← column names
====================...==    ← separator
row1a | row1b | row1c       ← data (repeats every ~50 rows)
row2a | row2b | row2c
…
====================...==    ← next header block (stripped)
col1 | col2 | col3
====================...==
…
```

All repeated header blocks are stripped before the table is rendered.
