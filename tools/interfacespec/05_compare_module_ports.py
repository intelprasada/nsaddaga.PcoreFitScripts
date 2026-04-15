#!/usr/bin/env python3
"""Compare module ports between typed connectivity and direction DB.

Reports missing and extra ports for a selected module using CSV-safe parsing.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare module ports between typed connectivity and direction CSV"
    )
    parser.add_argument("--typed-csv", required=True, help="Path to typed connectivity CSV")
    parser.add_argument("--directions-csv", required=True, help="Path to direction CSV")
    parser.add_argument("--module", required=True, help="Module name to compare")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    typed_csv = Path(args.typed_csv)
    directions_csv = Path(args.directions_csv)
    module = args.module.strip()

    if not typed_csv.exists():
        print(f"ERROR: --typed-csv not found: {typed_csv}", file=sys.stderr)
        sys.exit(1)
    if not directions_csv.exists():
        print(f"ERROR: --directions-csv not found: {directions_csv}", file=sys.stderr)
        sys.exit(1)

    conn_ports: set[str] = set()
    with typed_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if (row.get("instance_module") or "").strip() == module:
                conn_ports.add((row.get("port_name") or "").strip())

    dir_ports: set[str] = set()
    with directions_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if (row.get("module") or "").strip() == module:
                dir_ports.add((row.get("port") or "").strip())

    missing = sorted(dir_ports - conn_ports)
    extra = sorted(conn_ports - dir_ports)

    print(f"MODULE={module}")
    print(f"CONNECTIVITY_UNIQUE_PORTS={len(conn_ports)}")
    print(f"DIRECTION_PORTS={len(dir_ports)}")
    print(f"MISSING_IN_CONNECTIVITY={len(missing)}")
    for port in missing:
        print(f"MISSING_PORT={port}")
    print(f"EXTRA_IN_CONNECTIVITY={len(extra)}")
    for port in extra:
        print(f"EXTRA_PORT={port}")


if __name__ == "__main__":
    main()
