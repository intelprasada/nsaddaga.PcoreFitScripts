# SuperCSV — Standalone CSV Browser

A fast, interactive CSV viewer with all the analysis features from the
InterfaceSpec pipeline tool's Results tab.

## Quick start

```tcsh
python3 scripts/supercsv/supercsv.py path/to/file.csv

# Open several files at once — one tab each
python3 scripts/supercsv/supercsv.py file1.csv file2.csv

# No args → open-file dialog appears automatically
python3 scripts/supercsv/supercsv.py
```

Make it executable once and call directly:

```tcsh
chmod +x scripts/supercsv/supercsv.py
scripts/supercsv/supercsv.py results/*.csv
```

## Features

| Feature | Detail |
|---------|--------|
| **Multi-tab** | Each CSV opens as a separate tab; Ctrl+W closes current, middle-click on tab also closes |
| **Per-column filters** | Type in the box above a column; matched rows highlighted immediately |
| **Boolean column filter** | `(SMT) OR (THREAD)`, `(clk) AND NOT (gate)` — uses keyword + parenthesised atoms |
| **Numeric range filter** | `RANGE(10000-20000)` or `RANGE(10000-20000, 50000-60000)` in any column filter box |
| **Global Logic bar** | Cross-column expression: `1 AND (2 OR 3)`, `NOT 4` — column numbers shown as superscripts |
| **Sort** | Click any column header to sort asc/desc |
| **Column picker** | Columns button → tick/untick columns to show/hide |
| **Fit Cols ↔** | Fits all columns to fill the window width; re-fits automatically on font change and window resize |
| **Right-click paths** | Open file at correct line number in `$EDITOR` or `gvim`; "Other paths" sub-menu for rows with multiple path columns |
| **Export CSV** | Export button saves the current *filtered* view |
| **Email** | Email button sends the filtered view as a CSV attachment (`sendmail`) |
| **Font control** | A+ / A- / A buttons in toolbar; Ctrl++ / Ctrl+- / Ctrl+0 shortcuts |
| **Open recent** | File → Open Recent tracks last 12 opened paths (in-memory, per session) |

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| Ctrl+O | Open file(s) |
| Ctrl+W | Close current tab |
| Ctrl+Tab / Ctrl+Shift+Tab | Next / previous tab |
| Ctrl++ or Ctrl+= | Increase font size |
| Ctrl+- | Decrease font size |
| Ctrl+0 | Reset font to default (14 pt) |
| Ctrl+Q | Quit |

## Boolean filter syntax

### Per-column filter box

Plain text → substring match (case-insensitive).

Boolean atoms are `(pattern)` — parenthesised text.  
Operators: `AND`, `OR`, `NOT` (case-insensitive keywords).

```
(SMT) OR (THREAD)          # rows where column contains "SMT" or "THREAD"
(clk) AND NOT (gate)       # contains "clk" but not "gate"
(foo) AND (bar) OR (baz)   # precedence: NOT > AND > OR
```

### Numeric range filter

`RANGE(lo-hi)` filters rows where the cell's numeric value falls within the
range (both ends inclusive). Multiple ranges are comma-separated. Non-numeric
cells never match.

```
RANGE(10000-20000)                  # 10 000 ≤ value ≤ 20 000
RANGE(10000-20000, 25000-30000)     # value in either range
RANGE(10000-20000, 15000)           # range OR exact scalar value
RANGE(9950, 10050, 10150)           # three exact scalar values
NOT RANGE(10000-20000)              # outside the range
RANGE(10000-20000) AND (IfReset)    # in range AND cell contains "IfReset"
RANGE(10000-20000) OR (9000)        # in range OR cell contains "9000"
```

Each comma-separated item inside `RANGE(...)` can be:
- `lo-hi` — inclusive range
- `value` — exact scalar match (equivalent to `lo=value, hi=value`)

`RANGE(...)` is a full boolean atom — it composes freely with `AND`, `OR`,
and `NOT` alongside `(pattern)` atoms.

### Global Logic bar

Uses the **column index** shown as a superscript above each filter box.

```
1 AND 2        # both column-1 and column-2 filters must pass
1 OR 3         # column-1 OR column-3 filter passes
(1 OR 2) AND NOT 3
NOT 4
```

## Requirements

- Python 3.8+
- `tkinter` (standard library)
- `pandas` (available in the project environment)
- The `scripts/interfacespec/qtgui/` package (same repository — path resolved automatically)

No extra installation needed; `supercsv.py` adds the correct directory to
`sys.path` at startup.

---

# SuperTracker — CTE Tracker Log Browser

`supertracker.py` is a companion tool that extends SuperCSV to open CTE
tracker log files (`.elog`, `.elog.gz`).  It inherits every SuperCSV feature
unchanged: per-column filters, RANGE filters, boolean expressions, sort,
column picker, export, email, font control, and recent-file history.

## Quick start

```tcsh
# Open one or more tracker log files
python3 scripts/supercsv/supertracker.py regression/fe/.../fe_ifu_ic_sb_trk_T0.elog.gz

# Multiple files → one tab each
python3 scripts/supercsv/supertracker.py trk_T0.elog.gz trk_T1.elog.gz

# Mix tracker and CSV files in the same session
python3 scripts/supercsv/supertracker.py trk.elog.gz results.csv

# No args → open-file dialog (accepts .elog, .elog.gz, .csv, .tsv, .psv)
python3 scripts/supercsv/supertracker.py
```

## Tracker file format

CTE tracker logs use `|` as delimiter.  A 3-line header block repeats
approximately every 50 data rows and is automatically stripped:

```
===...===                    ← separator line (≥ 20 '=' characters)
Time | action | DebugID ...  ← column names (space-padded, outer pipes present)
===...===                    ← separator line
9950 | IfReset | -- ...      ← data rows
...
===...===                    ← header repeats — silently skipped by parser
```

Both plain `.elog` and gzip-compressed `.elog.gz` files are supported.

## Filter examples for tracker logs

```
# Time column — show only cycles 10 000–20 000 and 50 000–60 000
RANGE(10000-20000, 50000-60000)

# action column — show IfReset or IcFlush
(IfReset) OR (IcFlush)

# Combine: time in range AND specific action
RANGE(10000-50000) AND (IfReset)   ← xcol filter: col 1 AND col 2

# Exclude debug marker rows
NOT(--)
```

## Hex and binary values in RANGE (tracker only)

Elog columns often store values as hex (`0x3f`) or binary (`0b1010`)
strings.  SuperTracker understands these natively inside `RANGE(...)` — the
prefix is stripped for numeric comparison.

```
# Equivalent filters (hex 0x2710 = 10000, hex 0x4e20 = 20000)
RANGE(10000-20000)
RANGE(0x2710-0x4e20)

# Binary bounds (0b10 = 2, 0b1111 = 15)
RANGE(0b10-0b1111)

# Mix of decimal, hex, and binary in one expression
RANGE(0x10-0xff, 0b1010, 512)

# Works with NOT / AND / OR
NOT RANGE(0x0-0xf)                   # exclude values 0–15
RANGE(0x100-0x1ff) AND (CacheHit)   # hex range AND substring
```

Rules:
- Values prefixed `0x` / `0X` are parsed as hexadecimal integers.
- Values prefixed `0b` / `0B` are parsed as binary integers.
- Plain digits are parsed as decimal integers or floats.
- Cells that cannot be converted to a number (e.g., `--`, action strings)
  never match a `RANGE` filter.
- This behaviour is **tracker-only**.  In plain CSV tabs (opened via
  File → Open), `RANGE` always uses decimal parsing.

## Header-block stripping for plain delimited files

CTE tracker logs (`.elog`, `.elog.gz`) have their repeated header blocks
stripped **automatically**.  For any other delimited file, you tell
SuperTracker where the header block lives with `--header-row`.

### Specification format

```
--header-row N       # single header row at 0-based line N (default: 0)
--header-row N:k     # header block of k consecutive rows starting at line N
```

- **N** is the 0-based line index of the file — the same number you would
  see in a text editor minus one.
- **k** is the number of consecutive lines that form the block (default `1`).
- Column names come from the **last non-separator row** in the block.
  Separator rows are lines where every field consists of a single repeated
  non-alphanumeric character (e.g. `---`, `===`, `----------|--------`).
- **Every occurrence** of the header block found anywhere in the file is
  stripped — rows that appear before the first occurrence are kept as data.

### Example — pipe-delimited elog opened as a plain file

Some elog files have a 4-row header block starting partway down the file:

```
Line 0: (empty)
Line 1:    10850 | RoNuke occurred on T0
Line 2: (empty)
Line 3:          |     |     |    | P  |  ...   ← sub-heading
Line 4:          |     |     |    | I  |  ...   ← sub-heading
Line 5:  Time    | TID | LIP |MUX | D  |  ...   ← column names
Line 6:  --------|-----|-----|----|----|  ...   ← separator (dashes)
Line 7:    14650 |  T0 | 000...                 ← first data row
```

```tcsh
python3 supertracker.py --header-row 3:4 -d '|' fe_idq_wr_trk.elog.gz
```

SuperTracker extracts rows 3–6 as the header block, selects row 5 as the
column-name row (last non-separator), builds a strip-set from all four rows,
and removes every recurrence of those rows throughout the file.

### Example — space-delimited report with a multi-row header

```
Line 0: Generated: 2026-04-09
Line 1: (empty)
Line 2: Metric        Count  Pct    ← column names
Line 3:              (total) (%)    ← sub-heading
Line 4: -----------  ------  ---    ← separator
Line 5: IFU hits       1234  82%
```

```tcsh
python3 supertracker.py -d ' ' --header-row 2:3 report.txt
```

Column names are taken from row 4 — the last non-separator row in the
block.  Rows 0–1 before the header block are treated as data rows.

### Example — CSV with comment lines before the real header

```
Line 0: # auto-generated by run_regression.py
Line 1: # date: 2026-04-09
Line 2: test_name,result,cycles,errors
Line 3: lsd_test,PASS,14650,0
```

```tcsh
python3 supertracker.py --header-row 2 results.csv
```

Rows 0–1 (comment lines) are kept as data rows; row 2 becomes the header.

### Interactive dialog

When opening a non-tracker file through **File › Open**, the same option
appears as a text field labelled **Header row(s) (N or N:k)**.  The hint
below the field reads:

```
N = 0-based start row  ·  k = rows in header block (default 1)
```

An invalid spec (e.g. letters or two colons) shows a red error message
inline and keeps the dialog open.

---

## Column count override (`--num-cols`)

```
--num-cols N    # force exactly N columns (0 = auto, the default)
```

By default SuperTracker infers the column count from the header row.
Use `--num-cols` when the file has a known fixed width that differs from
what auto-detection would produce.

- If the header has **fewer** than N columns, synthetic names `Col<n>` are
  appended to reach N.
- If the header has **more** than N columns, the excess columns are dropped.
- Every data row is then padded (empty string) or trimmed to match N.

### Examples

```tcsh
# Space-delimited report: 8-column format, 3-row header starting at row 2
python3 supertracker.py -d ' ' --header-row 2:3 --num-cols 8 my_report.txt

# Pipe-delimited elog opened as plain file: known 21-column format
python3 supertracker.py --header-row 3:4 --num-cols 21 -d '|' fe_idq_wr_trk.elog.gz

# Auto-detect columns (default — omit the flag)
python3 supertracker.py results.csv
```

### In the dialog

The **Num columns (0 = auto)** spinner in the Open dialog corresponds to
`--num-cols`.  Leave it at 0 to auto-detect from the header.

---

## All flags combined

All flags compose freely:

```tcsh
# Pipe-delimited file, 4-row header at row 3, 21 columns, drop bad lines
python3 supertracker.py \
    -d '|' \
    --header-row 3:4 \
    --num-cols 21 \
    --drop-bad-lines \
    fe_idq_wr_trk.elog.gz

# Space-delimited, header at row 1, pad bad lines (default behaviour)
python3 supertracker.py -d ' ' --header-row 1 perf_summary.txt

# Multiple files — flags apply to all non-tracker files
python3 supertracker.py --header-row 0 -d ',' run_A.csv run_B.csv
```


## All SuperCSV filter features work identically

See the **Boolean filter syntax** and **Numeric range filter** sections above.
