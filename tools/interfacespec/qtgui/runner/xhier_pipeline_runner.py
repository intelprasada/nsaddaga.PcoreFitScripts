"""
XhierPipelineRunner — orchestrates the full cross-hierarchy pipeline with dynamic
sibling discovery:

  Phase 1: Module and parent pipelines (run in parallel; grandparent if applicable)
  Phase 2: Auto-discover sibling clusters using .hier hierarchy analysis
  Phase 3: Sibling cluster pipelines (run in parallel)
  Step 4:  Enrich parent using grandparent + siblings  (enrich_cross_hierarchy.py)
  Step 5:  Extract module top I/O from enriched parent (extract_module_top_io_table.py)
  Step 6:  Enrich module with parent outputs + siblings (enrich_cross_hierarchy.py)

All pipeline runs share the same --ts timestamp so their output directories
line up without any path-reconstruction heuristics.

Sibling clusters are auto-discovered by walking the .hier hierarchy: the structural
parent of the parent cluster (e.g., icore for fe) is identified, and all of its
children that are known CLUSTER_NAMES are treated as siblings. Their pipelines are
run automatically and their CSVs are passed to enrich_cross_hierarchy.py so that
cross-cluster consumers can be resolved to leaf-level units (e.g. msid/il instead
of just msid). This approach works even when icore (or any structural grandparent)
has no pipeline-able .v file.

The final result directory emitted to on_complete is the MODULE's run dir/results,
which contains 18_* and 20_* CSVs in addition to the standard 03/07/12 outputs.
"""

import csv
import os
import queue
import shutil
import subprocess
import threading
from datetime import datetime
from typing import Callable, Optional

from ..config import CLUSTER_NAMES, get_gen_dir, get_top_v
from ..hier_utils import build_children_map, build_module_parent_map


class XhierPipelineRunner(threading.Thread):
    """
    Runs the full xhier pipeline for a sub-cluster module.

    Args:
        scripts_dir:         path to the interfacespec scripts directory
        model_root:          model root directory
        module:              the sub-module to analyse (e.g. ifu, stsr, bac)
        module_top_v:        path to the module's .v file
        module_gen_dir:      gen dir for the module (also used for port_decls.v)
        parent:              parent cluster name (e.g. fe, msid, ooo)
        parent_top_v:        path to the parent cluster's .v file
        parent_gen_dir:      gen dir for the parent cluster
        grandparent:         grandparent cluster name, or None
        gparent_top_v:       path to the grandparent's .v file, or None
        gparent_gen_dir:     gen dir for the grandparent cluster, or None
        log_queue:           queue.Queue receiving (level, line) tuples
        on_complete:         callback(success: bool, result_dir: str | None)
        fallback_gen_dir:    optional fallback gen dir for the module pipeline
        out_root:            optional override for InterfaceSpecAgent output root
    """

    def __init__(
        self,
        scripts_dir: str,
        model_root: str,
        module: str,
        module_top_v: str,
        module_gen_dir: str,
        parent: str,
        parent_top_v: str,
        parent_gen_dir: str,
        grandparent: Optional[str],
        gparent_top_v: Optional[str],
        gparent_gen_dir: Optional[str],
        log_queue: queue.Queue,
        on_complete: Callable[[bool, Optional[str]], None],
        fallback_gen_dir: Optional[str] = None,
        out_root: Optional[str] = None,
    ):
        super().__init__(daemon=True)
        self._scripts_dir     = scripts_dir
        self._model_root      = model_root
        self._module          = module
        self._module_top_v    = module_top_v
        self._module_gen_dir  = module_gen_dir
        self._parent          = parent
        self._parent_top_v    = parent_top_v
        self._parent_gen_dir  = parent_gen_dir
        self._grandparent     = grandparent
        self._gparent_top_v   = gparent_top_v
        self._gparent_gen_dir = gparent_gen_dir or parent_gen_dir
        self._log_queue       = log_queue
        self._on_complete     = on_complete
        self._fallback_gen_dir = fallback_gen_dir
        self._out_root        = out_root

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log(self, level: str, line: str):
        self._log_queue.put((level, line))

    def _isa_base(self, gen_dir: str) -> str:
        """Return the pipeline output base dir for a given gen_dir.

        When out_root is set, run_cluster_pipeline.py uses it directly
        (no InterfaceSpecAgent sub-folder), so we must match that behaviour.
        Without out_root the default is <gen_dir>/InterfaceSpecAgent.
        """
        if self._out_root:
            return self._out_root
        return os.path.join(gen_dir, "InterfaceSpecAgent")

    def _run_dir(self, gen_dir: str, name: str, ts: str) -> str:
        return os.path.join(self._isa_base(gen_dir), f"{name}_pipeline_{ts}")

    def _script(self, name: str) -> str:
        return os.path.join(self._scripts_dir, name)

    def _run(self, cmd: list[str]) -> bool:
        """Run a subprocess; stream output to log_queue. Return True on success."""
        self._log("INFO", f"$ {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self._model_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in proc.stdout:
                self._log("INFO", line.rstrip())
            proc.wait()
            if proc.returncode != 0:
                self._log("ERROR", f"Command failed (exit {proc.returncode})")
                return False
            return True
        except Exception as exc:
            self._log("ERROR", f"Command launch failed: {exc}")
            return False

    def _run_concurrent(self, tasks: list) -> bool:
        """Run multiple pipeline commands concurrently.

        tasks: list of (label, cmd) tuples.
        Each task's output lines are prefixed with [label] for clarity.
        Returns True only if ALL tasks succeeded.
        """
        results: dict = {}
        results_lock = threading.Lock()

        def run_one(label: str, cmd: list) -> None:
            self._log("INFO", f"  [{label}] $ {' '.join(cmd)}")
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=self._model_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                for line in proc.stdout:
                    self._log("INFO", f"  [{label}] {line.rstrip()}")
                proc.wait()
                ok = proc.returncode == 0
                if not ok:
                    self._log("ERROR", f"  [{label}] failed (exit {proc.returncode})")
            except Exception as exc:
                self._log("ERROR", f"  [{label}] launch failed: {exc}")
                ok = False
            with results_lock:
                results[label] = ok

        threads = [
            threading.Thread(target=run_one, args=(label, cmd), daemon=True)
            for label, cmd in tasks
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return all(results.values())

    def _discover_siblings(self, parent_name: str) -> list:
        """Discover sibling cluster names using .hier hierarchy analysis.

        Walks up from *parent_name* in the .hier tree to find its structural
        parent (e.g., icore for fe), then returns all of that parent's children
        that are known CLUSTER_NAMES entries — excluding *parent_name* itself.

        This replaces the old grandparent-CSV approach so that sibling discovery
        works even when the structural grandparent (icore) is not a pipeline-able
        cluster and has no 12_* CSV.

        Returns a sorted list of cluster names (may be empty).
        """
        try:
            parent_map   = build_module_parent_map(self._model_root)
            children_map = build_children_map(self._model_root)

            hier_gpar = parent_map.get(parent_name)
            if not hier_gpar:
                self._log("INFO", f"  '{parent_name}' has no structural parent in hierarchy")
                return []

            self._log("INFO", f"  Structural grandparent (from .hier): {hier_gpar}")
            gpar_children = children_map.get(hier_gpar, [])
            self._log("INFO", f"  Children of '{hier_gpar}': {', '.join(sorted(gpar_children)) or '(none)'}")

            known = set(CLUSTER_NAMES)
            return sorted(c for c in gpar_children if c != parent_name and c in known)
        except Exception as exc:
            self._log("WARNING", f"  Could not analyse hierarchy for siblings: {exc}")
            return []

    # ------------------------------------------------------------------
    # Main thread body
    # ------------------------------------------------------------------

    def run(self):
        try:
            self._run_pipeline()
        except Exception as exc:  # noqa: BLE001
            self._log("ERROR", f"Unexpected error in xhier pipeline: {exc}")
            self._on_complete(False, None)

    def _run_pipeline(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        mod    = self._module
        par    = self._parent
        gpar   = self._grandparent

        mod_gen  = self._module_gen_dir
        par_gen  = self._parent_gen_dir
        gpar_gen = self._gparent_gen_dir

        mod_run  = self._run_dir(mod_gen,  mod,  ts)
        par_run  = self._run_dir(par_gen,  par,  ts)
        gpar_run = self._run_dir(gpar_gen, gpar, ts) if gpar else None

        def res(run_dir: str, fname: str) -> str:
            return os.path.join(run_dir, "results", fname)

        # ------------------------------------------------------------------
        # Phase 1: Run module, parent (and optional grandparent) in parallel
        # ------------------------------------------------------------------
        parallel_desc = "module + parent" + (" + grandparent" if gpar else "") + " pipelines in parallel"
        self._log("INFO", f"\n{'='*60}")
        self._log("INFO", f"[Phase 1] Running {parallel_desc}")

        tasks = []

        mod_cmd = [
            "python3", self._script("run_cluster_pipeline.py"),
            "--cluster", mod,
            "--top-v",   self._module_top_v,
            "--gen-dir", mod_gen,
            "--ts",      ts,
        ]
        if self._fallback_gen_dir:
            mod_cmd += ["--fallback-gen-dir", self._fallback_gen_dir]
        if self._out_root:
            mod_cmd += ["--out-root", self._out_root]
        tasks.append((mod, mod_cmd))

        par_cmd = [
            "python3", self._script("run_cluster_pipeline.py"),
            "--cluster", par,
            "--top-v",   self._parent_top_v,
            "--gen-dir", par_gen,
            "--ts",      ts,
        ]
        if self._out_root:
            par_cmd += ["--out-root", self._out_root]
        tasks.append((par, par_cmd))

        if gpar and gpar_run:
            gpar_cmd = [
                "python3", self._script("run_cluster_pipeline.py"),
                "--cluster", gpar,
                "--top-v",   self._gparent_top_v,
                "--gen-dir", gpar_gen,
                "--ts",      ts,
            ]
            if self._out_root:
                gpar_cmd += ["--out-root", self._out_root]
            tasks.append((gpar, gpar_cmd))

        if not self._run_concurrent(tasks):
            self._on_complete(False, None)
            return

        # ------------------------------------------------------------------
        # Phase 2: Auto-discover sibling clusters from .hier analysis
        # ------------------------------------------------------------------
        siblings = []
        sib_runs = {}

        self._log("INFO", f"\n{'='*60}")
        self._log("INFO", "[Phase 2] Discovering sibling clusters from hierarchy (.hier) analysis")
        siblings = self._discover_siblings(par)
        if siblings:
            self._log("INFO", f"  [HIER] Discovered sibling clusters: {', '.join(siblings)}")
        else:
            self._log("INFO", "  No sibling clusters found")

        # ------------------------------------------------------------------
        # Phase 3: Run sibling pipelines in parallel
        # ------------------------------------------------------------------
        if siblings:
            self._log("INFO", f"\n{'='*60}")
            self._log("INFO", f"[Phase 3] Running sibling pipelines: {', '.join(siblings)}")

            sib_tasks = []
            for sib in siblings:
                sib_top_v = get_top_v(sib, self._model_root)
                sib_gen   = get_gen_dir(sib, self._model_root)
                sib_run   = self._run_dir(sib_gen, sib, ts)

                if not os.path.isfile(sib_top_v):
                    self._log("WARNING", f"  Sibling '{sib}' .v not found: {sib_top_v} — skipping")
                    continue

                sib_runs[sib] = sib_run
                cmd = [
                    "python3", self._script("run_cluster_pipeline.py"),
                    "--cluster", sib,
                    "--top-v",   sib_top_v,
                    "--gen-dir", sib_gen,
                    "--ts",      ts,
                ]
                if self._out_root:
                    cmd += ["--out-root", self._out_root]
                sib_tasks.append((sib, cmd))

            if sib_tasks:
                if not self._run_concurrent(sib_tasks):
                    self._log("WARNING", "  Some sibling pipelines failed — enrichment may be incomplete")
                    # Don't abort; partial sibling data is still useful
        else:
            self._log("INFO", "\n[Phase 3] No siblings to run")

        # Build --sibling args for enrichment steps
        sib_args = []
        for sib, sib_run in sib_runs.items():
            sib_12 = res(sib_run, f"12_{sib}_query_io_table.csv")
            if os.path.isfile(sib_12):
                sib_args += ["--sibling", f"{sib}:{sib_12}"]
            else:
                self._log("WARNING", f"  Sibling '{sib}' 12_* CSV not found — skipping in enrichment")

        # ------------------------------------------------------------------
        # Step 4: Enrich parent using grandparent + siblings
        # ------------------------------------------------------------------
        self._log("INFO", f"\n{'='*60}")
        self._log("INFO", f"[4/6] Enriching parent '{par}' with grandparent '{gpar}'")

        par_12 = res(par_run, f"12_{par}_query_io_table.csv")
        par_19 = res(par_run, f"19_{par}_query_io_table_xhier.csv")

        if gpar and gpar_run:
            gpar_12_enrich = res(gpar_run, f"12_{gpar}_query_io_table.csv")
            cmd = [
                "python3", self._script("enrich_cross_hierarchy.py"),
                "--io-csv",  par_12,
                "--module",  par,
                "--parent",  f"{gpar}:{gpar_12_enrich}",
                "--out-csv", par_19,
            ] + sib_args
        else:
            # No grandparent: promote parent 12_* to 19_* with no enrichment
            self._log("INFO", "  No grandparent — promoting parent 12_* as 19_* (no enrichment needed)")
            if not os.path.isfile(par_12):
                self._log(
                    "ERROR",
                    f"  Expected parent output not found: {par_12}\n"
                    "  Check that the parent pipeline succeeded and produced 12_* output.",
                )
                self._on_complete(False, None)
                return
            os.makedirs(os.path.dirname(par_19), exist_ok=True)
            shutil.copy2(par_12, par_19)
            self._log("INFO", f"  Copied: {par_12}")
            self._log("INFO", f"       → {par_19}")
            cmd = None

        if cmd and not self._run(cmd):
            self._on_complete(False, None)
            return

        # ------------------------------------------------------------------
        # Step 5: Extract module top I/O from enriched parent 19_*
        # ------------------------------------------------------------------
        self._log("INFO", f"\n{'='*60}")
        self._log("INFO", f"[5/6] Extracting top I/O for '{mod}' from enriched parent")

        mod_18       = res(mod_run, f"18_{mod}_top_io_table.csv")
        port_decls_v = os.path.join(mod_gen, f"{mod}.port_decls.v")

        os.makedirs(os.path.dirname(mod_18), exist_ok=True)

        cmd = [
            "python3", self._script("extract_module_top_io_table.py"),
            "--io-csv",     par_19,
            "--module",     mod,
            "--port-decls", port_decls_v,
            "--out-csv",    mod_18,
        ]
        if not self._run(cmd):
            self._on_complete(False, None)
            return

        # ------------------------------------------------------------------
        # Step 6: Enrich module with parent outputs + siblings
        # ------------------------------------------------------------------
        self._log("INFO", f"\n{'='*60}")
        self._log("INFO", f"[6/6] Enriching module '{mod}' with parent '{par}' outputs")

        mod_20 = res(mod_run, f"20_{mod}_pseudo_top_enriched.csv")

        # Use only step 12 as the parent: it incorporates all step-07 data
        # and has is_connected_to_top correctly populated, which is required
        # for boundary-crossing sibling discovery.
        cmd = [
            "python3", self._script("enrich_cross_hierarchy.py"),
            "--io-csv",  mod_18,
            "--module",  mod,
            "--parent",  f"{par}:{par_12}",
            "--out-csv", mod_20,
        ] + sib_args
        if not self._run(cmd):
            self._on_complete(False, None)
            return

        # ------------------------------------------------------------------
        # Done — report module result dir
        # ------------------------------------------------------------------
        self._log("INFO", f"\n{'='*60}")
        self._log("OK",   f"xhier pipeline complete for '{mod}'")
        self._log("OK",   f"  18_* top I/O table : {mod_18}")
        self._log("OK",   f"  20_* enriched table: {mod_20}")
        if sib_runs:
            self._log("OK", f"  Siblings resolved  : {', '.join(sib_runs.keys())}")
        self._log("INFO", f"RUN_DIR={mod_run}")

        result_dir = os.path.join(mod_run, "results")
        if not os.path.isdir(result_dir):
            result_dir = mod_run
        self._on_complete(True, result_dir)
