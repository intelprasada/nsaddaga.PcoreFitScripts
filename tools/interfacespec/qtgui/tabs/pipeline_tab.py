"""
PipelineTab — Tab 1: run the InterfaceSpec pipeline for a cluster.

Layout:
  [Model root    ] [Browse]
  [Cluster ▼    ]  [Top-V  ] [Gen-dir ]
  [Run Pipeline button]
  ── Live log ──
"""

import os
import queue
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional

from ..config import (CLUSTER_NAMES, get_fallback_gen_dir, get_gen_dir,
                      get_icf_glob, get_top_v, get_hierarchy, build_hierarchy_index)
from ..hier_utils import build_children_map
from ..font_manager import FontManager
from ..runner.analysis_runner import AnalysisRunner
from ..runner.pipeline_runner import PipelineRunner
from ..runner.xhier_pipeline_runner import XhierPipelineRunner
from ..utils import find_latest_result_dir, scripts_dir
from ..widgets.load_existing_dialog import LoadExistingDialog
from ..widgets.log_panel import LogPanel
from ..widgets.path_entry import LabeledPathEntry


class PipelineTab(ttk.Frame):
    """
    Tab for configuring and running the pipeline + post-pipeline analysis.

    on_results_ready(cluster, result_dir, result_paths) is called when the
    full analysis completes and results are ready to display.
    """

    def __init__(self, parent,
                 on_results_ready: Callable[[str, str, dict], None],
                 **kwargs):
        super().__init__(parent, **kwargs)
        self._on_results_ready = on_results_ready
        self._log_queue: queue.Queue = queue.Queue()
        self._running = False
        self._hier_info = None   # ModuleHierInfo for selected sub-module (None = cluster-level run)

        self._build_form()
        self._build_log()
        self._poll_log()
        FontManager.add_listener(self._on_font_change)

    # ------------------------------------------------------------------
    # Form
    # ------------------------------------------------------------------

    def _build_form(self):
        form = ttk.LabelFrame(self, text="Pipeline Configuration", padding=8)
        form.pack(fill=tk.X, padx=8, pady=6)

        # Model root
        self._model_root = LabeledPathEntry(form, label="Model root:", mode="dir", width=60)
        self._model_root.pack(fill=tk.X, pady=2)

        # Output dir (optional — user-specified results location)
        self._out_dir = LabeledPathEntry(form, label="Output dir:", mode="dir", width=60,
                                         placeholder="(default: <gen-dir>/InterfaceSpecAgent)")
        self._out_dir.pack(fill=tk.X, pady=2)

        # Row: cluster + top-v
        row2 = ttk.Frame(form)
        row2.pack(fill=tk.X, pady=2)

        self._cluster_lbl = ttk.Label(row2, text="Cluster:", width=10, anchor="w",
                                      font=FontManager.get("normal"))
        self._cluster_lbl.pack(side=tk.LEFT)
        self._cluster_var = tk.StringVar(value=CLUSTER_NAMES[0])
        self._cluster_cb = ttk.Combobox(row2, textvariable=self._cluster_var,
                                        values=CLUSTER_NAMES, state="readonly", width=8,
                                        font=FontManager.get("normal"))
        self._cluster_cb.pack(side=tk.LEFT, padx=(0, 10))
        self._cluster_cb.bind("<<ComboboxSelected>>", self._on_cluster_change)

        self._topv_lbl = ttk.Label(row2, text="Top .v:", anchor="w",
                                   font=FontManager.get("normal"))
        self._topv_lbl.pack(side=tk.LEFT)
        self._top_v_var = tk.StringVar()
        self._top_v_entry = ttk.Entry(row2, textvariable=self._top_v_var, width=46,
                                      font=FontManager.get("normal"))
        self._top_v_entry.pack(side=tk.LEFT, padx=(2, 0))

        # Row: module (sub-module of cluster, optional)
        row2b = ttk.Frame(form)
        row2b.pack(fill=tk.X, pady=2)

        self._module_lbl = ttk.Label(row2b, text="Module:", width=10, anchor="w",
                                     font=FontManager.get("normal"))
        self._module_lbl.pack(side=tk.LEFT)
        self._module_var = tk.StringVar()
        self._module_cb = ttk.Combobox(row2b, textvariable=self._module_var,
                                       state="readonly", width=18,
                                       font=FontManager.get("normal"))
        self._module_cb.pack(side=tk.LEFT, padx=(0, 10))
        self._module_cb.bind("<<ComboboxSelected>>", self._on_module_change)

        ttk.Label(row2b, text="(leave blank to run the full cluster)",
                  font=FontManager.get("small"), foreground="gray").pack(side=tk.LEFT)

        # Gen dir — read-only, auto-computed from model root + cluster
        row3 = ttk.Frame(form)
        row3.pack(fill=tk.X, pady=1)
        self._gen_dir_prefix_lbl = ttk.Label(row3, text="Gen dir:", width=10, anchor="w",
                                             font=FontManager.get("normal"))
        self._gen_dir_prefix_lbl.pack(side=tk.LEFT)
        self._gen_dir_var = tk.StringVar()
        self._gen_dir_lbl = ttk.Label(row3, textvariable=self._gen_dir_var,
                                      foreground="gray", font=FontManager.get("small"),
                                      anchor="w")
        self._gen_dir_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Row: parent cluster + grandparent cluster (auto-populated for sub-modules)
        row3b = ttk.Frame(form)
        row3b.pack(fill=tk.X, pady=1)

        self._parent_lbl = ttk.Label(row3b, text="Parent:", width=10, anchor="w",
                                     font=FontManager.get("normal"))
        self._parent_lbl.pack(side=tk.LEFT)
        self._parent_var = tk.StringVar()
        self._parent_entry = ttk.Entry(row3b, textvariable=self._parent_var,
                                       state="readonly", width=10,
                                       font=FontManager.get("normal"))
        self._parent_entry.pack(side=tk.LEFT, padx=(0, 16))

        self._grandparent_lbl = ttk.Label(row3b, text="Grandparent:", anchor="w",
                                          font=FontManager.get("normal"))
        self._grandparent_lbl.pack(side=tk.LEFT)
        self._grandparent_var = tk.StringVar()
        self._grandparent_entry = ttk.Entry(row3b, textvariable=self._grandparent_var,
                                            state="readonly", width=10,
                                            font=FontManager.get("normal"))
        self._grandparent_entry.pack(side=tk.LEFT)

        # Auto-fill on model root / cluster change
        self._model_root.trace(lambda _: self._autofill())
        self._autofill()

        # Buttons
        btn_row = ttk.Frame(form)
        btn_row.pack(fill=tk.X, pady=(6, 0))
        self._run_btn = ttk.Button(btn_row, text="\u25b6  Run Pipeline",
                                   command=self._run_pipeline, style="Accent.TButton")
        self._run_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._load_btn = ttk.Button(btn_row, text="\U0001f4c2  Load Existing\u2026",
                                    command=self._open_load_dialog)
        self._load_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._status_label = ttk.Label(btn_row, text="", font=FontManager.get("normal"))
        self._status_label.pack(side=tk.LEFT)


    def _on_cluster_change(self, _event=None):
        # Reset module selection when cluster changes
        self._module_var.set("")
        self._hier_info = None
        self._refresh_module_list()
        self._autofill()

    def _on_module_change(self, _event=None):
        module = self._module_var.get().strip()
        root   = self._model_root.get()
        cluster = self._cluster_var.get()
        if module and module != cluster and root:
            info = get_hierarchy(module, root)
            self._hier_info = info
            if info:
                self._top_v_var.set(info.top_v)
                self._gen_dir_var.set(info.gen_dir)
                self._parent_var.set(info.parent or "")
                self._grandparent_var.set(info.grandparent or "")
                return
        # No sub-module selected — revert to cluster-level values
        self._hier_info = None
        self._autofill()
        self._parent_var.set("")
        self._grandparent_var.set("")

    def _refresh_module_list(self):
        """Populate the Module combobox with children of the selected cluster."""
        root    = self._model_root.get()
        cluster = self._cluster_var.get()
        options = [""]
        if root and cluster:
            try:
                cmap = build_children_map(root)
                options += sorted(cmap.get(cluster, []))
            except Exception:
                pass
        self._module_cb.configure(values=options)

    def _autofill(self):
        root = self._model_root.get()
        cluster = self._cluster_var.get()
        if root:
            self._top_v_var.set(get_top_v(cluster, root))
            self._gen_dir_var.set(get_gen_dir(cluster, root))
            self._refresh_module_list()

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------

    def _build_log(self):
        self._log = LogPanel(self, height=16)
        self._log.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

    def _on_font_change(self):
        """Propagate font changes to widgets that need explicit updates."""
        f  = FontManager.get("normal")
        fs = FontManager.get("small")
        for widget, font in [
            (self._cluster_lbl,        f),
            (self._cluster_cb,         f),
            (self._topv_lbl,           f),
            (self._top_v_entry,        f),
            (self._module_lbl,         f),
            (self._module_cb,          f),
            (self._gen_dir_prefix_lbl, f),
            (self._gen_dir_lbl,        fs),
            (self._parent_lbl,         f),
            (self._parent_entry,       f),
            (self._grandparent_lbl,    f),
            (self._grandparent_entry,  f),
            (self._status_label,       f),
        ]:
            try:
                widget.configure(font=font)
            except Exception:
                pass

    def _poll_log(self):
        """Drain the log queue and write to LogPanel. Re-schedules itself."""
        try:
            while True:
                level, line = self._log_queue.get_nowait()
                self._log.append(line, level)
        except queue.Empty:
            pass
        self.after(80, self._poll_log)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _run_pipeline(self):
        if self._running:
            return

        model_root = self._model_root.get()
        cluster    = self._cluster_var.get()
        top_v      = self._top_v_var.get().strip()
        gen_dir    = self._gen_dir_var.get().strip()
        out_root   = self._out_dir.get().strip() or None

        if not model_root or not os.path.isdir(model_root):
            messagebox.showerror("Invalid path", "Model root directory does not exist.")
            return
        if not top_v:
            messagebox.showerror("Missing field", "Top .v path is required.")
            return
        if not gen_dir:
            messagebox.showerror("Missing field", "Gen dir is required.")
            return
        if out_root and not os.path.isdir(out_root):
            try:
                os.makedirs(out_root, exist_ok=True)
            except Exception as exc:
                messagebox.showerror("Invalid path", f"Cannot create output dir:\n{exc}")
                return

        self._running = True
        self._run_btn.configure(state=tk.DISABLED)
        self._load_btn.configure(state=tk.DISABLED)
        self._set_status("Running pipeline…", "blue")
        self._log.clear()

        fallback = get_fallback_gen_dir(cluster, model_root)

        # ------------------------------------------------------------------
        # Choose runner: xhier (sub-module) vs standard (cluster-level)
        # ------------------------------------------------------------------
        hier = self._hier_info

        if hier is not None:
            # Sub-module selected — run full 6-step xhier pipeline
            module  = hier.module
            parent  = hier.xhier_parent
            gpar    = hier.xhier_grandparent

            from ..config import get_top_v as _gtv, get_gen_dir as _ggd

            par_top_v  = _gtv(parent,  model_root) if parent else None
            par_gen    = _ggd(parent,  model_root) if parent else None
            gpar_top_v = _gtv(gpar,    model_root) if gpar   else None
            gpar_gen   = _ggd(gpar,    model_root) if gpar   else None

            if not parent or not par_top_v or not par_gen:
                messagebox.showerror(
                    "Missing parent",
                    f"Cannot determine parent cluster for module '{module}'.",
                )
                self._running = False
                self._run_btn.configure(state=tk.NORMAL)
                self._load_btn.configure(state=tk.NORMAL)
                return

            self._log.append(
                f"Starting xhier pipeline for module '{module}' "
                f"(parent: {parent}" + (f", grandparent: {gpar}" if gpar else "") + ")",
                "STEP",
            )
            runner = XhierPipelineRunner(
                scripts_dir=scripts_dir(),
                model_root=model_root,
                module=module,
                module_top_v=top_v,
                module_gen_dir=gen_dir,
                parent=parent,
                parent_top_v=par_top_v,
                parent_gen_dir=par_gen,
                grandparent=gpar,
                gparent_top_v=gpar_top_v,
                gparent_gen_dir=gpar_gen,
                fallback_gen_dir=fallback,
                out_root=out_root,
                log_queue=self._log_queue,
                on_complete=lambda ok, rdir: self.after(
                    0, self._pipeline_done, ok, rdir, module, model_root, out_root
                ),
            )
        else:
            # Standard cluster-level pipeline
            self._log.append(f"Starting pipeline for cluster '{cluster}'", "STEP")
            runner = PipelineRunner(
                scripts_dir=scripts_dir(),
                model_root=model_root,
                cluster=cluster,
                top_v=top_v,
                gen_dir=gen_dir,
                fallback_gen_dir=fallback,
                out_root=out_root,
                log_queue=self._log_queue,
                on_complete=lambda ok, rdir: self.after(
                    0, self._pipeline_done, ok, rdir, cluster, model_root, out_root
                ),
            )

        runner.start()

    def _pipeline_done(self, success: bool, result_dir: Optional[str],
                       cluster: str, model_root: str, out_root: Optional[str]):
        if not success or not result_dir:
            self._set_status("Pipeline failed.", "red")
            self._running = False
            self._run_btn.configure(state=tk.NORMAL)
            self._load_btn.configure(state=tk.NORMAL)
            return

        self._set_status("Pipeline done. Running analysis…", "orange")
        self._log.append("Pipeline complete. Starting analysis scripts…", "STEP")

        analysis = AnalysisRunner(
            scripts_dir=scripts_dir(),
            model_root=model_root,
            cluster=cluster,
            result_dir=result_dir,
            log_queue=self._log_queue,
            on_complete=lambda paths: self.after(
                0, self._analysis_done, cluster, result_dir, paths
            ),
        )
        analysis.start()

    def _analysis_done(self, cluster: str, result_dir: str, result_paths: dict = None):
        self._set_status("✔  All done.", "green")
        self._running = False
        self._run_btn.configure(state=tk.NORMAL)
        self._load_btn.configure(state=tk.NORMAL)
        self._log.append("Analysis complete. Results ready.", "OK")
        self._on_results_ready(cluster, result_dir)

    # ------------------------------------------------------------------
    # Load Existing
    # ------------------------------------------------------------------

    def _open_load_dialog(self):
        """Open the 'Load Existing Results' dialog."""
        LoadExistingDialog(
            parent=self,
            cluster=self._cluster_var.get(),
            model_root=self._model_root.get(),
            out_root=self._out_dir.get().strip(),
            on_confirm=self._load_existing_confirm,
        )

    def _load_existing_confirm(self, result_dir: str, run_analysis: bool):
        """Called when user confirms a selection in LoadExistingDialog."""
        cluster    = self._cluster_var.get()
        model_root = self._model_root.get()

        if run_analysis:
            # Run analysis scripts on the selected directory, then display
            self._run_btn.configure(state=tk.DISABLED)
            self._load_btn.configure(state=tk.DISABLED)
            self._set_status("Running analysis on existing results…", "orange")
            self._log.append(
                f"Loading existing run: {result_dir}", "STEP"
            )
            analysis = AnalysisRunner(
                scripts_dir=scripts_dir(),
                model_root=model_root,
                cluster=cluster,
                result_dir=result_dir,
                log_queue=self._log_queue,
                on_complete=lambda paths: self.after(
                    0, self._analysis_done, cluster, result_dir, paths
                ),
            )
            analysis.start()
        else:
            # Just load CSVs from the existing results directory
            self._log.append(
                f"Loaded existing run: {result_dir}", "OK"
            )
            self._set_status("✔  Loaded existing results.", "green")
            self._on_results_ready(cluster, result_dir)

    def _set_status(self, msg: str, color: str):
        self._status_label.configure(text=msg, foreground=color)

    # ------------------------------------------------------------------
    # Accessors (used by MainApp to pre-populate SpecTab)
    # ------------------------------------------------------------------

    def get_model_root(self) -> str:
        return self._model_root.get()

    def get_cluster(self) -> str:
        return self._cluster_var.get()

    def get_module(self) -> str:
        """Return the selected sub-module, or empty string for cluster-level runs."""
        return self._module_var.get().strip()

    def get_hier_info(self):
        """Return the ModuleHierInfo for the selected sub-module, or None."""
        return self._hier_info

    def get_result_dir(self) -> Optional[str]:
        """Return the most recent result dir for current cluster/model/out_root."""
        root     = self._model_root.get()
        cluster  = self._cluster_var.get()
        out_root = self._out_dir.get().strip() or None
        if root and cluster:
            return find_latest_result_dir(cluster, root, out_root=out_root)
        return None

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def apply_settings(self, settings: dict):
        if "model_root" in settings:
            self._model_root.set(settings["model_root"])
        self._model_root.set_recent(settings.get("recent_model_root", []))
        if "out_dir" in settings:
            self._out_dir.set(settings["out_dir"])
        self._out_dir.set_recent(settings.get("recent_out_dir", []))
        if "cluster" in settings:
            self._cluster_var.set(settings["cluster"])
        self._autofill()
        if "module" in settings:
            self._module_var.set(settings["module"])
            self._on_module_change()

    def collect_settings(self) -> dict:
        return {
            "model_root":        self._model_root.get(),
            "recent_model_root": self._model_root.get_recent(),
            "cluster":           self._cluster_var.get(),
            "module":            self._module_var.get(),
            "out_dir":           self._out_dir.get(),
            "recent_out_dir":    self._out_dir.get_recent(),
        }

    def settings_vars(self):
        """Return the tkinter Variables that should trigger auto-save."""
        return [self._model_root.var, self._cluster_var, self._module_var, self._out_dir.var]
