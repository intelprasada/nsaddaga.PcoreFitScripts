#!/usr/bin/env python3
"""Derive top-module interface rows by combining parent and drilldown I/O tables.

This bridges two existing views:
- Parent-level row-wise I/O table, which knows how the target module connects to the
  outer hierarchy.
- Drilldown row-wise I/O table, which knows which child units consume top inputs and
  produce top outputs.

The output is a synthetic row-wise I/O CSV with one row per declared top port. It is
shaped so downstream interface-spec tooling can treat the top module like any other
module instance.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

from enrich_cross_hierarchy import (
    load_io_rows_by_port,
    trace_cross_hierarchy,
)

def _dedup_units(units: set[str]) -> set[str]:
    """Remove less-qualified duplicates.

    When a unit appears both as 'foo' (local) and 'parent/foo' (xhier-qualified),
    keep only the more-qualified version to avoid visual duplication.
    """
    result = set(units)
    for u in list(result):
        # If any other entry ends with '/u', drop the short form
        if any(other.endswith("/" + u) for other in result if other != u):
            result.discard(u)
    return result


PORT_DECL_RE = re.compile(r"^\s*(?P<direction>input|output|inout)\b(?P<body>[^;]*);")
PORT_DECL_DETAIL_RE = re.compile(
    r"^\s*"
    r"(?P<direction>input|output|inout)\s+"
    r"(?P<sv_type>[A-Za-z_][A-Za-z0-9_$]*)\s+"
    r"(?P<packed_width>\[[^\]]+\]\s*)?"
    r"(?P<signal_name>[A-Za-z_][A-Za-z0-9_$]*)"
    r"\s*(?P<unpacked_dim>\[[^\]]+\])?"
    r"\s*;"
)
TRAILING_DIMS_RE = re.compile(r"\s*(\[[^\]]+\]\s*)*$")
SIGNAL_NAME_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_$]*)$")

OUTPUT_FIELDS = [
    "cluster",
    "top_module",
    "instance_module",
    "instance_name",
    "port_name",
    "port_direction",
    "source_output_units",
    "connected_other_units",
    "xhier_consumers",
    "xhier_producers",
    "xhier_tiedoff_expr",
    "connected_tlm_units",
    "is_connected_to_top",
    "direction_sv_type",
    "direction_packed_width",
    "direction_unpacked_dim",
    "edge_role",
    "join_status",
    "join_reason_code",
    "connected_expr",
    "normalized_expr",
    "signal_name_normalized",
    "is_plain_identifier",
    "is_expression",
    "is_unconnected",
    "is_conditional",
    "guard_expr",
    "source_file",
    "source_line",
    "direction_source_file",
    "direction_decl_line",
    "query_selection_reason",
    "guard_alternative_count",
    "producer_cluster_owner",
    "producer_origin_hint",
    "producer_owner_evidence",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-io-csv", required=True, help="Parent-level 12_*_query_io_table.csv")
    parser.add_argument("--drilldown-io-csv", required=True, help="Drilldown 12_*_query_io_table.csv for the target top module")
    parser.add_argument("--port-decls", required=True, help="Generated <module>.port_decls.v file")
    parser.add_argument("--module", required=True, help="Top module name, for example ifu")
    parser.add_argument("--out-csv", required=True, help="Derived top-interface CSV output path")
    parser.add_argument(
        "--parent-hierarchy",
        action="append",
        default=[],
        metavar="MOD:CSV",
        help="Parent hierarchy I/O table for cross-hierarchy tracing as MODULE:CSV_PATH "
             "(nearest parent first, repeatable)",
    )
    parser.add_argument(
        "--sibling",
        action="append",
        default=[],
        metavar="MOD:CSV",
        help="Sibling cluster I/O table for drill-into as MODULE:CSV_PATH (repeatable)",
    )
    return parser.parse_args()


def strip_line_comment(line: str) -> str:
    if "//" not in line:
        return line
    return line.split("//", 1)[0]


def extract_signal_name_from_decl_body(body: str) -> str | None:
    no_dims = TRAILING_DIMS_RE.sub("", body).rstrip(" ;")
    match = SIGNAL_NAME_RE.search(no_dims.strip())
    if not match:
        return None
    return match.group(1)


def parse_port_decls(port_decls_path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with port_decls_path.open(encoding="utf-8", errors="replace") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = strip_line_comment(raw_line).strip()
            if not line or line.startswith("`"):
                continue

            match = PORT_DECL_RE.match(line)
            if not match:
                continue

            detail_match = PORT_DECL_DETAIL_RE.match(line)
            if detail_match:
                signal_name = detail_match.group("signal_name")
                sv_type = (detail_match.group("sv_type") or "").strip()
                packed_width = (detail_match.group("packed_width") or "").strip()
                unpacked_dim = (detail_match.group("unpacked_dim") or "").strip()
                direction = detail_match.group("direction")
            else:
                body = match.group("body")
                signal_name = extract_signal_name_from_decl_body(body)
                if signal_name is None:
                    continue
                direction = match.group("direction")
                sv_type = ""
                packed_width = ""
                unpacked_dim = ""

            rows.append(
                {
                    "port_name": signal_name,
                    "port_direction": direction,
                    "direction_sv_type": sv_type,
                    "direction_packed_width": packed_width,
                    "direction_unpacked_dim": unpacked_dim,
                    "direction_decl_line": str(line_number),
                    "direction_source_file": str(port_decls_path),
                }
            )
    return rows


def load_parent_rows(parent_csv: Path, module: str) -> dict[str, dict[str, str]]:
    by_port: dict[str, dict[str, str]] = {}
    with parent_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if (row.get("instance_module") or "").strip() != module:
                continue
            port_name = (row.get("port_name") or "").strip()
            if port_name:
                by_port[port_name] = dict(row)
    return by_port


def add_unit(unit_map: dict[str, set[str]], tlm_map: dict[str, set[str]], signal_name: str, unit: str) -> None:
    clean_signal = (signal_name or "").strip()
    clean_unit = (unit or "").strip()
    if not clean_signal or not clean_unit or clean_unit == "NONE":
        return
    target = tlm_map if clean_unit.lower().endswith("_tlm") else unit_map
    target[clean_signal].add(clean_unit)


def load_drilldown_usage(drilldown_csv: Path) -> tuple[
    dict[str, set[str]], dict[str, set[str]],
    dict[str, set[str]], dict[str, set[str]],
]:
    input_units: dict[str, set[str]] = defaultdict(set)
    input_tlms: dict[str, set[str]] = defaultdict(set)
    output_units: dict[str, set[str]] = defaultdict(set)
    output_tlms: dict[str, set[str]] = defaultdict(set)

    with drilldown_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if (row.get("is_connected_to_top") or "").strip().lower() != "true":
                continue
            signal_name = (row.get("signal_name_normalized") or row.get("connected_expr") or "").strip()
            if not signal_name or signal_name == "NONE":
                continue

            unit = (row.get("instance_name") or row.get("instance_module") or "").strip()
            direction = (row.get("port_direction") or "").strip().lower()
            if direction == "input":
                add_unit(input_units, input_tlms, signal_name, unit)
            elif direction == "output":
                add_unit(output_units, output_tlms, signal_name, unit)

    return input_units, input_tlms, output_units, output_tlms


def join_units(values: set[str]) -> str:
    return ";".join(sorted(values)) if values else "NONE"


def build_output_row(
    module: str,
    decl: dict[str, str],
    base_row: dict[str, str] | None,
    input_units: dict[str, set[str]],
    input_tlms: dict[str, set[str]],
    output_units: dict[str, set[str]],
    output_tlms: dict[str, set[str]],
) -> dict[str, str]:
    port_name = decl["port_name"]
    direction = decl["port_direction"]
    row = {field: "" for field in OUTPUT_FIELDS}

    if base_row:
        for field in OUTPUT_FIELDS:
            row[field] = base_row.get(field, "")

    cluster = row.get("cluster") or (base_row or {}).get("cluster", "") or "fe"
    row["cluster"] = cluster
    row["top_module"] = module
    row["instance_module"] = module
    row["instance_name"] = module
    row["port_name"] = port_name
    row["port_direction"] = direction
    row["direction_sv_type"] = decl["direction_sv_type"]
    row["direction_packed_width"] = decl["direction_packed_width"]
    row["direction_unpacked_dim"] = decl["direction_unpacked_dim"]
    row["direction_source_file"] = decl["direction_source_file"]
    row["direction_decl_line"] = decl["direction_decl_line"]
    row["connected_expr"] = port_name
    row["normalized_expr"] = port_name
    row["signal_name_normalized"] = port_name
    row["is_plain_identifier"] = "true"
    row["is_expression"] = "false"
    row["is_connected_to_top"] = "true"
    row["is_conditional"] = "false"
    row["guard_expr"] = ""
    row["join_status"] = row.get("join_status") or "resolved"
    row["join_reason_code"] = row.get("join_reason_code") or ""
    row["source_file"] = decl["direction_source_file"]
    row["source_line"] = decl["direction_decl_line"]
    row["guard_alternative_count"] = row.get("guard_alternative_count") or "0"

    row["xhier_consumers"] = "NONE"
    row["xhier_producers"] = "NONE"
    row["xhier_tiedoff_expr"] = ""

    if direction == "input":
        row["edge_role"] = "consumer_candidate"
        row["connected_other_units"] = join_units(input_units.get(port_name, set()))
        row["connected_tlm_units"] = join_units(input_tlms.get(port_name, set()))
        row["producer_origin_hint"] = row.get("producer_origin_hint") or "parent_view"
        row["is_unconnected"] = "true" if row.get("connected_other_units") == "NONE" and row.get("connected_tlm_units") == "NONE" else "false"
    else:
        row["edge_role"] = "producer_candidate"
        child_producers = output_units.get(port_name, set())
        child_tlms = output_tlms.get(port_name, set())
        row["source_output_units"] = join_units(child_producers) if child_producers else (row.get("source_output_units") or "NONE")
        row["connected_tlm_units"] = join_units(child_tlms) if child_tlms else (row.get("connected_tlm_units") or "NONE")
        row["producer_origin_hint"] = "drilldown_child_output" if child_producers else (row.get("producer_origin_hint") or "parent_view")
        row["producer_owner_evidence"] = join_units(child_producers) if child_producers else (row.get("producer_owner_evidence") or "NONE")
        row["is_unconnected"] = "true" if row["source_output_units"] == "NONE" else "false"

    if not row.get("source_output_units"):
        row["source_output_units"] = "NONE"
    if not row.get("connected_other_units"):
        row["connected_other_units"] = "NONE"
    if not row.get("connected_tlm_units"):
        row["connected_tlm_units"] = "NONE"
    if not row.get("producer_cluster_owner"):
        row["producer_cluster_owner"] = "NONE"
    if not row.get("producer_origin_hint"):
        row["producer_origin_hint"] = "NONE"
    if not row.get("producer_owner_evidence"):
        row["producer_owner_evidence"] = "NONE"

    reason = "derived_top_interface:parent_plus_drilldown" if base_row else "derived_top_interface:port_decls_only"
    row["query_selection_reason"] = reason
    return row


def main() -> None:
    args = parse_args()
    parent_rows = load_parent_rows(Path(args.parent_io_csv), args.module)
    input_units, input_tlms, output_units, output_tlms = load_drilldown_usage(Path(args.drilldown_io_csv))
    decls = parse_port_decls(Path(args.port_decls))

    # Load parent hierarchy and sibling tables for cross-hierarchy tracing
    xhier_parents: list[tuple[str, dict]] = []
    for spec in args.parent_hierarchy:
        mod, path = spec.split(":", 1)
        xhier_parents.append((mod, load_io_rows_by_port(path)))
        print(f"XHIER_PARENT {mod}: {path}", file=sys.stderr)
    xhier_siblings: dict[str, dict] = {}
    for spec in args.sibling:
        mod, path = spec.split(":", 1)
        xhier_siblings[mod.lower()] = load_io_rows_by_port(path)
        print(f"XHIER_SIBLING {mod}: {path}", file=sys.stderr)

    out_rows = []
    xhier_enriched_out = 0
    xhier_enriched_in = 0
    for decl in decls:
        row = build_output_row(
            module=args.module,
            decl=decl,
            base_row=parent_rows.get(decl["port_name"]),
            input_units=input_units,
            input_tlms=input_tlms,
            output_units=output_units,
            output_tlms=output_tlms,
        )

        # Cross-hierarchy tracing directly on derived rows
        if xhier_parents:
            port = row["port_name"]
            direction = row["port_direction"].lower()
            peers, tiedoff_expr = trace_cross_hierarchy(
                port, direction, xhier_parents, xhier_siblings, args.module,
            )
            if tiedoff_expr:
                row["xhier_tiedoff_expr"] = tiedoff_expr
            if peers:
                if direction == "output":
                    row["xhier_consumers"] = ";".join(peers)
                    # Merge into connected_other_units
                    existing = set()
                    if row["connected_other_units"] and row["connected_other_units"] != "NONE":
                        existing = set(row["connected_other_units"].split(";"))
                    existing.update(peers)
                    existing = _dedup_units(existing)
                    row["connected_other_units"] = ";".join(sorted(existing))
                    xhier_enriched_out += 1
                else:
                    row["xhier_producers"] = ";".join(peers)
                    # xhier uses actual RTL wiring — always prefer over ICF-based sou
                    row["source_output_units"] = ";".join(sorted(peers))
                    xhier_enriched_in += 1

        out_rows.append(row)

    out_path = Path(args.out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"WROTE={out_path}")
    print(f"ROWS={len(out_rows)}")
    print(f"PARENT_ROWS={len(parent_rows)}")
    print(f"DRILLDOWN_INPUT_PORTS={len(input_units)}")
    print(f"DRILLDOWN_OUTPUT_PORTS={len(output_units)}")
    if xhier_parents:
        print(f"XHIER_ENRICHED_OUTPUTS={xhier_enriched_out}")
        print(f"XHIER_ENRICHED_INPUTS={xhier_enriched_in}")


if __name__ == "__main__":
    main()
