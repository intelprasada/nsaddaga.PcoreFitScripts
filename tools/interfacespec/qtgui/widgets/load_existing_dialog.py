"""
LoadExistingDialog — lets the user pick a past pipeline run to load
into the Results tab without re-running the pipeline.

Behaviour:
 • Auto-discovers all past runs under the current model_root / cluster.
 • Displays them in a listbox (newest first) with timestamp + CSV count.
 • "Browse…" button lets user pick any directory manually.
 • Shows how many CSV files are found in the selected directory.
 • Optional "Re-run analysis scripts" checkbox for missing CSVs.
 • On OK: calls back on_confirm(result_dir, run_analysis=True/False).
"""

import glob
import os
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, List, Optional, Tuple

from ..font_manager import FontManager
from ..utils import list_pipeline_runs


class LoadExistingDialog(tk.Toplevel):
    """
    Modal dialog for selecting an existing pipeline run.

    Parameters
    ----------
    parent      : parent widget
    cluster     : current cluster (used to search for past runs)
    model_root  : model root directory
    on_confirm  : callback(result_dir: str, run_analysis: bool)
    """

    def __init__(
        self,
        parent,
        cluster: str,
        model_root: str,
        on_confirm: Callable[[str, bool], None],
        out_root: str = "",
    ):
        super().__init__(parent)
        self.title("Load Existing Results")
        self.resizable(True, True)
        self.geometry("820x480")
        self.grab_set()  # modal

        self._cluster    = cluster
        self._model_root = model_root
        self._out_root   = out_root
        self._on_confirm = on_confirm
        self._runs: List[Tuple[str, str]] = []  # (label, result_dir)
        self._selected_dir = tk.StringVar()
        self._run_analysis = tk.BooleanVar(value=False)

        self._build()
        self._load_runs()
        # Apply FontManager fonts, register listener for dynamic updates
        self._apply_fonts()
        FontManager.add_listener(self._apply_fonts)
        # Block until dialog closed
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.transient(parent)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        # ── Title bar ────────────────────────────────────────────────
        header = tk.Frame(self, bg="#3a3a5c", pady=6)
        header.pack(fill=tk.X)
        self._header_label = tk.Label(
            header,
            text=f"  Load existing results  —  cluster: {self._cluster}",
            bg="#3a3a5c", fg="white",
            font=FontManager.get("bold"),
        )
        self._header_label.pack(side=tk.LEFT, padx=8)

        # ── Left: run list ────────────────────────────────────────────
        body = ttk.Frame(self, padding=8)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)

        ttk.Label(body, text="Detected pipeline runs (newest first):").grid(
            row=0, column=0, sticky="w", pady=(0, 2)
        )
        ttk.Label(body, text="CSV files in selected run:").grid(
            row=0, column=1, sticky="w", padx=(12, 0), pady=(0, 2)
        )

        # Listbox for runs
        list_frame = ttk.Frame(body)
        list_frame.grid(row=1, column=0, sticky="nsew")
        sb_y = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        sb_x = ttk.Scrollbar(list_frame, orient=tk.HORIZONTAL)
        self._listbox = tk.Listbox(
            list_frame,
            yscrollcommand=sb_y.set,
            xscrollcommand=sb_x.set,
            selectmode=tk.SINGLE,
            font=FontManager.get("mono"),
            activestyle="dotbox",
            bg="#1a1a2e", fg="#c8d6e5",
            selectbackground="#3a7bd5", selectforeground="white",
            borderwidth=1, relief="sunken",
        )
        sb_y.config(command=self._listbox.yview)
        sb_x.config(command=self._listbox.xview)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        sb_x.pack(side=tk.BOTTOM, fill=tk.X)
        self._listbox.pack(fill=tk.BOTH, expand=True)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)
        self._listbox.bind("<Double-Button-1>", lambda _: self._ok())

        # Status panel (right column) — simple CSV list
        status_frame = ttk.Frame(body, padding=(12, 0, 0, 0))
        status_frame.grid(row=1, column=1, sticky="nsew")
        self._csv_status_lbl = ttk.Label(status_frame, text="—",
                                          foreground="#888",
                                          wraplength=200, justify="left")
        self._csv_status_lbl.pack(anchor="nw")

        # ── Selected dir path entry ───────────────────────────────────
        path_row = ttk.Frame(self, padding=(8, 4, 8, 0))
        path_row.pack(fill=tk.X)
        ttk.Label(path_row, text="Selected dir:").pack(side=tk.LEFT)
        self._sel_dir_entry = ttk.Entry(path_row, textvariable=self._selected_dir, width=72,
                                        font=FontManager.get("normal"))
        self._sel_dir_entry.pack(side=tk.LEFT, padx=4, expand=True, fill=tk.X)
        ttk.Button(path_row, text="Browse…", command=self._browse).pack(side=tk.LEFT)

        # ── Options ───────────────────────────────────────────────────
        opt_row = ttk.Frame(self, padding=(8, 4, 8, 0))
        opt_row.pack(fill=tk.X)
        ttk.Checkbutton(
            opt_row,
            text="Re-run analysis scripts for missing CSVs",
            variable=self._run_analysis,
        ).pack(side=tk.LEFT)

        # ── Buttons ───────────────────────────────────────────────────
        btn_row = ttk.Frame(self, padding=8)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="OK", command=self._ok, width=10).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_row, text="Cancel", command=self._cancel, width=10).pack(side=tk.RIGHT)

        # ── No-runs notice ────────────────────────────────────────────
        self._no_runs_lbl = ttk.Label(
            list_frame,
            text="No past runs found for this cluster / model root.",
            foreground="#f0a500",
        )

    # ------------------------------------------------------------------
    # Font updates
    # ------------------------------------------------------------------

    def _apply_fonts(self):
        """Update all non-ttk widget fonts from FontManager."""
        try:
            self._header_label.configure(font=FontManager.get("bold"))
            self._listbox.configure(font=FontManager.get("mono"))
            self._sel_dir_entry.configure(font=FontManager.get("normal"))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Populate list
    # ------------------------------------------------------------------

    def _load_runs(self):
        self._listbox.delete(0, tk.END)
        if not self._model_root or not self._cluster:
            self._no_runs_lbl.place(relx=0.5, rely=0.5, anchor="center")
            return
        self._runs = list_pipeline_runs(self._cluster, self._model_root)
        if not self._runs:
            self._no_runs_lbl.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self._no_runs_lbl.place_forget()
            for label, _ in self._runs:
                self._listbox.insert(tk.END, f"  {label}")
            self._listbox.selection_set(0)
            self._listbox.event_generate("<<ListboxSelect>>")

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _on_select(self, _event=None):
        sel = self._listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self._runs):
            _, result_dir = self._runs[idx]
            self._selected_dir.set(result_dir)
            self._update_csv_status(result_dir)

    def _browse(self):
        initial = self._out_root or self._model_root or "/"
        d = filedialog.askdirectory(
            parent=self, title="Select results directory", initialdir=initial
        )
        if d:
            self._selected_dir.set(d)
            self._listbox.selection_clear(0, tk.END)
            self._update_csv_status(d)

    def _update_csv_status(self, result_dir: str):
        if not result_dir or not os.path.isdir(result_dir):
            self._csv_status_lbl.configure(text="—", foreground="#888")
            return
        csv_files = sorted(glob.glob(os.path.join(result_dir, "*.csv")))
        if not csv_files:
            self._csv_status_lbl.configure(
                text="No CSV files found", foreground="#e05c5c"
            )
        else:
            names = "\n".join(os.path.basename(f) for f in csv_files)
            self._csv_status_lbl.configure(
                text=f"{len(csv_files)} CSV files:\n{names}",
                foreground="#22cc66",
            )

    # ------------------------------------------------------------------
    # OK / Cancel
    # ------------------------------------------------------------------

    def _ok(self):
        result_dir = self._selected_dir.get().strip()
        if not result_dir:
            tk.messagebox.showwarning("Nothing selected", "Please select a results directory.",
                                      parent=self)
            return
        if not os.path.isdir(result_dir):
            tk.messagebox.showerror("Not found",
                                    f"Directory not found:\n{result_dir}", parent=self)
            return
        run_analysis = self._run_analysis.get()
        self.destroy()
        self._on_confirm(result_dir, run_analysis)

    def _cancel(self):
        self.destroy()

