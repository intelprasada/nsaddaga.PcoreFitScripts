"""
PipelineRunner — runs run_cluster_pipeline.py in a background thread,
streams stdout/stderr to a queue, calls on_complete when done.
"""

import os
import queue
import subprocess
import threading
from typing import Callable, Optional


class PipelineRunner(threading.Thread):
    """
    Runs the InterfaceSpec pipeline for a single cluster.

    Args:
        scripts_dir:      path to the interfacespec scripts directory
        model_root:       model root directory
        cluster:          cluster name (fe, msid, ooo, …)
        top_v:            path to the top-level .v file
        gen_dir:          primary generated RTL directory (*.port_decls.v)
        fallback_gen_dir: optional secondary gen dir searched when a module is
                          missing from gen_dir (e.g. target/core/gen for ooo)
        out_root:         optional output root directory; if None the pipeline
                          defaults to <gen-dir>/InterfaceSpecAgent
        log_queue:        queue.Queue receiving (level, line) tuples
        on_complete:      callback(success: bool, result_dir: str | None)
    """

    def __init__(
        self,
        scripts_dir: str,
        model_root: str,
        cluster: str,
        top_v: str,
        gen_dir: str,
        log_queue: queue.Queue,
        on_complete: Callable[[bool, Optional[str]], None],
        fallback_gen_dir: Optional[str] = None,
        out_root: Optional[str] = None,
    ):
        super().__init__(daemon=True)
        self._scripts_dir     = scripts_dir
        self._model_root      = model_root
        self._cluster         = cluster
        self._top_v           = top_v
        self._gen_dir         = gen_dir
        self._fallback_gen_dir = fallback_gen_dir
        self._out_root        = out_root
        self._log_queue       = log_queue
        self._on_complete     = on_complete

    def run(self):
        script = os.path.join(self._scripts_dir, "run_cluster_pipeline.py")
        cmd = [
            "python3", script,
            "--cluster", self._cluster,
            "--top-v",   self._top_v,
            "--gen-dir", self._gen_dir,
        ]
        if self._fallback_gen_dir:
            cmd += ["--fallback-gen-dir", self._fallback_gen_dir]
        if self._out_root:
            cmd += ["--out-root", self._out_root]

        self._log("INFO", f"$ {' '.join(cmd)}")

        run_dir: Optional[str] = None
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
                line = line.rstrip()
                self._log("INFO", line)
                # The pipeline prints "RUN_DIR=<path>" — capture it as the
                # authoritative result directory for this run.
                if line.startswith("RUN_DIR="):
                    run_dir = line[len("RUN_DIR="):]
            proc.wait()
            success = proc.returncode == 0
        except Exception as exc:
            self._log("ERROR", f"Pipeline launch failed: {exc}")
            self._on_complete(False, None)
            return

        if success:
            self._log("OK", f"Pipeline finished (exit 0) for cluster '{self._cluster}'")
            # Use the RUN_DIR printed by the pipeline (most reliable).
            # Fall back to filesystem scan only if not captured.
            if run_dir:
                result_dir = os.path.join(run_dir, "results")
                if not os.path.isdir(result_dir):
                    result_dir = run_dir
            else:
                result_dir = self._find_result_dir()
            self._on_complete(True, result_dir)
        else:
            self._log("ERROR", f"Pipeline failed (exit {proc.returncode})")
            self._on_complete(False, None)

    def _find_result_dir(self) -> Optional[str]:
        """Find the newest results/ dir created by this pipeline run."""
        import glob as _glob
        # If user specified an out_root the pipeline puts runs there directly
        base = os.path.join(self._out_root, "InterfaceSpecAgent") if self._out_root \
               else os.path.join(self._gen_dir, "InterfaceSpecAgent")
        pattern = os.path.join(base, f"{self._cluster}_pipeline_*", "results")
        candidates = [d for d in _glob.glob(pattern) if os.path.isdir(d)]
        if not candidates:
            return None
        return max(candidates, key=os.path.getmtime)

    def _log(self, level: str, line: str):
        self._log_queue.put((level, line))
