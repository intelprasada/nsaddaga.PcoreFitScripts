#!/usr/bin/env python3
"""Run FE/MSID InterfaceSpec pipelines and cross-cluster augmentation.

This script is specific to the FE and MSID clusters. For other clusters
(ooo, exe, mlc, meu, pm), use run_cluster_pipeline.py instead.
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


def run_cluster_pipeline(
    scripts_dir: Path,
    cluster: str,
    top_v: Path,
    gen_dir: Path,
    run_dir: Path,
    preferred_guard_token: str,
) -> int:
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
            str(intermediate / f"01_{cluster}_connectivity_raw.csv"),
        ]
    )
    run_cmd(
        [
            "python3",
            str(scripts_dir / "02_extract_port_directions.py"),
            "--top-v",
            str(top_v),
            "--gen-dir",
            str(gen_dir),
            "--out-csv",
            str(intermediate / f"02_{cluster}_port_directions.csv"),
        ]
    )
    run_cmd(
        [
            "python3",
            str(scripts_dir / "03_join_connectivity_with_directions.py"),
            "--connectivity-csv",
            str(intermediate / f"01_{cluster}_connectivity_raw.csv"),
            "--directions-csv",
            str(intermediate / f"02_{cluster}_port_directions.csv"),
            "--out-csv",
            str(results / f"03_{cluster}_connectivity_typed.csv"),
            "--unresolved-csv",
            str(results / f"03_{cluster}_connectivity_unresolved.csv"),
        ]
    )
    run_cmd(
        [
            "python3",
            str(scripts_dir / "07_build_query_view_with_guard_bias.py"),
            "--typed-csv",
            str(results / f"03_{cluster}_connectivity_typed.csv"),
            "--out-query-csv",
            str(results / f"07_{cluster}_connectivity_query_view.csv"),
            "--out-anomalies-csv",
            str(results / f"07_{cluster}_guard_alternatives_anomalies.csv"),
            "--preferred-guard-token",
            preferred_guard_token,
        ]
    )
    run_cmd(
        [
            "python3",
            str(scripts_dir / "generate_module_io_table.py"),
            "--query-csv",
            str(results / f"07_{cluster}_connectivity_query_view.csv"),
            "--all-lines-io-table",
            "--out-csv",
            str(results / f"12_{cluster}_query_io_table.csv"),
        ]
    )
    run_cmd(
        [
            "python3",
            str(scripts_dir / "find_tiedoff_threaded_signals.py"),
            "--query-csv",
            str(results / f"12_{cluster}_query_io_table.csv"),
            "--out-csv",
            str(results / f"13_{cluster}_thread_tiedoff_from_io_table.csv"),
        ]
    )

    top_count = create_connected_to_top_subset(
        results / f"12_{cluster}_query_io_table.csv",
        results / f"17_{cluster}_connected_to_top_rows.csv",
    )
    print(f"{cluster.upper()}_TOP_ROWS={top_count}")
    return top_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run FE/MSID pipeline and FE<->MSID cross-cluster augmentation"
    )
    parser.add_argument(
        "--ts",
        default=datetime.now().strftime("%Y%m%d_%H%M%S"),
        help="Timestamp suffix for output directories",
    )
    parser.add_argument(
        "--gen-dir",
        default="target/fe/gen",
        help="Generated RTL directory containing fe.v, msid.v, and *.port_decls.v",
    )
    parser.add_argument(
        "--out-root",
        default="target/fe/gen/InterfaceSpecAgent",
        help="Pipeline output root",
    )
    parser.add_argument(
        "--preferred-guard-token",
        default="INTEL_INST_ON",
        help="Preferred guard token for query-view selection",
    )
    parser.add_argument(
        "--skip-hierarchy",
        action="store_true",
        help="Skip hierarchy summary pre-step",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scripts_dir = Path(__file__).resolve().parent
    gen_dir = Path(args.gen_dir)
    out_root = Path(args.out_root)
    ts = args.ts

    if not args.skip_hierarchy:
        run_cmd(["python3", str(Path("scripts/tmp/build_hier_cross_cluster_summary.py"))])

    fe_run_dir = out_root / f"fe_pipeline_{ts}"
    msid_run_dir = out_root / f"msid_pipeline_{ts}"

    run_cluster_pipeline(
        scripts_dir,
        "fe",
        gen_dir / "fe.v",
        gen_dir,
        fe_run_dir,
        args.preferred_guard_token,
    )
    run_cluster_pipeline(
        scripts_dir,
        "msid",
        gen_dir / "msid.v",
        gen_dir,
        msid_run_dir,
        args.preferred_guard_token,
    )

    missing_csv = out_root / f"cross_cluster_{ts}_missing.csv"
    augment_script = scripts_dir / "augment_fe_msid_cross_cluster_io.py"
    if augment_script.exists():
        run_cmd(
            [
                "python3",
                str(augment_script),
                "--bridge-v",
                str(gen_dir / "fe_te.v"),
                "--fe-io-csv",
                str(fe_run_dir / "results" / "12_fe_query_io_table.csv"),
                "--msid-io-csv",
                str(msid_run_dir / "results" / "12_msid_query_io_table.csv"),
                "--fe-port-decls",
                str(gen_dir / "fe.port_decls.v"),
                "--msid-port-decls",
                str(gen_dir / "msid.port_decls.v"),
                "--out-missing-csv",
                str(missing_csv),
            ]
        )
    else:
        print(f"SKIP: {augment_script.name} not found — cross-cluster augmentation skipped",
              file=sys.stderr)

    # Last step: emit IFU-only top boundary I/O table (one row per IFU top port).
    ifu_top_io_csv = fe_run_dir / "results" / "18_ifu_top_io_table.csv"
    run_cmd(
        [
            "python3",
            str(scripts_dir / "extract_module_top_io_table.py"),
            "--io-csv",
            str(fe_run_dir / "results" / "12_fe_query_io_table.csv"),
            "--module",
            "ifu",
            "--port-decls",
            str(gen_dir / "ifu.port_decls.v"),
            "--out-csv",
            str(ifu_top_io_csv),
        ]
    )

    print(f"TS={ts}")
    print(f"FE_RUN_DIR={fe_run_dir}")
    print(f"MSID_RUN_DIR={msid_run_dir}")
    print(f"CROSS_CLUSTER_MISSING={missing_csv}")
    print(f"IFU_TOP_IO_TABLE={ifu_top_io_csv}")


if __name__ == "__main__":
    main()