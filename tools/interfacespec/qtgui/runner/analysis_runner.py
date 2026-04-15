"""
AnalysisRunner — runs all post-pipeline analysis scripts in parallel threads:
  - find_dimension_mismatches.py       (per-cluster)
  - find_tiedoff_threaded_signals.py   (per-cluster)
  - find_cross_cluster_dimension_mismatches.py (if multiple io-table CSVs given)
Calls on_complete(result_paths: dict) when all jobs are done.
"""

import os
import queue
import subprocess
import threading
from typing import Callable, Dict, List, Optional

from ..utils import detect_name_token


class AnalysisRunner(threading.Thread):
    """
    Runs all analysis scripts for one cluster's pipeline result directory.

    Args:
        scripts_dir:        path to the interfacespec scripts directory
        model_root:         model root directory
        cluster:            cluster name
        result_dir:         pipeline results/ directory
        log_queue:          queue.Queue receiving (level, line) tuples
        on_complete:        callback(result_paths: dict)
        extra_io_csvs:      additional io_table CSVs for cross-cluster analysis
        cross_cluster_out:  output path for cross-cluster CSV (optional)
    """

    def __init__(
        self,
        scripts_dir: str,
        model_root: str,
        cluster: str,
        result_dir: str,
        log_queue: queue.Queue,
        on_complete: Callable[[Dict[str, str]], None],
        extra_io_csvs: Optional[List[str]] = None,
        cross_cluster_out: Optional[str] = None,
    ):
        super().__init__(daemon=True)
        self._scripts_dir       = scripts_dir
        self._model_root        = model_root
        self._cluster           = cluster
        self._result_dir        = result_dir
        self._log_queue         = log_queue
        self._on_complete       = on_complete
        self._extra_io_csvs     = extra_io_csvs or []
        self._cross_cluster_out = cross_cluster_out

    def run(self):
        # Detect the naming token used in pipeline output CSVs (top_module stem).
        # Falls back to cluster name for backward compatibility with older runs.
        token = detect_name_token(self._result_dir) or self._cluster

        io_csv      = os.path.join(self._result_dir, f"12_{token}_query_io_table.csv")
        query_csv   = os.path.join(self._result_dir, f"07_{token}_connectivity_query_view.csv")
        dim_out     = os.path.join(self._result_dir, f"dim_mismatches_{token}.csv")
        tiedoff_out = os.path.join(self._result_dir, f"tiedoff_threaded_{token}.csv")

        jobs = []

        # Dimension mismatches
        if os.path.isfile(io_csv):
            jobs.append(self._make_job(
                [os.path.join(self._scripts_dir, "find_dimension_mismatches.py"),
                 "--io-csv", io_csv, "--out-csv", dim_out],
                f"dim_mismatches:{dim_out}",
            ))

        # Threaded tie-offs
        if os.path.isfile(query_csv):
            jobs.append(self._make_job(
                [os.path.join(self._scripts_dir, "find_tiedoff_threaded_signals.py"),
                 "--query-csv", query_csv, "--out-csv", tiedoff_out],
                f"tiedoff_threaded:{tiedoff_out}",
            ))

        # Cross-cluster (optional — only if extra io_csvs provided)
        cross_out = None
        if self._extra_io_csvs and os.path.isfile(io_csv):
            cross_out = self._cross_cluster_out or os.path.join(
                os.path.dirname(self._result_dir),
                "dim_mismatches_cross_clusters.csv",
            )
            all_csvs = [io_csv] + self._extra_io_csvs
            jobs.append(self._make_job(
                [os.path.join(self._scripts_dir,
                              "find_cross_cluster_dimension_mismatches.py"),
                 "--io-csvs", *all_csvs, "--out-csv", cross_out],
                f"cross_cluster_dim:{cross_out}",
            ))

        # Run all jobs in parallel, wait for all
        threads = []
        for cmd, label in jobs:
            t = threading.Thread(target=self._run_job, args=(cmd, label), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        result_paths = {
            "dim_mismatches":   dim_out,
            "tiedoff_threaded": tiedoff_out,
            "unresolved":       os.path.join(self._result_dir,
                                             f"03_{token}_connectivity_unresolved.csv"),
        }
        if cross_out:
            result_paths["cross_cluster_dim"] = cross_out

        self._on_complete(result_paths)

    def _make_job(self, script_args: List[str], label: str):
        cmd = ["python3"] + script_args
        return cmd, label

    def _run_job(self, cmd: List[str], label: str):
        self._log("STEP", f"[analysis] {label.split(':')[0]} → running…")
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
            if proc.returncode == 0:
                out = label.split(":", 1)[1] if ":" in label else ""
                self._log("OK", f"[analysis] {label.split(':')[0]} done → {out}")
            else:
                self._log("ERROR", f"[analysis] {label.split(':')[0]} failed (exit {proc.returncode})")
        except Exception as exc:
            self._log("ERROR", f"[analysis] {label}: {exc}")

    def _log(self, level: str, line: str):
        self._log_queue.put((level, line))
