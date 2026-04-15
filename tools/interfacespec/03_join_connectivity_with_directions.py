#!/usr/bin/env python3
"""Join raw connectivity with module-port directions and classify edge roles.

Inputs:
- connectivity CSV from step 01
- directions CSV from step 02

Outputs:
- typed connectivity CSV with direction and role annotations
- unresolved CSV for mapping failures (missing module file, missing port, unknown direction)
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


VALID_DIRECTIONS = {"input", "output", "inout"}


def parse_bool_str(value: str) -> bool:
    return (value or "").strip().lower() == "true"


def read_directions(directions_csv: Path) -> tuple[dict[tuple[str, str], dict[str, str]], set[str]]:
    direction_map: dict[tuple[str, str], dict[str, str]] = {}
    modules_with_decls: set[str] = set()

    with directions_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"module", "port", "direction", "decl_line", "source_file"}
        fieldnames = set(reader.fieldnames or [])
        missing = required - fieldnames
        if missing:
            raise ValueError(
                "directions CSV missing required columns: " + ", ".join(sorted(missing))
            )

        for row in reader:
            module = (row.get("module") or "").strip()
            port = (row.get("port") or "").strip()
            direction = (row.get("direction") or "").strip().lower()
            if not module or not port:
                continue
            modules_with_decls.add(module)
            key = (module, port)
            if key not in direction_map:
                direction_map[key] = {
                    "direction": direction,
                    "sv_type": (row.get("sv_type") or "").strip(),
                    "packed_width": (row.get("packed_width") or "").strip(),
                    "unpacked_dim": (row.get("unpacked_dim") or "").strip(),
                    "decl_line": (row.get("decl_line") or "").strip(),
                    "source_file": (row.get("source_file") or "").strip(),
                }

    return direction_map, modules_with_decls


def classify_edge_role(direction: str) -> str:
    if direction == "output":
        return "producer_candidate"
    if direction == "input":
        return "consumer_candidate"
    if direction == "inout":
        return "bidirectional_candidate"
    return "unknown"


def unresolved_suggested_action(reason_code: str) -> str:
    if reason_code == "missing_module_port_decls":
        return "check_port_decls"
    if reason_code == "missing_port_in_port_decls":
        return "check_port_decls"
    if reason_code == "unknown_port_direction":
        return "manual_review"
    return "manual_review"


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Join connectivity CSV with direction DB and emit typed/unresolved rows"
    )
    parser.add_argument("--connectivity-csv", required=True, help="Step 01 output CSV")
    parser.add_argument("--directions-csv", required=True, help="Step 02 output CSV")
    parser.add_argument("--out-csv", required=True, help="Typed connectivity CSV output")
    parser.add_argument(
        "--unresolved-csv",
        required=True,
        help="Unresolved mapping rows CSV output",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    connectivity_csv = Path(args.connectivity_csv)
    directions_csv = Path(args.directions_csv)
    out_csv = Path(args.out_csv)
    unresolved_csv = Path(args.unresolved_csv)

    if not connectivity_csv.exists():
        print(f"ERROR: --connectivity-csv not found: {connectivity_csv}", file=sys.stderr)
        sys.exit(1)
    if not directions_csv.exists():
        print(f"ERROR: --directions-csv not found: {directions_csv}", file=sys.stderr)
        sys.exit(1)

    try:
        direction_map, modules_with_decls = read_directions(directions_csv)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    typed_rows: list[dict[str, str]] = []
    unresolved_rows: list[dict[str, str]] = []

    with connectivity_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {
            "cluster",
            "top_module",
            "instance_module",
            "instance_name",
            "port_name",
            "connected_expr",
            "normalized_expr",
            "signal_name_normalized",
            "is_plain_identifier",
            "is_unconnected",
            "is_conditional",
            "guard_expr",
            "source_file",
            "source_line",
        }
        fieldnames = set(reader.fieldnames or [])
        missing = required - fieldnames
        if missing:
            print(
                "ERROR: connectivity CSV missing required columns: "
                + ", ".join(sorted(missing)),
                file=sys.stderr,
            )
            sys.exit(1)

        for row in reader:
            module = (row.get("instance_module") or "").strip()
            port = (row.get("port_name") or "").strip()
            key = (module, port)

            resolved_direction = ""
            direction_decl_line = ""
            direction_source_file = ""
            direction_sv_type = ""
            direction_packed_width = ""
            direction_unpacked_dim = ""
            join_status = "resolved"
            reason_code = ""

            if module not in modules_with_decls:
                join_status = "unresolved"
                reason_code = "missing_module_port_decls"
            elif key not in direction_map:
                join_status = "unresolved"
                reason_code = "missing_port_in_port_decls"
            else:
                resolved_direction = direction_map[key]["direction"]
                direction_sv_type = direction_map[key]["sv_type"]
                direction_packed_width = direction_map[key]["packed_width"]
                direction_unpacked_dim = direction_map[key]["unpacked_dim"]
                direction_decl_line = direction_map[key]["decl_line"]
                direction_source_file = direction_map[key]["source_file"]
                if resolved_direction not in VALID_DIRECTIONS:
                    join_status = "unresolved"
                    reason_code = "unknown_port_direction"

            edge_role = classify_edge_role(resolved_direction) if join_status == "resolved" else "unknown"
            is_expression = str(
                (not parse_bool_str(row.get("is_plain_identifier", "")))
                and (not parse_bool_str(row.get("is_unconnected", "")))
            ).lower()

            typed_row = {
                "cluster": (row.get("cluster") or "").strip(),
                "top_module": (row.get("top_module") or "").strip(),
                "instance_module": module,
                "instance_name": (row.get("instance_name") or "").strip(),
                "port_name": port,
                "port_direction": resolved_direction,
                "direction_sv_type": direction_sv_type,
                "direction_packed_width": direction_packed_width,
                "direction_unpacked_dim": direction_unpacked_dim,
                "edge_role": edge_role,
                "join_status": join_status,
                "join_reason_code": reason_code,
                "connected_expr": (row.get("connected_expr") or "").strip(),
                "normalized_expr": (row.get("normalized_expr") or "").strip(),
                "signal_name_normalized": (row.get("signal_name_normalized") or "").strip(),
                "is_plain_identifier": (row.get("is_plain_identifier") or "").strip().lower(),
                "is_expression": is_expression,
                "is_unconnected": (row.get("is_unconnected") or "").strip().lower(),
                "is_conditional": (row.get("is_conditional") or "").strip().lower(),
                "guard_expr": (row.get("guard_expr") or "").strip(),
                "source_file": (row.get("source_file") or "").strip(),
                "source_line": (row.get("source_line") or "").strip(),
                "direction_source_file": direction_source_file,
                "direction_decl_line": direction_decl_line,
            }
            typed_rows.append(typed_row)

            if join_status == "unresolved":
                unresolved_rows.append(
                    {
                        "record_type": "mapping",
                        "signal_name": (row.get("signal_name_normalized") or "").strip(),
                        "connected_expr": (row.get("connected_expr") or "").strip(),
                        "reason_code": reason_code,
                        "context_module": module,
                        "context_instance": (row.get("instance_name") or "").strip(),
                        "context_port": port,
                        "expected_direction": "unknown",
                        "source_file": (row.get("source_file") or "").strip(),
                        "source_line": (row.get("source_line") or "").strip(),
                        "is_conditional": (row.get("is_conditional") or "").strip().lower(),
                        "guard_expr": (row.get("guard_expr") or "").strip(),
                        "suggested_action": unresolved_suggested_action(reason_code),
                    }
                )

    typed_rows.sort(
        key=lambda row: (
            row["instance_module"],
            row["instance_name"],
            row["port_name"],
            int(row["source_line"]) if row["source_line"].isdigit() else 0,
            row["connected_expr"],
        )
    )
    unresolved_rows.sort(
        key=lambda row: (
            row["reason_code"],
            row["context_module"],
            row["context_instance"],
            row["context_port"],
            int(row["source_line"]) if row["source_line"].isdigit() else 0,
        )
    )

    typed_fieldnames = [
        "cluster",
        "top_module",
        "instance_module",
        "instance_name",
        "port_name",
        "port_direction",
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
    ]
    unresolved_fieldnames = [
        "record_type",
        "signal_name",
        "connected_expr",
        "reason_code",
        "context_module",
        "context_instance",
        "context_port",
        "expected_direction",
        "source_file",
        "source_line",
        "is_conditional",
        "guard_expr",
        "suggested_action",
    ]

    write_csv(out_csv, typed_rows, typed_fieldnames)
    write_csv(unresolved_csv, unresolved_rows, unresolved_fieldnames)

    resolved_count = sum(1 for row in typed_rows if row["join_status"] == "resolved")
    unresolved_count = len(typed_rows) - resolved_count
    producer_count = sum(1 for row in typed_rows if row["edge_role"] == "producer_candidate")
    consumer_count = sum(1 for row in typed_rows if row["edge_role"] == "consumer_candidate")
    bidir_count = sum(1 for row in typed_rows if row["edge_role"] == "bidirectional_candidate")

    print(f"Typed connectivity rows: {len(typed_rows)}")
    print(f"Resolved rows: {resolved_count}")
    print(f"Unresolved rows: {unresolved_count}")
    print(
        "Edge role counts: "
        f"producer={producer_count}, consumer={consumer_count}, bidirectional={bidir_count}"
    )
    print(f"Typed output: {out_csv}")
    print(f"Unresolved output: {unresolved_csv}")


if __name__ == "__main__":
    main()
