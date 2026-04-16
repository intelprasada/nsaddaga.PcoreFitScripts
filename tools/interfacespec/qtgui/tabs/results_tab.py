"""
ResultsTab — Tab 3: inner Notebook with sub-tabs for each analysis result.

Sub-tabs are created for every CSV file found in the results directory,
plus one per generated Interface Spec.
"""

import glob
import os
import subprocess
import sys
import tempfile
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, Optional

from ..font_manager import FontManager
from ..utils import load_csv_to_df, md_to_html
from ..widgets.filtered_table import FilteredTable
from ..widgets.tab_tooltip import TabTooltip


def _label_from_filename(fname: str) -> str:
    """Turn '03_ooo_connectivity_unresolved.csv' into 'Connectivity Unresolved'."""
    name = os.path.splitext(fname)[0]          # strip .csv
    parts = name.split("_")
    # Drop leading numeric step token (e.g. "03")
    if parts and parts[0].isdigit():
        parts = parts[1:]
    # Drop second token if it looks like a cluster name (short, all-alpha)
    if len(parts) > 1 and parts[0].isalpha() and len(parts[0]) <= 6:
        parts = parts[1:]
    return " ".join(parts).replace("_", " ").title()


class ResultsTab(ttk.Frame):
    """
    Displays analysis results in a set of filterable, sortable sub-tabs.
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._model_root: str = ""
        self._out_root: str = ""

        # ---- Persistent toolbar (always visible, above the sub-tab strip) ----
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, pady=(4, 0))
        self._open_btn = ttk.Button(toolbar, text="📂  Open file…",
                                    command=self._open_csv_file)
        self._open_btn.pack(side=tk.LEFT, padx=8)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y,
                                                         padx=4, pady=2)
        self._toolbar_hint = ttk.Label(toolbar,
                                        text="Open any CSV or Markdown file as a new tab",
                                        foreground="#666")
        self._toolbar_hint.pack(side=tk.LEFT, padx=4)
        FontManager.add_listener(lambda: self._toolbar_hint.configure(
            font=FontManager.get("small")
        ))

        # ---- Inner notebook for result sub-tabs ----
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill=tk.BOTH, expand=True)
        self._subtabs: Dict[str, ttk.Frame] = {}      # key → frame
        self._tables:  Dict[str, FilteredTable] = {}  # key → FilteredTable
        TabTooltip(self._notebook, font=FontManager.get("small"))  # show _path on tab hover

        # Middle-click or right-click to close a tab
        self._notebook.bind("<Button-2>", self._on_tab_middle_click)
        self._notebook.bind("<Button-3>", self._on_tab_right_click)
        self._tab_context_menu = tk.Menu(self._notebook, tearoff=0)

        # Placeholder label when no data yet
        self._placeholder = ttk.Label(
            self, text="Run the pipeline to see results here.",
            font=FontManager.get("normal"), foreground="#888"
        )
        self._placeholder.pack(expand=True)
        FontManager.add_listener(lambda: self._placeholder.configure(
            font=FontManager.get("normal")
        ))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_model_root(self, model_root: str):
        """Propagate model root to all tables so relative paths can be opened."""
        self._model_root = model_root
        for table in self._tables.values():
            table.set_model_root(model_root)

    def load_results(self, result_dir: str, model_root: str = "",
                     out_root: str = ""):
        """
        Open every *.csv in *result_dir* as a sub-tab (sorted by filename).
        Pipeline-run tabs from any previous run are closed first so only the
        current run's results are shown.  Spec tabs and manually-opened CSV
        tabs are preserved.
        """
        if model_root:
            self._model_root = model_root
        if out_root:
            self._out_root = out_root
        if not result_dir or not os.path.isdir(result_dir):
            return
        csv_files = sorted(glob.glob(os.path.join(result_dir, "*.csv")))
        if not csv_files:
            return

        # Remove tabs that belong to a previous pipeline run (keys are file
        # paths that do not start with "spec_" or "open__").
        for key in list(self._subtabs.keys()):
            if not key.startswith("spec_") and not key.startswith("open__"):
                frame = self._subtabs.pop(key)
                self._notebook.forget(frame)
                self._tables.pop(key, None)

        self._hide_placeholder()
        for csv_path in csv_files:
            fname = os.path.basename(csv_path)
            label = _label_from_filename(fname)
            df = load_csv_to_df(csv_path)
            if df.empty:
                continue
            self._upsert_subtab(csv_path, label, df, path=csv_path)

    def load_spec(self, module: str, csv_path: str):
        """Add or refresh an Interface Spec sub-tab for the given module (CSV)."""
        self._hide_placeholder()
        if not csv_path or not os.path.isfile(csv_path):
            return
        df = load_csv_to_df(csv_path)
        if df.empty:
            return
        key   = f"spec_{module}"
        label = f"Spec: {module}"
        self._upsert_subtab(key, label, df, path=csv_path)
        # Switch to the new spec tab
        if key in self._subtabs:
            self._notebook.select(self._subtabs[key])

    def load_spec_md(self, module: str, md_path: str):
        """Add or refresh a markdown Interface Spec sub-tab for the given module."""
        self._hide_placeholder()
        if not md_path or not os.path.isfile(md_path):
            return
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return

        key   = f"spec_{module}"
        label = f"Spec: {module}"

        # Reuse existing frame or create a new one
        if key in self._subtabs:
            frame = self._subtabs[key]
            # Clear old widgets
            for child in frame.winfo_children():
                child.destroy()
        else:
            frame = ttk.Frame(self._notebook)
            self._notebook.add(frame, text=label)
            self._subtabs[key] = frame

        # Scrollable text widget showing markdown source
        txt = tk.Text(frame, wrap=tk.WORD, state=tk.NORMAL, relief=tk.FLAT,
                      font=FontManager.get("mono"))
        vsb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        txt.insert("1.0", content)
        txt.configure(state=tk.DISABLED)

        # Bottom toolbar: open-externally + preview-in-browser + path label
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(
            btn_frame, text="📂 Open file",
            command=lambda p=md_path: self._open_path_externally(p),
        ).pack(side=tk.LEFT, padx=4, pady=2)
        ttk.Button(
            btn_frame, text="🌐 Preview in Browser",
            command=lambda p=md_path, m=module: self._preview_md_in_browser(p, m),
        ).pack(side=tk.LEFT, padx=4, pady=2)
        ttk.Label(btn_frame, text=md_path, anchor="w",
                  foreground="gray").pack(side=tk.LEFT, padx=4)

        if key in self._subtabs:
            self._notebook.select(self._subtabs[key])

    def clear(self):
        for key in list(self._subtabs.keys()):
            frame = self._subtabs.pop(key)
            self._notebook.forget(frame)
        self._tables.clear()
        self._placeholder.pack(expand=True)

    # ------------------------------------------------------------------
    # Open CSV
    # ------------------------------------------------------------------

    def _open_csv_file(self):
        """Let the user pick a CSV or Markdown file and open it in a new sub-tab."""
        path = filedialog.askopenfilename(
            title="Open File",
            filetypes=[
                ("Supported files", "*.csv *.md"),
                ("CSV files", "*.csv"),
                ("Markdown files", "*.md"),
                ("All files", "*.*"),
            ],
            initialdir=self._out_root or self._model_root or os.path.expanduser("~"),
        )
        if not path:
            return
        fname = os.path.basename(path)
        self._hide_placeholder()

        # Route .md files to the markdown viewer
        if path.lower().endswith(".md"):
            module = os.path.splitext(fname)[0]
            self.load_spec_md(module, path)
            return

        df = load_csv_to_df(path)
        if df.empty:
            messagebox.showwarning(
                "Empty / unreadable",
                f"No data could be loaded from:\n{path}",
                parent=self,
            )
            return
        # Use full path as key so the same file re-loads into the same tab
        key   = f"open__{path}"
        label = f"📂 {fname}"
        self._upsert_subtab(key, label, df, path=path)
        if key in self._subtabs:
            self._notebook.select(self._subtabs[key])

    def _open_path_externally(self, path: str):
        """Open a file with the OS default application."""
        try:
            if sys.platform.startswith("darwin"):
                subprocess.Popen(["open", path])
            elif sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            messagebox.showerror("Open failed", str(exc), parent=self)

    def _preview_md_in_browser(self, md_path: str, title: str = ""):
        """Convert the markdown file to HTML and open it in the default browser."""
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                md_text = f.read()
        except Exception as exc:
            messagebox.showerror("Preview failed",
                                 f"Could not read file:\n{exc}", parent=self)
            return
        try:
            html = md_to_html(md_text, title=title or os.path.basename(md_path))
            # Write to a temp file that persists until OS cleans /tmp
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".html", delete=False,
                prefix=f"interfacespec_{title or 'spec'}_",
                encoding="utf-8",
            )
            tmp.write(html)
            tmp.flush()
            tmp.close()
            webbrowser.open(f"file://{tmp.name}")
        except Exception as exc:
            messagebox.showerror("Preview failed", str(exc), parent=self)

    # ------------------------------------------------------------------
    # Tab close (right-click menu / middle-click)
    # ------------------------------------------------------------------

    def _tab_key_at(self, event) -> Optional[str]:
        """Return the subtab key for the tab under the mouse, or None."""
        clicked = self._notebook.tk.call(
            self._notebook._w, "identify", "tab", event.x, event.y
        )
        if clicked == "":
            return None
        idx = int(clicked)
        tabs = self._notebook.tabs()  # list of widget path strings
        if not (0 <= idx < len(tabs)):
            return None
        tab_name = tabs[idx]
        for key, frame in self._subtabs.items():
            if str(frame) == tab_name:
                return key
        return None

    def _close_tab(self, key: str):
        """Remove the sub-tab identified by *key* from the notebook."""
        frame = self._subtabs.pop(key, None)
        if frame is None:
            return
        self._tables.pop(key, None)
        self._notebook.forget(frame)
        frame.destroy()
        if not self._subtabs:
            self._placeholder.pack(expand=True)

    def _on_tab_middle_click(self, event):
        key = self._tab_key_at(event)
        if key:
            self._close_tab(key)

    def _on_tab_right_click(self, event):
        key = self._tab_key_at(event)
        if not key:
            return
        menu = self._tab_context_menu
        menu.delete(0, tk.END)
        label = self._notebook.tab(self._subtabs[key], "text")
        menu.add_command(
            label=f'Close  \u201c{label}\u201d',
            command=lambda k=key: self._close_tab(k),
        )
        menu.add_separator()
        menu.add_command(label="Close All Tabs", command=self.clear)
        menu.tk_popup(event.x_root, event.y_root)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _upsert_subtab(self, key: str, label: str, df, path: str = ""):
        if key in self._subtabs:
            self._tables[key].load(df)
        else:
            frame = ttk.Frame(self._notebook)
            frame._path = path  # read by TabTooltip on hover
            table = FilteredTable(frame, model_root=self._model_root)
            table.set_tab_label(label)
            table.pack(fill=tk.BOTH, expand=True)
            table.load(df)
            self._notebook.add(frame, text=label)
            self._subtabs[key] = frame
            self._tables[key]  = table

    def _hide_placeholder(self):
        try:
            self._placeholder.pack_forget()
        except Exception:
            pass

