#!/usr/bin/env python3
"""Analyze row-count deltas between typed connectivity and direction CSV.

Outputs per-module metrics to explain why connectivity rows may exceed
port-direction rows.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze connectivity vs direction delta by module"
    )
    parser.add_argument("--typed-csv", required=True, help="Typed connectivity CSV")
    parser.add_argument("--directions-csv", required=True, help="Directions CSV")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    typed_csv = Path(args.typed_csv)
    directions_csv = Path(args.directions_csv)

    dir_count = defaultdict(int)
    with directions_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            module = (row.get("module") or "").strip()
            if module:
                dir_count[module] += 1

    conn_rows = defaultdict(int)
    unique_ports = defaultdict(set)
    with typed_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            module = (row.get("instance_module") or "").strip()
            inst = (row.get("instance_name") or "").strip()
            port = (row.get("port_name") or "").strip()
            if not module:
                continue
            conn_rows[module] += 1
            unique_ports[module].add((inst, port))

    modules = sorted(set(dir_count.keys()) | set(conn_rows.keys()))

    total_conn = sum(conn_rows.values())
    total_dir = sum(dir_count.values())
    total_unique = sum(len(unique_ports[m]) for m in modules)

    print("module,conn_rows,dir_rows,unique_instance_port,duplicate_extra,net_delta,unique_delta")
    for m in modules:
        c = conn_rows[m]
        d = dir_count[m]
        u = len(unique_ports[m])
        dup = c - u
        net = c - d
        uniq_delta = u - d
        print(f"{m},{c},{d},{u},{dup},{net},{uniq_delta}")

    print("TOTALS")
    print(f"total_conn_rows={total_conn}")
    print(f"total_dir_rows={total_dir}")
    print(f"total_unique_instance_port={total_unique}")
    print(f"total_duplicate_extra={total_conn-total_unique}")
    print(f"total_net_delta={total_conn-total_dir}")
    print(f"total_unique_delta={total_unique-total_dir}")


if __name__ == "__main__":
    main()
