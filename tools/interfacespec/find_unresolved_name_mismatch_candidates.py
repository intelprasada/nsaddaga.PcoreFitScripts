#!/usr/bin/env python3
"""Find likely naming mismatches for unresolved I/O rows.

This script scans rows where `source_output_units` or `connected_other_units`
is `NONE` and looks for potential naming mistakes:

1. Case-only mismatches (same name ignoring case)
2. Missing/extra 'T' mismatches (same name after removing 't'/'T')

It is intentionally diagnostic only: it does not modify or auto-resolve rows.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


def normalize(value: str) -> str:
    return (value or "").strip()


def remove_t(value: str) -> str:
    return normalize(value).lower().replace("t", "")


def is_none(value: str) -> bool:
    return normalize(value).upper() == "NONE"


def is_case_only_mismatch(lhs: str, rhs: str) -> bool:
    a = normalize(lhs)
    b = normalize(rhs)
    return bool(a and b and a != b and a.lower() == b.lower())


def is_t_only_mismatch(lhs: str, rhs: str) -> bool:
    a = normalize(lhs)
    b = normalize(rhs)
    if not a or not b:
        return False
    if a.lower() == b.lower():
        return False
    return remove_t(a) == remove_t(b)


def load_rows(csv_path: Path, source_label: str) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = []
        for idx, row in enumerate(reader, start=2):
            out = dict(row)
            out["__row_number"] = str(idx)
            out["__source_label"] = source_label
            out["__source_path"] = str(csv_path)
            rows.append(out)
        return rows


def parse_ref(spec: str) -> tuple[str, Path]:
    if ":" not in spec:
        raise ValueError(f"Invalid --reference format: {spec}. Expected LABEL:CSV_PATH")
    label, path = spec.split(":", 1)
    if not label.strip() or not path.strip():
        raise ValueError(f"Invalid --reference format: {spec}. Expected LABEL:CSV_PATH")
    return label.strip(), Path(path.strip())


def direction_rank(unresolved_direction: str, candidate_direction: str) -> int:
    unresolved = normalize(unresolved_direction).lower()
    candidate = normalize(candidate_direction).lower()
    if unresolved == "input" and candidate == "output":
        return 0
    if unresolved == "output" and candidate == "input":
        return 0
    if candidate in ("inout",):
        return 1
    return 2


def resolved_signal_rank(row: dict[str, str]) -> int:
    score = 0
    if not is_none(row.get("source_output_units", "NONE")):
        score += 1
    if not is_none(row.get("connected_other_units", "NONE")):
        score += 1
    if not is_none(row.get("producer_cluster_owner", "NONE")):
        score += 1
    return -score


def collect_candidates(
    unresolved_row: dict[str, str],
    candidate_rows: list[dict[str, str]],
    max_candidates_per_type: int,
) -> list[dict[str, str]]:
    query_port = normalize(unresolved_row.get("port_name", ""))
    if not query_port:
        return []

    unresolved_dir = unresolved_row.get("port_direction", "")
    unresolved_row_id = (
        unresolved_row.get("__source_label", ""),
        unresolved_row.get("__row_number", ""),
        unresolved_row.get("port_name", ""),
    )

    case_only: list[dict[str, str]] = []
    t_only: list[dict[str, str]] = []

    for cand in candidate_rows:
        cand_id = (
            cand.get("__source_label", ""),
            cand.get("__row_number", ""),
            cand.get("port_name", ""),
        )
        if cand_id == unresolved_row_id:
            continue

        candidate_port = normalize(cand.get("port_name", ""))
        if not candidate_port:
            continue

        if is_case_only_mismatch(query_port, candidate_port):
            case_only.append(cand)
            continue
        if is_t_only_mismatch(query_port, candidate_port):
            t_only.append(cand)

    def sort_key(cand: dict[str, str]) -> tuple[int, int, str, str, str]:
        return (
            direction_rank(unresolved_dir, cand.get("port_direction", "")),
            resolved_signal_rank(cand),
            normalize(cand.get("__source_label", "")),
            normalize(cand.get("instance_module", "")),
            normalize(cand.get("port_name", "")),
        )

    case_only.sort(key=sort_key)
    t_only.sort(key=sort_key)

    out_rows: list[dict[str, str]] = []
    for match_type, group in (("case_only", case_only), ("missing_or_extra_T", t_only)):
        for cand in group[:max_candidates_per_type]:
            unresolved_fields = []
            if is_none(unresolved_row.get("source_output_units", "NONE")):
                unresolved_fields.append("source_output_units")
            if is_none(unresolved_row.get("connected_other_units", "NONE")):
                unresolved_fields.append("connected_other_units")

            out_rows.append(
                {
                    "unresolved_source": unresolved_row.get("__source_label", ""),
                    "unresolved_source_path": unresolved_row.get("__source_path", ""),
                    "unresolved_row_number": unresolved_row.get("__row_number", ""),
                    "unresolved_cluster": unresolved_row.get("cluster", ""),
                    "unresolved_instance_module": unresolved_row.get("instance_module", ""),
                    "unresolved_instance_name": unresolved_row.get("instance_name", ""),
                    "unresolved_port_name": query_port,
                    "unresolved_port_direction": unresolved_row.get("port_direction", ""),
                    "unresolved_fields": ";".join(unresolved_fields) if unresolved_fields else "NONE",
                    "unresolved_source_output_units": unresolved_row.get("source_output_units", ""),
                    "unresolved_connected_other_units": unresolved_row.get("connected_other_units", ""),
                    "candidate_match_type": match_type,
                    "candidate_source": cand.get("__source_label", ""),
                    "candidate_source_path": cand.get("__source_path", ""),
                    "candidate_row_number": cand.get("__row_number", ""),
                    "candidate_cluster": cand.get("cluster", ""),
                    "candidate_instance_module": cand.get("instance_module", ""),
                    "candidate_instance_name": cand.get("instance_name", ""),
                    "candidate_port_name": cand.get("port_name", ""),
                    "candidate_port_direction": cand.get("port_direction", ""),
                    "candidate_source_output_units": cand.get("source_output_units", ""),
                    "candidate_connected_other_units": cand.get("connected_other_units", ""),
                    "candidate_producer_cluster_owner": cand.get("producer_cluster_owner", ""),
                    "candidate_producer_owner_evidence": cand.get("producer_owner_evidence", ""),
                }
            )

    return out_rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Investigate unresolved NONE rows for likely case-only and missing/extra T name mismatches"
        )
    )
    parser.add_argument("--io-csv", required=True, help="Target I/O CSV to analyze")
    parser.add_argument("--out-csv", required=True, help="Output CSV for mismatch candidates")
    parser.add_argument(
        "--reference",
        action="append",
        default=[],
        metavar="LABEL:CSV",
        help="Optional reference CSV to search for candidates (repeatable)",
    )
    parser.add_argument(
        "--max-candidates-per-type",
        type=int,
        default=5,
        help="Maximum number of candidates per unresolved row per match type",
    )
    args = parser.parse_args()

    io_path = Path(args.io_csv)
    out_path = Path(args.out_csv)
    if not io_path.exists():
        raise SystemExit(f"ERROR: io-csv not found: {io_path}")

    target_rows = load_rows(io_path, "target")
    all_candidate_rows = list(target_rows)

    for spec in args.reference:
        label, ref_path = parse_ref(spec)
        if not ref_path.exists():
            raise SystemExit(f"ERROR: reference CSV not found: {ref_path}")
        all_candidate_rows.extend(load_rows(ref_path, label))

    unresolved_rows = [
        row
        for row in target_rows
        if is_none(row.get("source_output_units", "NONE"))
        or is_none(row.get("connected_other_units", "NONE"))
    ]

    output_rows: list[dict[str, str]] = []
    for unresolved in unresolved_rows:
        output_rows.extend(
            collect_candidates(
                unresolved_row=unresolved,
                candidate_rows=all_candidate_rows,
                max_candidates_per_type=max(1, args.max_candidates_per_type),
            )
        )

    fieldnames = [
        "unresolved_source",
        "unresolved_source_path",
        "unresolved_row_number",
        "unresolved_cluster",
        "unresolved_instance_module",
        "unresolved_instance_name",
        "unresolved_port_name",
        "unresolved_port_direction",
        "unresolved_fields",
        "unresolved_source_output_units",
        "unresolved_connected_other_units",
        "candidate_match_type",
        "candidate_source",
        "candidate_source_path",
        "candidate_row_number",
        "candidate_cluster",
        "candidate_instance_module",
        "candidate_instance_name",
        "candidate_port_name",
        "candidate_port_direction",
        "candidate_source_output_units",
        "candidate_connected_other_units",
        "candidate_producer_cluster_owner",
        "candidate_producer_owner_evidence",
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"INPUT_ROWS={len(target_rows)}")
    print(f"UNRESOLVED_ROWS={len(unresolved_rows)}")
    print(f"MISMATCH_CANDIDATES={len(output_rows)}")
    print(f"WROTE={out_path}")


if __name__ == "__main__":
    main()
