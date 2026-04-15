#!/usr/bin/env python3
"""Enrich STSR connected-to-top CSV: resolve NONE source_output_units rows
using cross-hierarchy trace results. Skips scan/clk infrastructure signals."""

import csv
import sys
import shutil

SKIP_PREFIXES = ("fscan", "Mclk", "scan")

# Manually traced cross-cluster mappings for STSR NONE signals
# Format: port_name -> (source_output_units, producer_cluster_owner,
#                       producer_origin_hint, producer_owner_evidence)
ENRICHMENT_MAP = {
    "RoNukeT_M907H": (
        "OOO<-ROB (cross-cluster via FE)",
        "ooo",
        "ROB nuke broadcast: per-thread Reorder Buffer nuke, width [CORE_SMT_NUM_THREADS-1:0]",
        "fe:fe.port_decls.v(input);msid:msid.port_decls.v(input);ooo:fe_tb(output)",
    ),
}


def main():
    if len(sys.argv) < 2:
        print("Usage: enrich_stsr_none_rows.py <csv_path> [--dry-run]")
        sys.exit(1)

    csv_path = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    bak_path = csv_path + ".bak"
    shutil.copy2(csv_path, bak_path)
    print(f"Backup: {bak_path}")

    enriched = 0
    skipped_infra = 0
    still_none = 0
    rows_out = []

    with open(bak_path, newline="") as fin:
        reader = csv.DictReader(fin)
        fieldnames = reader.fieldnames
        for row in reader:
            pn = row["port_name"]
            sou = row["source_output_units"]

            if sou == "NONE":
                if pn.startswith(SKIP_PREFIXES):
                    skipped_infra += 1
                elif pn in ENRICHMENT_MAP:
                    new_sou, owner, hint, evidence = ENRICHMENT_MAP[pn]
                    row["source_output_units"] = new_sou
                    row["producer_cluster_owner"] = owner
                    row["producer_origin_hint"] = hint
                    row["producer_owner_evidence"] = evidence
                    enriched += 1
                else:
                    still_none += 1

            rows_out.append(row)

    if not dry_run:
        with open(csv_path, "w", newline="") as fout:
            writer = csv.DictWriter(fout, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows_out)
        print(f"Wrote: {csv_path}")
    else:
        print("[DRY RUN] No file written.")

    print(f"Enriched:       {enriched}")
    print(f"Skipped (infra):{skipped_infra}")
    print(f"Still NONE:     {still_none}")


if __name__ == "__main__":
    main()
