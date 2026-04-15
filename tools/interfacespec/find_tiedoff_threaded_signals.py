#!/usr/bin/env python3
"""Filter query-view CSV rows for review-worthy connectivity cases.

Selection rule:
1) Threaded tied-off style rows:
    - direction_packed_width or direction_unpacked_dim contains "THREAD"
    - and (is_unconnected is true OR connected_expr contains a tick ('))
2) Any unconnected row (is_unconnected is true)

Output columns are intentionally limited to the requested schema.
"""

import argparse
import csv
from pathlib import Path

OUTPUT_COLUMNS = [
    "top_module",
    "instance_module",
    "port_name",
    "port_direciton",
    "source_output_units",
    "connected_other_units",
    "connected_expr",
    "direction_packed_width",
    "direction_unpacked_dim",
    "is_unconnected",
    "source_line",
    "direction_source_file",
    "direction_decl_line",
]


def is_thread_tiedoff_or_unconnected(row: dict[str, str]) -> bool:
    packed = (row.get("direction_packed_width") or "")
    unpacked = (row.get("direction_unpacked_dim") or "")
    connected_expr = (row.get("connected_expr") or "")
    is_unconnected = (row.get("is_unconnected") or "").strip().lower() == "true"

    threaded_tiedoff = ("THREAD" in packed or "THREAD" in unpacked) and (is_unconnected or ("'" in connected_expr))
    return threaded_tiedoff or is_unconnected


def project_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "top_module": row.get("top_module", ""),
        "instance_module": row.get("instance_module", ""),
        "port_name": row.get("port_name", ""),
        "port_direciton": row.get("port_direction", ""),
        "source_output_units": row.get("source_output_units", ""),
        "connected_other_units": row.get("connected_other_units", ""),
        "connected_expr": row.get("connected_expr", ""),
        "direction_packed_width": row.get("direction_packed_width", ""),
        "direction_unpacked_dim": row.get("direction_unpacked_dim", ""),
        "is_unconnected": row.get("is_unconnected", ""),
        "source_line": row.get("source_line", ""),
        "direction_source_file": row.get("direction_source_file", ""),
        "direction_decl_line": row.get("direction_decl_line", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query-csv", required=True, help="Path to 07_*_connectivity_query_view.csv")
    parser.add_argument("--out-csv", required=True, help="Path to write filtered output CSV")
    args = parser.parse_args()

    in_csv = Path(args.query_csv)
    out_csv = Path(args.out_csv)

    rows_out: list[dict[str, str]] = []
    with in_csv.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if is_thread_tiedoff_or_unconnected(row):
                rows_out.append(project_row(row))

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"WROTE={out_csv}")
    print(f"ROWS={len(rows_out)}")


if __name__ == "__main__":
    main()
