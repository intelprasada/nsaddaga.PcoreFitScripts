#!/usr/bin/env python3
"""Report duplicate instance+port rows from typed connectivity CSV.

A duplicate is defined as more than one row sharing:
- instance_module
- instance_name
- port_name

This is useful to explain deltas where connectivity rows exceed direction rows.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report duplicate instance+port rows from typed connectivity CSV"
    )
    parser.add_argument("--typed-csv", required=True, help="Path to typed connectivity CSV")
    parser.add_argument("--out-csv", required=True, help="Output duplicate report CSV path")
    return parser.parse_args()


def safe_join(values: set[str]) -> str:
    if not values:
        return ""
    return " | ".join(sorted(values))


def main() -> None:
    args = parse_args()
    typed_csv = Path(args.typed_csv)
    out_csv = Path(args.out_csv)

    if not typed_csv.exists():
        print(f"ERROR: --typed-csv not found: {typed_csv}", file=sys.stderr)
        sys.exit(1)

    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)

    with typed_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {
            "instance_module",
            "instance_name",
            "port_name",
            "connected_expr",
            "guard_expr",
            "source_file",
            "source_line",
        }
        fieldnames = set(reader.fieldnames or [])
        missing = required - fieldnames
        if missing:
            print(
                "ERROR: typed CSV missing required columns: " + ", ".join(sorted(missing)),
                file=sys.stderr,
            )
            sys.exit(1)

        for row in reader:
            key = (
                (row.get("instance_module") or "").strip(),
                (row.get("instance_name") or "").strip(),
                (row.get("port_name") or "").strip(),
            )
            grouped[key].append(row)

    report_rows: list[dict[str, str | int]] = []
    for (instance_module, instance_name, port_name), rows in grouped.items():
        if len(rows) <= 1:
            continue

        connected_exprs = {
            (row.get("connected_expr") or "").strip()
            for row in rows
            if (row.get("connected_expr") or "").strip()
        }
        guard_exprs = {
            (row.get("guard_expr") or "").strip()
            for row in rows
            if (row.get("guard_expr") or "").strip()
        }
        source_locations = {
            f"{(row.get('source_file') or '').strip()}:{(row.get('source_line') or '').strip()}"
            for row in rows
        }

        report_rows.append(
            {
                "instance_module": instance_module,
                "instance_name": instance_name,
                "port_name": port_name,
                "row_count": len(rows),
                "unique_connected_expr_count": len(connected_exprs),
                "connected_exprs": safe_join(connected_exprs),
                "guard_exprs": safe_join(guard_exprs),
                "source_locations": safe_join(source_locations),
            }
        )

    report_rows.sort(
        key=lambda r: (
            -int(r["row_count"]),
            str(r["instance_module"]),
            str(r["instance_name"]),
            str(r["port_name"]),
        )
    )

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "instance_module",
                "instance_name",
                "port_name",
                "row_count",
                "unique_connected_expr_count",
                "connected_exprs",
                "guard_exprs",
                "source_locations",
            ],
        )
        writer.writeheader()
        writer.writerows(report_rows)

    print(f"WROTE={out_csv}")
    print(f"DUPLICATE_INSTANCE_PORTS={len(report_rows)}")


if __name__ == "__main__":
    main()
