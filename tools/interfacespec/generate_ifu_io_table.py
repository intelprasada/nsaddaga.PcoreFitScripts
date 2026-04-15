#!/usr/bin/env python3
"""Generate IFU I/O connectivity table from connectivity_typed.csv.

Signal identity is the IFU port name. The connection expression is used only to
resolve producer/peer units (for plain identifiers and expression identifiers).

Output columns:
- signal_name
- direction
- source_output_units: for IFU inputs, units that drive identifiers in expr
- other_connected_units: other peer units touching identifiers in expr
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
import re


def is_plain_identifier(name: str) -> bool:
    if not name:
        return False
    first = name[0]
    if not (first.isalpha() or first == "_"):
        return False
    return all(char.isalnum() or char in "_$" for char in name)


def parse_bool_str(value: str) -> bool:
    return (value or "").strip().lower() == "true"


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def extract_identifiers(expr: str) -> list[str]:
    if not expr:
        return []
    tokens = IDENTIFIER_RE.findall(expr)
    return sorted(set(tokens))


def main() -> None:
    base = Path("target/fe/gen/InterfaceSpecAgent/fe")
    in_csv = base / "connectivity_typed.csv"
    out_csv = base / "ifu_io_units_table.csv"

    rows = list(csv.DictReader(in_csv.open(newline="", encoding="utf-8")))

    by_identifier: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        sig = (row.get("signal_name_normalized") or "").strip()
        if not sig:
            continue
        by_identifier[sig].append(row)

    agg: dict[tuple[str, str], dict[str, set[str]]] = {}
    for row in rows:
        if (row.get("instance_module") or "").strip() != "ifu":
            continue

        signal_name = (row.get("port_name") or "").strip()
        if not signal_name:
            continue

        direction = (row.get("port_direction") or "unknown").strip()
        connected_expr = (row.get("connected_expr") or "").strip()
        signal_name_normalized = (row.get("signal_name_normalized") or "").strip()

        if parse_bool_str(row.get("is_plain_identifier", "")) and signal_name_normalized:
            expr_identifiers = [signal_name_normalized]
        else:
            expr_identifiers = extract_identifiers(connected_expr)

        key = (signal_name, direction)

        if key not in agg:
            agg[key] = {
                "source_output_units": set(),
                "other_connected_units": set(),
            }

        peer_rows: list[dict[str, str]] = []
        for identifier in expr_identifiers:
            peer_rows.extend(by_identifier.get(identifier, []))

        peer_rows = [
            peer
            for peer in peer_rows
            if (peer.get("instance_module") or "").strip()
            and (peer.get("instance_module") or "").strip() != "ifu"
        ]

        if direction == "input":
            for peer in peer_rows:
                peer_unit = (peer.get("instance_module") or "").strip()
                peer_dir = (peer.get("port_direction") or "").strip().lower()
                if not peer_unit:
                    continue
                if peer_dir == "output":
                    agg[key]["source_output_units"].add(peer_unit)
                else:
                    agg[key]["other_connected_units"].add(peer_unit)

            if not agg[key]["source_output_units"]:
                if expr_identifiers:
                    agg[key]["source_output_units"].add("FE_TOP_OR_UNKNOWN")
                else:
                    agg[key]["source_output_units"].add("CONSTANT_OR_EXPRESSION")

            if not agg[key]["other_connected_units"] and not peer_rows:
                agg[key]["other_connected_units"].add("NONE")
        else:
            agg[key]["source_output_units"].add("ifu")
            if peer_rows:
                for peer in peer_rows:
                    peer_unit = (peer.get("instance_module") or "").strip()
                    if peer_unit:
                        agg[key]["other_connected_units"].add(peer_unit)
            else:
                agg[key]["other_connected_units"].add("FE_TOP_OR_NONE")

    out_rows = [
        {
            "signal_name": signal_name,
            "direction": direction,
            "source_output_units": ";".join(sorted(values["source_output_units"])),
            "other_connected_units": ";".join(sorted(values["other_connected_units"])),
        }
        for (signal_name, direction), values in agg.items()
    ]
    out_rows.sort(key=lambda row: (row["direction"], row["signal_name"]))

    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "signal_name",
                "direction",
                "source_output_units",
                "other_connected_units",
            ],
        )
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"WROTE={out_csv}")
    print(f"ROWS={len(out_rows)}")


if __name__ == "__main__":
    main()
