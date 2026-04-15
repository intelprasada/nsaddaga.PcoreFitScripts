#!/usr/bin/env python3
"""Enrich an I/O table with cross-hierarchy consumer/producer resolution.

For rows where is_connected_to_top=true, traces the signal through parent
hierarchy I/O tables to find consuming units (for outputs) or producing
units (for inputs) across cluster boundaries.

Algorithm:
    1. For each row with is_connected_to_top=true:
       a. Look in the nearest parent I/O table for all rows sharing the same
          port_name. Rows belonging to our module are checked for
          continues-upward; all other rows are classified as peers.
       b. If the signal also crosses the parent boundary (continues_up=true),
          repeat at the next parent level.
       c. For each peer cluster found, if a sibling I/O table is available,
          drill into it to find the leaf unit inside that cluster.

    2. For outputs: peer consumers (input-direction peers) are collected.
       For inputs: peer producers (output-direction peers) are collected.

Example:
    python3 scripts/interfacespec/enrich_cross_hierarchy.py \\
        --io-csv 12_idq_query_io_table.csv \\
        --module idq \\
        --parent msid:12_msid_query_io_table_v2.csv \\
        --parent icore:12_icore_query_io_table.csv \\
        --sibling ooo:12_ooo_query_io_table.csv \\
        --out-csv 12_idq_query_io_table_xhier.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path


def load_io_rows_by_port(csv_path: str) -> dict[str, list[dict[str, str]]]:
    """Load I/O table and index rows by port_name."""
    by_port: dict[str, list[dict[str, str]]] = defaultdict(list)
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            port = (row.get("port_name") or "").strip()
            if port:
                by_port[port].append(row)
    return by_port


def find_peers_at_level(
    port_name: str,
    parent_by_port: dict[str, list[dict[str, str]]],
    my_module: str,
) -> tuple[set[str], set[str], bool, bool, str]:
    """Find peer consumers and producers at a parent hierarchy level.

    Returns (consumers, producers, continues_up, is_tiedoff, tiedoff_expr):
    - consumers: instance_modules with input direction (they consume the signal)
    - producers: instance_modules with output direction (they produce the signal)
    - continues_up: True if the signal also crosses this parent's boundary
    - is_tiedoff: True if our module's row is driven by a constant expression
    - tiedoff_expr: the literal connected_expr value when tied off, else empty
    """
    consumers: set[str] = set()
    producers: set[str] = set()
    continues_up = False
    is_tiedoff = False
    tiedoff_expr = ""
    my_lower = my_module.lower()

    for row in parent_by_port.get(port_name, []):
        inst = (row.get("instance_module") or "").strip()
        direction = (row.get("port_direction") or "").strip().lower()
        is_top = (row.get("is_connected_to_top") or "").strip().lower() == "true"

        if inst.lower() == my_lower:
            # This is our own module's row at this level — check boundary
            if is_top:
                continues_up = True
            # Detect constant tie-off: source_output_units is CONSTANT_OR_EXPRESSION
            # or connected_expr contains a Verilog literal tick
            src = (row.get("source_output_units") or "").strip()
            expr = (row.get("connected_expr") or "").strip()
            if src == "CONSTANT_OR_EXPRESSION" or ("'" in expr):
                is_tiedoff = True
                tiedoff_expr = expr
            continue

        # Peer unit at this level
        if direction == "output":
            producers.add(inst)
        elif direction in ("input", "inout"):
            consumers.add(inst)

    return consumers, producers, continues_up, is_tiedoff, tiedoff_expr


def drill_into_sibling(
    port_name: str,
    sibling_by_port: dict[str, list[dict[str, str]]],
    direction_filter: str,
) -> set[str]:
    """Find leaf units inside a sibling cluster for a given signal.

    direction_filter: 'input' to find consuming leaf units,
                      'output' to find producing leaf units.
    """
    units: set[str] = set()
    for row in sibling_by_port.get(port_name, []):
        if (row.get("port_direction") or "").strip().lower() == direction_filter:
            inst = (row.get("instance_module") or "").strip()
            if inst:
                units.add(inst)
    return units


def trace_cross_hierarchy(
    port_name: str,
    row_direction: str,
    parents: list[tuple[str, dict]],
    siblings: dict[str, dict],
    target_module: str,
) -> tuple[list[str], str]:
    """Trace a connected-to-top signal through parent hierarchies.

    For outputs: collects consumer units (input peers) at each parent level.
    For inputs: collects producer units (output peers) at each parent level.
    Drills into sibling I/O tables when available.

    Returns (peers, tiedoff_expr):
    - peers: sorted list of cross-hierarchy peer paths
    - tiedoff_expr: the literal tie-off expression if detected, else empty
    """
    results: list[str] = []
    found_tiedoff_expr = ""
    current_module = target_module

    for parent_module, parent_by_port in parents:
        consumers, producers, continues_up, is_tiedoff, tiedoff_expr = find_peers_at_level(
            port_name, parent_by_port, current_module,
        )

        if row_direction == "output":
            peers = consumers
            drill_dir = "input"
        else:
            peers = producers
            drill_dir = "output"

        # For inputs: if our module's row is tied off to a constant at this
        # parent level, record it as a producer.
        if row_direction == "input" and is_tiedoff and not peers:
            results.append(f"{parent_module}/TIEDOFF_CONST")
            if tiedoff_expr:
                found_tiedoff_expr = tiedoff_expr

        for peer in sorted(peers):
            sib = siblings.get(peer.lower())
            if sib:
                # Drill into sibling cluster to find the leaf unit
                leaves = drill_into_sibling(port_name, sib, drill_dir)
                for leaf in sorted(leaves):
                    results.append(f"{peer}/{leaf}")
                if not leaves:
                    # Sibling table loaded but signal not found inside — use cluster name
                    results.append(f"{parent_module}/{peer}")
            else:
                results.append(f"{parent_module}/{peer}")

        if not continues_up:
            break
        current_module = parent_module

    # If the signal still continues_up after exhausting all parent levels,
    # the signal crosses the cluster boundary.  Scan all available sibling
    # cluster tables directly to find consumers (for outputs) or producers
    # (for inputs) that imported this signal from an upper boundary.
    if continues_up and siblings:
        if row_direction == "output":
            scan_dir = "input"
        else:
            scan_dir = "output"
        for sib_name, sib_by_port in siblings.items():
            leaves = drill_into_sibling(port_name, sib_by_port, scan_dir)
            for leaf in sorted(leaves):
                results.append(f"{sib_name}/{leaf}")
            if not leaves:
                # Signal IS a top-level port of the sibling cluster
                for row in sib_by_port.get(port_name, []):
                    is_top = (row.get("is_connected_to_top") or "").strip().lower() == "true"
                    if is_top:
                        results.append(sib_name)
                        break

    return sorted(set(results)), found_tiedoff_expr


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--io-csv", required=True, help="Target I/O table to enrich")
    p.add_argument("--module", required=True, help="Target module name (e.g., idq)")
    p.add_argument(
        "--parent", action="append", required=True, metavar="MOD:CSV",
        help="Parent I/O table as MODULE:CSV_PATH (nearest parent first, repeatable)",
    )
    p.add_argument(
        "--sibling", action="append", default=[], metavar="MOD:CSV",
        help="Sibling cluster I/O table for drill-into as MODULE:CSV_PATH (repeatable)",
    )
    p.add_argument("--out-csv", required=True, help="Output enriched CSV path")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # Load parent hierarchy I/O tables (ordered nearest to farthest ancestor)
    parents: list[tuple[str, dict]] = []
    for spec in args.parent:
        mod, path = spec.split(":", 1)
        by_port = load_io_rows_by_port(path)
        parents.append((mod, by_port))
        print(
            f"PARENT {mod}: {path} ({sum(len(v) for v in by_port.values())} rows)",
            file=sys.stderr,
        )

    # Load sibling cluster I/O tables (for drill-into)
    siblings: dict[str, dict] = {}
    for spec in args.sibling:
        mod, path = spec.split(":", 1)
        siblings[mod.lower()] = load_io_rows_by_port(path)
        print(f"SIBLING {mod}: {path}", file=sys.stderr)

    # Load target I/O table
    with open(args.io_csv, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    print(
        f"TARGET {args.module}: {args.io_csv} ({len(rows)} rows)",
        file=sys.stderr,
    )

    # Insert enrichment columns after is_connected_to_top
    xhier_cols = ["xhier_consumers", "xhier_producers"]
    out_fields = list(fields)
    if "is_connected_to_top" in out_fields:
        idx = out_fields.index("is_connected_to_top") + 1
        for i, col in enumerate(xhier_cols):
            out_fields.insert(idx + i, col)
    else:
        out_fields.extend(xhier_cols)

    enriched_out = 0
    enriched_in = 0
    out_rows: list[dict] = []

    for row in rows:
        out = dict(row)
        out["xhier_consumers"] = "NONE"
        out["xhier_producers"] = "NONE"

        is_top = (row.get("is_connected_to_top") or "").strip().lower() == "true"
        direction = (row.get("port_direction") or "").strip().lower()
        port = (row.get("port_name") or "").strip()

        if is_top and port and direction in ("output", "input"):
            peers, tiedoff_expr = trace_cross_hierarchy(
                port, direction, parents, siblings, args.module,
            )
            if peers:
                if direction == "output":
                    out["xhier_consumers"] = ";".join(peers)
                    enriched_out += 1
                else:
                    out["xhier_producers"] = ";".join(peers)
                    enriched_in += 1

        out_rows.append(out)

    # Write enriched output
    out_path = Path(args.out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=out_fields)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"WROTE={out_path}", file=sys.stderr)
    print(f"ROWS={len(out_rows)}", file=sys.stderr)
    print(f"ENRICHED_TOTAL={enriched_out + enriched_in}", file=sys.stderr)
    print(f"ENRICHED_OUTPUTS={enriched_out}", file=sys.stderr)
    print(f"ENRICHED_INPUTS={enriched_in}", file=sys.stderr)


if __name__ == "__main__":
    main()
