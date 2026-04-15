"""
SpecRunner — runs generate_interface_spec.py in a background thread.
Calls on_complete(success, out_md) when done.
"""

import os
import queue
import subprocess
import threading
from typing import Callable, Optional


class SpecRunner(threading.Thread):
    """
    Runs generate_interface_spec.py for a single module.

    Per README intent "Generate interface specification for a module":
      1. Prefers 20_<module>_pseudo_top_enriched.csv (xhier-enriched) over
         18_<module>_top_io_table.csv (plain top I/O) as --io-csv source.
         20_* has xhier_consumers/xhier_producers populated; when cou=NONE,
         xhier_consumers is required for correct interface classification.
      2. Optionally passes --internal-csv 12_<module>_query_io_table.csv for
         sub-grouping by internal consumer/producer.
      3. Calls generate_interface_spec.py --io-csv <csv> --module <module>
         --cluster <cluster> --subgroup-threshold <n> --out-md <out_md>
      4. Output is <module>_interface_spec.md in the same results directory.

    Args:
        scripts_dir:         path to the interfacespec scripts directory
        model_root:          model root directory (used as cwd)
        io_csv:              path to 20_* enriched CSV (or 18_* fallback)
        module:              module name (e.g. "ifu", "idq", "stsr")
        cluster:             parent cluster description (e.g. "fe")
        subgroup_threshold:  signals-per-interface threshold for sub-grouping (default 10)
        out_md:              output markdown path
        internal_csv:        optional path to 12_* internal IO table for sub-grouping
        log_queue:           queue.Queue receiving (level, line) tuples
        on_complete:         callback(success: bool, out_md: str | None)
    """

    def __init__(
        self,
        scripts_dir: str,
        model_root: str,
        io_csv: str,
        module: str,
        cluster: str,
        subgroup_threshold: int,
        out_md: str,
        log_queue: queue.Queue,
        on_complete: Callable[[bool, Optional[str]], None],
        internal_csv: Optional[str] = None,
    ):
        super().__init__(daemon=True)
        self._scripts_dir        = scripts_dir
        self._model_root         = model_root
        self._io_csv             = io_csv
        self._module             = module
        self._cluster            = cluster
        self._subgroup_threshold = subgroup_threshold
        self._out_md             = out_md
        self._internal_csv       = internal_csv
        self._log_queue          = log_queue
        self._on_complete        = on_complete

    def run(self):
        script = os.path.join(self._scripts_dir, "generate_interface_spec.py")
        cmd = [
            "python3", script,
            "--io-csv",             self._io_csv,
            "--module",             self._module,
            "--cluster",            self._cluster,
            "--subgroup-threshold", str(self._subgroup_threshold),
            "--out-md",             self._out_md,
        ]
        if self._internal_csv and os.path.isfile(self._internal_csv):
            cmd += ["--internal-csv", self._internal_csv]
        self._log("STEP", f"[spec] Generating interface spec for '{self._module}'…")
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
                self._log("INFO", f"  {line.rstrip()}")
            proc.wait()
            success = proc.returncode == 0
        except Exception as exc:
            self._log("ERROR", f"[spec] Launch failed: {exc}")
            self._on_complete(False, None)
            return

        if success:
            self._log("OK", f"[spec] Done → {self._out_md}")
            self._on_complete(True, self._out_md)
        else:
            self._log("ERROR", f"[spec] Failed (exit {proc.returncode})")
            self._on_complete(False, None)

    def _log(self, level: str, line: str):
        self._log_queue.put((level, line))
