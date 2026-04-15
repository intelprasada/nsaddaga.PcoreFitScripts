#!/usr/bin/env python3
"""Generate a compact review table for unresolved name mismatch candidates."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def sort_key(row: dict[str, str]) -> tuple[int, str, str, str, str]:
    candidate_source = (row.get("candidate_source") or "").strip().lower()
    return (
        1 if candidate_source == "target" else 0,
        row.get("unresolved_port_name") or "",
        row.get("candidate_match_type") or "",
        row.get("candidate_source") or "",
        row.get("candidate_instance_module") or "",
    )


def make_unit(cluster: str, instance_module: str) -> str:
    cluster = (cluster or "").strip()
    instance_module = (instance_module or "").strip()
    if cluster and instance_module:
        return f"{cluster}/{instance_module}"
    return cluster or instance_module


def transform_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "mismatch_signal": row.get("unresolved_port_name", ""),
        "matched_signal": row.get("candidate_port_name", ""),
        "match_type": row.get("candidate_match_type", ""),
        "target_unit": make_unit(
            row.get("unresolved_cluster", ""),
            row.get("unresolved_instance_module", ""),
        ),
        "source_unit": make_unit(
            row.get("candidate_cluster", ""),
            row.get("candidate_instance_module", ""),
        ),
        "target_direction": row.get("unresolved_port_direction", ""),
        "source_direction": row.get("candidate_port_direction", ""),
        "target_missing_fields": row.get("unresolved_fields", ""),
        "candidate_source_table": row.get("candidate_source", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate compact review CSV for unresolved name mismatch candidates"
    )
    parser.add_argument("--in-csv", required=True, help="Step-14 candidate CSV")
    parser.add_argument("--out-csv", required=True, help="Compact output CSV")
    args = parser.parse_args()

    input_path = Path(args.in_csv)
    output_path = Path(args.out_csv)
    if not input_path.exists():
        raise SystemExit(f"ERROR: input CSV not found: {input_path}")

    rows = load_rows(input_path)
    rows.sort(key=sort_key)

    fieldnames = [
        "mismatch_signal",
        "matched_signal",
        "match_type",
        "target_unit",
        "source_unit",
        "target_direction",
        "source_direction",
        "target_missing_fields",
        "candidate_source_table",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(transform_row(row))

    print(f"INPUT_ROWS={len(rows)}")
    print(f"WROTE={output_path}")


if __name__ == "__main__":
    main()
