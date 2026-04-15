#!/usr/bin/env python3
"""Augment FE/MSID IO tables with cross-cluster unit connectivity via fe_te.v."""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


IDENTIFIER_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_$]*)\b")
LITERAL_RE = re.compile(r"\b\d+'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ_]+\b|'[01xXzZ]")
PORT_DECL_RE = re.compile(r"^\s*(input|output|inout)\b(?P<body>[^;]*);")
TRAILING_DIMS_RE = re.compile(r"\s*(\[[^\]]+\]\s*)*$")
SIGNAL_NAME_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_$]*)$")
INSTANCE_START_RE = {
    "fe": re.compile(r"^\s*fe\s+fe\s*\("),
    "msid": re.compile(r"^\s*msid\s+msid\s*\("),
}
INSTANCE_BIND_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*?)\)\s*,?")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-v", required=True)
    parser.add_argument("--fe-io-csv", required=True)
    parser.add_argument("--msid-io-csv", required=True)
    parser.add_argument("--fe-port-decls", required=True)
    parser.add_argument("--msid-port-decls", required=True)
    parser.add_argument("--out-missing-csv", required=True)
    return parser.parse_args()


def strip_line_comment(line: str) -> str:
    if "//" not in line:
        return line
    return line.split("//", 1)[0]


def extract_identifiers(expr: str) -> list[str]:
    if not expr:
        return []
    stripped = LITERAL_RE.sub(" ", expr)
    return sorted(set(IDENTIFIER_RE.findall(stripped)))


def extract_signal_name_from_decl_body(body: str) -> str | None:
    no_dims = TRAILING_DIMS_RE.sub("", body)
    match = SIGNAL_NAME_RE.search(no_dims.strip())
    if not match:
        return None
    return match.group(1)


def load_top_port_directions(path: Path) -> dict[str, str]:
    port_directions: dict[str, str] = {}
    for raw_line in path.read_text(errors="ignore").splitlines():
        line = strip_line_comment(raw_line).strip()
        if not line or line.startswith("`"):
            continue
        match = PORT_DECL_RE.match(line)
        if not match:
            continue
        signal_name = extract_signal_name_from_decl_body(match.group("body"))
        if signal_name:
            port_directions[signal_name] = match.group(1)
    return port_directions


def get_row_identifiers(row: dict[str, str]) -> list[str]:
    if (row.get("is_plain_identifier") or "").strip().lower() == "true":
        signal_name = (row.get("signal_name_normalized") or "").strip()
        if signal_name:
            return [signal_name]
    return extract_identifiers((row.get("connected_expr") or "").strip())


def get_top_port_matches(row: dict[str, str], top_ports: dict[str, str]) -> list[str]:
    return sorted({identifier for identifier in get_row_identifiers(row) if identifier in top_ports})


def parse_instance_bindings(path: Path, cluster: str) -> dict[str, str]:
    lines = path.read_text(errors="ignore").splitlines()
    start = None
    for index, line in enumerate(lines):
        if INSTANCE_START_RE[cluster].match(line):
            start = index + 1
            break
    if start is None:
        raise RuntimeError(f"could not find {cluster} instance in {path}")

    bindings: dict[str, str] = {}
    for line in lines[start:]:
        if re.match(r"^\s*\);\s*$", line):
            break
        match = INSTANCE_BIND_RE.search(strip_line_comment(line))
        if not match:
            continue
        port_name = match.group(1)
        expr = match.group(2).strip()
        identifiers = extract_identifiers(expr)
        if len(identifiers) == 1:
            bindings[port_name] = identifiers[0]
    return bindings


def parse_units(value: str) -> set[str]:
    value = (value or "").strip()
    if not value or value == "NONE":
        return set()
    return {item for item in value.split(";") if item}


def join_units(units: set[str]) -> str:
    return ";".join(sorted(units)) if units else "NONE"


def build_top_port_index(rows: list[dict[str, str]], top_ports: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    index: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if (row.get("is_connected_to_top") or "").strip().lower() != "true":
            continue
        for port in get_top_port_matches(row, top_ports):
            index[port].append(row)
    return index


def build_boundary_map(bridge_v: Path) -> dict[tuple[str, str], list[dict[str, str]]]:
    fe_bindings = parse_instance_bindings(bridge_v, "fe")
    msid_bindings = parse_instance_bindings(bridge_v, "msid")

    fe_by_net: dict[str, list[str]] = defaultdict(list)
    msid_by_net: dict[str, list[str]] = defaultdict(list)
    for port, net in fe_bindings.items():
        fe_by_net[net].append(port)
    for port, net in msid_bindings.items():
        msid_by_net[net].append(port)

    boundary_map: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for net in sorted(set(fe_by_net) & set(msid_by_net)):
        for fe_port in fe_by_net[net]:
            for msid_port in msid_by_net[net]:
                boundary_map[("fe", fe_port)].append(
                    {"remote_cluster": "msid", "remote_port": msid_port, "net": net}
                )
                boundary_map[("msid", msid_port)].append(
                    {"remote_cluster": "fe", "remote_port": fe_port, "net": net}
                )
    return boundary_map


def augment_rows(
    cluster: str,
    rows: list[dict[str, str]],
    local_top_ports: dict[str, str],
    remote_rows: list[dict[str, str]],
    remote_top_ports: dict[str, str],
    boundary_map: dict[tuple[str, str], list[dict[str, str]]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    remote_index = build_top_port_index(remote_rows, remote_top_ports)
    augmented_rows: list[dict[str, str]] = []
    missing_rows: list[dict[str, str]] = []

    for row in rows:
        out_row = dict(row)
        source_units = parse_units(row.get("source_output_units", ""))
        other_units = parse_units(row.get("connected_other_units", ""))

        if (row.get("is_connected_to_top") or "").strip().lower() == "true":
            matched_ports = get_top_port_matches(row, local_top_ports)
            for local_top_port in matched_ports:
                remote_links = boundary_map.get((cluster, local_top_port), [])
                if not remote_links:
                    continue

                found_remote_unit = False
                for remote_link in remote_links:
                    remote_port = remote_link["remote_port"]
                    remote_cluster = remote_link["remote_cluster"]
                    remote_port_rows = remote_index.get(remote_port, [])
                    if not remote_port_rows:
                        missing_rows.append(
                            {
                                "cluster": cluster,
                                "instance_module": row.get("instance_module", ""),
                                "instance_name": row.get("instance_name", ""),
                                "port_name": row.get("port_name", ""),
                                "local_top_port": local_top_port,
                                "net": remote_link["net"],
                                "remote_cluster": remote_cluster,
                                "remote_top_port": remote_port,
                                "reason": "no_remote_io_rows_for_boundary_port",
                            }
                        )
                        continue

                    for remote_row in remote_port_rows:
                        remote_unit = (remote_row.get("instance_module") or "").strip()
                        remote_dir = (remote_row.get("port_direction") or "").strip()
                        if not remote_unit:
                            continue
                        found_remote_unit = True
                        if remote_dir == "output":
                            source_units.add(remote_unit)
                        elif remote_dir in ("input", "inout"):
                            other_units.add(remote_unit)

                if not found_remote_unit:
                    missing_rows.append(
                        {
                            "cluster": cluster,
                            "instance_module": row.get("instance_module", ""),
                            "instance_name": row.get("instance_name", ""),
                            "port_name": row.get("port_name", ""),
                            "local_top_port": local_top_port,
                            "net": remote_links[0]["net"],
                            "remote_cluster": remote_links[0]["remote_cluster"],
                            "remote_top_port": remote_links[0]["remote_port"],
                            "reason": "boundary_mapped_but_no_remote_units",
                        }
                    )

        out_row["source_output_units"] = join_units(source_units)
        out_row["connected_other_units"] = join_units(other_units)
        augmented_rows.append(out_row)

    return augmented_rows, missing_rows


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()

    fe_io_path = Path(args.fe_io_csv)
    msid_io_path = Path(args.msid_io_csv)
    bridge_v = Path(args.bridge_v)
    fe_port_decls = Path(args.fe_port_decls)
    msid_port_decls = Path(args.msid_port_decls)
    out_missing_csv = Path(args.out_missing_csv)

    fe_rows, fe_fields = read_csv(fe_io_path)
    msid_rows, msid_fields = read_csv(msid_io_path)
    fe_top_ports = load_top_port_directions(fe_port_decls)
    msid_top_ports = load_top_port_directions(msid_port_decls)
    boundary_map = build_boundary_map(bridge_v)

    augmented_fe_rows, fe_missing = augment_rows(
        "fe", fe_rows, fe_top_ports, msid_rows, msid_top_ports, boundary_map
    )
    augmented_msid_rows, msid_missing = augment_rows(
        "msid", msid_rows, msid_top_ports, fe_rows, fe_top_ports, boundary_map
    )

    write_csv(fe_io_path, augmented_fe_rows, fe_fields)
    write_csv(msid_io_path, augmented_msid_rows, msid_fields)

    missing_fields = [
        "cluster",
        "instance_module",
        "instance_name",
        "port_name",
        "local_top_port",
        "net",
        "remote_cluster",
        "remote_top_port",
        "reason",
    ]
    missing_rows = fe_missing + msid_missing
    write_csv(out_missing_csv, missing_rows, missing_fields)

    print(f"UPDATED_FE={fe_io_path}")
    print(f"UPDATED_MSID={msid_io_path}")
    print(f"WROTE_MISSING={out_missing_csv}")
    print(f"FE_MISSING_ROWS={len(fe_missing)}")
    print(f"MSID_MISSING_ROWS={len(msid_missing)}")


if __name__ == "__main__":
    main()