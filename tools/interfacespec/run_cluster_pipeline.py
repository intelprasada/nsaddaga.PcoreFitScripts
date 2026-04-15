#!/usr/bin/env python3
"""Run the InterfaceSpec extraction pipeline for any single cluster.

This is a generalized version of run_fe_msid_pipeline_with_cross_cluster.py
that works with any cluster (fe, msid, ooo, exe, mlc, meu, pm, etc.).

Example usage:
    # OOO cluster
    python3 scripts/interfacespec/run_cluster_pipeline.py \
        --cluster ooo --top-v target/ooo/gen/ooo.v --gen-dir target/ooo/gen

    # FE cluster (equivalent to the FE portion of the FE/MSID pipeline)
    python3 scripts/interfacespec/run_cluster_pipeline.py \
        --cluster fe --top-v target/fe/gen/fe.v --gen-dir target/fe/gen

    # EXE cluster
    python3 scripts/interfacespec/run_cluster_pipeline.py \
        --cluster exe --top-v target/exe/gen/exe.v --gen-dir target/exe/gen

    # With optional module-level I/O table extraction
    python3 scripts/interfacespec/run_cluster_pipeline.py \
        --cluster ooo --top-v target/ooo/gen/ooo.v --gen-dir target/ooo/gen \
        --module-io rat
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def run_cmd(cmd: list[str]) -> None:
    print("RUN:", " ".join(cmd), file=sys.stderr)
    subprocess.run(cmd, check=True)


def create_connected_to_top_subset(io_csv: Path, out_csv: Path) -> int:
    with io_csv.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    selected = [
        row
        for row in rows
        if (row.get("is_connected_to_top") or "").strip().lower() == "true"
    ]

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(selected)
    return len(selected)


MON_TLM_FILTER_THRESHOLD = 4000


def filter_mon_tlm_units(typed_csv: Path, filtered_csv: Path) -> tuple[int, int]:
    """Remove rows whose instance_module ends with _mon or _tlm.

    Returns (original_row_count, filtered_row_count).
    """
    with typed_csv.open(newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    kept = [
        r for r in rows
        if not (r.get("instance_module") or "").strip().endswith(("_mon", "_tlm"))
    ]

    with filtered_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)

    return len(rows), len(kept)


def run_cluster_pipeline(
    scripts_dir: Path,
    cluster: str,
    top_v: Path,
    gen_dir: Path,
    run_dir: Path,
    preferred_guard_token: str,
    fallback_gen_dir: Path | None = None,
    parent_specs: list[str] | None = None,
    sibling_specs: list[str] | None = None,
) -> int:
    # Use the top-level RTL file's stem as the naming token for all output CSVs.
    # e.g. ooo.v -> "ooo", rat_top.v -> "rat_top"
    top_module = top_v.stem

    intermediate = run_dir / "intermediate"
    results = run_dir / "results"
    intermediate.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)

    run_cmd(
        [
            "python3",
            str(scripts_dir / "01_extract_connectivity.py"),
            "--top-v",
            str(top_v),
            "--cluster",
            cluster,
            "--out-csv",
            str(intermediate / f"01_{top_module}_connectivity_raw.csv"),
        ]
    )
    step02_cmd = [
        "python3",
        str(scripts_dir / "02_extract_port_directions.py"),
        "--top-v",
        str(top_v),
        "--gen-dir",
        str(gen_dir),
        "--out-csv",
        str(intermediate / f"02_{top_module}_port_directions.csv"),
    ]
    if fallback_gen_dir:
        step02_cmd += ["--fallback-gen-dir", str(fallback_gen_dir)]
    run_cmd(step02_cmd)
    run_cmd(
        [
            "python3",
            str(scripts_dir / "03_join_connectivity_with_directions.py"),
            "--connectivity-csv",
            str(intermediate / f"01_{top_module}_connectivity_raw.csv"),
            "--directions-csv",
            str(intermediate / f"02_{top_module}_port_directions.csv"),
            "--out-csv",
            str(results / f"03_{top_module}_connectivity_typed.csv"),
            "--unresolved-csv",
            str(results / f"03_{top_module}_connectivity_unresolved.csv"),
        ]
    )

    # --- Decision: filter _mon/_tlm units when step 3 output exceeds threshold ---
    typed_csv_path = results / f"03_{top_module}_connectivity_typed.csv"
    with typed_csv_path.open(newline="") as fh:
        step3_row_count = sum(1 for _ in fh) - 1  # exclude header

    if step3_row_count > MON_TLM_FILTER_THRESHOLD:
        filtered_path = results / f"03_{top_module}_connectivity_typed_filtered.csv"
        orig_count, kept_count = filter_mon_tlm_units(typed_csv_path, filtered_path)
        removed = orig_count - kept_count
        print(
            f"FILTER: step 3 has {orig_count} rows (>{MON_TLM_FILTER_THRESHOLD}), "
            f"removed {removed} _mon/_tlm rows, {kept_count} rows remain",
            file=sys.stderr,
        )
        effective_typed_csv = filtered_path
    else:
        print(
            f"FILTER: step 3 has {step3_row_count} rows (<={MON_TLM_FILTER_THRESHOLD}), "
            f"no _mon/_tlm filtering applied",
            file=sys.stderr,
        )
        effective_typed_csv = typed_csv_path

    run_cmd(
        [
            "python3",
            str(scripts_dir / "07_build_query_view_with_guard_bias.py"),
            "--typed-csv",
            str(effective_typed_csv),
            "--out-query-csv",
            str(results / f"07_{top_module}_connectivity_query_view.csv"),
            "--out-anomalies-csv",
            str(results / f"07_{top_module}_guard_alternatives_anomalies.csv"),
            "--preferred-guard-token",
            preferred_guard_token,
        ]
    )
    run_cmd(
        [
            "python3",
            str(scripts_dir / "generate_module_io_table.py"),
            "--query-csv",
            str(results / f"07_{top_module}_connectivity_query_view.csv"),
            "--all-lines-io-table",
            "--out-csv",
            str(results / f"12_{top_module}_query_io_table.csv"),
        ]
    )
    run_cmd(
        [
            "python3",
            str(scripts_dir / "find_tiedoff_threaded_signals.py"),
            "--query-csv",
            str(results / f"12_{top_module}_query_io_table.csv"),
            "--out-csv",
            str(results / f"13_{top_module}_thread_tiedoff_from_io_table.csv"),
        ]
    )

    top_count = create_connected_to_top_subset(
        results / f"12_{top_module}_query_io_table.csv",
        results / f"17_{top_module}_connected_to_top_rows.csv",
    )
    print(f"{cluster.upper()}_TOP_ROWS={top_count}")

    # --- Step 19: cross-hierarchy enrichment (optional) ---
    if parent_specs:
        enrich_cmd = [
            "python3",
            str(scripts_dir / "enrich_cross_hierarchy.py"),
            "--io-csv",
            str(results / f"12_{top_module}_query_io_table.csv"),
            "--module",
            cluster,
            "--out-csv",
            str(results / f"19_{top_module}_query_io_table_xhier.csv"),
        ]
        for spec in parent_specs:
            enrich_cmd.extend(["--parent", spec])
        for spec in (sibling_specs or []):
            enrich_cmd.extend(["--sibling", spec])
        run_cmd(enrich_cmd)

    # --- Step 14: diagnostic-only unresolved name mismatch investigation ---
    mismatch_cmd = [
        "python3",
        str(scripts_dir / "find_unresolved_name_mismatch_candidates.py"),
        "--io-csv",
        str(results / f"12_{top_module}_query_io_table.csv"),
        "--out-csv",
        str(results / f"14_{top_module}_unresolved_name_mismatch_candidates.csv"),
    ]
    for spec in (parent_specs or []):
        mismatch_cmd.extend(["--reference", spec])
    for spec in (sibling_specs or []):
        mismatch_cmd.extend(["--reference", spec])
    run_cmd(mismatch_cmd)

    # --- Step 15: compact unresolved name mismatch review table ---
    run_cmd(
        [
            "python3",
            str(scripts_dir / "generate_unresolved_name_mismatch_compact.py"),
            "--in-csv",
            str(results / f"14_{top_module}_unresolved_name_mismatch_candidates.csv"),
            "--out-csv",
            str(results / f"15_{top_module}_unresolved_name_mismatch_compact.csv"),
        ]
    )

    return top_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run InterfaceSpec extraction pipeline for a single cluster"
    )
    parser.add_argument(
        "--cluster",
        required=True,
        help="Cluster name (e.g., fe, msid, ooo, exe, mlc, meu, pm)",
    )
    parser.add_argument(
        "--top-v",
        required=True,
        help="Path to generated top-level RTL (e.g., target/ooo/gen/ooo.v)",
    )
    parser.add_argument(
        "--gen-dir",
        required=True,
        help="Generated RTL directory containing *.port_decls.v files",
    )
    parser.add_argument(
        "--fallback-gen-dir",
        default=None,
        help="Optional secondary gen dir searched when a module is missing from --gen-dir (e.g. target/core/gen for ooo)",
    )
    parser.add_argument(
        "--out-root",
        default=None,
        help="Pipeline output root (default: <gen-dir>/InterfaceSpecAgent)",
    )
    parser.add_argument(
        "--ts",
        default=datetime.now().strftime("%Y%m%d_%H%M%S"),
        help="Timestamp suffix for output directories",
    )
    parser.add_argument(
        "--preferred-guard-token",
        default="INTEL_INST_ON",
        help="Preferred guard token for query-view selection",
    )
    parser.add_argument(
        "--module-io",
        default=None,
        help="Optional: extract a module-level I/O table (e.g., --module-io rat)",
    )
    parser.add_argument(
        "--parent",
        action="append",
        default=[],
        metavar="MOD:CSV",
        help="Parent I/O table as MODULE:CSV_PATH for cross-hierarchy enrichment (nearest parent first, repeatable)",
    )
    parser.add_argument(
        "--sibling",
        action="append",
        default=[],
        metavar="MOD:CSV",
        help="Sibling cluster I/O table for drill-into as MODULE:CSV_PATH (repeatable)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scripts_dir = Path(__file__).resolve().parent
    top_v = Path(args.top_v)
    gen_dir = Path(args.gen_dir)
    fallback_gen_dir = Path(args.fallback_gen_dir) if args.fallback_gen_dir else None
    cluster = args.cluster
    ts = args.ts

    if not top_v.exists():
        print(f"ERROR: top-level RTL not found: {top_v}", file=sys.stderr)
        sys.exit(1)

    out_root = Path(args.out_root) if args.out_root else gen_dir / "InterfaceSpecAgent"
    run_dir = out_root / f"{cluster}_pipeline_{ts}"

    run_cluster_pipeline(
        scripts_dir,
        cluster,
        top_v,
        gen_dir,
        run_dir,
        args.preferred_guard_token,
        fallback_gen_dir=fallback_gen_dir,
        parent_specs=args.parent or None,
        sibling_specs=args.sibling or None,
    )

    # Optional: module-level I/O table
    top_module = top_v.stem
    if args.module_io:
        module = args.module_io
        module_port_decls = gen_dir / f"{module}.port_decls.v"
        module_top_io_csv = run_dir / "results" / f"18_{module}_top_io_table.csv"
        cmd = [
            "python3",
            str(scripts_dir / "extract_module_top_io_table.py"),
            "--io-csv",
            str(run_dir / "results" / f"12_{top_module}_query_io_table.csv"),
            "--module",
            module,
            "--out-csv",
            str(module_top_io_csv),
        ]
        if module_port_decls.exists():
            cmd.extend(["--port-decls", str(module_port_decls)])
        run_cmd(cmd)
        print(f"MODULE_TOP_IO_TABLE={module_top_io_csv}")

    print(f"TS={ts}")
    print(f"RUN_DIR={run_dir}")


if __name__ == "__main__":
    main()
