#!/usr/bin/env python3
"""
supertracker — interactive CTE tracker file viewer.

CTE tracker components produce pipe-delimited log files with a 3-line
repeating header block throughout the file:

    ===...===           (separator line — starts with 20+ '=' characters)
    col1 | col2 | ...   (column names, space-padded)
    ===...===           (separator line)
    row1_val1 | ...     (data rows — repeat every ~50 rows)
    ...

SuperTracker strips all repeated header blocks and loads the data into the
same FilteredTable widget used by SuperCSV, giving you the full set of
filtering, sorting, column-hiding, email, and export capabilities.

All standard CSV / TSV / PSV files are also supported (delegated to SuperCSV).

Usage
-----
    python3 scripts/supercsv/supertracker.py [file1.elog.gz [file2.elog ...]]
    python3 scripts/supercsv/supertracker.py              # opens file dialog

If no files are supplied an Open dialog is shown immediately.
"""

import gzip
import csv
import sys
import os
import argparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ── Path setup ────────────────────────────────────────────────────────────────
# filtered_table.py, font_manager.py, and supercsv.py all live in this dir.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import pandas as pd
    from filtered_table import FilteredTable
    from font_manager import FontManager
    from theme_manager import ThemeManager  # noqa: F401 – used by inherited toolbar
    from supercsv import SuperCSV, _RECENT_MAX, _detect_delimiter, _DELIM_CHOICES, _sep_label
except ImportError as exc:
    print(f"ERROR: cannot import dependencies: {exc}", file=sys.stderr)
    print(f"  Expected at: {_HERE}/", file=sys.stderr)
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────

_APP_TITLE   = "SuperTracker"
_WINDOW_SIZE = "1600x900"

# A line is an elog separator if it starts with this many '=' characters.
_SEP_MIN_EQ  = 20


# ─────────────────────────────────────────────────────────────────────────────
# Elog parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_elog(path: str) -> bool:
    """Return True for .elog or .elog.gz file paths."""
    p = path.lower()
    return p.endswith(".elog") or p.endswith(".elog.gz")


def _is_separator(line: str) -> bool:
    """Return True if *line* is an elog section-separator (===...===)."""
    return line.startswith("=" * _SEP_MIN_EQ)


# ─────────────────────────────────────────────────────────────────────────────
# SuperTracker CSV reader (header-block aware)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_header_spec(spec: str) -> "tuple[int, int]":
    """Parse a header-spec string into ``(start_row, row_count)``.

    Accepted formats:
    * ``"N"``    → row N is the single header row  (count = 1)
    * ``"N:k"``  → rows N .. N+k-1 form the header block
    """
    spec = spec.strip()
    if ":" in spec:
        start_s, count_s = spec.split(":", 1)
        return int(start_s), max(1, int(count_s))
    return int(spec), 1


def _is_separator_row(row: list) -> bool:
    """True when the row looks like a visual separator line.

    Handles two cases:

    * **Single-cell** rows whose value is two or more repetitions of the
      same non-alphanumeric character (e.g. ``=====`` or ``-----``).
    * **Multi-cell** rows (pipe-delimited, etc.) where *every non-empty
      cell* consists entirely of a single repeated non-alphanumeric
      character (e.g. ``---------|----|----`` splits into ``['---------',
      '----', '----']``).
    """
    if not row:
        return False
    cells = [c.strip() for c in row]
    non_empty = [c for c in cells if c]
    if not non_empty:
        return False
    return all(len(set(c)) == 1 and not c[0].isalnum() for c in non_empty)


def _read_tracker_csv(path: str, sep: str,
                      header_spec: str = "0",
                      num_cols: int = 0,
                      drop_bad: bool = False) -> "tuple[pd.DataFrame, int]":
    """Read a delimited file with SuperTracker header-block awareness.

    The user specifies a *header_spec* of the form ``"N"`` or ``"N:k"``,
    meaning rows N..N+k-1 of the file form **one occurrence** of the header
    block.  Any row in the file that exactly matches any row of that block
    is stripped from data (handles repeated headers).  All other rows —
    including those before row N — are treated as data rows.

    Column names are taken from the first non-separator row of the block.
    If *num_cols* > 0 the column list is padded (``Col_N+1``…) or trimmed
    to that width.

    When *drop_bad* is True, data rows with the wrong column count are
    dropped; otherwise they are padded/trimmed.

    Returns ``(DataFrame, n_adjusted)`` where *n_adjusted* counts rows
    that were padded, trimmed, or dropped.
    """
    # ── 1. Read raw rows with encoding fallback ───────────────────────────
    import gzip as _gzip
    rows: list = []
    opener = _gzip.open if path.endswith(".gz") else open
    for enc in ("utf-8", "latin-1"):
        try:
            with opener(path, "rt", encoding=enc, errors="strict", newline="") as fh:
                if sep == " ":
                    rows = [line.split() for line in fh]
                else:
                    rows = list(csv.reader(fh, delimiter=sep))
            break
        except (UnicodeDecodeError, LookupError):
            pass
    else:
        with opener(path, "rt", encoding="utf-8", errors="replace", newline="") as fh:
            if sep == " ":
                rows = [line.split() for line in fh]
            else:
                rows = list(csv.reader(fh, delimiter=sep))

    if not rows:
        return pd.DataFrame(), 0

    # ── 2. Parse header spec and extract block ────────────────────────────
    # Header spec indices refer to raw file line numbers (0-based), consistent
    # with what the user sees in a text editor.  We extract the block BEFORE
    # any row-dropping so the indices are stable.
    start, count = _parse_header_spec(header_spec)
    header_block = rows[start: start + count]  # list of row lists

    # ── 3. Find column names = last non-separator row in block ───────────
    # Using the *last* non-separator row handles multi-row headers like:
    #   row N+0: "   | P |  ..."   (sub-heading line)
    #   row N+1: "   | I |  ..."   (sub-heading line)
    #   row N+2: "Time | TID | ..."  ← actual column names
    #   row N+3: "---------|-----|" (separator)
    col_names: list = []
    for r in header_block:
        if r and not _is_separator_row(r):
            col_names = [c.strip() for c in r]   # keep updating → last wins
    if not col_names and header_block:
        col_names = [c.strip() for c in header_block[0]]   # last-resort fallback

    if not col_names:
        return pd.DataFrame(), 0

    # ── 4. Build the set of row tuples to strip ───────────────────────────
    # Each row in the header block is canonicalised (stripped) before hashing.
    strip_set: set = set()
    for r in header_block:
        strip_set.add(tuple(c.strip() for c in r))

    # ── 5. Filter: remove header-block rows, separator rows, and blank rows
    filtered = [
        r for r in rows
        if r and tuple(c.strip() for c in r) not in strip_set
    ]

    # ── 6. Apply num_cols override ────────────────────────────────────────
    if num_cols > 0:
        if len(col_names) < num_cols:
            col_names += [f"Col{i + 1}" for i in range(len(col_names), num_cols)]
        else:
            col_names = col_names[:num_cols]
    ncols = len(col_names)

    # ── 7. Pad / drop data rows ───────────────────────────────────────────
    n_adjusted = 0
    data: list = []
    for row in filtered:
        if len(row) != ncols:
            n_adjusted += 1
            if drop_bad:
                continue                               # discard the row
            if len(row) < ncols:
                row = row + [""] * (ncols - len(row))  # pad right
            else:
                row = row[:ncols]                      # trim right
        data.append(row)

    df = pd.DataFrame(data, columns=col_names)
    return df.fillna(""), n_adjusted


def _parse_elog_stream(fileobj) -> tuple:
    """
    Parse an elog byte / text stream.

    The format is:
        <separator>          <- marks start of header block
        col1 | col2 | ...    <- column names (may recur throughout file)
        <separator>
        data_row | ...
        ...

    Only the first header block is used to extract column names.
    All subsequent header blocks (repeats) are silently skipped.

    Parameters
    ----------
    fileobj : file-like
        Opened in binary or text mode.

    Returns
    -------
    (columns, rows) : (list[str], list[list[str]])
    """
    columns = None
    rows    = []
    # State machine:
    #   0 = normal (reading data rows)
    #   1 = just saw a separator, next line is column-name line
    #   2 = just read column-name line, next line is trailing separator (skip)
    state = 0

    for raw in fileobj:
        # Normalise to str regardless of open mode
        if isinstance(raw, bytes):
            line = raw.decode("utf-8", errors="replace").rstrip("\n\r")
        else:
            line = raw.rstrip("\n\r")

        if _is_separator(line):
            if state == 0:
                state = 1   # entering header block
            elif state == 2:
                state = 0   # trailing separator — header block done
            # else: consecutive separators, stay in state 1
            continue

        if state == 1:
            # This is the column-name line
            state = 2
            if columns is None:
                # First header — extract column names
                parts = [c.strip() for c in line.split("|")]
                # Strip empty tokens from outer pipe chars (e.g. "| a | b |")
                if parts and parts[0] == "":
                    parts = parts[1:]
                if parts and parts[-1] == "":
                    parts = parts[:-1]
                columns = parts
            # Repeated header — do nothing (skip the line)
            continue

        if state == 2:
            # Trailing separator consumed above; this branch shouldn't trigger
            # but handle gracefully: treat as data if not a separator.
            state = 0
            # Fall through to data-row handling below.

        # ── Data row ─────────────────────────────────────────────────────────
        cells = [c.strip() for c in line.split("|")]
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if not any(cells):      # skip fully blank lines
            continue
        rows.append(cells)

    return (columns or []), rows


def _load_elog_as_df(path: str, num_cols: int = 0) -> pd.DataFrame:
    """
    Open *path* (.elog or .elog.gz), parse its content, and return a DataFrame.

    Rows shorter than the column list are right-padded with empty strings.
    Rows longer than the column list are truncated (shouldn't happen in
    well-formed files but handled defensively).

    Parameters
    ----------
    num_cols : int
        When > 0 override the inferred column count from the header block.
        The header names are padded (``Col_N+1 …``) or trimmed to match.
        0 means auto-detect from the first header block (default).
    """
    open_fn = gzip.open if path.endswith(".gz") else open

    with open_fn(path, "rb") as fh:
        cols, rows = _parse_elog_stream(fh)

    # Apply num_cols override on the column names list.
    if num_cols > 0:
        if len(cols) < num_cols:
            cols = cols + [f"Col{i + 1}" for i in range(len(cols), num_cols)]
        else:
            cols = cols[:num_cols]

    n = len(cols)
    padded = []
    for r in rows:
        if len(r) < n:
            r = r + [""] * (n - len(r))
        elif len(r) > n:
            r = r[:n]
        padded.append(r)

    return pd.DataFrame(padded, columns=cols).fillna("")


# ─────────────────────────────────────────────────────────────────────────────
# Tracker-specific RANGE filter with hex / binary value support
# ─────────────────────────────────────────────────────────────────────────────
# These functions mirror _parse_range_pairs / _range_mask / _apply_col_filter
# from filtered_table.py but extend them to understand 0x and 0b prefixes in
# both the RANGE spec itself and the cell values of the DataFrame column.
# They are injected only into elog tabs; regular CSV tabs keep the standard
# decimal-only behaviour.

import re as _re

# Matches a single number token: hex (0x...), binary (0b...), or decimal/float.
_XT_NUM_PAT = r'(?:0[xX][0-9a-fA-F]+|0[bB][01]+|-?\d+(?:\.\d+)?)'

# lo-hi range: both endpoints may be any of the three bases.
_XT_RANGE_RE = _re.compile(
    rf'^\s*({_XT_NUM_PAT})\s*-\s*({_XT_NUM_PAT})\s*$'
)
# Bare scalar (no '-' separator).
_XT_SCALAR_RE = _re.compile(rf'^\s*({_XT_NUM_PAT})\s*$')


def _xt_parse_number(s: str) -> float:
    """
    Convert a string token to float, supporting 0x (hex) and 0b (binary)
    prefixes as well as plain integers and floats.
    """
    s = s.strip()
    try:
        return float(int(s, 0))   # int(s, 0) handles 0x / 0b / octal / decimal-int
    except (ValueError, TypeError):
        return float(s)           # decimal float fallback


def _xt_parse_range_pairs(spec: str):
    """
    Extended version of _parse_range_pairs that accepts 0x/0b prefixes.

    Each comma-separated item may be:
    - ``lo-hi``  (either end may be 0x / 0b / decimal)
    - ``value``  exact match (0x / 0b / decimal)

    Examples::

        "0x10-0xff"          → [(16, 255)]
        "0b10-0b1111"        → [(2, 15)]
        "10-20, 0x10, 16"    → [(10, 20), (16, 16), (16, 16)]
    """
    pairs = []
    for part in spec.split(','):
        p = part.strip()
        m = _XT_RANGE_RE.match(p)
        if m:
            pairs.append((_xt_parse_number(m.group(1)), _xt_parse_number(m.group(2))))
            continue
        m2 = _XT_SCALAR_RE.match(p)
        if m2:
            v = _xt_parse_number(m2.group(1))
            pairs.append((v, v))
    return pairs


def _xt_coerce_series(series: "pd.Series") -> "pd.Series":
    """
    Coerce a string series to numeric, understanding 0x / 0b prefixes.

    Returns a float64 Series; non-parseable cells become NaN.
    """
    def _conv(s):
        try:
            return float(int(str(s).strip(), 0))
        except (ValueError, TypeError):
            try:
                return float(str(s).strip())
            except (ValueError, TypeError):
                return float("nan")
    return series.map(_conv)


def _xt_apply_col_filter(series: "pd.Series", text: str) -> "pd.Series":
    """
    Drop-in replacement for filtered_table._apply_col_filter that adds
    hex/binary support inside RANGE(...) atoms and when coercing column values.

    All other filter behaviour (plain substring, AND/OR/NOT boolean expressions,
    decimal RANGE) is identical to the base version.
    """
    from filtered_table import _apply_col_filter   # import lazily to avoid circularity

    text = text.strip()
    if not text:
        return pd.Series(True, index=series.index)

    # Fast path: no RANGE keyword → delegate entirely to base function.
    if not _re.search(r'\bRANGE\b', text, _re.IGNORECASE):
        return _apply_col_filter(series, text)

    # ── Boolean expression mode ───────────────────────────────────────────────
    _BOOL_KW = {"AND", "OR", "NOT"}
    raw = _re.findall(
        r'\bRANGE\([^)]*\)|\([^()]*\)|\b(?:AND|OR|NOT)\b|[^\s()]+',
        text, _re.IGNORECASE
    )
    tokens  = [t.strip() for t in raw if t.strip()]
    pos     = [0]
    all_true = pd.Series(True, index=series.index)

    def peek():
        return tokens[pos[0]].upper() if pos[0] < len(tokens) else None

    def consume():
        t = tokens[pos[0]]; pos[0] += 1; return t

    def match_atom(atom: str) -> "pd.Series":
        if atom.upper().startswith("RANGE("):
            pairs = _xt_parse_range_pairs(atom[6:-1])
            if not pairs:
                return all_true.copy()
            numeric = _xt_coerce_series(series)
            mask = pd.Series(False, index=series.index)
            for lo, hi in pairs:
                mask = mask | ((numeric >= lo) & (numeric <= hi))
            return mask
        inner = atom[1:-1].strip().lower()
        if not inner:
            return all_true.copy()
        return series.str.lower().str.contains(inner, na=False, regex=False)

    def parse_or():
        left = parse_and()
        while peek() == "OR":
            consume(); left = left | parse_and()
        return left

    def parse_and():
        left = parse_not()
        while peek() == "AND":
            consume(); left = left & parse_not()
        return left

    def parse_not():
        if peek() == "NOT":
            consume(); return ~parse_atom_pos()
        return parse_atom_pos()

    def parse_atom_pos():
        if peek() is None:
            return all_true.copy()
        tok = consume()
        if tok.startswith("(") or tok.upper().startswith("RANGE("):
            return match_atom(tok)
        # Bare word atom — treat as substring (e.g. "IC OR SB")
        if tok.upper() not in _BOOL_KW:
            return series.str.lower().str.contains(tok.lower(), na=False, regex=False)
        return all_true.copy()

    try:
        return parse_or()
    except Exception:
        return _apply_col_filter(series, text)   # safe fallback


# ─────────────────────────────────────────────────────────────────────────────
# SuperTracker — extends SuperCSV
# ─────────────────────────────────────────────────────────────────────────────

class SuperTracker(SuperCSV):
    """
    SuperCSV subclass that adds native support for CTE tracker files
    (.elog / .elog.gz).

    All SuperCSV features (per-column filter expressions, global logic bar,
    column picker, email, export, right-click path opener, font controls,
    recent-files list, multi-tab) are fully inherited.

    Additional SuperTracker-only features:
    - The Open dialog also shows tracker file types.
    - .elog / .elog.gz files are parsed via _load_elog_as_df().
    - Users can specify the header row index and number of columns for
      non-tracker CSV files (via dialog or CLI flags).
    """

    def __init__(self, files: list, cli_sep: str = None,
                 cli_drop_bad: bool = False,
                 cli_header_spec: str = "0",
                 cli_num_cols: int = 0):
        # Store SuperTracker-specific CLI defaults before super().__init__
        # which immediately calls _open_csv for any files supplied.
        self._cli_header_spec = cli_header_spec
        self._cli_num_cols    = cli_num_cols
        # super().__init__ builds the UI, sets title/geometry, and loads any
        # initial files via self._open_csv() — which we override below so
        # tracker files are routed correctly even at startup.
        super().__init__(files, cli_sep=cli_sep, cli_drop_bad=cli_drop_bad)
        # Override title and window size after super sets them.
        self.title(_APP_TITLE)
        self.geometry(_WINDOW_SIZE)

    # ------------------------------------------------------------------
    # Open dialog — extended filetypes
    # ------------------------------------------------------------------

    def _open_dialog(self):
        """Open-file dialog that accepts both tracker logs and standard delimited files."""
        paths = filedialog.askopenfilenames(
            title="Open tracker / delimited file(s)",
            filetypes=[
                ("Tracker logs",    "*.elog *.elog.gz"),
                ("Delimited files", "*.csv *.tsv *.psv *.txt"),
                ("All files",       "*.*"),
            ],
        )
        for p in paths:
            self._open_csv(p)

    # ------------------------------------------------------------------
    # _open_csv override — route tracker files / show extended dialog
    # ------------------------------------------------------------------

    def _open_csv(self, path: str, sep: str = None, drop_bad: bool = False,
                  header_spec: str = None, num_cols: int = None):
        """
        Open *path* in a new tab.

        Tracker files (*.elog, *.elog.gz) are parsed by the tracker loader;
        *num_cols* is forwarded if supplied.

        For non-tracker files, if *sep* is None (interactive open) an
        extended dialog is shown that lets the user pick delimiter, header
        spec (N or N:k), num columns, and drop-bad-lines behaviour.  When
        sep is provided (e.g. from CLI or recent-files) the CLI defaults
        are used directly.
        """
        if _is_elog(path):
            nc = num_cols if num_cols is not None else self._cli_num_cols
            self._open_elog(path, num_cols=nc)
            return

        # Non-elog path --------------------------------------------------
        if sep is None:
            # Interactive: show extended options dialog
            choice = self._ask_tracker_csv_options(path)
            if choice is None:
                return
            sep, drop_bad, header_spec, num_cols = choice
        else:
            # CLI / recent: use supplied or CLI defaults
            if header_spec is None:
                header_spec = self._cli_header_spec
            if num_cols is None:
                num_cols = self._cli_num_cols

        self._open_tracker_csv(path, sep=sep, drop_bad=drop_bad,
                               header_spec=header_spec, num_cols=num_cols)

    # ------------------------------------------------------------------
    # Extended options dialog (delimiter + header row + num cols + drop)
    # ------------------------------------------------------------------

    def _ask_tracker_csv_options(self, path: str,
                                 initial_sep: str = None,
                                 initial_drop: bool = False,
                                 initial_header_spec: str = "0",
                                 initial_num_cols: int = 0):
        """Show a dialog to configure CSV reading options.

        Returns ``(sep, drop_bad, header_spec, num_cols)`` or ``None`` if
        the user cancelled.

        *header_spec* is a string ``"N"`` or ``"N:k"`` where N is the
        0-based start row of the header block and k is the number of rows
        it spans (default 1).  All rows in the file that match any row of
        that block are stripped; all other rows become data.
        """
        detected = _detect_delimiter(path) if initial_sep is None else initial_sep
        result   = [None]

        dlg = tk.Toplevel(self)
        dlg.title("Open CSV / Tracker Options")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.transient(self)

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        row_idx = 0

        # ── Detected delimiter ────────────────────────────────────────
        ttk.Label(body, text="Detected delimiter:").grid(
            row=row_idx, column=0, sticky="w", pady=2)
        ttk.Label(body, text=_sep_label(detected),
                  font=FontManager.get("bold")).grid(
            row=row_idx, column=1, sticky="w", padx=8)
        row_idx += 1

        # ── Delimiter chooser ─────────────────────────────────────────
        ttk.Label(body, text="Delimiter:").grid(
            row=row_idx, column=0, sticky="w", pady=2)
        combo_labels = [label for label, _ in _DELIM_CHOICES]
        combo_var    = tk.StringVar()
        combo        = ttk.Combobox(body, textvariable=combo_var,
                                    values=combo_labels, state="readonly", width=20)
        combo.grid(row=row_idx, column=1, sticky="w", padx=8)
        detected_label = next((lbl for lbl, s in _DELIM_CHOICES if s == detected), None)
        if initial_sep is not None:
            init_label = next((lbl for lbl, s in _DELIM_CHOICES if s == initial_sep), None)
            combo_var.set(init_label if init_label else combo_labels[0])
        elif detected_label:
            combo_var.set(detected_label)
        else:
            combo_var.set(combo_labels[0])
        row_idx += 1

        # ── Header spec ───────────────────────────────────────────────
        ttk.Label(body, text="Header row(s)  (N  or  N:k):").grid(
            row=row_idx, column=0, sticky="w", pady=2)
        hdr_var   = tk.StringVar(value=initial_header_spec)
        hdr_entry = ttk.Entry(body, textvariable=hdr_var, width=12)
        hdr_entry.grid(row=row_idx, column=1, sticky="w", padx=8)
        row_idx += 1
        ttk.Label(body,
                  text="N = 0-based start row  ·  k = rows in header block (default 1)",
                  font=FontManager.get("small")).grid(
            row=row_idx, column=0, columnspan=2, sticky="w", padx=4)
        row_idx += 1

        # ── Num columns ───────────────────────────────────────────────
        ttk.Label(body, text="Num columns (0 = auto):").grid(
            row=row_idx, column=0, sticky="w", pady=2)
        ncols_var = tk.IntVar(value=initial_num_cols)
        ttk.Spinbox(body, textvariable=ncols_var, from_=0, to=99999,
                    width=8).grid(row=row_idx, column=1, sticky="w", padx=8)
        row_idx += 1

        # ── Drop-bad-lines checkbox ───────────────────────────────────
        drop_var = tk.BooleanVar(value=initial_drop)
        ttk.Checkbutton(body,
                        text="Drop non-compliant rows (instead of padding)",
                        variable=drop_var).grid(
            row=row_idx, column=0, columnspan=2, sticky="w", pady=4)
        row_idx += 1

        # ── Validation error label ────────────────────────────────────
        err_var = tk.StringVar()
        ttk.Label(body, textvariable=err_var,
                  foreground="red").grid(
            row=row_idx, column=0, columnspan=2, sticky="w")
        row_idx += 1

        # ── Buttons ───────────────────────────────────────────────────
        btn_frame = ttk.Frame(body)
        btn_frame.grid(row=row_idx, column=0, columnspan=2, pady=(8, 0))

        def _ok(_e=None):
            spec = hdr_var.get().strip()
            try:
                _parse_header_spec(spec)
            except (ValueError, IndexError):
                err_var.set(f"Invalid header spec '{spec}'.  Use N or N:k.")
                return
            chosen_label = combo_var.get()
            sep = next((s for lbl, s in _DELIM_CHOICES if lbl == chosen_label), ",")
            result[0] = (sep, drop_var.get(), spec, ncols_var.get())
            dlg.destroy()

        def _cancel():
            dlg.destroy()

        ttk.Button(btn_frame, text="Open",   command=_ok).pack(side=tk.LEFT,  padx=4)
        ttk.Button(btn_frame, text="Cancel", command=_cancel).pack(side=tk.LEFT, padx=4)
        dlg.bind("<Return>",  _ok)
        dlg.bind("<Escape>",  lambda e: _cancel())

        self.update_idletasks()
        dlg.update_idletasks()
        px, py = self.winfo_rootx(), self.winfo_rooty()
        pw, ph = self.winfo_width(), self.winfo_height()
        dw, dh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        dlg.geometry(f"+{px + pw//2 - dw//2}+{py + ph//2 - dh//2}")

        self.wait_window(dlg)
        return result[0]

    # ------------------------------------------------------------------
    # Non-tracker CSV loader (header-spec + num_cols aware)
    # ------------------------------------------------------------------

    def _open_tracker_csv(self, path: str, sep: str = ",",
                          drop_bad: bool = False,
                          header_spec: str = "0",
                          num_cols: int = 0):
        """Load a plain CSV/delimited file using the SuperTracker reader.

        Uses :func:`_read_tracker_csv` which strips any repeated header
        blocks defined by *header_spec* and applies *num_cols*.
        """
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            messagebox.showerror("File not found", path)
            return

        self._set_status(f"Loading {path} …")
        self.update_idletasks()

        try:
            df, n_adj = _read_tracker_csv(path, sep,
                                          header_spec=header_spec,
                                          num_cols=num_cols,
                                          drop_bad=drop_bad)
        except Exception as exc:
            messagebox.showerror("Error reading file", f"{path}\n\n{exc}")
            self._set_status("")
            return

        if df.empty and n_adj == 0:
            messagebox.showinfo("Empty file", f"No data found in:\n{path}")
            self._set_status("")
            return

        model_root = os.path.dirname(path)
        table = FilteredTable(self._notebook, model_root=model_root)
        basename = os.path.basename(path)
        table.set_tab_label(basename)
        table.load(df)

        # Attach metadata for reload / tooltip
        table._sep         = sep
        table._path        = path
        table._drop_bad    = drop_bad
        table._header_spec = header_spec
        table._num_cols    = num_cols

        sep_tag  = f" [{_sep_label(sep)}]" if sep != "," else ""
        adj_note = f"  ({n_adj} rows adjusted)" if n_adj else ""
        self._notebook.add(table, text=f"  {basename}{sep_tag}  ")
        self._notebook.select(table)

        entry = (path, sep)
        if entry in self._recent:
            self._recent.remove(entry)
        self._recent.insert(0, entry)
        if len(self._recent) > _RECENT_MAX:
            self._recent = self._recent[:_RECENT_MAX]
        self._refresh_recent_menu()

        nrows, ncols_df = df.shape
        self._set_status(
            f"Loaded {nrows:,} rows × {ncols_df} cols  |  "
            f"delimiter: {_sep_label(sep)}  "
            f"header: {header_spec}  "
            f"num cols: {num_cols or 'auto'}"
            f"{adj_note}  |  {path}"
        )

    # ------------------------------------------------------------------
    # Tracker file loader
    # ------------------------------------------------------------------

    def _open_elog(self, path: str, num_cols: int = 0):
        """
        Parse a tracker file and load it into a new FilteredTable tab.

        Handles both plain .elog and gzip-compressed .elog.gz transparently.
        Repeated header blocks are stripped; only data rows are kept.

        Parameters
        ----------
        num_cols : int
            When > 0 override the inferred column count (same as _load_elog_as_df).
        """
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            messagebox.showerror("File not found", path)
            return

        self._set_status(f"Loading {path} ...")
        self.update_idletasks()

        try:
            df = _load_elog_as_df(path, num_cols=num_cols)
        except Exception as exc:
            messagebox.showerror("Error reading tracker file", f"{path}\n\n{exc}")
            self._set_status("")
            return

        model_root = os.path.dirname(path)
        table = FilteredTable(self._notebook, model_root=model_root)
        # Inject hex/binary-aware RANGE filter — tracker tabs only.
        table._col_filter_fn = _xt_apply_col_filter

        basename = os.path.basename(path)
        table.set_tab_label(basename)
        table.load(df)
        table._sep       = "|"
        table._path      = path
        table._num_cols  = num_cols
        table._drop_bad  = False
        table._header_spec = "0"

        self._notebook.add(table, text=f"  {basename}  ")
        self._notebook.select(table)

        # Track in the recent-files list (same format as SuperCSV: (path, sep))
        entry = (path, "|")
        if entry in self._recent:
            self._recent.remove(entry)
        self._recent.insert(0, entry)
        if len(self._recent) > _RECENT_MAX:
            self._recent = self._recent[:_RECENT_MAX]
        self._refresh_recent_menu()

        nc_note = f"  num cols: {num_cols}" if num_cols else ""
        rows, cols = df.shape
        self._set_status(
            f"Loaded {rows:,} rows x {cols} columns  |  CTE tracker{nc_note}  |  {path}"
        )

    # ------------------------------------------------------------------
    # Reload with delimiter — extended to remember header_row / num_cols
    # ------------------------------------------------------------------

    def _reload_with_delimiter(self):
        """Close current tab and re-open the same file with updated options."""
        current = self._notebook.select()
        if not current:
            messagebox.showinfo("No tab", "No file is currently open.")
            return
        widget = self._notebook.nametowidget(current)
        path = getattr(widget, "_path", None)
        if not path:
            messagebox.showinfo("No file", "Cannot determine the file path for this tab.")
            return

        if _is_elog(path):
            # Elog: only num_cols is configurable.  Prompt with a simple dialog.
            old_nc = getattr(widget, "_num_cols", 0)
            nc_var = tk.IntVar(value=old_nc)
            dlg = tk.Toplevel(self)
            dlg.title("Tracker Options")
            dlg.resizable(False, False)
            dlg.grab_set()
            dlg.transient(self)
            body = ttk.Frame(dlg, padding=12)
            body.pack(fill=tk.BOTH, expand=True)
            ttk.Label(body, text="Num columns (0 = auto):").grid(
                row=0, column=0, sticky="w", pady=4)
            ttk.Spinbox(body, textvariable=nc_var, from_=0, to=99999,
                        width=8).grid(row=0, column=1, sticky="w", padx=8)
            result = [None]
            def _ok(_e=None):
                result[0] = nc_var.get()
                dlg.destroy()
            def _cancel():
                dlg.destroy()
            bf = ttk.Frame(body)
            bf.grid(row=1, column=0, columnspan=2, pady=(8, 0))
            ttk.Button(bf, text="Reload", command=_ok).pack(side=tk.LEFT, padx=4)
            ttk.Button(bf, text="Cancel", command=_cancel).pack(side=tk.LEFT, padx=4)
            dlg.bind("<Return>", _ok)
            dlg.bind("<Escape>", lambda e: _cancel())
            self.update_idletasks()
            dlg.update_idletasks()
            px, py = self.winfo_rootx(), self.winfo_rooty()
            pw, ph = self.winfo_width(), self.winfo_height()
            dw, dh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
            dlg.geometry(f"+{px + pw//2 - dw//2}+{py + ph//2 - dh//2}")
            self.wait_window(dlg)
            if result[0] is None:
                return
            self._notebook.forget(current)
            self._open_elog(path, num_cols=result[0])
            return

        # Non-elog: show full options dialog pre-populated with current values
        old_sep  = getattr(widget, "_sep",         ",")
        old_drop = getattr(widget, "_drop_bad",    False)
        old_spec = getattr(widget, "_header_spec", "0")
        old_nc   = getattr(widget, "_num_cols",    0)
        choice = self._ask_tracker_csv_options(
            path,
            initial_sep=old_sep,
            initial_drop=old_drop,
            initial_header_spec=old_spec,
            initial_num_cols=old_nc,
        )
        if choice is None:
            return
        sep, drop_bad, header_spec, num_cols = choice
        self._notebook.forget(current)
        self._open_tracker_csv(path, sep=sep, drop_bad=drop_bad,
                               header_spec=header_spec, num_cols=num_cols)

    # ------------------------------------------------------------------
    # About dialog override
    # ------------------------------------------------------------------

    def _show_about(self):
        win = tk.Toplevel(self)
        win.title("About SuperTracker")
        win.resizable(True, True)
        txt = tk.Text(win, wrap="word", relief=tk.FLAT,
                      font=FontManager.get("small"),
                      bg="#ffffff", fg="#222222",
                      padx=12, pady=10, width=120, height=20)
        txt.insert("1.0",
            "SuperTracker — CTE tracker file viewer\n\n"
            "Built on SuperCSV and the FilteredTable widget.\n\n"
            "Supported file formats:\n"
            "  .elog, .elog.gz  — pipe-delimited CTE tracker logs\n"
            "                     (3-line repeated header every ~50 rows)\n"
            "  .csv, .tsv, .psv — standard delimited files\n\n"
            "All SuperCSV features available:\n"
            "  • Per-column filter boxes with boolean expressions\n"
            "    e.g.  (SMT) OR (THREAD)\n"
            "  • Global Logic bar  e.g.  1 AND NOT 3\n"
            "  • Hex/binary-aware RANGE filter (tracker files):\n"
            "    e.g.  RANGE(0x10-0xff)  /  RANGE(0b10-0b1111)\n"
            "  • Right-click paths → open in editor at correct line\n"
            "  • Column picker (hide/show columns)\n"
            "  • Fit Cols ↔ button + auto-refit on font change and window resize\n"
            "  • Email filtered view as CSV attachment\n"
            "  • Export filtered view to CSV\n"
            "  • Sort by clicking column headers\n"
            "  • Font-size control  (Ctrl++  /  Ctrl+-  /  Ctrl+0)\n\n"
            "SuperTracker-only features:\n"
            "  • Header row: specify which row index (0-based) is the column header\n"
            "  • Num columns: override the auto-detected column count\n\n"
            "Keyboard shortcuts:\n"
            "  Ctrl+O      Open file(s)\n"
            "  Ctrl+W      Close current tab\n"
            "  Ctrl+Tab    Next tab\n"
            "  Ctrl+Q      Quit"
        )
        txt.configure(state="disabled")
        txt.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 0))
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=6)
        win.transient(self)
        win.grab_set()
        self.update_idletasks()
        px, py = self.winfo_rootx(), self.winfo_rooty()
        pw, ph = self.winfo_width(), self.winfo_height()
        win.update_idletasks()
        ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry(f"+{px + pw//2 - ww//2}+{py + ph//2 - wh//2}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="supertracker",
        description=(
            "Interactive CTE tracker file viewer for pipe-delimited and standard delimited files."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Open one tracker file:
  python3 supertracker.py fe_ifu_ic_sb_trk_T0.elog.gz

  # Open several tracker logs:
  python3 supertracker.py trk_T0.elog.gz trk_T1.elog.gz

  # Mix tracker and CSV files:
  python3 supertracker.py results.elog.gz summary.csv

  # Open a space-delimited file, header block starting at row 2 (3 rows tall), 8 columns:
  python3 supertracker.py -d ' ' --header-row 2:3 --num-cols 8 my_report.txt

  # Drop non-compliant rows instead of padding:
  python3 supertracker.py --drop-bad-lines data.csv

  # Open file dialog (no args):
  python3 supertracker.py
""",
    )
    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help=(
            "Tracker or delimited files to open.  An Open dialog is shown when no files are supplied."
        ),
    )
    parser.add_argument(
        "-d", "--delimiter",
        metavar="SEP",
        default=None,
        help=(
            "Delimiter for non-tracker files on the command line (e.g. ',' '\\t' '|').  Tracker files are always auto-parsed."
        ),
    )
    parser.add_argument(
        "--header-row",
        metavar="N[:k]",
        type=str,
        default="0",
        help=(
            "Header block specification for non-tracker files.  "
            "N is the 0-based start row; k (optional) is the number of rows in the block (default 1).  "
            "E.g. '0' = single header on row 0, '2:3' = 3-row header starting at row 2.  "
            "All occurrences of the header block throughout the file are stripped."
        ),
    )
    parser.add_argument(
        "--num-cols",
        metavar="N",
        type=int,
        default=0,
        help=(
            "Override the number of columns.  0 means auto-detect from the header row.  "
            "Applies to both tracker and non-tracker files."
        ),
    )
    parser.add_argument(
        "--drop-bad-lines",
        action="store_true",
        default=False,
        help="Drop rows that do not comply with the expected number of columns instead of padding them.",
    )
    args = parser.parse_args()

    cli_sep = args.delimiter.replace("\\t", "\t") if args.delimiter else None

    app = SuperTracker(
        args.files,
        cli_sep=cli_sep,
        cli_drop_bad=args.drop_bad_lines,
        cli_header_spec=args.header_row,
        cli_num_cols=args.num_cols,
    )
    app.mainloop()


if __name__ == "__main__":
    main()
