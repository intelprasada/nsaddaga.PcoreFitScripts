---
name: interface-spec
description: >
  Generate an interface specification for any RTL module in the GFC repository.
  Covers the full pipeline from connectivity extraction through cross-hierarchy
  enrichment to final interface spec markdown generation. Works for any unit
  (STSR, IDQ, IFU, DSBE, etc.) within any cluster (FE, MSID, OOO, EXE, MLC).
metadata:
  owner: Navadeep Saddaga
  owner_linux_name: nsaddaga
  ai_note: Written with assistance from Claude Opus 4.6
---

## Scope

Use this skill when asked to:

- Generate an interface specification for a module
- Run the InterfaceSpec pipeline for a unit
- Classify signals by connected external unit
- Identify cross-cluster signal sources/consumers for a module
- Produce a summarized interface document from RTL connectivity data

## Prerequisites

Before generating an interface spec, a pipeline run must exist for both
the target module and its parent cluster. If outputs do not exist, run
the pipeline first.

Required artifacts (per module):

| Artifact | Step | Description |
|----------|------|-------------|
| `07_<cluster>_connectivity_query_view.csv` | 07 | Guard-biased query view |
| `12_<parent>_query_io_table.csv` | 12 | Parent cluster query I/O table |
| `12_<grandparent>_query_io_table.csv` | 12 | Grandparent cluster query I/O table (for parent enrichment) |
| `19_<parent>_query_io_table_xhier.csv` | 19 | Enriched parent query I/O table (used to extract step 18 module top I/O) |
| `17_<module>_connected_to_top_rows.csv` | 17 | Module top-interface rows with xhier enrichment |

## Step-by-Step Workflow

### Step 1: Determine Module and Parent Cluster

Map the user's unit of interest to its parent cluster and RTL paths:

| Unit | Parent | Grandparent | top_v | gen_dir |
|------|--------|-------------|-------|---------|
| stsr | msid | fe | `target/fe/gen/stsr.v` | `target/fe/gen` |
| idq | msid | fe | `target/fe/gen/idq.v` | `target/fe/gen` |
| ifu | fe | icore | `target/fe/gen/ifu.v` | `target/fe/gen` |
| dsbe | msid | fe | `target/fe/gen/dsbe.v` | `target/fe/gen` |
| bpu | fe | icore | `target/fe/gen/bpu.v` | `target/fe/gen` |
| bac | fe | icore | `target/fe/gen/bac.v` | `target/fe/gen` |
| rat | ooo | icore | `target/ooo/gen/rat.v` | `target/ooo/gen` |

For units not listed, check `target/<cluster>/gen/<module>.v`.
MSID and FE units share `target/fe/gen/`.

### Step 2: Run Triple-Pipeline Extraction

Run the **module**, **parent**, and **grandparent** pipelines:

```tcsh
set ts=`date +%Y%m%d_%H%M%S`

# Module pipeline (unit of interest)
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster <module> --top-v <module_top_v> --gen-dir <gen_dir> \
  --ts "$ts"

# Parent cluster pipeline
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster <parent> --top-v <parent_top_v> --gen-dir <gen_dir> \
  --ts "$ts"

# Grandparent cluster pipeline (needed to enrich the parent)
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster <grandparent> --top-v <grandparent_top_v> --gen-dir <gp_gen_dir> \
  --ts "$ts"
```

### Step 2b: Enrich Parent I/O Table Using Grandparent

The parent's raw `12_*` may have `source_output_units = NONE` for inputs
that come from outside the parent cluster. Enrich it before extracting
the child module rows:

```tcsh
python3 scripts/interfacespec/enrich_cross_hierarchy.py \
  --io-csv <parent_run>/results/12_<parent>_query_io_table.csv \
  --module <parent> \
  --parent <grandparent>:<gp_run>/results/12_<grandparent>_query_io_table.csv \
  --out-csv <parent_run>/results/19_<parent>_query_io_table_xhier.csv
```

### Step 2c: Extract Module Top I/O from Enriched Parent

The module is the top of its own pipeline, so its own `12_*` has no
`instance_module` rows for itself. Extract step 18 from the **enriched
parent** `19_*_query_io_table_xhier.csv`:

```tcsh
python3 scripts/interfacespec/extract_module_top_io_table.py \
  --io-csv <parent_run>/results/19_<parent>_query_io_table_xhier.csv \
  --module <module> \
  --port-decls <gen_dir>/<module>.port_decls.v \
  --out-csv <module_run>/results/18_<module>_top_io_table.csv
```

This ensures the extracted child I/O table carries `xhier_producers`
and `xhier_consumers` columns from the parent enrichment, resolving
NONE entries for signals that originate outside the parent cluster.

### Step 3: Cross-Hierarchy Enrichment

Enrich the module I/O table with cross-hierarchy producer/consumer info
from the parent cluster:

```tcsh
python3 scripts/interfacespec/enrich_cross_hierarchy.py \
  --io-csv <module_run>/results/18_<module>_top_io_table.csv \
  --module <module> \
  --parent <parent>:<parent_run>/results/07_<parent>_connectivity_query_view.csv \
  --parent <parent>:<parent_run>/results/12_<parent>_query_io_table.csv \
  --out-csv <module_run>/results/20_<module>_pseudo_top_enriched.csv
```

### Step 4: Generate Interface Specification

Use the enriched `18_*` table (which has parent-level consumer/producer
info and xhier columns) as input:

```tcsh
python3 scripts/interfacespec/generate_interface_spec.py \
  --io-csv <module_run>/results/18_<module>_top_io_table.csv \
  --module <module> \
  --cluster <parent_cluster> \
  --subgroup-threshold 10 \
  --out-md <module_run>/results/<module>_interface_spec.md
```

### Step 5: Review Output

The generated markdown contains:

- **Interface Summary** table with signal counts per interface group
- **Per-interface detail** sections with signal tables
- **Sub-grouped** large interfaces by primary internal consumer
- **Special categories** (Clocks, Scan/DFT, Control Registers, Power
  Overrides, LSD, CDT) listed first, never sub-grouped

## Classification Rules Reference

Signal classification follows
[interface_spec_rules.md](interface_spec_rules.md). Key rules:

1. **Rule 1**: Group by external connected unit (`sou` for inputs,
   `xhier_consumers` for outputs)
2. **Rule 2**: Special categories override unit grouping (Clocks,
   Scan/DFT, CR, Power, LSD, CDT)
3. **Rule 5**: Sub-group large interfaces by primary internal consumer
4. **Rule 8**: Prefer consumers NOT ending in `_emu`, `_inst`, `_tlm`

## Scripts Reference

| Script | Purpose | Status |
|--------|---------|--------|
| `run_cluster_pipeline.py` | Run full extraction pipeline for any cluster | Mature |
| `generate_interface_spec.py` | Generate interface spec from connected-to-top CSV | Mature |
| `enrich_cross_hierarchy.py` | Cross-hierarchy producer/consumer resolution | Mature |
| `generate_module_io_table.py` | Module I/O table with unit columns | Mature |
| `interface_spec_rules.md` | Classification rules document | Reference |

**Do not use** the following (not mature):

- `generate_interface_spec_from_io_table.py` — ML-based, experimental
- `unsupervised_signal_classifier.py` — unsupervised clustering, experimental
- `generate_signal_descriptions.py` — NL descriptions, FE-biased

## Concrete Example: STSR

STSR sits inside MSID, which sits inside FE. The hierarchy is:
STSR → MSID (parent) → FE (grandparent).

```tcsh
set ts=`date +%Y%m%d_%H%M%S`

# 1. STSR pipeline (module)
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster stsr --top-v target/fe/gen/stsr.v --gen-dir target/fe/gen \
  --ts "$ts"

# 2. MSID pipeline (parent)
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster msid --top-v target/fe/gen/msid.v --gen-dir target/fe/gen \
  --ts "$ts"

# 3. FE pipeline (grandparent — needed to enrich MSID)
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster fe --top-v target/fe/gen/fe.v --gen-dir target/fe/gen \
  --ts "$ts"

# 4. Enrich MSID using FE
python3 scripts/interfacespec/enrich_cross_hierarchy.py \
  --io-csv target/fe/gen/InterfaceSpecAgent/msid_pipeline_${ts}/results/12_msid_query_io_table.csv \
  --module msid \
  --parent fe:target/fe/gen/InterfaceSpecAgent/fe_pipeline_${ts}/results/12_fe_query_io_table.csv \
  --out-csv target/fe/gen/InterfaceSpecAgent/msid_pipeline_${ts}/results/19_msid_query_io_table_xhier.csv

# 5. Extract STSR top I/O from enriched MSID
python3 scripts/interfacespec/extract_module_top_io_table.py \
  --io-csv target/fe/gen/InterfaceSpecAgent/msid_pipeline_${ts}/results/19_msid_query_io_table_xhier.csv \
  --module stsr \
  --port-decls target/fe/gen/stsr.port_decls.v \
  --out-csv target/fe/gen/InterfaceSpecAgent/stsr_pipeline_${ts}/results/18_stsr_top_io_table.csv

# 6. Enrich STSR using MSID
python3 scripts/interfacespec/enrich_cross_hierarchy.py \
  --io-csv target/fe/gen/InterfaceSpecAgent/stsr_pipeline_${ts}/results/18_stsr_top_io_table.csv \
  --module stsr \
  --parent msid:target/fe/gen/InterfaceSpecAgent/msid_pipeline_${ts}/results/07_msid_connectivity_query_view.csv \
  --parent msid:target/fe/gen/InterfaceSpecAgent/msid_pipeline_${ts}/results/12_msid_query_io_table.csv \
  --out-csv target/fe/gen/InterfaceSpecAgent/stsr_pipeline_${ts}/results/20_stsr_pseudo_top_enriched.csv

# 7. Generate spec
python3 scripts/interfacespec/generate_interface_spec.py \
  --io-csv target/fe/gen/InterfaceSpecAgent/stsr_pipeline_${ts}/results/18_stsr_top_io_table.csv \
  --module stsr --cluster MSID
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Duplicate signal rows in output | Script deduplicates by `(port_name, port_direction)` — merges `connected_other_units` |
| `fscan_*` signals in Clocks | Fixed: Scan/DFT check runs before Clock check |
| All outputs show "Unknown" | Ensure `18_*_top_io_table.csv` has `source_output_units` and `connected_other_units` populated from parent enrichment |
| NONE in `source_output_units` | Enrich the parent using grandparent (step 2b), then re-extract child from enriched `19_*` |
| Empty `18_*_top_io_table.csv` | The module is the top of its own pipeline — extract from the **enriched parent** `19_*_query_io_table_xhier.csv` (step 2c) |
| Missing `xhier_producers`/`xhier_consumers` in `18_*` | Parent was not enriched before extraction — run step 2b then re-extract |
| Too many small interfaces | Increase `--subgroup-threshold` to merge small groups |
