#!/usr/bin/env python3
"""Find connected units for a given port name using query connectivity evidence."""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

REQUIRED_QUERY_COLUMNS = {
    "top_module",
    "instance_module",
    "instance_name",
    "port_name",
    "port_direction",
    "connected_expr",
    "normalized_expr",
    "signal_name_normalized",
    "is_unconnected",
    "edge_role",
    "source_line",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port-name", required=True, help="Exact port_name to resolve")
    p.add_argument("--mincols-csv", required=True, help="Input 11_*_thread_tiedoff_mincols.csv")
    p.add_argument("--query-csv", required=True, help="Input 07_*_connectivity_query_view.csv")
    p.add_argument("--out-csv", required=True, help="Output CSV path")
    return p.parse_args()


def as_bool(v: str) -> bool:
    return (v or "").strip().lower() == "true"


def conn_key(row: dict[str, str]) -> str:
    sig = (row.get("signal_name_normalized") or "").strip()
    if sig:
        return f"sig:{sig}"
    norm = (row.get("normalized_expr") or "").strip()
    if norm:
        return f"expr:{norm}"
    return f"raw:{(row.get('connected_expr') or '').strip()}"


def main() -> None:
    args = parse_args()
    mincols_csv = Path(args.mincols_csv)
    query_csv = Path(args.query_csv)
    out_csv = Path(args.out_csv)

    with query_csv.open(newline="") as f:
        q_reader = csv.DictReader(f)
        missing = sorted(REQUIRED_QUERY_COLUMNS - set(q_reader.fieldnames or []))
        if missing:
            raise SystemExit("query-csv missing required columns: " + ", ".join(missing))
        q_rows = list(q_reader)

    by_key: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in q_rows:
        if not as_bool(row.get("is_unconnected", "")):
            by_key[conn_key(row)].append(row)

    with mincols_csv.open(newline="") as f:
        m_reader = csv.DictReader(f)
        min_rows = [r for r in m_reader if (r.get("port_name") or "") == args.port_name]

    if not min_rows:
        raise SystemExit(f"No rows found for port_name={args.port_name} in {mincols_csv}")

    out_fields = list(min_rows[0].keys()) + ["connected_units", "source_output_units_for_input"]
    out_rows = []

    for mrow in min_rows:
        q_matches = [
            q for q in q_rows
            if q.get("top_module", "") == mrow.get("top_module", "")
            and q.get("instance_module", "") == mrow.get("instance_module", "")
            and q.get("port_name", "") == mrow.get("port_name", "")
            and q.get("source_line", "") == mrow.get("source_line", "")
        ]

        connected_units: set[str] = set()
        source_output_units: set[str] = set()

        for qrow in q_matches:
            if as_bool(qrow.get("is_unconnected", "")):
                continue
            peers = by_key.get(conn_key(qrow), [])
            for peer in peers:
                same_endpoint = (
                    peer.get("instance_module", "") == qrow.get("instance_module", "")
                    and peer.get("instance_name", "") == qrow.get("instance_name", "")
                    and peer.get("port_name", "") == qrow.get("port_name", "")
                )
                if same_endpoint:
                    continue

                unit = peer.get("instance_module", "")
                if unit:
                    connected_units.add(unit)

                if (mrow.get("port_direciton", "").lower() == "input") and (
                    peer.get("port_direction", "").lower() in {"output", "inout"}
                    or peer.get("edge_role", "") in {"producer_candidate", "bidirectional"}
                ):
                    source_output_units.add(unit)

        out = dict(mrow)
        out["connected_units"] = ";".join(sorted(connected_units))
        out["source_output_units_for_input"] = ";".join(sorted(source_output_units))
        out_rows.append(out)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fields)
        w.writeheader()
        w.writerows(out_rows)

    print(f"WROTE={out_csv}")
    print(f"ROWS={len(out_rows)}")


if __name__ == "__main__":
    main()
