#!/usr/bin/env python3
"""
supercsv — standalone CSV browser built on the FilteredTable widget.

Features
--------
* Open one or more CSV files; each becomes a tab.
* Per-column filter boxes with boolean expressions: ``(SMT) OR (THREAD)``
* Global Logic: bar for cross-column boolean logic: ``1 AND NOT 3``
* Superscript column-number labels aligned with each filter box.
* Right-click on any cell that looks like a file path → open in editor at
  the correct line number; "Other paths in this row" sub-menu.
* Column visibility picker (hide/show columns).
* Email the current filtered view as a CSV attachment.
* Export the current filtered view to a new CSV file.
* Sort by clicking column headers (toggle asc/desc).
* Font-size control in the toolbar (–  /  +  / reset).
* Horizontal-scroll sync: filter boxes always stay aligned with columns.

Usage
-----
    python3 scripts/supercsv/supercsv.py [file1.csv [file2.csv ...]]

If no files are supplied, an Open dialog is shown immediately.
"""

import csv
import sys
import os
import argparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ── Ensure this script's own directory is first on sys.path ──────────────────
# filtered_table.py and font_manager.py live here (authoritative copies).
_HERE = os.path.dirname(os.path.abspath(__file__))   # scripts/supercsv/
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import pandas as pd
    from filtered_table import FilteredTable
    from font_manager import FontManager
    from theme_manager import ThemeManager
except ImportError as exc:
    print(f"ERROR: cannot import FilteredTable/FontManager/ThemeManager: {exc}", file=sys.stderr)
    print(f"  Expected at: {_HERE}/", file=sys.stderr)
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────

_APP_TITLE   = "SuperCSV Browser"
_WINDOW_SIZE = "1400x820"
_FONT_SIZES  = [10, 11, 12, 13, 14, 15, 16, 18, 20]
_DEFAULT_FONT = 14

_RECENT_MAX = 12   # keep the last N paths in the recent-files list

# ── Delimiter helpers ─────────────────────────────────────────────────────────

# Human-readable label → actual separator string
_DELIM_CHOICES = [
    ("Comma  (,)",      ","),
    ("Tab  (\\t)",      "\t"),
    ("Pipe  (|)",       "|"),
    ("Semicolon  (;)",  ";"),
    ("Space",           " "),
    ("Colon  (:)",      ":"),
]
_DELIM_LABEL = {sep: label for label, sep in _DELIM_CHOICES}
_DELIM_LABEL["\t"] = "TAB"   # compact label used in tab titles

# Extensions that strongly hint at delimiter without sniffing
_EXT_SEP = {
    ".tsv": "\t",
    ".psv": "|",
    ".csv": ",",
}


def _detect_delimiter(path: str) -> str:
    """Return the most likely delimiter for *path*.

    Strategy (first that succeeds wins):
      1. File extension lookup (.tsv, .psv, .csv).
      2. csv.Sniffer on the first 4 KB.
      3. Fall back to comma.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in _EXT_SEP:
        return _EXT_SEP[ext]
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            sample = fh.read(4096)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t|;: ")
        return dialect.delimiter
    except Exception:
        return ","


def _sep_label(sep: str) -> str:
    """Short human-readable label for *sep* (used in tab titles & status)."""
    return _DELIM_LABEL.get(sep, repr(sep))


def _read_csv_padded(path: str, sep: str) -> "tuple[pd.DataFrame, int]":
    """Read *path* with *sep*, normalising every row to the header width.

    - Rows with **fewer** columns are padded with empty strings on the right.
    - Rows with **more** columns are trimmed to the header column count.

    Returns ``(DataFrame, n_adjusted)`` where *n_adjusted* is the number of
    rows that were padded or trimmed (0 means the file was clean).
    """
    # Try UTF-8 first; fall back to latin-1 on decode errors.
    rows: list = []
    for enc in ("utf-8", "latin-1"):
        try:
            with open(path, encoding=enc, errors="strict", newline="") as fh:
                if sep == " ":
                    # split() with no args collapses any whitespace run.
                    rows = [line.split() for line in fh]
                else:
                    rows = list(csv.reader(fh, delimiter=sep))
            break
        except (UnicodeDecodeError, LookupError):
            pass
    else:
        # Last resort: replace undecodable bytes.
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            if sep == " ":
                rows = [line.split() for line in fh]
            else:
                rows = list(csv.reader(fh, delimiter=sep))

    # Drop leading completely-empty rows.
    while rows and len(rows[0]) == 0:
        rows.pop(0)

    if not rows:
        return pd.DataFrame(), 0

    header = rows[0]
    ncols = len(header)

    n_adjusted = 0
    data: list = []
    for row in rows[1:]:
        if len(row) == 0:
            continue  # skip blank lines
        if len(row) != ncols:
            n_adjusted += 1
            if len(row) < ncols:
                row = row + [""] * (ncols - len(row))   # pad right
            else:
                row = row[:ncols]                        # trim right
        data.append(row)

    df = pd.DataFrame(data, columns=header)
    return df.fillna(""), n_adjusted


class _TabTooltip:
    """Tooltip that shows the full file path when hovering over a notebook tab.

    Appears after a short delay, follows the cursor within the same tab, and
    disappears as soon as the cursor leaves the tab strip.

    Usage::

        _TabTooltip(notebook)   # attach once; no reference needs to be kept
    """

    _DELAY_MS = 600   # ms before the tooltip becomes visible

    def __init__(self, notebook: "ttk.Notebook"):
        self._nb    = notebook
        self._win: "tk.Toplevel | None"  = None
        self._after_id: "str | None"     = None
        self._last_tab: "int | None"     = None

        notebook.bind("<Motion>", self._on_motion, add="+")
        notebook.bind("<Leave>",  self._on_leave,  add="+")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_motion(self, event: "tk.Event"):
        # Use the same low-level Tcl call used by _on_tab_middle_click.
        try:
            raw = self._nb.tk.call(self._nb._w, "identify", "tab",
                                   event.x, event.y)
        except tk.TclError:
            self._hide()
            return

        if raw == "" or raw is None:
            # Mouse is over the content area, not the tab strip.
            self._hide()
            self._last_tab = None
            return

        tab_id = int(raw)

        if tab_id != self._last_tab:
            # Entered a different tab — reset and schedule a fresh tooltip.
            self._last_tab = tab_id
            self._hide()
            self._after_id = self._nb.after(
                self._DELAY_MS,
                lambda tid=tab_id, rx=event.x_root, ry=event.y_root:
                    self._show(tid, rx, ry),
            )
        elif self._win:
            # Same tab — keep the tooltip glued near the cursor.
            self._win.geometry(f"+{event.x_root + 14}+{event.y_root + 20}")

    def _on_leave(self, _event):
        self._hide()
        self._last_tab = None

    # ------------------------------------------------------------------
    # Show / hide
    # ------------------------------------------------------------------

    def _show(self, tab_id: int, root_x: int, root_y: int):
        """Create the tooltip Toplevel for *tab_id*."""
        self._after_id = None
        try:
            widget = self._nb.nametowidget(self._nb.tabs()[tab_id])
        except (IndexError, tk.TclError):
            return

        path = getattr(widget, "_path", None)
        if not path:
            return

        self._hide()   # destroy any stale window

        win = tk.Toplevel(self._nb)
        win.overrideredirect(True)
        win.attributes("-topmost", True)

        # Style: neutral tooltip colours that read well on any theme.
        tk.Label(
            win,
            text=path,
            background="#ffffe0",
            foreground="#1a1a1a",
            relief="solid",
            borderwidth=1,
            font=FontManager.get("small"),
            padx=8,
            pady=4,
        ).pack()

        win.geometry(f"+{root_x + 14}+{root_y + 20}")
        self._win = win

    def _hide(self):
        """Cancel any pending show and destroy the tooltip window."""
        if self._after_id is not None:
            try:
                self._nb.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None


class SuperCSV(tk.Tk):
    """Top-level window for the SuperCSV standalone browser."""

    def __init__(self, csv_files: list, cli_sep: str = None,
                 cli_drop_bad: bool = False):
        super().__init__()

        self.title(_APP_TITLE)
        self.geometry(_WINDOW_SIZE)

        # ── Font + Theme setup ────────────────────────────────────────────────
        self._style = ttk.Style(self)
        self._style.theme_use("clam")
        # Apply initial font *before* building widgets so they inherit it
        self._font_size_var = tk.IntVar(value=_DEFAULT_FONT)
        FontManager._size = _DEFAULT_FONT          # set singleton state
        ThemeManager.apply_to_style(self._style)   # colors first
        ThemeManager.apply_to_root(self)           # plain-tk defaults
        FontManager.apply_to_style(self._style)    # fonts on top

        # ── Recent-file list (in-memory; not persisted) ───────────────
        self._recent: list = []

        # ── UI ────────────────────────────────────────────────────────
        self._build_menu()
        self._build_toolbar()

        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        self._notebook.bind("<ButtonRelease-2>", self._on_tab_middle_click)
        self._notebook.bind("<Button-3>",         self._on_tab_right_click)
        self._tab_ctx_menu = tk.Menu(self._notebook, tearoff=0)
        _TabTooltip(self._notebook)   # show full path on tab hover

        self._status = ttk.Label(self, anchor="w", padding=(6, 2))
        self._status.pack(fill=tk.X, side=tk.BOTTOM)

        # ── Keyboard shortcuts ────────────────────────────────────────
        self.bind("<Control-o>",      lambda e: self._open_dialog())
        self.bind("<Control-w>",      lambda e: self._close_current_tab())
        self.bind("<Control-Tab>",    lambda e: self._next_tab(+1))
        self.bind("<Control-ISO_Left_Tab>", lambda e: self._next_tab(-1))
        self.bind("<Control-plus>",   lambda e: self._bump_font(+1))
        self.bind("<Control-equal>",  lambda e: self._bump_font(+1))
        self.bind("<Control-minus>",  lambda e: self._bump_font(-1))
        self.bind("<Control-0>",      lambda e: self._reset_font())

        # ── Load initial files ────────────────────────────────────────
        if csv_files:
            for f in csv_files:
                # cli_sep=None → auto-detect + ask; explicit → skip dialog
                self._open_csv(f, sep=cli_sep, drop_bad=cli_drop_bad)
        else:
            # Show open dialog on startup when no args given
            self.after(100, self._open_dialog)

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        # File
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(
            label="Open file...", accelerator="Ctrl+O", command=self._open_dialog
        )
        self._recent_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Open Recent", menu=self._recent_menu)
        file_menu.add_separator()
        file_menu.add_command(
            label="Reload with delimiter...", command=self._reload_with_delimiter
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Close Tab", accelerator="Ctrl+W", command=self._close_current_tab
        )
        file_menu.add_separator()
        file_menu.add_command(label="Quit", accelerator="Ctrl+Q", command=self.quit)

        self.bind("<Control-q>", lambda e: self.quit())

        # View
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(
            label="Increase font  (Ctrl++)", command=lambda: self._bump_font(+1)
        )
        view_menu.add_command(
            label="Decrease font  (Ctrl+-)", command=lambda: self._bump_font(-1)
        )
        view_menu.add_command(
            label="Reset font  (Ctrl+0)", command=self._reset_font
        )

        # Help
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About SuperCSV", command=self._show_about)

    def _refresh_recent_menu(self):
        self._recent_menu.delete(0, tk.END)
        if not self._recent:
            self._recent_menu.add_command(label="(no recent files)", state=tk.DISABLED)
            return
        for entry in self._recent:
            if isinstance(entry, tuple):
                path, sep = entry
            else:
                path, sep = entry, ","   # legacy plain-string entries
            suffix = f"  [{_sep_label(sep)}]" if sep != "," else ""
            self._recent_menu.add_command(
                label=path + suffix,
                command=lambda p=path, s=sep: self._open_csv(p, sep=s),
            )

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self):
        toolbar = ttk.Frame(self, relief="flat")
        toolbar.pack(fill=tk.X, padx=4, pady=(4, 2))

        ttk.Button(toolbar, text="Open", command=self._open_dialog
                   ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="Close tab", command=self._close_current_tab
                   ).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(toolbar, text="Delimiter...", command=self._reload_with_delimiter
                   ).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        # Font size controls (right-aligned): A-  [spinbox]  A+  reset
        ttk.Button(toolbar, text="A+", width=3, command=lambda: self._bump_font(+1)
                   ).pack(side=tk.RIGHT, padx=(2, 4))
        self._font_spin = ttk.Spinbox(
            toolbar, from_=7, to=22, width=4,
            textvariable=self._font_size_var,
            command=self._apply_font_entry,   # arrow clicks
        )
        self._font_spin.pack(side=tk.RIGHT, padx=2)
        self._font_spin.bind("<Return>",   lambda e: self._apply_font_entry())
        self._font_spin.bind("<FocusOut>", lambda e: self._apply_font_entry())
        ttk.Button(toolbar, text="A-", width=3, command=lambda: self._bump_font(-1)
                   ).pack(side=tk.RIGHT, padx=2)
        ttk.Button(toolbar, text="A", width=3, command=self._reset_font
                   ).pack(side=tk.RIGHT, padx=2)
        ttk.Label(toolbar, text="Font:").pack(side=tk.RIGHT, padx=(0, 2))

        # Theme picker (right-aligned, just left of font controls)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.RIGHT, fill=tk.Y, padx=6)
        theme_names = ThemeManager.names()
        self._theme_var = tk.StringVar(value=ThemeManager.name())
        theme_cb = ttk.Combobox(
            toolbar, textvariable=self._theme_var,
            values=theme_names, state="readonly", width=14,
        )
        theme_cb.pack(side=tk.RIGHT, padx=2)
        theme_cb.bind("<<ComboboxSelected>>",
                      lambda e: self._set_theme(self._theme_var.get()))
        ttk.Label(toolbar, text="Theme:").pack(side=tk.RIGHT, padx=(0, 2))

    # ------------------------------------------------------------------
    # Theme control
    # ------------------------------------------------------------------

    def _set_theme(self, name: str):
        ThemeManager.set_theme(name)
        ThemeManager.apply_to_style(self._style)
        ThemeManager.apply_to_root(self)
        # Re-apply fonts so they are not lost after the color reset
        FontManager.apply_to_style(self._style)
        ThemeManager.restyle_widget_tree(self)
        self._set_status(f"Theme: {name}")

    # ------------------------------------------------------------------
    # Font control
    # ------------------------------------------------------------------

    def _bump_font(self, delta: int):
        new = max(7, min(22, FontManager.size() + delta))
        self._set_font(new)

    def _reset_font(self):
        self._set_font(_DEFAULT_FONT)

    def _apply_font_entry(self):
        """Called when user types a number or uses spinbox arrows."""
        try:
            v = int(self._font_size_var.get())
            self._set_font(max(7, min(22, v)))
        except (ValueError, tk.TclError):
            # Restore the spinbox to current valid value
            self._font_size_var.set(FontManager.size())

    def _set_font(self, size: int):
        FontManager._size = size
        FontManager.apply_to_style(self._style)
        FontManager._notify()
        self._font_size_var.set(size)
        self._set_status(f"Font size: {size}pt")

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _open_dialog(self):
        paths = filedialog.askopenfilenames(
            title="Open file(s)",
            filetypes=[
                ("Delimited files", "*.csv *.tsv *.psv *.txt"),
                ("CSV",  "*.csv"),
                ("TSV",  "*.tsv"),
                ("All files", "*.*"),
            ],
        )
        for p in paths:
            self._open_csv(p)

    def _open_csv(self, path: str, sep: str = None, drop_bad: bool = False):
        """Load *path* into a new tab.

        Parameters
        ----------
        path : str
            File to open.
        sep : str or None
            Delimiter to use.  If None, auto-detect then ask the user to
            confirm / override via a small dialog.
        drop_bad : bool
            When True, rows with a wrong number of fields are silently
            dropped instead of raising an error.
        """
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            messagebox.showerror("File not found", f"{path}")
            return

        # Auto-detect if no explicit sep provided
        if sep is None:
            detected = _detect_delimiter(path)
            choice = self._ask_delimiter(detected, path)
            if choice is None:          # user cancelled
                return
            sep, drop_bad = choice

        self._set_status(f"Loading {path} ...")
        self.update_idletasks()

        n_adjusted = 0   # rows padded/trimmed (only used in non-drop mode)
        try:
            if drop_bad:
                # Skip (drop) rows with a wrong number of fields.
                read_sep = r" +" if sep == " " else sep
                df = pd.read_csv(
                    path, sep=read_sep, dtype=str, engine="python",
                    skipinitialspace=(sep == " "),
                    on_bad_lines="skip",
                ).fillna("")
            else:
                # Left-justify: pad short rows with empty columns,
                # trim long rows to the header column count.
                df, n_adjusted = _read_csv_padded(path, sep)
        except Exception as exc:
            messagebox.showerror("Error reading file", f"{path}\n\n{exc}")
            self._set_status("")
            return

        # Use the file's directory as model_root so relative paths resolve
        model_root = os.path.dirname(path)

        table = FilteredTable(self._notebook, model_root=model_root)

        basename = os.path.basename(path)
        # Append delimiter hint to tab label when it isn't a plain comma
        tab_suffix = f" [{_sep_label(sep)}]" if sep != "," else ""
        tab_label  = basename + tab_suffix

        table.set_tab_label(tab_label)
        table.load(df)
        # Store sep + options so "Reload with delimiter…" can re-use them
        table._sep      = sep
        table._path     = path
        table._drop_bad = drop_bad

        self._notebook.add(table, text=f"  {tab_label}  ")
        self._notebook.select(table)

        # Track in recent list (store path + sep together)
        entry = (path, sep)
        if entry in self._recent:
            self._recent.remove(entry)
        self._recent.insert(0, entry)
        if len(self._recent) > _RECENT_MAX:
            self._recent = self._recent[:_RECENT_MAX]
        self._refresh_recent_menu()

        rows, cols = df.shape
        if drop_bad:
            adj_note = "  |  bad lines dropped"
        elif n_adjusted > 0:
            adj_note = f"  |  {n_adjusted} rows adjusted (padded/trimmed)"
        else:
            adj_note = ""
        self._set_status(
            f"Loaded {rows:,} rows x {cols} columns  |  "
            f"delimiter: {_sep_label(sep)}{adj_note}  |  {path}"
        )

    # ------------------------------------------------------------------
    # Delimiter picker dialog
    # ------------------------------------------------------------------

    def _ask_delimiter(self, detected: str, path: str,
                       drop_bad: bool = False) -> "tuple[str, bool] | None":
        """Show a small dialog asking the user to confirm or change the delimiter.

        Returns ``(sep, drop_bad)`` tuple, or ``None`` if the user cancelled.
        """
        dlg = tk.Toplevel(self)
        dlg.title("Choose delimiter")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        result: list = [None]   # mutable container so inner funcs can write it

        # ── header ────────────────────────────────────────────────────
        hdr = tk.Frame(dlg, bg="#1a237e")
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text=f"  Delimiter for: {os.path.basename(path)}",
                 bg="#1a237e", fg="#ffffff",
                 font=FontManager.get("bold")).pack(side=tk.LEFT, padx=10, pady=6)

        # ── body ──────────────────────────────────────────────────────
        body = ttk.Frame(dlg, padding=12)
        body.pack(fill=tk.BOTH)

        ttk.Label(body, text="Auto-detected:").grid(row=0, column=0, sticky="e", padx=6)
        ttk.Label(body, text=_sep_label(detected),
                  foreground="#1565c0").grid(row=0, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(body, text="Use delimiter:").grid(row=1, column=0, sticky="e", padx=6, pady=4)

        # Dropdown (common choices)
        sep_var = tk.StringVar()
        combo_labels = [label for label, _ in _DELIM_CHOICES]
        combo = ttk.Combobox(body, textvariable=sep_var,
                             values=combo_labels, state="readonly", width=20)
        # Pre-select detected value in combo
        detected_label = next((lbl for lbl, s in _DELIM_CHOICES if s == detected), None)
        if detected_label:
            sep_var.set(detected_label)
        else:
            sep_var.set(combo_labels[0])
        combo.grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(body, text="Custom:").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        custom_var = tk.StringVar()
        custom_entry = ttk.Entry(body, textvariable=custom_var, width=8)
        custom_entry.grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(body, text="(overrides dropdown; use \\t for tab)",
                  foreground="#666").grid(row=2, column=2, sticky="w", padx=4)

        # ── drop bad lines option ─────────────────────────────────────
        drop_var = tk.BooleanVar(value=drop_bad)
        drop_cb = ttk.Checkbutton(
            body,
            text="Drop lines with wrong number of fields",
            variable=drop_var,
        )
        drop_cb.grid(row=3, column=0, columnspan=3, sticky="w", padx=6, pady=(8, 0))
        ttk.Label(
            body,
            text="(unchecked: short rows padded with empty cols, long rows trimmed;"
                 "  checked: rows silently dropped)",
            foreground="#666",
        ).grid(row=4, column=0, columnspan=3, sticky="w", padx=24, pady=(0, 4))

        def _get_sep() -> str:
            raw = custom_var.get().strip()
            if raw:
                # Allow \t escape in the entry box
                return raw.replace("\\t", "\t")
            chosen_label = sep_var.get()
            for lbl, s in _DELIM_CHOICES:
                if lbl == chosen_label:
                    return s
            return ","

        def _ok(event=None):
            result[0] = (_get_sep(), drop_var.get())
            dlg.destroy()

        def _cancel():
            dlg.destroy()

        # ── buttons ───────────────────────────────────────────────────
        btn_row = ttk.Frame(body)
        btn_row.grid(row=5, column=0, columnspan=3, pady=(10, 0))
        ttk.Button(btn_row, text="OK", width=10, command=_ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="Cancel", width=10, command=_cancel).pack(side=tk.LEFT, padx=4)

        dlg.bind("<Return>", _ok)
        dlg.bind("<Escape>", lambda e: _cancel())

        # Centre over parent
        dlg.update_idletasks()
        pw = self.winfo_x();  ph = self.winfo_y()
        dw = dlg.winfo_reqwidth(); dh = dlg.winfo_reqheight()
        dlg.geometry(f"+{pw + 80}+{ph + 120}")

        self.wait_window(dlg)
        return result[0]

    # ------------------------------------------------------------------
    # "Reload with delimiter…" — change delimiter on the active tab
    # ------------------------------------------------------------------

    def _reload_with_delimiter(self):
        """Close current tab and re-open the same file with a new delimiter."""
        current = self._notebook.select()
        if not current:
            messagebox.showinfo("No tab", "No file is currently open.")
            return
        widget = self._notebook.nametowidget(current)
        path = getattr(widget, "_path", None)
        if not path:
            messagebox.showinfo("No file", "Cannot determine the file path for this tab.")
            return
        old_sep      = getattr(widget, "_sep",      ",")
        old_drop_bad = getattr(widget, "_drop_bad", False)
        choice = self._ask_delimiter(old_sep, path, drop_bad=old_drop_bad)
        if choice is None:
            return
        new_sep, new_drop_bad = choice
        self._notebook.forget(current)
        self._open_csv(path, sep=new_sep, drop_bad=new_drop_bad)

    # ------------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------------

    def _close_current_tab(self):
        current = self._notebook.select()
        if current:
            self._notebook.forget(current)
            self._set_status("")

    def _on_tab_middle_click(self, event):
        """Close tab on middle-click of the tab strip."""
        clicked = self._notebook.tk.call(
            self._notebook._w, "identify", "tab", event.x, event.y
        )
        if clicked != "":
            self._notebook.forget(int(clicked))

    def _on_tab_right_click(self, event):
        """Show context menu on right-click of the tab strip."""
        clicked = self._notebook.tk.call(
            self._notebook._w, "identify", "tab", event.x, event.y
        )
        if clicked == "":
            return
        idx = int(clicked)
        tab_name = self._notebook.tab(idx, "text")
        self._tab_ctx_menu.delete(0, "end")
        self._tab_ctx_menu.add_command(
            label=f"Close \u201c{tab_name}\u201d",
            command=lambda i=idx: self._close_tab_by_index(i),
        )
        self._tab_ctx_menu.add_separator()
        self._tab_ctx_menu.add_command(
            label="Close All Tabs",
            command=self._close_all_tabs,
        )
        self._tab_ctx_menu.tk_popup(event.x_root, event.y_root)

    def _close_tab_by_index(self, idx: int):
        """Close the tab at the given index (if still valid)."""
        tabs = self._notebook.tabs()
        if 0 <= idx < len(tabs):
            self._notebook.forget(idx)
            self._set_status("")

    def _close_all_tabs(self):
        """Close every open tab."""
        for tab in self._notebook.tabs():
            self._notebook.forget(tab)
        self._set_status("")

    def _next_tab(self, direction: int):
        tabs = self._notebook.tabs()
        if not tabs:
            return
        current = self._notebook.select()
        try:
            idx = list(tabs).index(current)
        except ValueError:
            idx = 0
        self._notebook.select((idx + direction) % len(tabs))

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _set_status(self, msg: str):
        self._status.configure(text=msg)

    def _show_about(self):
        win = tk.Toplevel(self)
        win.title("About SuperCSV")
        win.resizable(True, True)
        txt = tk.Text(win, wrap="word", relief=tk.FLAT,
                      font=FontManager.get("small"),
                      bg="#ffffff", fg="#222222",
                      padx=12, pady=10, width=120, height=18)
        txt.insert("1.0",
            "SuperCSV — standalone CSV browser\n\n"
            "Built on the FilteredTable widget from the InterfaceSpec pipeline.\n\n"
            "Features:\n"
            "  • Per-column filter boxes with boolean expressions\n"
            "    e.g.  (SMT) OR (THREAD)\n"
            "  • Global Logic bar  e.g.  1 AND NOT 3\n"
            "  • Right-click paths → open in editor at correct line\n"
            "  • Column picker (hide/show columns)\n"
            "  • Email filtered view as CSV attachment\n"
            "  • Export filtered view to CSV\n"
            "  • Sort by clicking column headers\n"
            "  • Fit Cols ↔ button + auto-refit on font change and window resize\n"
            "  • Font-size control  (Ctrl++  /  Ctrl+-  /  Ctrl+0)\n\n"
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

def main():
    parser = argparse.ArgumentParser(
        prog="supercsv",
        description="Interactive browser for CSV/TSV/PSV and other delimited files.",
    )
    parser.add_argument(
        "csv_files",
        nargs="*",
        metavar="FILE",
        help="Delimited files to open (opens a file dialog if none supplied).",
    )
    parser.add_argument(
        "-d", "--delimiter",
        metavar="SEP",
        default=None,
        help=(
            "Delimiter to use for ALL files supplied on the command line "
            "(e.g. ',' '\\t' '|' ';').  "
            "Without this flag each file is auto-detected then confirmed interactively."
        ),
    )
    parser.add_argument(
        "--drop-bad-lines",
        action="store_true",
        default=False,
        help=(
            "Drop rows whose field count does not match the header instead of "
            "padding short rows / trimming long rows (the default behaviour)."
        ),
    )
    args = parser.parse_args()

    # Unescape \\t → actual tab (shell normally strips one layer)
    cli_sep = args.delimiter.replace("\\t", "\t") if args.delimiter else None

    app = SuperCSV(args.csv_files, cli_sep=cli_sep,
                   cli_drop_bad=args.drop_bad_lines)
    app.mainloop()


if __name__ == "__main__":
    main()
