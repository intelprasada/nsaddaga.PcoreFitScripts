#!/usr/bin/env python3
"""Find signals with mismatched dimensions across non-TLM units in a query I/O table CSV."""

import argparse
import csv
from collections import defaultdict


def main():
    parser = argparse.ArgumentParser(
        description="Flag signals whose packed_width or unpacked_dim differ across units (excluding *_tlm)."
    )
    parser.add_argument("--io-csv", required=True, help="Path to 12_*_query_io_table.csv")
    parser.add_argument("--out-csv", default=None, help="Optional output CSV for mismatched rows")
    args = parser.parse_args()

    # Group rows by port_name, skipping *_tlm instances
    signal_dims = defaultdict(list)

    with open(args.io_csv, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mod = row["instance_module"]
            if mod.endswith("_tlm"):
                continue
            port = row["port_name"]
            signal_dims[port].append({
                "instance_module": mod,
                "instance_name": row["instance_name"],
                "port_direction": row["port_direction"],
                "direction_sv_type": row["direction_sv_type"],
                "direction_packed_width": row.get("direction_packed_width", ""),
                "direction_unpacked_dim": row.get("direction_unpacked_dim", ""),
                "source_file": row.get("direction_source_file", ""),
                "decl_line": row.get("direction_decl_line", ""),
            })

    # Find mismatches: signals where not all rows share the same (packed_width, unpacked_dim)
    mismatched = {}
    for port, entries in signal_dims.items():
        if len(entries) < 2:
            continue
        dims_set = set()
        for e in entries:
            dims_set.add((e["direction_packed_width"], e["direction_unpacked_dim"]))
        if len(dims_set) > 1:
            mismatched[port] = entries

    # Report
    if not mismatched:
        print("No dimension mismatches found across non-TLM units.")
        return

    print(f"Found {len(mismatched)} signal(s) with dimension mismatches across non-TLM units:\n")

    out_rows = []
    for port in sorted(mismatched):
        entries = mismatched[port]
        print(f"  Signal: {port}")
        for e in entries:
            pw = e["direction_packed_width"] or "(scalar)"
            ud = e["direction_unpacked_dim"] or "(none)"
            print(f"    {e['instance_module']:30s}  dir={e['port_direction']:6s}  "
                  f"type={e['direction_sv_type']:6s}  packed={pw:30s}  unpacked={ud}")
            out_rows.append({
                "port_name": port,
                "instance_module": e["instance_module"],
                "instance_name": e["instance_name"],
                "port_direction": e["port_direction"],
                "direction_sv_type": e["direction_sv_type"],
                "direction_packed_width": e["direction_packed_width"],
                "direction_unpacked_dim": e["direction_unpacked_dim"],
                "direction_source_file": e["source_file"],
                "direction_decl_line": e["decl_line"],
            })
        print()

    if args.out_csv and out_rows:
        with open(args.out_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=out_rows[0].keys())
            writer.writeheader()
            writer.writerows(out_rows)
        print(f"Wrote {len(out_rows)} rows to {args.out_csv}")


if __name__ == "__main__":
    main()
