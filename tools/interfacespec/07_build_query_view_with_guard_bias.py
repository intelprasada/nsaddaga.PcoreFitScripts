#!/usr/bin/env python3
"""Build a query-friendly connectivity view with guard bias and anomaly export.

This script treats repeated instance/port rows as guard alternatives (not strict
"duplicates"). It selects one preferred row per (instance_module, instance_name,
port_name) key for query workflows, while exporting all multi-row keys as
anomalies for audit.

Selection bias:
1) Prefer rows whose guard expression contains a preferred token (default:
   INTEL_INST_ON).
2) If multiple candidates remain, prefer the candidate with the most specific
   guard expression (longer string length).
3) If still tied, pick the earliest source_line.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path


def parse_int(value: str) -> int:
    value = (value or "").strip()
    return int(value) if value.isdigit() else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create guard-biased query view and anomaly CSV from typed connectivity"
    )
    parser.add_argument("--typed-csv", required=True, help="Input typed connectivity CSV")
    parser.add_argument("--out-query-csv", required=True, help="Output query view CSV")
    parser.add_argument("--out-anomalies-csv", required=True, help="Output anomaly CSV")
    parser.add_argument(
        "--preferred-guard-token",
        default="INTEL_INST_ON",
        help="Token to prioritize when selecting among guard alternatives",
    )
    return parser.parse_args()


def choose_preferred_row(rows: list[dict[str, str]], preferred_token: str) -> tuple[dict[str, str], str]:
    if preferred_token:
        positive_pattern = re.compile(rf"(?<![A-Za-z0-9_$!]){re.escape(preferred_token)}(?![A-Za-z0-9_$])")
        negated_pattern = re.compile(rf"!\s*{re.escape(preferred_token)}(?![A-Za-z0-9_$])")
        positive_preferred = [
            row
            for row in rows
            if positive_pattern.search((row.get("guard_expr") or "").strip())
            and not negated_pattern.search((row.get("guard_expr") or "").strip())
        ]
        fallback_preferred = [
            row
            for row in rows
            if positive_pattern.search((row.get("guard_expr") or "").strip())
        ]
        preferred = positive_preferred if positive_preferred else fallback_preferred
    else:
        preferred = []

    if len(preferred) == 1:
        return preferred[0], f"preferred_guard_token:{preferred_token}"

    candidate_rows = preferred if preferred else rows

    # Prefer more specific guard expression, then earliest source line.
    candidate_rows = sorted(
        candidate_rows,
        key=lambda row: (
            -len((row.get("guard_expr") or "").strip()),
            parse_int(row.get("source_line") or ""),
        ),
    )

    if preferred:
        return candidate_rows[0], f"preferred_guard_token_tiebreak:{preferred_token}"
    return candidate_rows[0], "no_preferred_guard_token_match"


def main() -> None:
    args = parse_args()
    typed_csv = Path(args.typed_csv)
    out_query_csv = Path(args.out_query_csv)
    out_anomalies_csv = Path(args.out_anomalies_csv)
    preferred_token = (args.preferred_guard_token or "").strip()

    if not typed_csv.exists():
        print(f"ERROR: --typed-csv not found: {typed_csv}", file=sys.stderr)
        sys.exit(1)

    groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    original_fieldnames: list[str] = []

    with typed_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        original_fieldnames = list(reader.fieldnames or [])
        required = {
            "instance_module",
            "instance_name",
            "port_name",
            "guard_expr",
            "connected_expr",
            "source_file",
            "source_line",
        }
        missing = required - set(original_fieldnames)
        if missing:
            print(
                "ERROR: typed CSV missing required columns: " + ", ".join(sorted(missing)),
                file=sys.stderr,
            )
            sys.exit(1)

        for row in reader:
            key = (
                (row.get("instance_module") or "").strip(),
                (row.get("instance_name") or "").strip(),
                (row.get("port_name") or "").strip(),
            )
            groups[key].append(row)

    query_rows: list[dict[str, str]] = []
    anomaly_rows: list[dict[str, str | int]] = []

    for key, rows in groups.items():
        chosen, reason = choose_preferred_row(rows, preferred_token)

        out_row = dict(chosen)
        out_row["query_selection_reason"] = reason
        out_row["guard_alternative_count"] = str(len(rows))
        query_rows.append(out_row)

        if len(rows) > 1:
            guards = sorted({(r.get("guard_expr") or "").strip() for r in rows})
            exprs = sorted({(r.get("connected_expr") or "").strip() for r in rows})
            locations = sorted(
                {
                    f"{(r.get('source_file') or '').strip()}:{(r.get('source_line') or '').strip()}"
                    for r in rows
                }
            )
            anomaly_rows.append(
                {
                    "instance_module": key[0],
                    "instance_name": key[1],
                    "port_name": key[2],
                    "alternative_count": len(rows),
                    "chosen_guard_expr": (chosen.get("guard_expr") or "").strip(),
                    "chosen_connected_expr": (chosen.get("connected_expr") or "").strip(),
                    "selection_reason": reason,
                    "all_guard_exprs": " | ".join(guards),
                    "all_connected_exprs": " | ".join(exprs),
                    "source_locations": " | ".join(locations),
                }
            )

    query_rows.sort(
        key=lambda row: (
            (row.get("instance_module") or "").strip(),
            (row.get("instance_name") or "").strip(),
            (row.get("port_name") or "").strip(),
            parse_int(row.get("source_line") or ""),
        )
    )
    anomaly_rows.sort(
        key=lambda row: (
            str(row["instance_module"]),
            str(row["instance_name"]),
            str(row["port_name"]),
        )
    )

    out_query_csv.parent.mkdir(parents=True, exist_ok=True)
    query_fieldnames = list(original_fieldnames) + [
        "query_selection_reason",
        "guard_alternative_count",
    ]
    with out_query_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=query_fieldnames)
        writer.writeheader()
        writer.writerows(query_rows)

    out_anomalies_csv.parent.mkdir(parents=True, exist_ok=True)
    anomaly_fieldnames = [
        "instance_module",
        "instance_name",
        "port_name",
        "alternative_count",
        "chosen_guard_expr",
        "chosen_connected_expr",
        "selection_reason",
        "all_guard_exprs",
        "all_connected_exprs",
        "source_locations",
    ]
    with out_anomalies_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=anomaly_fieldnames)
        writer.writeheader()
        writer.writerows(anomaly_rows)

    print(f"WROTE_QUERY_VIEW={out_query_csv}")
    print(f"QUERY_ROWS={len(query_rows)}")
    print(f"WROTE_ANOMALIES={out_anomalies_csv}")
    print(f"ANOMALY_KEYS={len(anomaly_rows)}")
    print(f"PREFERRED_GUARD_TOKEN={preferred_token}")


if __name__ == "__main__":
    main()
