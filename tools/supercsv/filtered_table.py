"""
FilteredTable — sortable, per-column-filterable Treeview backed by a pandas DataFrame.
"""

import json
import os
import re
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

import pandas as pd

from font_manager import FontManager
from theme_manager import ThemeManager

# ── Email tool (scripts/email/email_sender.py) ────────────────────────────────
_email_dir = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "email")
)
if _email_dir not in sys.path:
    sys.path.insert(0, _email_dir)
from email_sender import EmailDialog, send_email_with_attachment  # noqa: E402

# File extensions treated as openable paths
_PATH_EXTENSIONS = {
    ".v", ".sv", ".svh", ".vs", ".e", ".py", ".csv", ".txt",
    ".icf", ".hier", ".xml", ".yaml", ".yml", ".json", ".tcsh", ".sh",
}

# Editors tried in order (first found wins)
_EDITORS = ["gvim", "gedit", "xdg-open", "emacs", "nedit", "mousepad", "kate"]


def _fmt_num(v) -> str:
    """Format a numeric value: integer if whole, else up to 4 decimal places."""
    try:
        if float(v) == int(float(v)):
            return str(int(float(v)))
        return f"{float(v):.4g}"
    except (TypeError, ValueError):
        return str(v)


def _looks_like_path(value: str) -> bool:
    """Return True if the cell value looks like a file path."""
    v = value.strip()
    if not v:
        return False
    # Absolute path
    if v.startswith("/"):
        return True
    # Relative path with a known extension
    _, ext = os.path.splitext(v)
    if ext.lower() in _PATH_EXTENSIONS:
        return True
    # Relative path containing a directory separator
    if "/" in v and not v.startswith("#"):
        return True
    return False


def _resolve_path(value: str, model_root: str) -> str:
    """Return an absolute path, resolving relative ones against model_root."""
    v = value.strip()
    if os.path.isabs(v):
        return v
    candidate = os.path.join(model_root, v)
    return candidate


def _open_file(path: str, line: Optional[int] = None):
    """Open a file in the first available editor, optionally at a line."""
    for editor in _EDITORS:
        try:
            which = subprocess.run(["which", editor], capture_output=True, text=True)
            if which.returncode != 0:
                continue
            if editor == "gvim" and line:
                subprocess.Popen([editor, f"+{line}", path])
            elif editor == "emacs" and line:
                subprocess.Popen([editor, f"+{line}", path])
            else:
                subprocess.Popen([editor, path])
            return
        except Exception:
            continue
    messagebox.showerror("No editor found",
                         f"Could not find a text editor to open:\n{path}\n\n"
                         f"Tried: {', '.join(_EDITORS)}")


# ── Numeric representation helpers (value info bar) ──────────────────────────

def _group4(digits: str) -> str:
    """Insert '_' every 4 digits counting from the RIGHT (like numeric literals).

    Examples:
        '101010'   → '10_1010'
        '11111111' → '1111_1111'
        'DEADBEEF' → 'DEAD_BEEF'
        '10000'    → '1_0000'
        '2A'       → '2A'          (≤4 chars — no separator)
    """
    if len(digits) <= 4:
        return digits
    # Reverse, chunk into 4s, reverse each chunk, reverse the chunk list, join.
    rev = digits[::-1]
    chunks = [rev[i:i+4][::-1] for i in range(0, len(rev), 4)]
    return "_".join(reversed(chunks))


def _try_parse_numeric(raw: str):
    """Return a float if *raw* looks like a number (dec/hex/bin/oct), else None."""
    s = raw.strip()
    if not s:
        return None
    try:
        return float(int(s, 0))   # handles 0x, 0b, 0o, plain int
    except (ValueError, TypeError):
        pass
    try:
        return float(s)           # plain float like "3.14"
    except (ValueError, TypeError):
        return None


def _numeric_repr(raw: str) -> str:
    """If *raw* is a numeric string return 'dec | hex | bin' label, else ''."""
    v = _try_parse_numeric(raw)
    if v is None:
        return ""
    # Only show integer representations for integer-valued numbers.
    if v == int(v):
        n = int(v)
        return f"  {n}  =  0x{n:X}  =  0b{n:b}"
    # Float: just show the decimal value (no meaningful hex/bin form)
    return f"  {v}"


# ── RANGE filter helpers ─────────────────────────────────────────────────────
_RANGE_PART_RE   = re.compile(r'^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*$')
_RANGE_SCALAR_RE = re.compile(r'^\s*(-?\d+(?:\.\d+)?)\s*$')

def _parse_range_pairs(spec: str):
    """
    Parse the contents of RANGE(...) into a list of (lo, hi) float pairs.

    Each comma-separated segment may be:
    - ``lo-hi``  → inclusive range, e.g. ``10000-20000``
    - ``value``  → exact scalar match, e.g. ``16``  (stored as (v, v))

    Examples::

        "10000-20000"              → [(10000, 20000)]
        "10000-20000, 25000-30000" → [(10000, 20000), (25000, 30000)]
        "10-20, 16, 30-50"        → [(10, 20), (16, 16), (30, 50)]

    Segments that match neither form are silently skipped.
    """
    pairs = []
    for part in spec.split(','):
        p = part.strip()
        m = _RANGE_PART_RE.match(p)
        if m:
            pairs.append((float(m.group(1)), float(m.group(2))))
            continue
        m2 = _RANGE_SCALAR_RE.match(p)
        if m2:
            v = float(m2.group(1))
            pairs.append((v, v))
    return pairs

def _range_mask(series: "pd.Series", pairs) -> "pd.Series":
    """
    Return a boolean mask that is True wherever *series* (coerced to numeric)
    falls within **any** of the given (lo, hi) pairs (both ends inclusive).
    Non-numeric cells are treated as False.
    """
    src = series.str.strip() if hasattr(series, 'str') else series
    numeric = pd.to_numeric(src, errors='coerce')
    mask = pd.Series(False, index=series.index)
    for lo, hi in pairs:
        mask = mask | ((numeric >= lo) & (numeric <= hi))
    return mask
# ─────────────────────────────────────────────────────────────────────────────


def _apply_col_filter(series: "pd.Series", text: str) -> "pd.Series":
    """
    Apply one column-filter text to a pandas Series and return a boolean mask.

    Supported filter syntaxes
    ─────────────────────────
    Plain substring (default when no keywords present):
        foo               → rows whose cell contains "foo" (case-insensitive)

    Numeric range:
        RANGE(lo-hi)                     → lo ≤ value ≤ hi
        RANGE(lo-hi, lo-hi, ...)         → value falls in any of the ranges

    Boolean expression (AND / OR / NOT with (pattern) or RANGE(...) atoms):
        (SMT) OR (THREAD)                → contains "SMT" or "THREAD"
        NOT(DEBUG)                       → does NOT contain "DEBUG"
        (SMT) AND NOT(DEBUG)             → contains "SMT" and not "DEBUG"
        RANGE(10000-20000, 25000-30000)  → numeric range (standalone)
        RANGE(10000-20000) AND (IfReset) → range AND substring
        NOT RANGE(10000-20000)           → outside the range

    Precedence: NOT > AND > OR.
    Atoms are either ``(pattern)`` for substring or ``RANGE(...)`` for numeric range.
    """
    text = text.strip()
    if not text:
        return pd.Series(True, index=series.index)

    # ── Simple substring mode (no boolean keywords) ──────────────────
    if not re.search(r'\b(?:AND|OR|NOT|RANGE)\b', text, re.IGNORECASE):
        return series.str.lower().str.contains(text.lower(), na=False, regex=False)

    # ── Boolean expression mode ───────────────────────────────────────
    # Tokenise into: AND / OR / NOT keywords, RANGE(...) atoms, (pattern) atoms,
    # and bare-word atoms (no parentheses required).
    # RANGE(...) must come before bare (...) so it isn't consumed piecemeal.
    # [^\s()]+ at the end captures bare words like IC, SB, -- etc.
    _BOOL_KW = {"AND", "OR", "NOT"}
    raw = re.findall(
        r'\bRANGE\([^)]*\)|\([^()]*\)|\b(?:AND|OR|NOT)\b|[^\s()]+',
        text, re.IGNORECASE
    )
    tokens = [t.strip() for t in raw if t.strip()]
    pos    = [0]

    all_true = pd.Series(True, index=series.index)

    def peek():
        return tokens[pos[0]].upper() if pos[0] < len(tokens) else None

    def consume():
        t = tokens[pos[0]]; pos[0] += 1; return t

    def match_atom(atom: str) -> "pd.Series":
        # RANGE(...) atom — numeric range filter
        if atom.upper().startswith("RANGE("):
            pairs = _parse_range_pairs(atom[6:-1])   # strip leading "RANGE(" and trailing ")"
            return _range_mask(series, pairs) if pairs else all_true.copy()
        # (pattern) atom — case-insensitive substring filter
        inner = atom[1:-1].strip().lower()            # strip surrounding ( )
        if not inner:
            return all_true.copy()
        return series.str.lower().str.contains(inner, na=False, regex=False)

    def parse_or():
        left = parse_and()
        while peek() == "OR":
            consume()
            left = left | parse_and()
        return left

    def parse_and():
        left = parse_not()
        while peek() == "AND":
            consume()
            left = left & parse_not()
        return left

    def parse_not():
        if peek() == "NOT":
            consume()
            return ~parse_atom_pos()
        return parse_atom_pos()

    def parse_atom_pos():
        t = peek()
        if t is None:
            return all_true.copy()
        tok = consume()
        if tok.startswith("(") or tok.upper().startswith("RANGE("):
            return match_atom(tok)
        # Bare word atom — treat as substring (e.g. "IC OR SB")
        if tok.upper() not in _BOOL_KW:
            return series.str.lower().str.contains(tok.lower(), na=False, regex=False)
        # Leaked keyword in atom position → no-constraint
        return all_true.copy()

    try:
        result = parse_or()
    except Exception:
        # Parse failure → fall back to simple substring
        result = series.str.lower().str.contains(text.lower(), na=False, regex=False)

    return result


class FilteredTable(ttk.Frame):
    """
    A Treeview table backed by a pandas DataFrame with:
    - Per-column text filter entries (debounced 200 ms)
    - Click-to-sort column headers (asc/desc toggle, ▲/▼ indicator)
    - Right-click context menu: open file / open folder / copy value / copy row
    - Row count label  "Showing N of M rows"
    - Export CSV button for the currently filtered view
    - Font-size-aware (responds to FontManager changes)

    Usage:
        table = FilteredTable(parent, model_root="/path/to/repo")
        table.load(df)          # replace displayed data
        table.clear()           # clear everything
    """

    _SORT_NONE = ""
    _SORT_ASC  = " ▲"
    _SORT_DESC = " ▼"

    def __init__(self, parent, model_root: str = "", **kwargs):
        super().__init__(parent, **kwargs)
        self._model_root = model_root
        self._tab_label: str = ""
        self._df_full: pd.DataFrame = pd.DataFrame()
        self._df_view: pd.DataFrame = pd.DataFrame()
        self._all_columns: List[str] = []   # all columns from DataFrame
        self._columns: List[str] = []        # currently visible columns
        self._hidden_cols: set = set()       # columns hidden by user
        self._sort_col: Optional[str] = None
        self._sort_asc: bool = True
        self._filter_vars: dict = {}
        self._filter_entries: dict = {}
        self._after_id = None
        self._sync_id = None
        self._font_after_id = None
        self._fit_after_id = None      # debounce handle for _fit_columns
        self._resize_after_id = None   # debounce handle for window resize
        self._last_scroll_first: float = 0.0   # last reported xview position
        self._session_path: Optional[str] = None  # path of last saved/loaded session file

        # Pluggable column-filter function.  SuperTracker overrides this on
        # elog tabs to get hex/binary-aware RANGE parsing.
        self._col_filter_fn = _apply_col_filter

        # Auto-filter toggle: when True filters run 200 ms after each
        # keystroke (legacy behaviour).  When False, filters only run on
        # Enter or the Apply button.
        self._auto_filter_var = tk.BooleanVar(value=False)

        # Register for theme-change notifications so we can recolor non-ttk
        # widgets (filter entries, tree row tags) when the theme switches.
        ThemeManager.add_listener(self._on_theme_change)

        # --- Top bar: row count + export + email + columns buttons ---
        topbar = ttk.Frame(self)
        topbar.pack(fill=tk.X, pady=(0, 2))
        self._count_label = ttk.Label(topbar, text="No data")
        self._count_label.pack(side=tk.LEFT, padx=4)
        ttk.Button(topbar, text="📧 Email…",    command=self._email_view).pack(side=tk.RIGHT, padx=4)
        ttk.Button(topbar, text="Export CSV…", command=self._export).pack(side=tk.RIGHT, padx=4)
        self._col_btn = ttk.Button(topbar, text="Columns ▾",
                                   command=self._open_column_picker)
        self._col_btn.pack(side=tk.RIGHT, padx=4)
        ttk.Button(topbar, text="Fit Cols ↔",
                   command=self._fit_columns).pack(side=tk.RIGHT, padx=4)
        ttk.Separator(topbar, orient=tk.VERTICAL).pack(side=tk.RIGHT, padx=3, fill=tk.Y, pady=2)
        ttk.Button(topbar, text="Load Session…",
                   command=self._load_session).pack(side=tk.RIGHT, padx=4)
        ttk.Button(topbar, text="Save Session…",
                   command=self._save_session).pack(side=tk.RIGHT, padx=4)
        ttk.Separator(topbar, orient=tk.VERTICAL).pack(side=tk.RIGHT, padx=3, fill=tk.Y, pady=2)
        ttk.Button(topbar, text="Summary…",
                   command=self._summarize_table).pack(side=tk.RIGHT, padx=4)

        # --- Filter expression bar (boolean combinator for column filters) ---
        expr_bar = ttk.Frame(self)
        expr_bar.pack(fill=tk.X, pady=(0, 1))
        ttk.Label(expr_bar, text="Logic:").pack(side=tk.LEFT, padx=(4, 2))
        self._filter_expr_var = tk.StringVar()
        self._filter_expr_var.trace_add("write", lambda *_: self._schedule_filter())
        self._filter_expr_entry = tk.Entry(
            expr_bar, textvariable=self._filter_expr_var,
            font=FontManager.get("small"),
            bg=ThemeManager.get("filter_idle_bg"),
            fg=ThemeManager.get("entry_fg"),
            insertbackground=ThemeManager.get("entry_insert"),
            relief=tk.SOLID, bd=1, width=35,
        )
        self._filter_expr_entry.pack(side=tk.LEFT, padx=(0, 4), fill=tk.X, expand=True)
        self._filter_expr_entry.bind("<Return>", lambda e: self._apply_filters())
        # Hint: shows which filter numbers map to which columns
        self._filter_hint_label = ttk.Label(expr_bar, text="", foreground=ThemeManager.get("hint_fg"))
        self._filter_hint_label.pack(side=tk.LEFT, padx=4)
        def _show_expr_help():
            # Custom dialog — wide enough to read without wrapping.
            win = tk.Toplevel(self)
            win.title("Filter Expression Help")
            win.resizable(True, True)
            txt = tk.Text(win, wrap="word", relief=tk.FLAT,
                          font=FontManager.get("small"),
                          bg=ThemeManager.get("entry_bg"), fg=ThemeManager.get("entry_fg"),
                          padx=10, pady=8, width=80, height=18)
            txt.insert("1.0",
                "Use column numbers (1, 2, 3…) to reference the filter boxes left to right.\n\n"
                "Operators (case-insensitive):  AND   OR   NOT   ( )\n\n"
                "Examples:\n"
                "  1 OR 2              — rows matching filter 1 OR filter 2\n"
                "  1 AND NOT 3         — rows matching filter 1 but NOT filter 3\n"
                "  (1 OR 2) AND 4      — combined with filter 4\n\n"
                "Leave blank for default behaviour: all active filters are AND'd together.\n\n"
                "Active filter numbers are shown to the right of this box as you type.\n\n"
                "── Column filter syntax ──────────────────────────────────────────────────\n\n"
                "  hello              — substring match (case-insensitive)\n"
                "  (hello) OR (world) — boolean expression with substring atoms\n"
                "  NOT (foo)          — rows that do NOT contain 'foo'\n"
                "  RANGE(lo-hi)       — numeric range, both ends inclusive\n"
                "  RANGE(10-20, 30)   — multiple ranges / scalar values\n\n"
                "  Tracker tool only — hex/binary-aware RANGE:\n"
                "    RANGE(0x10-0xff)  same as RANGE(16-255)\n"
                "    RANGE(0b10-0b11)  same as RANGE(2-3)\n"
            )
            txt.configure(state="disabled")
            txt.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 0))
            ttk.Button(win, text="Close", command=win.destroy).pack(pady=6)
            win.transient(self)
            win.grab_set()
            # Centre over parent window
            self.update_idletasks()
            px, py = self.winfo_rootx(), self.winfo_rooty()
            pw, ph = self.winfo_width(), self.winfo_height()
            win.update_idletasks()
            ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
            win.geometry(f"+{px + pw//2 - ww//2}+{py + ph//2 - wh//2}")
        ttk.Button(expr_bar, text="?", width=2,
                   command=_show_expr_help).pack(side=tk.LEFT, padx=(0, 4))
        # Apply button — always available; essential in manual mode.
        ttk.Button(expr_bar, text="Apply ⏎",
                   command=self._apply_filters).pack(side=tk.LEFT, padx=(0, 4))
        # Clear all filters button.
        ttk.Button(expr_bar, text="Clear ✕",
                   command=self._clear_filters).pack(side=tk.LEFT, padx=(0, 4))
        # Auto/Manual toggle checkbox.
        self._auto_cb = ttk.Checkbutton(
            expr_bar, text="Auto",
            variable=self._auto_filter_var,
            command=self._on_auto_filter_toggle,
        )
        self._auto_cb.pack(side=tk.LEFT, padx=(0, 4))

        # --- Numeric value info bar: shown when selected cell is numeric ---
        # Three groups: [DEC value 📋] [HEX value 📋] [BIN value 📋]
        # Font is fixed at 24 pt (independent of the global font-size control).
        _VAL_FONT = ("TkFixedFont", 24, "bold")
        _BTN_FONT = ("TkDefaultFont", 10)
        self._val_bar = ttk.Frame(self)
        self._val_bar.pack(fill=tk.X, pady=(2, 2))

        self._val_groups: list = []  # list of (label_widget, copy_var) tuples
        for prefix in ("DEC", "HEX", "BIN"):
            grp = ttk.Frame(self._val_bar, relief=tk.GROOVE, borderwidth=1)
            grp.pack(side=tk.LEFT, padx=6, pady=2)
            ttk.Label(grp, text=prefix, font=_BTN_FONT,
                      foreground=ThemeManager.get("dim_fg")).pack(side=tk.LEFT, padx=(4, 2))
            # BIN panel is 3× wider to accommodate long binary strings with _ separators
            panel_width = 60 if prefix == "BIN" else 20
            lbl = tk.Label(grp, text="", font=_VAL_FONT,
                           fg=ThemeManager.get("sel_fg"),
                           bg=ThemeManager.get("sel_bg"),
                           padx=6, pady=2, anchor="w", width=panel_width)
            lbl.pack(side=tk.LEFT)
            # Copy button: copies lbl's current text to clipboard
            copy_btn = tk.Button(
                grp, text="📋", font=_BTN_FONT,
                relief=tk.FLAT, cursor="hand2",
                bg=ThemeManager.get("bg"), fg=ThemeManager.get("fg"),
                activebackground=ThemeManager.get("sel_bg"),
                activeforeground=ThemeManager.get("sel_fg"),
                command=lambda l=lbl: self._copy_val(l.cget("text").strip()),
            )
            copy_btn.pack(side=tk.LEFT, padx=(2, 4))
            self._val_groups.append(lbl)
        # Store refs by name for easy update
        self._val_dec_lbl, self._val_hex_lbl, self._val_bin_lbl = self._val_groups

        # --- Main area: vsb on right, filter canvas + tree on left ---
        # This layout ensures the filter row is exactly as wide as the tree
        # (excluding the vertical scrollbar) so columns stay aligned.
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)

        vsb = ttk.Scrollbar(main_frame, orient=tk.VERTICAL)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        left_frame = ttk.Frame(main_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Filter canvas: scrolls horizontally in sync with the Treeview.
        # An inner ttk.Frame holds the actual Entry widgets.
        self._filter_canvas = tk.Canvas(left_frame, height=30,
                                        highlightthickness=0)
        self._filter_canvas.pack(fill=tk.X, side=tk.TOP)
        self._filter_inner = ttk.Frame(self._filter_canvas)
        self._filter_win = self._filter_canvas.create_window(
            (0, 0), window=self._filter_inner, anchor="nw"
        )
        # Keep canvas height in sync with inner frame's natural height.
        # scrollregion is managed exclusively by _sync_filter_widths so that
        # it always matches the Treeview's total column width (proportional
        # xview fractions will then align correctly).
        self._filter_inner.bind(
            "<Configure>",
            lambda e: self._filter_canvas.configure(height=e.height)
        )

        # Treeview
        self._tree = ttk.Treeview(left_frame, show="headings", selectmode="browse")
        self._tree.configure(yscrollcommand=vsb.set,
                             xscrollcommand=self._on_tree_hscroll)
        self._tree.pack(fill=tk.BOTH, expand=True)
        vsb.configure(command=self._tree.yview)

        # Refit columns when the Treeview is resized (window resize events).
        self._tree.bind("<Configure>", self._on_resize)

        # Shared horizontal scrollbar at the bottom
        hsb = ttk.Scrollbar(self, orient=tk.HORIZONTAL,
                             command=self._on_h_scroll)
        hsb.pack(fill=tk.X)
        self._hsb = hsb

        self._apply_row_colors()

        # Sync filter widths whenever the user drags a column separator
        self._tree.bind("<ButtonRelease-1>",
                        lambda _: self._schedule_sync())

        # Track which column was last clicked so the value info bar knows
        # which cell to inspect when <<TreeviewSelect>> fires.
        self._last_clicked_col: Optional[str] = None
        def _on_tree_click(event):
            region = self._tree.identify_region(event.x, event.y)
            if region == "cell":
                col_id = self._tree.identify_column(event.x)
                # col_id is "#1", "#2", … — convert to column name
                try:
                    idx = int(col_id.lstrip("#")) - 1
                    self._last_clicked_col = self._columns[idx] if 0 <= idx < len(self._columns) else None
                except (ValueError, IndexError):
                    self._last_clicked_col = None
        self._tree.bind("<Button-1>", _on_tree_click)

        # Show numeric representation in all bases when a cell is selected
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # Right-click context menu
        self._ctx_menu = tk.Menu(self._tree, tearoff=0)
        self._tree.bind("<Button-3>", self._on_right_click)
        # Right-click on a column heading → column summary
        self._tree.bind("<Button-3>", self._on_right_click)
        # Heading right-click is detected inside _on_right_click via identify_region

        # Respond to font size changes
        FontManager.add_listener(self._on_font_change)
        # Deregister both listeners when this widget is destroyed
        self.bind("<Destroy>", self._on_widget_destroy, add="+")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_model_root(self, model_root: str):
        """Update the model root used to resolve relative file paths."""
        self._model_root = model_root

    def set_tab_label(self, label: str):
        """Set the tab label used as the default email subject."""
        self._tab_label = label

    def load(self, df: pd.DataFrame):
        """Replace the table contents with a new DataFrame."""
        self._df_full = df.copy()
        self._all_columns = list(df.columns)
        # Carry over hidden cols that still exist in the new data; clear stale ones
        self._hidden_cols = {c for c in self._hidden_cols if c in self._all_columns}
        self._columns = [c for c in self._all_columns if c not in self._hidden_cols]
        self._sort_col = None
        self._sort_asc = True
        self._rebuild_columns()
        self._rebuild_filters()
        self._apply_filters()
        self._update_col_btn_label()

    def clear(self):
        self._df_full = pd.DataFrame()
        self._df_view = pd.DataFrame()
        self._all_columns = []
        self._columns = []
        self._hidden_cols = set()
        self._filter_vars = {}
        self._filter_entries = {}
        for child in self._filter_inner.winfo_children():
            child.destroy()
        self._tree.configure(columns=[])
        self._tree.delete(*self._tree.get_children())
        self._count_label.configure(text="No data")
        self._update_col_btn_label()

    # ------------------------------------------------------------------
    # Right-click context menu
    # ------------------------------------------------------------------

    def _on_right_click(self, event: tk.Event):
        """Show context menu; determine which cell was right-clicked.
        If the click is on a column heading, show a column-summary menu instead."""
        region = self._tree.identify_region(event.x, event.y)
        col_id = self._tree.identify_column(event.x)
        col_index = int(col_id.lstrip("#")) - 1 if col_id else -1

        # ── Heading right-click → column summary ─────────────────────────
        if region == "heading":
            if 0 <= col_index < len(self._columns):
                col_name = self._columns[col_index]
                menu = tk.Menu(self._tree, tearoff=0)
                menu.add_command(
                    label=f"📊  Summarize column: {col_name}",
                    command=lambda c=col_name: self._summarize_column(c),
                )
                menu.tk_popup(event.x_root, event.y_root)
            return

        row_id = self._tree.identify_row(event.y)

        if not row_id:
            return

        # Select the row under the cursor
        self._tree.selection_set(row_id)

        # Get clicked cell value and all row values
        row_values = self._tree.item(row_id, "values")

        cell_value = str(row_values[col_index]) if 0 <= col_index < len(row_values) else ""
        full_row   = "\t".join(str(v) for v in row_values)

        # ── Discover line-offset once from the clicked column (or the first
        # path column found left-to-right in the row) so that ALL path
        # columns use the same strategy/offset.  This prevents pass-3 from
        # picking a completely different line column for the 2nd/3rd path.
        discovered_offset: Optional[int] = None
        if _looks_like_path(cell_value):
            discovered_offset = self._discover_line_offset(col_index)
        if discovered_offset is None:
            # Try left-to-right path columns until one yields an offset
            for i, v in enumerate(row_values):
                if _looks_like_path(str(v)):
                    discovered_offset = self._discover_line_offset(i)
                    if discovered_offset is not None:
                        break

        line_num = self._line_num_with_offset(row_values, col_index, discovered_offset)

        # Build context menu dynamically
        menu = self._ctx_menu
        menu.delete(0, tk.END)

        # ── File path actions (when cell looks like a path) ────────────
        if _looks_like_path(cell_value):
            abs_path = _resolve_path(cell_value, self._model_root)
            exists   = os.path.isfile(abs_path)

            menu.add_command(
                label=f"📄  Open file{' (line ' + str(line_num) + ')' if line_num else ''}",
                command=lambda p=abs_path, ln=line_num: _open_file(p, ln),
                state=tk.NORMAL if exists else tk.DISABLED,
            )
            menu.add_command(
                label="📁  Open containing folder",
                command=lambda p=abs_path: self._open_folder(os.path.dirname(p)),
                state=tk.NORMAL if os.path.isdir(os.path.dirname(abs_path)) else tk.DISABLED,
            )
            menu.add_separator()
            menu.add_command(
                label="📋  Copy file path",
                command=lambda v=abs_path: self._copy_to_clipboard(v),
            )
        else:
            menu.add_command(
                label="📋  Copy cell value",
                command=lambda v=cell_value: self._copy_to_clipboard(v),
            )

        menu.add_command(
            label="📋  Copy full row (tab-separated)",
            command=lambda r=full_row: self._copy_to_clipboard(r),
        )

        # ── Scan all cells in this row for paths ──────────────────────
        # Keep (original_col_index, col_name, path_value) so we can
        # look up the correct line number for each path column.
        path_cells = [
            (i, self._columns[i], str(v))
            for i, v in enumerate(row_values)
            if i != col_index and _looks_like_path(str(v))
        ]
        if path_cells:
            menu.add_separator()
            menu.add_command(label="Other paths in this row:", state=tk.DISABLED)
            for other_col_idx, col_name, pv in path_cells[:6]:   # cap at 6
                abs_p    = _resolve_path(pv, self._model_root)
                # Use the same offset discovered above; fall back to an
                # independent offset only if the forced one gives nothing.
                other_ln = self._line_num_with_offset(
                    row_values, other_col_idx, discovered_offset
                )
                lbl_line = f"  line {other_ln}" if other_ln else ""
                lbl      = f"   {col_name}: {pv}{lbl_line}"
                if len(lbl) > 72:
                    lbl = lbl[:69] + "…"
                menu.add_command(
                    label=lbl,
                    command=lambda p=abs_p, ln=other_ln: _open_file(p, ln),
                    state=tk.NORMAL if os.path.isfile(abs_p) else tk.DISABLED,
                )

        menu.tk_popup(event.x_root, event.y_root)

    def _discover_line_offset(self, path_col_index: int) -> Optional[int]:
        """
        Work out *which column offset* leads to the line-number column for
        ``path_col_index``, using column names only (no row values needed).

        Returns ``line_col_index - path_col_index`` so callers can apply the
        same offset to any other path column.

        Strategy priority (same as before, but returns the offset, not the
        value, so the same structural pattern is re-used for all paths):
          1. Prefix match   – e.g. ``decl_file`` → ``decl_line`` (offset may be ±N)
          2. Adjacent ≤3    – nearest column within 3 positions that has a
                              line keyword, sorted by abs distance
          3. Any line col   – closest line-keyword column anywhere in the row
        """
        _LINE_KWS = ("line", "decl_line", "lineno", "line_no", "line_num", "line_number")

        if path_col_index < 0 or path_col_index >= len(self._columns):
            return None

        path_col_name = self._columns[path_col_index].lower()
        prefix = path_col_name
        for suffix in ("_file", "_path", "_src", "_decl", "_loc"):
            if path_col_name.endswith(suffix):
                prefix = path_col_name[: -len(suffix)]
                break

        line_cols = [
            (i, col)
            for i, col in enumerate(self._columns)
            if any(kw in col.lower() for kw in _LINE_KWS)
        ]
        if not line_cols:
            return None

        # Pass 1 – prefix match
        if prefix:
            for i, col in line_cols:
                if col.lower().startswith(prefix):
                    return i - path_col_index

        # Pass 2 – adjacent within 3 (closest first)
        adjacent = sorted(
            [(i, col) for i, col in line_cols if abs(i - path_col_index) <= 3],
            key=lambda x: abs(x[0] - path_col_index),
        )
        if adjacent:
            return adjacent[0][0] - path_col_index

        # Pass 3 – any line col (closest first)
        closest = min(line_cols, key=lambda x: abs(x[0] - path_col_index))
        return closest[0] - path_col_index

    def _line_num_with_offset(
        self,
        row_values,
        path_col_index: int,
        offset: Optional[int],
    ) -> Optional[int]:
        """
        Read the integer value at ``path_col_index + offset``.
        If ``offset`` is None, or the resulting cell is not a positive integer,
        fall back to ``_discover_line_offset`` for this specific column and
        try that offset instead.
        """
        def _val(idx):
            try:
                v = int(float(str(row_values[idx])))
                return v if v > 0 else None
            except (ValueError, IndexError, TypeError):
                return None

        if offset is not None:
            idx = path_col_index + offset
            if 0 <= idx < len(self._columns):
                v = _val(idx)
                if v is not None:
                    return v

        # Offset didn't work for this column — discover independently
        local_offset = self._discover_line_offset(path_col_index)
        if local_offset is not None:
            idx = path_col_index + local_offset
            if 0 <= idx < len(self._columns):
                return _val(idx)
        return None

    def _guess_line_number(self, row_values, path_col_index: int) -> Optional[int]:
        """Public wrapper kept for external callers."""
        offset = self._discover_line_offset(path_col_index)
        return self._line_num_with_offset(row_values, path_col_index, offset)


    def _copy_to_clipboard(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _open_folder(self, folder: str):
        for cmd in [["xdg-open"], ["nautilus"], ["thunar"], ["dolphin"]]:
            try:
                which = subprocess.run(["which", cmd[0]], capture_output=True)
                if which.returncode == 0:
                    subprocess.Popen(cmd + [folder])
                    return
            except Exception:
                pass
        # Fallback: open a terminal in the folder
        messagebox.showinfo("Folder", folder)

    # ------------------------------------------------------------------
    # Internal: color helpers
    # ------------------------------------------------------------------

    def _apply_row_colors(self):
        self._tree.tag_configure("odd",      background=ThemeManager.get("row_odd_bg"),
                                             foreground=ThemeManager.get("fg"))
        self._tree.tag_configure("even",     background=ThemeManager.get("row_even_bg"),
                                             foreground=ThemeManager.get("fg"))
        self._tree.tag_configure("selected", background=ThemeManager.get("sel_bg"),
                                             foreground=ThemeManager.get("sel_fg"))

    # ------------------------------------------------------------------
    # Internal: font size change
    # ------------------------------------------------------------------

    def _on_font_change(self):
        """Debounced entry point: schedule the real font update in 80 ms.

        Debouncing means rapid A+/A− clicks only trigger one redraw instead of
        one per click.  ttk.Style changes propagate automatically to all
        existing Treeview rows, so NO _repopulate() is needed here.
        """
        if getattr(self, "_font_after_id", None):
            try:
                self.after_cancel(self._font_after_id)
            except tk.TclError:
                pass
        self._font_after_id = self.after(80, self._apply_font_change)

    def _apply_font_change(self):
        """Apply font/style updates without touching row data."""
        self._font_after_id = None
        s = FontManager.size()
        style = ttk.Style(self)
        # Row height and font propagate to all existing rows automatically.
        style.configure("Treeview",         font=FontManager.get("normal"),
                        rowheight=int(s * 2.2))
        style.configure("Treeview.Heading", font=FontManager.get("bold"))
        # Update filter entry fonts (column filters + logic box)
        for entry in self._filter_entries.values():
            try:
                entry.configure(font=FontManager.get("small"))
            except tk.TclError:
                pass
        try:
            self._filter_expr_entry.configure(font=FontManager.get("small"))
        except tk.TclError:
            pass
        # Update superscript number label fonts
        num_font = (FontManager.get("small").cget("family"), max(7, s - 3))
        for lbl in getattr(self, "_filter_num_labels", {}).values():
            try:
                lbl.configure(font=num_font)
            except tk.TclError:
                pass
        self._schedule_sync()
        # Column widths depend on font metrics — refit after the style update
        # has been processed by Tk (small delay ensures measure() uses new font).
        self._schedule_fit()

    # ------------------------------------------------------------------
    # Internal: cleanup on destroy
    # ------------------------------------------------------------------

    def _on_widget_destroy(self, event=None):
        """Remove manager listeners when this widget is destroyed."""
        try:
            FontManager.remove_listener(self._on_font_change)
        except Exception:
            pass
        try:
            ThemeManager.remove_listener(self._on_theme_change)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal: theme change
    # ------------------------------------------------------------------

    def _on_theme_change(self):
        """Re-apply theme colors to non-ttk widgets owned by this instance.

        Called by the ThemeManager listener whenever the theme switches.
        ttk widgets are handled globally by ThemeManager.apply_to_style();
        we only need to re-configure plain tk widgets here.
        """
        # Re-color tree row tags
        self._apply_row_colors()

        # Re-color the Logic: filter entry
        try:
            active = bool(self._filter_expr_var.get().strip())
            new_bg = (ThemeManager.get("filter_active_bg") if active
                      else ThemeManager.get("filter_idle_bg"))
            self._filter_expr_entry.configure(
                bg=new_bg,
                fg=ThemeManager.get("entry_fg"),
                insertbackground=ThemeManager.get("entry_insert"),
            )
        except tk.TclError:
            pass

        # Re-color each column filter entry
        for col, entry in list(getattr(self, "_filter_entries", {}).items()):
            try:
                var = self._filter_vars.get(col)
                active_col = bool(var and var.get().strip())
                bg = (ThemeManager.get("filter_active_bg") if active_col
                      else ThemeManager.get("filter_idle_bg"))
                entry.configure(bg=bg, fg=ThemeManager.get("entry_fg"),
                                insertbackground=ThemeManager.get("entry_insert"))
            except tk.TclError:
                pass

        # Re-color the hint label
        try:
            self._filter_hint_label.configure(foreground=ThemeManager.get("hint_fg"))
        except tk.TclError:
            pass

        # Re-color the DEC/HEX/BIN value display labels and copy buttons
        for lbl in getattr(self, "_val_groups", []):
            try:
                lbl.configure(fg=ThemeManager.get("sel_fg"),
                               bg=ThemeManager.get("sel_bg"))
            except tk.TclError:
                pass
        # Copy buttons are children of the lbl's parent frame
        for lbl in getattr(self, "_val_groups", []):
            try:
                parent = lbl.master
                for child in parent.winfo_children():
                    cls = child.winfo_class()
                    if cls == "Button":
                        child.configure(bg=ThemeManager.get("bg"),
                                        fg=ThemeManager.get("fg"),
                                        activebackground=ThemeManager.get("sel_bg"),
                                        activeforeground=ThemeManager.get("sel_fg"))
                    elif cls == "Label":
                        child.configure(fg=ThemeManager.get("dim_fg"))
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # Internal: column fit to window
    # ------------------------------------------------------------------

    def _on_resize(self, event=None):
        """Debounce Treeview <Configure> events so rapid resizes only trigger
        one _fit_columns call (200 ms quiet period)."""
        if not self._columns:
            return
        if getattr(self, "_resize_after_id", None):
            try:
                self.after_cancel(self._resize_after_id)
            except tk.TclError:
                pass
        self._resize_after_id = self.after(200, self._fit_columns)

    def _schedule_fit(self):
        """Debounce _fit_columns (120 ms) so rapid font steps collapse."""
        if getattr(self, "_fit_after_id", None):
            try:
                self.after_cancel(self._fit_after_id)
            except tk.TclError:
                pass
        self._fit_after_id = self.after(120, self._fit_columns)

    def _fit_columns(self):
        """Distribute column widths to fill the visible Treeview area.

        Algorithm
        ---------
        1. For each column compute a *natural* width = max(header_text_px,
           longest_sample_cell_px, MIN_W).  Font measurement is done with
           tkinter.font.Font so it honours the current font size.
        2. If the sum of natural widths is less than the available Treeview
           width, distribute the surplus proportionally.
        3. If natural widths exceed the available width the natural widths are
           used as-is (user can scroll horizontally).
        """
        if not self._columns:
            return
        self.update_idletasks()
        avail_w = self._tree.winfo_width()
        if avail_w <= 1:
            # Widget not yet rendered — retry shortly.
            self.after(150, self._fit_columns)
            return

        from tkinter.font import Font as _TkFont
        try:
            hdr_font  = _TkFont(font=FontManager.get("bold"))
            cell_font = _TkFont(font=FontManager.get("normal"))
        except Exception:
            hdr_font = cell_font = None

        def _px(text, font):
            if font:
                return font.measure(str(text))
            return len(str(text)) * 7

        HPAD  = 28   # extra for sort-arrow + left/right heading padding
        CPAD  = 14   # extra for left/right cell padding
        MIN_W = 40   # absolute minimum regardless of content

        natural = []
        for col in self._columns:
            display = col.replace("_", " ").title()
            hdr_w  = _px(display, hdr_font) + HPAD
            # Sample up to 200 rows so wide-value columns are not undersized.
            sample  = self._df_full[col].head(200).astype(str)
            longest = max(sample, key=len, default="")
            cell_w  = _px(longest, cell_font) + CPAD
            natural.append(max(hdr_w, cell_w, MIN_W))

        total_nat = sum(natural)
        if total_nat < avail_w:
            # Proportionally distribute surplus to all columns.
            extra  = avail_w - total_nat
            widths = [n + int(extra * n / total_nat) for n in natural]
            # Correct rounding error (last column absorbs the difference).
            widths[-1] += avail_w - sum(widths)
        else:
            widths = natural

        for col, w in zip(self._columns, widths):
            self._tree.column(col, width=max(w, MIN_W))

        self.after_idle(self._sync_filter_widths)

    # ------------------------------------------------------------------
    # Internal: column setup
    # ------------------------------------------------------------------

    def _rebuild_columns(self):
        self._tree.configure(columns=self._columns)
        for col in self._columns:
            display = col.replace("_", " ").title()
            self._tree.heading(col, text=display,
                               command=lambda c=col: self._on_sort(c))
            # Placeholder width — _fit_columns will compute proper widths once
            # the widget has real geometry (after_idle below).
            self._tree.column(col, width=80, stretch=False, anchor="w")
        # Fit column widths to window after geometry is available.
        self.after_idle(self._fit_columns)

    # ------------------------------------------------------------------
    # Internal: filter row
    # ------------------------------------------------------------------

    def _rebuild_filters(self):
        # Save current filter text before destroying widgets so visibility
        # changes don't wipe filters for still-visible columns.
        saved_values = {col: var.get() for col, var in self._filter_vars.items()}

        for child in self._filter_inner.winfo_children():
            child.destroy()
        self._filter_vars    = {}
        self._filter_entries = {}
        self._filter_num_labels = {}

        # Derive tiny font for the superscript numbers (a few pts smaller than "small")
        num_font = (FontManager.get("small").cget("family"), max(7, FontManager.size() - 3))

        for i, col in enumerate(self._columns):
            n = i + 1   # 1-indexed — matches the boolean expression syntax

            # ── Row 0: small column-number label (superscript style) ──
            num_lbl = tk.Label(
                self._filter_inner, text=str(n),
                font=num_font,
                fg=ThemeManager.get("accent_fg"),
                bg=ThemeManager.get("accent_bg"),
                padx=1, pady=0,
                relief=tk.FLAT, anchor="center",
            )
            num_lbl.grid(row=0, column=i, sticky="ew", padx=1, pady=(1, 0))
            self._filter_num_labels[col] = num_lbl

            var = tk.StringVar()
            # Restore any filter the user had set for this column
            if col in saved_values and saved_values[col]:
                var.set(saved_values[col])
            var.trace_add("write", lambda *_, c=col: self._schedule_filter())
            self._filter_vars[col] = var

            # ── Row 1: the actual filter entry ────────────────────────
            entry = tk.Entry(self._filter_inner, textvariable=var, width=4,
                             font=FontManager.get("small"),
                             bg=ThemeManager.get("filter_idle_bg"),
                             fg=ThemeManager.get("entry_fg"),
                             insertbackground=ThemeManager.get("entry_insert"),
                             relief=tk.SOLID, bd=1)
            entry.grid(row=1, column=i, sticky="ew", padx=1, pady=(0, 2))
            self._filter_entries[col] = entry
            entry.bind("<Return>", lambda e: self._apply_filters())

            # Highlight entry when it has content
            var.trace_add("write", lambda *_, e=entry, v=var: self._update_filter_bg(e, v))

            # Re-apply highlight for restored values
            self._update_filter_bg(entry, var)

        self.after_idle(self._sync_filter_widths)

    # ------------------------------------------------------------------
    # Internal: horizontal scroll sync (filter canvas ↔ Treeview)
    # ------------------------------------------------------------------

    def _on_tree_hscroll(self, first: str, last: str):
        """Called by Treeview when its horizontal scroll position changes."""
        self._last_scroll_first = float(first)
        self._hsb.set(first, last)
        self._filter_canvas.xview_moveto(float(first))

    def _on_h_scroll(self, *args):
        """Called by the shared horizontal scrollbar."""
        self._tree.xview(*args)
        self._filter_canvas.xview(*args)

    def _schedule_sync(self):
        """Debounce _sync_filter_widths so rapid events don't pile up."""
        if self._sync_id:
            self.after_cancel(self._sync_id)
        self._sync_id = self.after(80, self._sync_filter_widths)

    def _sync_filter_widths(self):
        """
        Resize each filter entry column to exactly match the corresponding
        Treeview column width.  Also updates the canvas scroll region so the
        filter row scrolls in step with the Treeview.
        """
        self._sync_id = None
        if not self._columns:
            return
        total_w = 0
        for i, col in enumerate(self._columns):
            try:
                w = self._tree.column(col, "width")
            except Exception:
                w = 80
            # weight=0 + minsize=w gives the column exactly w pixels
            self._filter_inner.columnconfigure(i, minsize=w, weight=0)
            total_w += w + 2   # +2 accounts for padx=1 on each side
        # Let tkinter recalculate the inner frame height
        self._filter_inner.update_idletasks()
        h = max(self._filter_inner.winfo_reqheight(), 28)
        self._filter_canvas.configure(
            height=h,
            scrollregion=(0, 0, total_w, h),
        )
        # Re-apply the last known horizontal scroll position so the filter
        # boxes stay aligned with the columns after any width recalculation.
        self._filter_canvas.update_idletasks()
        self._filter_canvas.xview_moveto(self._last_scroll_first)

    # ------------------------------------------------------------------
    # Internal: filter row bg
    # ------------------------------------------------------------------

    def _update_filter_bg(self, entry: tk.Entry, var: tk.StringVar):
        bg = (ThemeManager.get("filter_active_bg") if var.get().strip()
              else ThemeManager.get("filter_idle_bg"))
        entry.configure(bg=bg)

    # ------------------------------------------------------------------
    # Internal: clear all filters
    # ------------------------------------------------------------------

    def _clear_filters(self):
        """Clear every column filter box, the Logic bar, and reapply."""
        for var in self._filter_vars.values():
            var.set("")
        self._filter_expr_var.set("")
        self._clear_val_bar()
        self._apply_filters()

    def _clear_val_bar(self):
        """Blank all three numeric value labels."""
        for lbl in self._val_groups:
            lbl.configure(text="")

    def _copy_val(self, text: str):
        """Copy *text* to the system clipboard, stripping visual '_' separators."""
        if text:
            self.clipboard_clear()
            self.clipboard_append(text.replace("_", ""))

    # ------------------------------------------------------------------
    # Internal: numeric value info bar
    # ------------------------------------------------------------------

    def _on_tree_select(self, _event=None):
        """When a row is selected, find which column was clicked and if the
        cell value is numeric show dec / hex / bin in the info bar."""
        sel = self._tree.selection()
        if not sel:
            self._clear_val_bar()
            return
        iid = sel[0]
        values = self._tree.item(iid, "values")
        if not values:
            self._clear_val_bar()
            return

        # Identify the clicked column (stored by the Button-1 binding).
        col_name = getattr(self, "_last_clicked_col", None)
        if col_name and col_name in self._columns:
            idx = self._columns.index(col_name)
            raw = str(values[idx]).strip() if idx < len(values) else ""
        else:
            # Fallback: use first numeric cell in the row
            raw = ""
            for i, v in enumerate(values):
                if i < len(self._columns):
                    candidate = str(v).strip()
                    if _try_parse_numeric(candidate) is not None:
                        raw = candidate
                        break

        v = _try_parse_numeric(raw)
        if v is None or v != int(v):
            # Non-numeric or float — blank labels
            self._clear_val_bar()
            if v is not None:
                # Float: show decimal only
                self._val_dec_lbl.configure(text=str(v))
                self._val_hex_lbl.configure(text="")
                self._val_bin_lbl.configure(text="")
            return

        n = int(v)
        self._val_dec_lbl.configure(text=str(n))
        self._val_hex_lbl.configure(text=f"0x{_group4(f'{n:X}')}")
        self._val_bin_lbl.configure(text=f"0b{_group4(f'{n:b}')}")

    def _schedule_filter(self):
        if not self._auto_filter_var.get():
            return  # manual mode — only Enter or Apply button triggers filtering
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(200, self._apply_filters)

    def _on_auto_filter_toggle(self):
        """Called when the Auto checkbox is toggled.

        Switching to Auto immediately applies pending filters so the view
        is up-to-date.  Switching to Manual does nothing — the user decides
        when to apply.
        """
        if self._auto_filter_var.get():
            self._apply_filters()

    # ------------------------------------------------------------------
    # Internal: filtering + sorting
    # ------------------------------------------------------------------

    def _apply_filters(self):
        df = self._df_full.copy()

        expr = self._filter_expr_var.get().strip()

        if not expr:
            # ── Default: AND all active per-column filters ──────────────
            self._filter_expr_entry.configure(bg=ThemeManager.get("filter_idle_bg"))
            active_hints: List[str] = []
            for i, (col, var) in enumerate(self._filter_vars.items(), start=1):
                text = var.get().strip()
                if text:
                    active_hints.append(f"{i}={col}")
                    if col in df.columns:
                        df = df[self._col_filter_fn(df[col], text)]
            self._filter_hint_label.configure(
                text=("Active: " + "  ".join(active_hints)) if active_hints else ""
            )
        else:
            # ── Expression mode: build per-column boolean masks ──────────
            if df.empty:
                combined_mask = pd.Series([], dtype=bool)
            else:
                all_true = pd.Series(True, index=df.index)
                masks: dict = {}
                active_hints = []
                for i, (col, var) in enumerate(self._filter_vars.items(), start=1):
                    text = var.get().strip()
                    if text:
                        active_hints.append(f"{i}={col}")
                    if text and col in df.columns:
                        masks[i] = self._col_filter_fn(df[col], text)
                    else:
                        masks[i] = all_true.copy()

                self._filter_hint_label.configure(
                    text=("Active: " + "  ".join(active_hints)) if active_hints else ""
                )
                try:
                    combined_mask = self._eval_filter_expr(expr, masks, df)
                    self._filter_expr_entry.configure(bg=ThemeManager.get("ok_fg"))  # valid expr
                    df = df[combined_mask.values]
                except Exception as exc:
                    self._filter_expr_entry.configure(bg=ThemeManager.get("err_fg"))  # syntax error
                    # Fall back to AND of all active filters
                    for col, var in self._filter_vars.items():
                        text = var.get().strip()
                        if text and col in df.columns:
                            df = df[self._col_filter_fn(df[col], text)]

        # Apply sort — numeric if every non-empty value in the column parses as
        # a number; otherwise case-insensitive string sort.
        if self._sort_col and self._sort_col in df.columns:
            _col = df[self._sort_col]
            _non_empty = _col[_col != ""]
            _is_numeric = (
                len(_non_empty) > 0
                and pd.to_numeric(_non_empty, errors="coerce").notna().all()
            )
            if _is_numeric:
                df = df.sort_values(
                    self._sort_col, ascending=self._sort_asc,
                    key=lambda s: pd.to_numeric(s, errors="coerce"),
                    na_position="last",
                )
            else:
                df = df.sort_values(
                    self._sort_col, ascending=self._sort_asc,
                    key=lambda s: s.str.lower(),
                )

        self._df_view = df
        self._repopulate()

    # ------------------------------------------------------------------
    # Internal: boolean filter expression evaluator
    # ------------------------------------------------------------------

    def _eval_filter_expr(self, expr: str, masks: dict,
                          df: "pd.DataFrame") -> "pd.Series":
        """
        Evaluate a boolean expression referencing column-filter masks by
        1-based column number.  Grammar (precedence: NOT > AND > OR):

            expr    → or_expr
            or_expr → and_expr  ('OR'  and_expr)*
            and_expr→ not_expr  ('AND' not_expr)*
            not_expr→ 'NOT' not_expr | atom
            atom    → '(' expr ')' | INTEGER

        An integer that has no active filter mask (filter box empty) is
        treated as all-True (no constraint).
        Raises ValueError on parse errors.
        """
        all_true = pd.Series(True, index=df.index)
        tokens   = re.findall(r'\d+|AND|OR|NOT|\(|\)', expr.upper())
        pos      = [0]
        total    = len(tokens)

        def peek():
            return tokens[pos[0]] if pos[0] < total else None

        def consume():
            t = tokens[pos[0]]
            pos[0] += 1
            return t

        def parse_or():
            left = parse_and()
            while peek() == "OR":
                consume()
                right = parse_and()
                left  = left | right
            return left

        def parse_and():
            left = parse_not()
            while peek() == "AND":
                consume()
                right = parse_not()
                left  = left & right
            return left

        def parse_not():
            if peek() == "NOT":
                consume()
                return ~parse_atom()
            return parse_atom()

        def parse_atom():
            t = peek()
            if t is None:
                raise ValueError("Unexpected end of expression")
            if t == "(":
                consume()
                result = parse_or()
                if peek() != ")":
                    raise ValueError("Missing closing ')'")
                consume()
                return result
            elif t.isdigit():
                n = int(consume())
                return masks.get(n, all_true.copy())
            else:
                raise ValueError(f"Unexpected token: {t!r}")

        result = parse_or()
        if pos[0] < total:
            raise ValueError(f"Unexpected token after expression: {tokens[pos[0]]!r}")
        return result

    def _on_sort(self, col: str):
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        # Update heading indicators
        for c in self._columns:
            base = c.replace("_", " ").title()
            if c == self._sort_col:
                indicator = self._SORT_ASC if self._sort_asc else self._SORT_DESC
                self._tree.heading(c, text=base + indicator,
                                   command=lambda cc=c: self._on_sort(cc))
            else:
                self._tree.heading(c, text=base,
                                   command=lambda cc=c: self._on_sort(cc))
        self._apply_filters()

    # ------------------------------------------------------------------
    # Internal: populate tree rows
    # ------------------------------------------------------------------

    def _repopulate(self):
        self._tree.delete(*self._tree.get_children())
        # Vectorised extraction: fillna+astype(str) is 10-50× faster than iterrows()
        if self._df_view.empty:
            total = len(self._df_full)
            self._count_label.configure(text=f"Showing 0 of {total} rows")
            return
        rows_data = (
            self._df_view[self._columns]
            .fillna("")
            .astype(str)
            .values
            .tolist()
        )
        insert = self._tree.insert  # cache attribute lookup outside the loop
        for i, vals in enumerate(rows_data):
            insert("", tk.END, values=vals, tags=("odd" if i % 2 else "even",))
        total = len(self._df_full)
        shown = len(rows_data)
        self._count_label.configure(text=f"Showing {shown} of {total} rows")

    # ------------------------------------------------------------------
    # Internal: column visibility
    # ------------------------------------------------------------------

    def _update_col_btn_label(self):
        """Update the Columns button label to show hidden count."""
        hidden = len(self._hidden_cols)
        if hidden:
            self._col_btn.configure(text=f"Columns ▾  ({hidden} hidden)")
        else:
            self._col_btn.configure(text="Columns ▾")

    def _open_column_picker(self):
        """Open the column visibility dialog."""
        if not self._all_columns:
            return
        ColumnPickerDialog(self, self._all_columns, self._hidden_cols,
                           self._apply_column_visibility)

    def _apply_column_visibility(self, hidden_cols: set):
        """Called by ColumnPickerDialog when user confirms changes."""
        self._hidden_cols = hidden_cols
        self._columns = [c for c in self._all_columns if c not in self._hidden_cols]
        # Reset sort if the sorted column is now hidden
        if self._sort_col and self._sort_col not in self._columns:
            self._sort_col = None
            self._sort_asc = True
        self._rebuild_columns()
        self._rebuild_filters()
        self._apply_filters()
        self._update_col_btn_label()

    # ------------------------------------------------------------------
    # Internal: export
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Session save / load
    # ------------------------------------------------------------------

    def _serialize_session(self) -> dict:
        """Return a dict representing the current filter + visibility state."""
        cols = {
            col: var.get()
            for col, var in self._filter_vars.items()
            if var.get().strip()
        }
        visibility = {
            col: (col not in self._hidden_cols)
            for col in self._all_columns
        }
        col_order = list(self._columns)  # currently visible order
        return {
            "v": 1,
            "cols": cols,
            "logic": self._filter_expr_var.get().strip(),
            "visibility": visibility,
            "col_order": col_order,
        }

    def _save_session(self):
        """Serialize current session state and save to a .session.json file."""
        path = filedialog.asksaveasfilename(
            defaultextension=".session.json",
            filetypes=[
                ("Session files", "*.session.json"),
                ("JSON files", "*.json"),
                ("All files", "*.*"),
            ],
            title="Save Session",
        )
        if not path:
            return
        data = self._serialize_session()
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except OSError as exc:
            messagebox.showerror("Save Session", f"Could not write file:\n{exc}", parent=self)
            return
        self._session_path = path
        # Brief status feedback via count label
        prev = self._count_label.cget("text")
        short = os.path.basename(path)
        self._count_label.configure(text=f"✓ Session saved → {short}")
        self.after(2500, lambda: self._count_label.configure(text=prev))

    def _load_session(self):
        """Load a .session.json file and apply filters + column visibility."""
        path = filedialog.askopenfilename(
            filetypes=[
                ("Session files", "*.session.json"),
                ("JSON files", "*.json"),
                ("All files", "*.*"),
            ],
            title="Load Session",
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            messagebox.showerror("Load Session", f"Could not read session file:\n{exc}", parent=self)
            return
        self._session_path = path
        self._apply_session(data)

    def _apply_session(self, data: dict):
        """Apply a session dict (parsed from .session.json) to this table."""
        skipped_filters: list = []
        skipped_visibility: list = []

        # ── 1. Apply per-column filters ──────────────────────────────────
        for col, expr in data.get("cols", {}).items():
            if col in self._filter_vars:
                self._filter_vars[col].set(expr)
            else:
                skipped_filters.append(col)

        # ── 2. Apply global logic bar ─────────────────────────────────────
        self._filter_expr_var.set(data.get("logic", ""))

        # ── 3. Apply column visibility ────────────────────────────────────
        visibility = data.get("visibility", {})
        if visibility:
            new_hidden: set = set()
            for col in self._all_columns:
                if col in visibility:
                    if not visibility[col]:   # False → hidden
                        new_hidden.add(col)
                else:
                    # column not mentioned in session → keep current state
                    if col in self._hidden_cols:
                        new_hidden.add(col)
            if new_hidden != self._hidden_cols:
                self._hidden_cols = new_hidden
                self._rebuild_filters()   # rebuilds columns + filter widgets

        # ── 4. Run filters immediately ────────────────────────────────────
        self._apply_filters()

        # ── 5. Report any skipped items ───────────────────────────────────
        msgs = []
        if skipped_filters:
            msgs.append("Filters skipped (columns not in this file):\n  " +
                        ", ".join(skipped_filters))
        if skipped_visibility:
            msgs.append("Visibility skipped (columns not in this file):\n  " +
                        ", ".join(skipped_visibility))
        if msgs:
            messagebox.showinfo("Load Session", "\n\n".join(msgs), parent=self)

    # ------------------------------------------------------------------
    # Summary: column and table
    # ------------------------------------------------------------------

    @staticmethod
    def _col_stats(series: "pd.Series") -> list:
        """
        Return a list of (stat_name, value) pairs describing *series*.
        Numeric columns get descriptive statistics; object/mixed columns
        get frequency-based statistics plus a top-values table.
        """
        rows = []
        total = len(series)
        null_count = series.isna().sum() + (series.astype(str).str.strip() == "").sum()
        null_count = min(null_count, total)  # avoid double-counting NaN+""
        non_null = series.dropna()
        non_null = non_null[non_null.astype(str).str.strip() != ""]

        rows.append(("Total rows",   total))
        rows.append(("Non-empty",    int(len(non_null))))
        rows.append(("Empty / null", int(null_count)))
        rows.append(("Unique values", int(non_null.nunique())))

        # Try numeric interpretation
        numeric = pd.to_numeric(non_null, errors="coerce").dropna()
        if len(numeric) >= 2:
            rows.append(("── Numeric stats ──", ""))
            rows.append(("Count (numeric)", int(len(numeric))))
            rows.append(("Min",    _fmt_num(numeric.min())))
            rows.append(("Max",    _fmt_num(numeric.max())))
            rows.append(("Mean",   _fmt_num(numeric.mean())))
            rows.append(("Median", _fmt_num(numeric.median())))
            rows.append(("Std dev",_fmt_num(numeric.std())))
            rows.append(("25 %",   _fmt_num(numeric.quantile(0.25))))
            rows.append(("75 %",   _fmt_num(numeric.quantile(0.75))))

        # Top value frequencies
        if non_null.nunique() > 0:
            vc = non_null.astype(str).value_counts()
            rows.append(("── Top values (value : count) ──", ""))
            for val, cnt in vc.head(20).items():
                pct = 100.0 * cnt / total
                label = val if len(val) <= 40 else val[:37] + "…"
                rows.append((label, f"{cnt}  ({pct:.1f} %)"))

        return rows

    def _show_summary_window(self, title: str, stat_rows: list):
        """
        Display a list of (stat_name, value) pairs in a Toplevel Treeview.
        *stat_rows* may be a flat list for a single column or a list of
        (col_name, stat_list) tuples for the multi-column table summary.
        """
        win = tk.Toplevel(self)
        win.title(title)
        win.resizable(True, True)

        frame = ttk.Frame(win)
        frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # Detect multi-column mode
        multi = stat_rows and isinstance(stat_rows[0], tuple) and \
                isinstance(stat_rows[0][1], list)

        if not multi:
            # ── Single column: two-column Treeview ───────────────────────
            tree = ttk.Treeview(frame, columns=("stat", "value"),
                                show="headings", height=28)
            tree.heading("stat",  text="Statistic")
            tree.heading("value", text="Value")
            tree.column("stat",  width=220, anchor="w")
            tree.column("value", width=260, anchor="w")
            vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=vsb.set)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            vsb.pack(side=tk.RIGHT, fill=tk.Y)
            for i, (k, v) in enumerate(stat_rows):
                tag = "section" if str(v) == "" else ("even" if i % 2 == 0 else "odd")
                tree.insert("", "end", values=(k, v), tags=(tag,))
            tree.tag_configure("section", background=ThemeManager.get("section_bg"), font=FontManager.get("bold"))
            tree.tag_configure("even",    background=ThemeManager.get("row_even_bg"),  foreground=ThemeManager.get("fg"))
            tree.tag_configure("odd",     background=ThemeManager.get("row_odd_bg"),   foreground=ThemeManager.get("fg"))
        else:
            # ── Multi-column table summary ────────────────────────────────
            # Collect all unique stat names in order
            stat_names_ordered = []
            seen = set()
            for _, rows in stat_rows:
                for k, _ in rows:
                    if k not in seen:
                        stat_names_ordered.append(k)
                        seen.add(k)

            col_names = [cn for cn, _ in stat_rows]
            columns = ["stat"] + col_names
            tree = ttk.Treeview(frame, columns=columns, show="headings", height=28)
            tree.heading("stat", text="Statistic")
            tree.column("stat", width=200, anchor="w")
            for cn in col_names:
                tree.heading(cn, text=cn)
                tree.column(cn, width=160, anchor="w")

            hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
            vsb = ttk.Scrollbar(frame, orient="vertical",   command=tree.yview)
            tree.configure(xscrollcommand=hsb.set, yscrollcommand=vsb.set)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            vsb.pack(side=tk.RIGHT, fill=tk.Y)
            hsb.pack(side=tk.BOTTOM, fill=tk.X)

            # Build lookup: col_name → {stat_name: value}
            lookup = {}
            for cn, rows in stat_rows:
                lookup[cn] = {k: v for k, v in rows}

            for i, sname in enumerate(stat_names_ordered):
                is_section = all(
                    str(lookup.get(cn, {}).get(sname, "")) == "" for cn in col_names
                )
                tag = "section" if is_section else ("even" if i % 2 == 0 else "odd")
                vals = [sname] + [lookup.get(cn, {}).get(sname, "") for cn in col_names]
                tree.insert("", "end", values=vals, tags=(tag,))

            tree.tag_configure("section", background=ThemeManager.get("section_bg"), font=FontManager.get("bold"))
            tree.tag_configure("even",    background=ThemeManager.get("row_even_bg"),  foreground=ThemeManager.get("fg"))
            tree.tag_configure("odd",     background=ThemeManager.get("row_odd_bg"),   foreground=ThemeManager.get("fg"))

        ttk.Button(win, text="Close", command=win.destroy).pack(pady=6)
        win.transient(self)
        self.update_idletasks()
        px, py = self.winfo_rootx(), self.winfo_rooty()
        pw, ph = self.winfo_width(), self.winfo_height()
        win.update_idletasks()
        ww = max(win.winfo_reqwidth(), 520)
        wh = max(win.winfo_reqheight(), 400)
        win.geometry(f"{ww}x{wh}+{px + pw//2 - ww//2}+{py + ph//2 - wh//2}")

    def _summarize_column(self, col_name: str):
        """Show a statistics summary for a single column (from _df_view)."""
        if self._df_view.empty:
            messagebox.showinfo("Summary", "No data loaded.", parent=self)
            return
        if col_name not in self._df_view.columns:
            return
        rows = self._col_stats(self._df_view[col_name])
        label = self._tab_label or "table"
        self._show_summary_window(f"Column summary — {col_name}  ({label})", rows)

    def _summarize_table(self):
        """Show a statistics summary for all visible columns."""
        if self._df_view.empty:
            messagebox.showinfo("Summary", "No data loaded.", parent=self)
            return
        stat_rows = [
            (col, self._col_stats(self._df_view[col]))
            for col in self._columns
            if col in self._df_view.columns
        ]
        label = self._tab_label or "table"
        n_rows = len(self._df_view)
        self._show_summary_window(
            f"Table summary — {label}  ({n_rows} rows, {len(stat_rows)} cols)",
            stat_rows,
        )

    # ------------------------------------------------------------------
    # Internal: export to CSV
    # ------------------------------------------------------------------

    def _export(self):
        if self._df_view.empty:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self._df_view.to_csv(path, index=False)

    # ------------------------------------------------------------------
    # Internal: email
    # ------------------------------------------------------------------

    def _email_view(self):
        """Open the email dialog to send the current filtered view."""
        if self._df_view.empty:
            messagebox.showwarning("No data",
                                   "No rows to email — the current filtered view is empty.",
                                   parent=self)
            return
        subject = f"InterfaceSpec: {self._tab_label}" if self._tab_label else "InterfaceSpec Results"
        extra = ([self._session_path]
                 if self._session_path and os.path.isfile(self._session_path)
                 else None)
        EmailDialog(self, df=self._df_view.copy(), default_subject=subject,
                    extra_files=extra)



# ======================================================================
# ColumnPickerDialog — choose which columns to show / hide
# ======================================================================

class ColumnPickerDialog(tk.Toplevel):
    """
    Modal dialog that lets the user check/uncheck columns to show or hide
    in the FilteredTable.  Changes are applied when the user clicks Apply.

    Args:
        parent         : FilteredTable instance (parent widget)
        all_columns    : ordered list of all column names
        hidden_cols    : set of currently hidden column names
        callback       : callable(hidden_cols: set) invoked on Apply
    """

    def __init__(self, parent, all_columns: list, hidden_cols: set, callback):
        super().__init__(parent)
        self.title("Choose Columns")
        self.resizable(True, True)
        self.minsize(320, 300)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        self._all_columns = all_columns
        self._callback    = callback
        self._vars: dict  = {}   # col → BooleanVar (True = visible)

        # ── header ──────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=ThemeManager.get("hdr_bg"))
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="Show / Hide Columns",
                 bg=ThemeManager.get("hdr_bg"), fg=ThemeManager.get("hdr_fg"),
                 font=FontManager.get("bold"), pady=6).pack(side=tk.LEFT, padx=8)

        # ── search box ──────────────────────────────────────────────
        search_frame = ttk.Frame(self)
        search_frame.pack(fill=tk.X, padx=8, pady=(6, 2))
        ttk.Label(search_frame, text="🔍 Filter columns:").pack(side=tk.LEFT)
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._filter_list())
        search_entry = ttk.Entry(search_frame, textvariable=self._search_var,
                                 font=FontManager.get("normal"))
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        # ── quick-action buttons ─────────────────────────────────────
        btn_row = ttk.Frame(self)
        btn_row.pack(fill=tk.X, padx=8, pady=2)
        ttk.Button(btn_row, text="✔ Show all",  command=self._select_all).pack(side=tk.LEFT,  padx=2)
        ttk.Button(btn_row, text="✘ Hide all",  command=self._clear_all).pack(side=tk.LEFT,  padx=2)
        ttk.Button(btn_row, text="↺ Reset",     command=self._reset).pack(side=tk.LEFT,  padx=2)
        self._vis_label = ttk.Label(btn_row, text="")
        self._vis_label.pack(side=tk.RIGHT, padx=4)

        # ── scrollable checklist ─────────────────────────────────────
        list_frame = ttk.Frame(self)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        canvas = tk.Canvas(list_frame, highlightthickness=0)
        vsb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._inner = ttk.Frame(canvas)
        self._canvas_win = canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>",
                         lambda e: canvas.configure(
                             scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(self._canvas_win, width=e.width))
        # Mouse-wheel scrolling
        canvas.bind_all("<MouseWheel>",
                        lambda e, c=canvas: c.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        self._canvas = canvas

        # Build BooleanVars for all columns
        for col in all_columns:
            var = tk.BooleanVar(value=(col not in hidden_cols))
            var.trace_add("write", lambda *_: self._update_vis_label())
            self._vars[col] = var

        self._render_checkboxes(all_columns)
        self._update_vis_label()

        # ── bottom buttons ───────────────────────────────────────────
        btn_bar = ttk.Frame(self)
        btn_bar.pack(fill=tk.X, padx=8, pady=(4, 8))
        ttk.Button(btn_bar, text="Apply",  command=self._on_apply).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_bar, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=4)

        # Position near parent
        self.update_idletasks()
        px = parent.winfo_rootx() + 30
        py = parent.winfo_rooty() + 30
        self.geometry(f"+{px}+{py}")
        self.focus_set()

    # ------------------------------------------------------------------

    def _render_checkboxes(self, cols: list):
        """(Re)populate the inner frame with checkboxes for *cols*."""
        for child in self._inner.winfo_children():
            child.destroy()
        for col in cols:
            cb = ttk.Checkbutton(
                self._inner,
                text=col.replace("_", " ").title() + f"  ({col})",
                variable=self._vars[col],
                onvalue=True, offvalue=False,
            )
            cb.pack(anchor="w", padx=4, pady=1)

    def _filter_list(self):
        """Re-render checkboxes matching the search string."""
        q = self._search_var.get().strip().lower()
        filtered = [c for c in self._all_columns if q in c.lower()] if q else self._all_columns
        self._render_checkboxes(filtered)

    def _select_all(self):
        for v in self._vars.values():
            v.set(True)

    def _clear_all(self):
        for v in self._vars.values():
            v.set(False)

    def _reset(self):
        """Show all columns (remove all hidden)."""
        self._select_all()

    def _update_vis_label(self):
        visible = sum(1 for v in self._vars.values() if v.get())
        total   = len(self._vars)
        self._vis_label.configure(text=f"{visible}/{total} visible")

    def _on_apply(self):
        hidden = {col for col, var in self._vars.items() if not var.get()}
        self._callback(hidden)
        self.destroy()
