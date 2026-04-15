#!/usr/bin/env python3
"""Extract a top-module I/O table from a row-wise query I/O table.

This script keeps only rows that represent a selected module as seen from the
parent/top query I/O table (for example IFU rows from FE step-12 output).

Typical usage:
    python3 extract_module_top_io_table.py \
      --io-csv target/fe/gen/InterfaceSpecAgent/fe_pipeline_<ts>/results/12_fe_query_io_table.csv \
      --module ifu \
      --port-decls target/fe/gen/ifu.port_decls.v \
      --out-csv target/fe/gen/InterfaceSpecAgent/fe_pipeline_<ts>/results/18_ifu_top_io_table.csv
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

PORT_DECL_RE = re.compile(r"^\s*(input|output|inout)\b")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--io-csv", required=True, help="Input row-wise IO table (12_*_query_io_table.csv)")
    parser.add_argument("--module", required=True, help="Module name to extract (for example: ifu)")
    parser.add_argument("--out-csv", required=True, help="Output CSV path")
    parser.add_argument(
        "--port-decls",
        help="Optional <module>.port_decls.v file used for count validation and missing list reporting",
    )
    return parser.parse_args()


def read_rows(io_csv: Path, module: str) -> tuple[list[dict[str, str]], list[str]]:
    with io_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader if (r.get("instance_module") or "").strip() == module]
    return rows, fieldnames


def dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    # Query-view selection should already be unique, but dedupe by port+direction
    # to guarantee one logical row per module boundary port.
    by_key: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (
            (row.get("port_name") or "").strip(),
            (row.get("port_direction") or "").strip(),
        )
        if key not in by_key:
            by_key[key] = row
            continue

        existing = by_key[key]
        # Prefer connected-to-top rows when duplicates appear.
        cur_top = (row.get("is_connected_to_top") or "").strip().lower() == "true"
        old_top = (existing.get("is_connected_to_top") or "").strip().lower() == "true"
        if cur_top and not old_top:
            by_key[key] = row

    return list(by_key.values())


def parse_port_decls_counts(port_decls: Path) -> tuple[int, int, int]:
    in_count = 0
    out_count = 0
    inout_count = 0

    for raw in port_decls.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line or line.startswith("`"):
            continue
        match = PORT_DECL_RE.match(line)
        if not match:
            continue
        direction = match.group(1)
        if direction == "input":
            in_count += 1
        elif direction == "output":
            out_count += 1
        elif direction == "inout":
            inout_count += 1

    return in_count, out_count, inout_count


def write_rows(out_csv: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    io_csv = Path(args.io_csv)
    out_csv = Path(args.out_csv)
    module = args.module

    rows, fieldnames = read_rows(io_csv, module)
    deduped = dedupe_rows(rows)

    # Stable sort for readability.
    deduped.sort(
        key=lambda r: (
            (r.get("direction_decl_line") or ""),
            (r.get("port_direction") or ""),
            (r.get("port_name") or ""),
        )
    )

    write_rows(out_csv, deduped, fieldnames)

    input_rows = sum(1 for r in deduped if (r.get("port_direction") or "").strip() == "input")
    output_rows = sum(1 for r in deduped if (r.get("port_direction") or "").strip() == "output")
    inout_rows = sum(1 for r in deduped if (r.get("port_direction") or "").strip() == "inout")

    print(f"WROTE={out_csv}")
    print(f"MODULE={module}")
    print(f"ROWS={len(deduped)}")
    print(f"INPUT_ROWS={input_rows}")
    print(f"OUTPUT_ROWS={output_rows}")
    print(f"INOUT_ROWS={inout_rows}")

    if args.port_decls:
        port_decls = Path(args.port_decls)
        decl_in, decl_out, decl_inout = parse_port_decls_counts(port_decls)
        print(f"DECL_INPUT_ROWS={decl_in}")
        print(f"DECL_OUTPUT_ROWS={decl_out}")
        print(f"DECL_INOUT_ROWS={decl_inout}")
        print(f"COUNT_MATCH={decl_in == input_rows and decl_out == output_rows and decl_inout == inout_rows}")


if __name__ == "__main__":
    main()
