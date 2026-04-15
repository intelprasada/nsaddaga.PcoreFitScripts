"""
SpecTab — Tab 2: generate an interface specification for a module.

Layout:
  [Result dir      ] [Browse]
  [Module   ]  [Cluster   ]  [Subgroup threshold: ___]
  [Generate Spec button]  [Core Hierarchy button]
  ── Live log ──

Per README intent "Generate interface specification for a module":
  - Requires 18_<module>_top_io_table.csv in the result directory.
  - Runs generate_interface_spec.py --io-csv <18_csv> --module <module>
    --cluster <cluster> --subgroup-threshold <n>
  - Output: <module>_interface_spec.md in the same results directory.
"""

import glob as _glob
import os
import queue
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional

from ..font_manager import FontManager
from ..runner.spec_runner import SpecRunner
from ..utils import scripts_dir
from ..widgets.hierarchy_viewer import HierarchyViewer
from ..widgets.log_panel import LogPanel
from ..widgets.path_entry import LabeledPathEntry


class SpecTab(ttk.Frame):
    """
    Tab for generating an interface specification for a named module.

    on_spec_ready(module, out_md) is called when the markdown file is ready.
    """

    def __init__(self, parent,
                 on_spec_ready: Callable[[str, str], None],
                 **kwargs):
        super().__init__(parent, **kwargs)
        self._on_spec_ready = on_spec_ready
        self._log_queue: queue.Queue = queue.Queue()
        self._running = False

        self._build_form()
        self._build_log()
        self._poll_log()
        FontManager.add_listener(self._on_font_change)

    # ------------------------------------------------------------------
    # Form
    # ------------------------------------------------------------------

    def _build_form(self):
        form = ttk.LabelFrame(self, text="Interface Spec Configuration", padding=8)
        form.pack(fill=tk.X, padx=8, pady=6)

        # Result dir
        self._result_dir = LabeledPathEntry(form, label="Result dir:", mode="dir", width=60)
        self._result_dir.pack(fill=tk.X, pady=2)

        # Row 2: module | cluster | subgroup threshold
        row2 = ttk.Frame(form)
        row2.pack(fill=tk.X, pady=2)

        ttk.Label(row2, text="Module:", width=10, anchor="w").pack(side=tk.LEFT)
        self._module_var = tk.StringVar()
        self._module_entry = ttk.Entry(row2, textvariable=self._module_var, width=18,
                                       font=FontManager.get("normal"))
        self._module_entry.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(row2, text="Cluster:", anchor="w").pack(side=tk.LEFT)
        self._cluster_var = tk.StringVar()
        self._cluster_entry = ttk.Entry(row2, textvariable=self._cluster_var, width=12,
                                        font=FontManager.get("normal"))
        self._cluster_entry.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(row2, text="Subgroup threshold:", anchor="w").pack(side=tk.LEFT)
        self._threshold_var = tk.IntVar(value=10)
        self._threshold_spin = ttk.Spinbox(
            row2, from_=1, to=200, textvariable=self._threshold_var, width=5,
            font=FontManager.get("normal"),
        )
        self._threshold_spin.pack(side=tk.LEFT, padx=2)

        # Model root (hidden — needed for cwd)
        self._model_root_var = tk.StringVar()

        # Buttons
        btn_row = ttk.Frame(form)
        btn_row.pack(fill=tk.X, pady=(6, 0))
        self._gen_btn = ttk.Button(btn_row, text="▶  Generate Spec",
                                   command=self._generate)
        self._gen_btn.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="🌳 Core Hierarchy",
                   command=self._show_hierarchy).pack(side=tk.LEFT, padx=(0, 8))
        self._status_label = ttk.Label(btn_row, text="")
        self._status_label.pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------

    def _build_log(self):
        self._log = LogPanel(self, height=16)
        self._log.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

    def _on_font_change(self):
        """Propagate font changes to widgets that need explicit updates."""
        f = FontManager.get("normal")
        for widget in [self._module_entry, self._cluster_entry, self._threshold_spin]:
            try:
                widget.configure(font=f)
            except Exception:
                pass

    def _poll_log(self):
        try:
            while True:
                level, line = self._log_queue.get_nowait()
                self._log.append(line, level)
        except queue.Empty:
            pass
        self.after(80, self._poll_log)

    # ------------------------------------------------------------------
    # Core Hierarchy
    # ------------------------------------------------------------------

    def _show_hierarchy(self):
        model_root = self._model_root_var.get().strip()
        if not model_root:
            messagebox.showinfo(
                "Model root not set",
                "The model root is not set yet.\n"
                "Please run the pipeline first, or set it in Settings.",
                parent=self,
            )
            return
        HierarchyViewer(self, model_root)

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------

    def _generate(self):
        if self._running:
            return

        result_dir = self._result_dir.get().strip()
        module     = self._module_var.get().strip()
        cluster    = self._cluster_var.get().strip()
        threshold  = self._threshold_var.get()
        model_root = self._model_root_var.get().strip()

        if not result_dir or not os.path.isdir(result_dir):
            messagebox.showerror("Invalid path", "Result directory does not exist.")
            return
        if not module:
            messagebox.showerror("Missing field", "Module name is required.")
            return

        # Prefer 20_* (xhier-enriched) over 18_* (plain top I/O table).
        # 20_* has xhier_consumers/xhier_producers populated by enrich_cross_hierarchy.py
        # which is required for correct interface classification when cou=NONE.
        io_csv = None
        for pattern, label in [
            (f"20_{module}_pseudo_top_enriched.csv", "20_*"),
            (f"20_*{module}*enriched*.csv",          "20_*"),
            (f"18_{module}_top_io_table.csv",         "18_*"),
            (f"18_*{module}*top_io_table*.csv",       "18_*"),
        ]:
            matches = _glob.glob(os.path.join(result_dir, pattern))
            if matches:
                io_csv = matches[0]
                break
        if not io_csv:
            messagebox.showerror(
                "Missing file",
                f"Cannot find 20_{module}_pseudo_top_enriched.csv or "
                f"18_{module}_top_io_table.csv in:\n{result_dir}\n\n"
                "Run the full pipeline first to generate these files.",
            )
            return

        # Locate 12_* internal IO table for sub-grouping (optional)
        internal_csv = None
        for pattern in [
            f"12_{module}_query_io_table.csv",
            f"12_*{module}*query_io_table*.csv",
        ]:
            matches = _glob.glob(os.path.join(result_dir, pattern))
            if matches:
                internal_csv = matches[0]
                break

        out_md = os.path.join(result_dir, f"{module}_interface_spec.md")

        # Fall back model_root to grandparent of result_dir if not set
        if not model_root:
            model_root = str(os.path.abspath(
                os.path.join(result_dir, os.pardir, os.pardir, os.pardir, os.pardir)
            ))

        self._running = True
        self._gen_btn.configure(state=tk.DISABLED)
        self._set_status("Generating…", "blue")
        self._log.clear()

        runner = SpecRunner(
            scripts_dir=scripts_dir(),
            model_root=model_root,
            io_csv=io_csv,
            internal_csv=internal_csv,
            module=module,
            cluster=cluster,
            subgroup_threshold=threshold,
            out_md=out_md,
            log_queue=self._log_queue,
            on_complete=lambda ok, path: self.after(
                0, self._spec_done, ok, path, module
            ),
        )
        runner.start()

    def _spec_done(self, success: bool, out_md: Optional[str], module: str):
        self._running = False
        self._gen_btn.configure(state=tk.NORMAL)
        if success and out_md:
            self._set_status("✔  Done.", "green")
            self._on_spec_ready(module, out_md)
        else:
            self._set_status("Failed.", "red")

    def _set_status(self, msg: str, color: str):
        self._status_label.configure(text=msg, foreground=color)

    # ------------------------------------------------------------------
    # Public — called by MainApp after pipeline completes
    # ------------------------------------------------------------------

    def set_result_dir(self, result_dir: str):
        self._result_dir.set(result_dir)

    def set_model_root(self, model_root: str):
        self._model_root_var.set(model_root)

    def set_cluster(self, cluster: str):
        """Auto-fill the Cluster field when a pipeline run completes."""
        self._cluster_var.set(cluster)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def apply_settings(self, settings: dict):
        if "result_dir" in settings:
            self._result_dir.set(settings["result_dir"])
        self._result_dir.set_recent(settings.get("recent_result_dir", []))
        if "module" in settings:
            self._module_var.set(settings["module"])
        if "cluster" in settings:
            self._cluster_var.set(settings["cluster"])
        if "subgroup_threshold" in settings:
            self._threshold_var.set(settings["subgroup_threshold"])
        if "model_root" in settings:
            self._model_root_var.set(settings["model_root"])

    def collect_settings(self) -> dict:
        return {
            "result_dir":        self._result_dir.get(),
            "recent_result_dir": self._result_dir.get_recent(),
            "module":            self._module_var.get(),
            "cluster":           self._cluster_var.get(),
            "subgroup_threshold": self._threshold_var.get(),
        }

    def settings_vars(self):
        """Return the tkinter Variables that should trigger auto-save."""
        return [self._result_dir.var, self._module_var, self._cluster_var, self._threshold_var]
