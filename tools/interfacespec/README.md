# InterfaceSpec Scripts README

This directory contains a small pipeline for extracting instance connectivity, module port directions, typed connectivity, and module-level I/O summary tables from generated RTL.

This README is written for both humans and AI agents. If you are an AI agent, use the routing rules and command recipes below to decide what to do for a user request.

## Primary Goal

Use generated top-level RTL and generated `.port_decls.v` files to answer questions such as:
- What is signal `sigA`, where does it appear, and which units connect to it?
- Which ports or signals are parameterized by `CORE_SMT_NUM_THREADS`?
- Generate an I/O table for a module such as `ifu` or `stsr`.
- Re-run the extraction pipeline for a chosen cluster and top module into a clean output directory.

The core extraction pipeline (steps 01 through 19) is **cluster-generic** and works for any cluster that has generated top-level RTL and `.port_decls.v` files.

## Cluster RTL Locations

Generated top-level RTL and port declarations follow a per-cluster layout under `target/<cluster>/gen/`:

| Cluster | `--top-v` | `--gen-dir` |
|---------|-----------|-------------|
| `fe`    | `target/fe/gen/fe.v` | `target/fe/gen` |
| `msid`  | `target/fe/gen/msid.v` | `target/fe/gen` |
| `ooo`   | `target/ooo/gen/ooo.v` | `target/ooo/gen` |
| `exe`   | `target/exe/gen/exe.v` | `target/exe/gen` |
| `mlc`   | `target/mlc/gen/mlc.v` | `target/mlc/gen` |
| `meu`   | `target/meu/gen/meu.v` | `target/meu/gen` |
| `pm`    | `target/pm/gen/pm.v` | `target/pm/gen` |

The general pattern is `target/<cluster>/gen/<cluster>.v`. MSID is an exception — it is generated alongside FE under `target/fe/gen/`.

**Prerequisites:** Before running the pipeline for a cluster, verify that the required generated files exist. The pipeline needs at minimum:
- `<gen-dir>/<cluster>.v` — the top-level generated RTL
- `<gen-dir>/<cluster>.port_decls.v` — the port declarations for the top module
- `<gen-dir>/*.port_decls.v` — port declarations for sub-modules instantiated by the top module

If any of these files are missing, the pipeline will fail. To check, run:

```tcsh
# Replace <cluster> and <gen-dir> with actual values
set cluster=ooo
set gen_dir=target/ooo/gen
ls "$gen_dir/${cluster}.v" "$gen_dir/${cluster}.port_decls.v"
# If either file is missing, the gen directory needs to be populated first.
# Check how many port_decls files exist:
ls "$gen_dir"/*.port_decls.v | wc -l
```

If the per-cluster directory is empty or missing files, `target/core/gen/`  may contain fallback RTL that can be used as `--gen-dir` instead.

## Scripts

- `01_extract_connectivity.py`
  - Input: top-level generated RTL such as `target/fe/gen/fe.v`, `target/ooo/gen/ooo.v`, etc.
  - Output: raw connectivity CSV with one row per instance-port connection
  - Key fields: `instance_module`, `instance_name`, `port_name`, `connected_expr`, `signal_name_normalized`, `source_file`, `source_line`, `guard_expr`

- `02_extract_port_directions.py`
  - Input: top-level RTL or module CSV, plus generated RTL directory containing `*.port_decls.v`
  - Output: module-port direction DB
  - Key fields: `module`, `port`, `direction`, `sv_type`, `packed_width`, `unpacked_dim`, `decl_line`, `source_file`
  - This is the script that preserves packed and unpacked dimensions from port declarations.

- `03_join_connectivity_with_directions.py`
  - Input: step 1 CSV and step 2 CSV
  - Output: typed connectivity CSV and unresolved CSV
  - Key fields: `port_direction`, `direction_sv_type`, `direction_packed_width`, `direction_unpacked_dim`, `edge_role`, `join_status`

- `generate_module_io_table.py`
  - Mode A input: typed connectivity CSV and a module name
  - Mode A output: per-port I/O table for that module
  - Mode A key fields: `signal_name`, `direction`, `source_output_units`, `other_connected_units`, `tlm_units_connected`
  - Mode B input: query view CSV (`07_*_connectivity_query_view.csv`) with `--all-lines-io-table`
  - Mode B output: row-wise I/O table (all query-view rows preserved) with added columns:
    - `source_output_units`
    - `connected_other_units`
    - `connected_tlm_units`
    - `is_connected_to_top`
    - `producer_cluster_owner`
    - `producer_origin_hint`
    - `producer_owner_evidence`
  - Producer resolution in Mode B uses ICF declarations (`core/*/rtl/*.icf`) to infer output-owner clusters.
  - For MLi* signals sourced by MEU-owned outputs, `producer_origin_hint` is set to `mlc_interface`.
  - When `source_output_units` has no row-level producer match, it is backfilled from ICF ownership as `CLUSTER/UNIT` (for example `MEU/DCU`) instead of `NONE`.
  - In Mode B, the two added columns are inserted immediately after `port_direction`.
  - Preferred over specialized module-specific table generators for new work.

- `find_tiedoff_threaded_signals.py`
  - Input: query view CSV (`07_*_connectivity_query_view.csv`)
  - Output: filtered CSV of tied-off threaded signals with a reduced schema
  - Selection rule:
    - `direction_packed_width` or `direction_unpacked_dim` contains `THREAD`
    - and (`is_unconnected` is true or `connected_expr` contains a tick `'`)
  - Output columns:
    - `top_module`, `instance_module`, `port_name`, `port_direciton`, `connected_expr`,
      `direction_packed_width`, `direction_unpacked_dim`, `is_unconnected`, `source_line`,
      `direction_source_file`, `direction_decl_line`

- `generate_ifu_io_table.py`
  - Historical specialized helper for IFU.
  - Prefer `generate_module_io_table.py --module ifu` unless the user explicitly asks for the legacy IFU script.

- `find_dimension_mismatches.py`
  - Input: query I/O table CSV (`12_*_query_io_table.csv`)
  - Output: CSV of signals whose `direction_packed_width` or `direction_unpacked_dim` differ across non-TLM units within a single cluster
  - Rows with `instance_module` ending in `_tlm` are excluded
  - Only signals appearing on two or more non-TLM units are checked
  - Key output fields: `port_name`, `instance_module`, `instance_name`, `port_direction`, `direction_sv_type`, `direction_packed_width`, `direction_unpacked_dim`, `direction_source_file`, `direction_decl_line`

- `find_cross_cluster_dimension_mismatches.py`
  - Input: two or more query I/O table CSVs (`12_*_query_io_table.csv`), one per cluster, via `--io-csvs`
  - Output: CSV of signals that appear in more than one cluster with differing `direction_packed_width` or `direction_unpacked_dim` across non-TLM units
  - Rows with `instance_module` ending in `_tlm` are excluded
  - Key output fields: `port_name`, `cluster`, `instance_module`, `instance_name`, `port_direction`, `direction_sv_type`, `direction_packed_width`, `direction_unpacked_dim`, `direction_source_file`, `direction_decl_line`

- `enrich_cross_hierarchy.py`
  - Purpose: Enrich an I/O table with cross-hierarchy consumer/producer resolution.
  - For rows where `is_connected_to_top=true`, traces the signal through parent hierarchy I/O tables to find consuming units (for outputs) or producing units (for inputs) across cluster boundaries.
  - Input: `--io-csv` (step 12 output), `--module`, one or more `--parent MOD:CSV` (nearest parent first), optional `--sibling MOD:CSV` for drill-into
  - Output: `--out-csv` enriched CSV with two added columns: `xhier_consumers` and `xhier_producers`
  - Algorithm:
    - For each connected-to-top row, looks in parent I/O table for peers sharing the same `port_name`.
    - For outputs: collects peer input-direction units as cross-hierarchy consumers.
    - For inputs: collects peer output-direction units as cross-hierarchy producers.
    - If a sibling I/O table is available for a peer cluster, drills into it to resolve leaf units.
    - If the signal continues upward through the parent boundary, repeats at the next ancestor level.
  - Detects constant tie-offs at parent levels and records them as `PARENT/TIEDOFF_CONST`.
  - Key output fields: `xhier_consumers`, `xhier_producers` (semicolon-separated paths like `CLUSTER/UNIT`)
  - Integrated into `run_cluster_pipeline.py` as step 19 (optional, requires `--parent`).

- `run_cluster_pipeline.py`
  - Purpose: **Generalized single-cluster pipeline runner.** Runs steps 01/02/03/07/12/13/14/15/17 for any cluster, plus optional step 19 for cross-hierarchy enrichment.
  - Required args: `--cluster`, `--top-v`, `--gen-dir`
  - Optional args: `--out-root`, `--ts`, `--preferred-guard-token`, `--module-io`, `--parent MOD:CSV`, `--sibling MOD:CSV`
  - Output root defaults to `<gen-dir>/InterfaceSpecAgent/<cluster>_pipeline_<ts>/`
  - No hardcoded FE/MSID assumptions. Works for ooo, exe, mlc, meu, pm, or any cluster with generated RTL.
  - Use `--module-io <module>` to also extract a step 18 module top I/O table.
  - Use `--parent` and `--sibling` to enable step 19 cross-hierarchy enrichment (produces `19_<cluster>_query_io_table_xhier.csv`).
  - Always emits step 14 diagnostic candidates as `14_<cluster>_unresolved_name_mismatch_candidates.csv`.
  - Always emits step 15 compact review table as `15_<cluster>_unresolved_name_mismatch_compact.csv`.

- `find_unresolved_name_mismatch_candidates.py`
  - Purpose: Diagnostic-only scan for unresolved `NONE` rows that may be caused by likely naming mistakes.
  - Input: `--io-csv`, optional repeatable `--reference LABEL:CSV`
  - Output: candidate CSV with unresolved signal, candidate signal, source/target units, and match type.
  - Match classes: case-only mismatch and missing/extra `T` mismatch.
  - Does not auto-resolve `source_output_units` or `connected_other_units`.

- `generate_unresolved_name_mismatch_compact.py`
  - Purpose: Convert the detailed step-14 mismatch candidate CSV into a compact review table.
  - Input: `--in-csv` (step 14 output)
  - Output: `15_<cluster>_unresolved_name_mismatch_compact.csv`
  - Key output fields: `mismatch_signal`, `matched_signal`, `match_type`, `target_unit`, `source_unit`, `target_missing_fields`.

- `run_fe_msid_pipeline_with_cross_cluster.py`
  - Purpose: Runs FE + MSID pipelines together with cross-cluster augmentation via `fe_te.v`.
  - This script is **FE/MSID-specific** — it hardcodes the two cluster names and the augmentation bridge.
  - For running a single cluster other than FE/MSID, use `run_cluster_pipeline.py` instead.

- `07_build_query_view_with_guard_bias.py`
  - Input: typed connectivity CSV
  - Outputs:
    - query-biased CSV with one selected row per `(instance_module, instance_name, port_name)`
    - anomaly CSV containing all multi-row guard alternatives
  - Selection bias prefers guard expressions containing `INTEL_INST_ON` (configurable via `--preferred-guard-token`).

### Scripts NOT in the Main Pipeline

The following scripts are **not** invoked by `run_fe_msid_pipeline_with_cross_cluster.py` but provide additional diagnostic and analysis capabilities.

#### Diagnostic Scripts

- `04_report_duplicate_instance_ports.py`
  - Purpose: Report duplicate `(instance_module, instance_name, port_name)` rows in a typed connectivity CSV.
  - Input: `--typed-csv` (step 3 output)
  - Output: `--out-csv` duplicate-report CSV
  - Use when: investigating why connectivity row counts exceed direction row counts.

- `05_compare_module_ports.py`
  - Purpose: Compare port sets between typed connectivity CSV and direction CSV for a given module; reports missing/extra ports.
  - Input: `--typed-csv`, `--directions-csv`, `--module`
  - Output: stdout text report (missing/extra port counts and names)
  - Use when: validating that step 3 join resolved all ports for a specific module.

- `06_analyze_connectivity_direction_delta.py`
  - Purpose: Per-module row-count delta analysis explaining why connectivity rows may exceed direction rows (guard-duplicated ports, etc.).
  - Input: `--typed-csv`, `--directions-csv`
  - Output: stdout CSV-style table (`conn_rows`, `dir_rows`, `unique_instance_port`, `duplicate_extra`, `net_delta`)
  - Use when: auditing row-count differences across the full cluster.

#### Analysis / Enrichment Scripts

- `derive_top_interface_from_drilldown.py`
  - Purpose: Synthesize a top-module interface CSV by combining a parent-level I/O table, a drilldown I/O table, and port declarations.
  - Input: `--parent-io-csv`, `--drilldown-io-csv`, `--port-decls` (`.port_decls.v`), `--module`, `--out-csv`
  - Output: derived top-interface CSV (same schema as `12_*_query_io_table.csv`)
  - Use when: building a module-boundary I/O table from a hierarchical drilldown (e.g., IFU within FE).

- `extract_rtl_functional_purpose.py`
  - Purpose: Scan RTL source files for each signal in an I/O table and extract a one-line functional purpose from nearby comments, `desc()` annotations, and usage patterns.
  - Input: `--io-csv`, `--out-csv`, `--workspace` (root dir), `--extra-rtl-dirs`
  - Output: enriched CSV with `functional_purpose` column added after `port_name`
  - Use when: you need RTL-derived context for each signal (e.g., whether it's a flop, assign, clock-gate; which files produce/consume it).

- `enrich_cross_hierarchy.py`
  - Purpose: Enrich an I/O table with cross-hierarchy consumer/producer resolution (standalone mode).
  - Input: `--io-csv`, `--module`, `--parent MOD:CSV` (repeatable), `--sibling MOD:CSV` (repeatable), `--out-csv`
  - Output: enriched CSV with `xhier_consumers` and `xhier_producers` columns
  - Use when: you need to trace which units in other clusters produce or consume a signal that crosses a cluster boundary.
  - Integrated into `run_cluster_pipeline.py` as step 19.

- `find_port_connected_units.py`
  - Purpose: Resolve which units are connected to a given port name using connectivity query evidence.
  - Input: `--port-name`, `--mincols-csv`, `--query-csv`, `--out-csv`
  - Output: per-port connected-units CSV
  - Use when: investigating connectivity for a single signal interactively.

#### Interface Spec Generation

- `generate_interface_spec.py`
  - Purpose: Generate a module interface specification markdown from a connected-to-top CSV. Classifies signals by external unit (Rule 1), special categories (Rule 2), and sub-groups large interfaces by primary internal consumer (Rule 5). Deduplicates per-instance rows.
  - Input: `--io-csv` (17_*_connected_to_top_rows.csv), `--module`, `--cluster`, `--subgroup-threshold` (default 10), `--out-md`
  - Output: `<module>_interface_spec.md`
  - Rules: See `interface_spec_rules.md`
  - Use when: generating a structured interface document for any unit.
  - Skill: See `SKILL.md` for the full end-to-end workflow.

<!-- NOT MATURE YET — Do not run these scripts for now.

- `generate_signal_descriptions.py`
  - Purpose: Generate natural-language functional purpose descriptions for interface signals using architectural domain knowledge, signal-name token semantics, and RTL-extracted context.
  - Input: `--io-csv`, `--rtl-purpose-csv` (optional, from `extract_rtl_functional_purpose.py`), `--out-csv`
  - Output: enriched CSV with `nl_description` column (prose-quality descriptions)
  - Filters: signals ending in `_inst` are excluded from description generation and tagged as `EXCLUDED`.
  - Use when: you want human-readable signal descriptions or need text features for the ML classifier.

#### ML / Classification Scripts

- `generate_interface_spec_from_io_table.py`
  - Purpose: Generate interface spec artifacts using hybrid deterministic rules + optional sklearn LogisticRegression ML classification.
  - Input: `--io-csv`, `--module`, `--out-dir`, `--min-ml-seed-rows`, `--rtl-search-files`
  - Output: `<module>_interface_features.csv`, `<module>_interface_classified.csv`, `<module>_interface_by_block.csv`, `<module>_interface_spec.md`
  - Use when: generating a rule-based interface specification with functional group assignments (e.g., snoop_coherency, branch_prediction, clock_reset_power).

- `unsupervised_signal_classifier.py`
  - Purpose: Graph-regularized deep embedding (denoising autoencoder) + UMAP + HDBSCAN unsupervised clustering pipeline for interface signal classification.
  - Input: `--io-csv`, `--out-dir`, `--module`, `--nl-descriptions-csv` (optional, from `generate_signal_descriptions.py`), plus tuning params (`--ae-hidden`, `--ae-bottleneck`, `--ae-epochs`, `--hdbscan-min-cluster`, etc.)
  - Output: scored CSV (`*_ml_classified.csv`), cluster summary CSV, cluster details JSON, 2D scatter plot PNG
  - Filters: signals ending in `_inst` are excluded from clustering (cluster_id=-2, flagged `EXCLUDED`).
  - Use when: discovering natural signal groupings without labeled training data. NL description features improve cluster separation.

END NOT MATURE -->

#### Legacy Scripts

- `generate_ifu_io_table.py`
  - Purpose: Historical specialized helper for IFU I/O table generation with hardcoded paths.
  - No CLI args — uses hardcoded paths under `target/fe/gen/InterfaceSpecAgent/fe/`.
  - Prefer `generate_module_io_table.py --module ifu` for all new work.

## Data Model

The pipeline is intentionally split into stages.

1. Raw connectivity
- Truth source for instance-level wiring found in the generated top RTL.
- Best for questions about where a connection was seen and under which preprocessor guards.

2. Port direction DB
- Truth source for port direction and preserved type/width metadata from `.port_decls.v`.
- Best for questions about whether a port is input/output/inout and whether it is parameterized.

3. Typed connectivity
- Join of stage 1 and stage 2.
- Best general-purpose dataset for tracing, filtering, and answering signal questions.

3b. Guard-biased query view (optional post-process)
- Derived from typed connectivity by selecting one row per instance-port key.
- Use for query-style requests when mutually-exclusive guard alternatives exist.
- Keep anomaly CSV for audit and debug.

4. Module I/O table
- Aggregated view for a single module.
- Best for questions like "what units feed STSR?" or "which TLM units observe this IFU signal?"

## AI Agent Routing Rules

When a user asks a question, first classify it into one of the intent types below.

### Intent: Build cross-cluster hierarchy summary (first step)

Use as the first step for new exploration sessions when the user asks for
overall hierarchy/cross-cluster understanding (for example FE/MSID interaction
with other core clusters).

Action:
1. Build or refresh the hierarchy summary as the first step for any new exploration session.
2. Create/update command trace log.
3. Run the hierarchy summary builder.
4. Use generated summary and extracts as baseline context before deeper signal queries.

Summary format notes:
- First-level hierarchy lines are emitted as:
  - `<cluster> -> <child1>, <child2>, ... (<hier_path>)`
- `icore.hier` (`core/common/rtl/global/icore.hier`) is used as the one-level-up source of truth for per-core modules.
- The summary includes one line for `icore` and one first-level line for each module instantiated under `icore`.
- Child modules ending with `_inst` or `_tlm` are excluded from these first-level lists.
- Cross-cluster signal extracts are filtered by modules derived from `icore.hier` excluding `fe`, `msid`, `icore_clk`, and `core_fivr`.

Command:

```tcsh
python3 scripts/tmp/build_hier_cross_cluster_summary.py
```

Expected outputs:
- `target/fe/gen/InterfaceSpecAgent/logs/hier_cross_cluster_summary_<ts>.txt`
- `target/fe/gen/InterfaceSpecAgent/logs/fe_cross_cluster_signals_<ts>.csv`
- `target/fe/gen/InterfaceSpecAgent/logs/msid_cross_cluster_signals_<ts>.csv`
- `target/fe/gen/InterfaceSpecAgent/logs/hier_trace_<ts>.log`

### Intent: Re-run the extraction pipeline

Use when the user asks to regenerate output for any cluster or top module.

Action:
0. **Verify prerequisites:** Check that `<gen-dir>/<cluster>.v` and `<gen-dir>/<cluster>.port_decls.v` exist. If any required files are missing, inform the user which files are absent and that the gen directory needs to be populated before the pipeline can run.
1. (First) Build or refresh cross-cluster hierarchy summary (see above). This is required for all clusters.
2. Create a fresh timestamped output directory under the target cluster path.
3. Run step 1 into `intermediate/01_*`.
3. Run step 2 into `intermediate/02_*`.
4. Run step 3 into `results/03_*`.
5. Optional post-step: build query view into `results/07_*`.
6. Optional post-step: build all-lines query I/O table into `results/12_*`.
7. Optional post-step: find tiedoff threaded signals into `results/13_*`.
8. Optional post-step: build connected-to-top subset into `results/17_*`.
9. Optional post-step: cross-hierarchy enrichment into `results/19_*` (requires `--parent`).
10. If the user asks for a module table, run `generate_module_io_table.py` or `extract_module_top_io_table.py`.
11. For FE+MSID specifically, optional cross-cluster augmentation via `fe_te.v`.

Preferred one-command flow for **any single cluster**:

```tcsh
# OOO cluster
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster ooo --top-v target/ooo/gen/ooo.v --gen-dir target/ooo/gen

# EXE cluster
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster exe --top-v target/exe/gen/exe.v --gen-dir target/exe/gen

# MLC cluster
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster mlc --top-v target/mlc/gen/mlc.v --gen-dir target/mlc/gen

# FE cluster (single-cluster mode, no cross-cluster augmentation)
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster fe --top-v target/fe/gen/fe.v --gen-dir target/fe/gen

# With optional module-level I/O table extraction
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster ooo --top-v target/ooo/gen/ooo.v --gen-dir target/ooo/gen \
  --module-io rat

# With cross-hierarchy enrichment (step 19)
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster ooo --top-v target/core/gen/ooo.v --gen-dir target/core/gen \
  --parent icore:<icore_pipeline>/results/12_icore_query_io_table.csv \
  --sibling fe:<fe_pipeline>/results/12_fe_query_io_table.csv \
  --sibling msid:<msid_pipeline>/results/12_msid_query_io_table.csv
```

Preferred one-command flow for **FE + MSID with cross-cluster augmentation**:

```tcsh
python3 scripts/interfacespec/run_fe_msid_pipeline_with_cross_cluster.py
```

This FE/MSID-specific command runs:
- hierarchy baseline
- FE pipeline `01/02/03/07/12/13/17`
- MSID pipeline `01/02/03/07/12/13/17`
- FE<->MSID augmentation via `fe_te.v`
- missing report at `target/fe/gen/InterfaceSpecAgent/cross_cluster_<ts>_missing.csv`

Manual step-by-step command sequence (works for any cluster):

```tcsh
set ts=`date +%Y%m%d_%H%M%S`
set cluster=<cluster>
set top_v=<path_to_top_v>
set gen_dir=<path_to_generated_rtl_dir>
set run_dir=${gen_dir}/InterfaceSpecAgent/${cluster}_pipeline_${ts}
mkdir -p "$run_dir/intermediate" "$run_dir/results"
python3 scripts/interfacespec/01_extract_connectivity.py \
  --top-v "$top_v" \
  --cluster "$cluster" \
  --out-csv "$run_dir/intermediate/01_${cluster}_connectivity_raw.csv"
python3 scripts/interfacespec/02_extract_port_directions.py \
  --top-v "$top_v" \
  --gen-dir "$gen_dir" \
  --out-csv "$run_dir/intermediate/02_${cluster}_port_directions.csv"
python3 scripts/interfacespec/03_join_connectivity_with_directions.py \
  --connectivity-csv "$run_dir/intermediate/01_${cluster}_connectivity_raw.csv" \
  --directions-csv "$run_dir/intermediate/02_${cluster}_port_directions.csv" \
  --out-csv "$run_dir/results/03_${cluster}_connectivity_typed.csv" \
  --unresolved-csv "$run_dir/results/03_${cluster}_connectivity_unresolved.csv"

# build query view for row-wise queries
python3 scripts/interfacespec/07_build_query_view_with_guard_bias.py \
  --typed-csv "$run_dir/results/03_${cluster}_connectivity_typed.csv" \
  --out-query-csv "$run_dir/results/07_${cluster}_connectivity_query_view.csv" \
  --out-anomalies-csv "$run_dir/results/07_${cluster}_guard_alternatives_anomalies.csv" \
  --preferred-guard-token INTEL_INST_ON

# build all-lines query I/O table
python3 scripts/interfacespec/generate_module_io_table.py \
  --query-csv "$run_dir/results/07_${cluster}_connectivity_query_view.csv" \
  --all-lines-io-table \
  --out-csv "$run_dir/results/12_${cluster}_query_io_table.csv"
```

Example substitutions:

```tcsh
# FE and MSID (under target/fe/gen)
set cluster=fe
set top_v=target/fe/gen/fe.v
set gen_dir=target/fe/gen

set cluster=msid
set top_v=target/fe/gen/msid.v
set gen_dir=target/fe/gen

# All other clusters (under target/<cluster>/gen)
set cluster=ooo
set top_v=target/ooo/gen/ooo.v
set gen_dir=target/ooo/gen

set cluster=exe
set top_v=target/exe/gen/exe.v
set gen_dir=target/exe/gen
```

### Intent: Generate an I/O table for one module

Use when the user asks for a table like IFU I/O, STSR I/O, or another module summary.

Action:
1. Ensure a typed connectivity CSV already exists.
2. Run `generate_module_io_table.py` with the requested module name.
3. Store output under the same pipeline run or cluster result directory.

Generic example:

```tcsh
set cluster=<cluster>
set module=<module>
set typed_csv=<path_to_typed_connectivity_csv>
python3 scripts/interfacespec/generate_module_io_table.py \
  --typed-csv "$typed_csv" \
  --module "$module" \
  --out-csv target/${cluster}/gen/InterfaceSpecAgent/${cluster}/${module}_io_units_table.csv
```

### Intent: Generate an I/O table for all query-view rows

Use when the user asks to enrich every row in query view with unit connectivity columns.

Action:
1. Ensure query view CSV from step 7 already exists.
2. Run `generate_module_io_table.py` with `--query-csv` and `--all-lines-io-table`.
3. Use the resulting CSV as the row-level I/O table.

Generic example:

```tcsh
python3 scripts/interfacespec/generate_module_io_table.py \
  --query-csv target/${cluster}/gen/InterfaceSpecAgent/${cluster}_pipeline_<timestamp>/results/07_${cluster}_connectivity_query_view.csv \
  --all-lines-io-table \
  --out-csv target/${cluster}/gen/InterfaceSpecAgent/${cluster}_pipeline_<timestamp>/results/12_${cluster}_query_io_table.csv
```

### Intent: Run full pipeline for a module and resolve xhier using parent outputs

Use when the user wants a module-level I/O table and cross-hierarchy enrichment
for any sub-cluster module (e.g., `stsr`, `idq`, `ifu`, `dsbe`, `bpu`, `bac`, `rat`).

First, determine the module's parent cluster using the mapping in `SKILL.md`
or the Cluster RTL Locations table above. The general pattern is:

| Unit | Parent | Grandparent | `--top-v` | `--gen-dir` |
|------|--------|-------------|-----------|-------------|
| stsr | msid | fe | `target/fe/gen/stsr.v` | `target/fe/gen` |
| idq | msid | fe | `target/fe/gen/idq.v` | `target/fe/gen` |
| ifu | fe | icore | `target/fe/gen/ifu.v` | `target/fe/gen` |
| dsbe | msid | fe | `target/fe/gen/dsbe.v` | `target/fe/gen` |
| bpu | fe | icore | `target/fe/gen/bpu.v` | `target/fe/gen` |
| bac | fe | icore | `target/fe/gen/bac.v` | `target/fe/gen` |
| rat | ooo | icore | `target/ooo/gen/rat.v` | `target/ooo/gen` |

Required behavior:
1. Run the module pipeline with `cluster=<module>`.
2. Run the parent cluster pipeline with `cluster=<parent>`.
3. Run the grandparent cluster pipeline with `cluster=<grandparent>`.
4. Enrich the parent's `12_*` using the grandparent's `12_*` to produce `19_<parent>_query_io_table_xhier.csv`. This resolves NONE entries in `source_output_units` for signals originating outside the parent cluster.
5. Extract the module's top I/O table from the **enriched parent** `19_*` (not the raw `12_*`).
6. Run `enrich_cross_hierarchy.py` on the module's I/O using the parent's query view and query I/O outputs.
7. Check rows where `source_output_units` or `other_connected_units` is `NONE` — only scan/clk/DFT signals should remain unresolved.

Command sequence (expanded — replace `<module>`, `<parent>`, paths as needed):

```tcsh
set ts=`date +%Y%m%d_%H%M%S`
set module=<module>           # e.g., stsr, ifu, idq, dsbe, bpu, bac, rat
set parent=<parent_cluster>   # e.g., msid, fe, ooo
set gparent=<grandparent>     # e.g., fe, icore
set mod_top_v=<module_top_v>  # e.g., target/fe/gen/stsr.v
set par_top_v=<parent_top_v>  # e.g., target/fe/gen/msid.v
set gp_top_v=<gp_top_v>       # e.g., target/fe/gen/fe.v
set gen_dir=<gen_dir>         # e.g., target/fe/gen
set gp_gen_dir=<gp_gen_dir>   # e.g., target/fe/gen (same for FE/MSID)

# Module pipeline (unit of interest)
set mod_run_dir=${gen_dir}/InterfaceSpecAgent/${module}_pipeline_${ts}
mkdir -p "$mod_run_dir/intermediate" "$mod_run_dir/results"

python3 scripts/interfacespec/01_extract_connectivity.py \
  --top-v "$mod_top_v" \
  --cluster "$module" \
  --out-csv "$mod_run_dir/intermediate/01_${module}_connectivity_raw.csv"
python3 scripts/interfacespec/02_extract_port_directions.py \
  --top-v "$mod_top_v" \
  --gen-dir "$gen_dir" \
  --out-csv "$mod_run_dir/intermediate/02_${module}_port_directions.csv"
python3 scripts/interfacespec/03_join_connectivity_with_directions.py \
  --connectivity-csv "$mod_run_dir/intermediate/01_${module}_connectivity_raw.csv" \
  --directions-csv "$mod_run_dir/intermediate/02_${module}_port_directions.csv" \
  --out-csv "$mod_run_dir/results/03_${module}_connectivity_typed.csv" \
  --unresolved-csv "$mod_run_dir/results/03_${module}_connectivity_unresolved.csv"
python3 scripts/interfacespec/07_build_query_view_with_guard_bias.py \
  --typed-csv "$mod_run_dir/results/03_${module}_connectivity_typed.csv" \
  --out-query-csv "$mod_run_dir/results/07_${module}_connectivity_query_view.csv" \
  --out-anomalies-csv "$mod_run_dir/results/07_${module}_guard_alternatives_anomalies.csv" \
  --preferred-guard-token INTEL_INST_ON
python3 scripts/interfacespec/generate_module_io_table.py \
  --query-csv "$mod_run_dir/results/07_${module}_connectivity_query_view.csv" \
  --all-lines-io-table \
  --out-csv "$mod_run_dir/results/12_${module}_query_io_table.csv"

# Parent cluster pipeline (provides context for step 18 extraction and xhier)
set par_run_dir=${gen_dir}/InterfaceSpecAgent/${parent}_pipeline_${ts}
mkdir -p "$par_run_dir/intermediate" "$par_run_dir/results"

python3 scripts/interfacespec/01_extract_connectivity.py \
  --top-v "$par_top_v" \
  --cluster "$parent" \
  --out-csv "$par_run_dir/intermediate/01_${parent}_connectivity_raw.csv"
python3 scripts/interfacespec/02_extract_port_directions.py \
  --top-v "$par_top_v" \
  --gen-dir "$gen_dir" \
  --out-csv "$par_run_dir/intermediate/02_${parent}_port_directions.csv"
python3 scripts/interfacespec/03_join_connectivity_with_directions.py \
  --connectivity-csv "$par_run_dir/intermediate/01_${parent}_connectivity_raw.csv" \
  --directions-csv "$par_run_dir/intermediate/02_${parent}_port_directions.csv" \
  --out-csv "$par_run_dir/results/03_${parent}_connectivity_typed.csv" \
  --unresolved-csv "$par_run_dir/results/03_${parent}_connectivity_unresolved.csv"
python3 scripts/interfacespec/07_build_query_view_with_guard_bias.py \
  --typed-csv "$par_run_dir/results/03_${parent}_connectivity_typed.csv" \
  --out-query-csv "$par_run_dir/results/07_${parent}_connectivity_query_view.csv" \
  --out-anomalies-csv "$par_run_dir/results/07_${parent}_guard_alternatives_anomalies.csv" \
  --preferred-guard-token INTEL_INST_ON
python3 scripts/interfacespec/generate_module_io_table.py \
  --query-csv "$par_run_dir/results/07_${parent}_connectivity_query_view.csv" \
  --all-lines-io-table \
  --out-csv "$par_run_dir/results/12_${parent}_query_io_table.csv"

# Grandparent cluster pipeline (needed to enrich parent)
set gp_run_dir=${gp_gen_dir}/InterfaceSpecAgent/${gparent}_pipeline_${ts}
mkdir -p "$gp_run_dir/intermediate" "$gp_run_dir/results"

python3 scripts/interfacespec/01_extract_connectivity.py \
  --top-v "$gp_top_v" \
  --cluster "$gparent" \
  --out-csv "$gp_run_dir/intermediate/01_${gparent}_connectivity_raw.csv"
python3 scripts/interfacespec/02_extract_port_directions.py \
  --top-v "$gp_top_v" \
  --gen-dir "$gp_gen_dir" \
  --out-csv "$gp_run_dir/intermediate/02_${gparent}_port_directions.csv"
python3 scripts/interfacespec/03_join_connectivity_with_directions.py \
  --connectivity-csv "$gp_run_dir/intermediate/01_${gparent}_connectivity_raw.csv" \
  --directions-csv "$gp_run_dir/intermediate/02_${gparent}_port_directions.csv" \
  --out-csv "$gp_run_dir/results/03_${gparent}_connectivity_typed.csv" \
  --unresolved-csv "$gp_run_dir/results/03_${gparent}_connectivity_unresolved.csv"
python3 scripts/interfacespec/07_build_query_view_with_guard_bias.py \
  --typed-csv "$gp_run_dir/results/03_${gparent}_connectivity_typed.csv" \
  --out-query-csv "$gp_run_dir/results/07_${gparent}_connectivity_query_view.csv" \
  --out-anomalies-csv "$gp_run_dir/results/07_${gparent}_guard_alternatives_anomalies.csv" \
  --preferred-guard-token INTEL_INST_ON
python3 scripts/interfacespec/generate_module_io_table.py \
  --query-csv "$gp_run_dir/results/07_${gparent}_connectivity_query_view.csv" \
  --all-lines-io-table \
  --out-csv "$gp_run_dir/results/12_${gparent}_query_io_table.csv"

# Enrich parent using grandparent
python3 scripts/interfacespec/enrich_cross_hierarchy.py \
  --io-csv "$par_run_dir/results/12_${parent}_query_io_table.csv" \
  --module "$parent" \
  --parent "${gparent}:$gp_run_dir/results/12_${gparent}_query_io_table.csv" \
  --out-csv "$par_run_dir/results/19_${parent}_query_io_table_xhier.csv"

# Extract module top I/O from enriched parent
python3 scripts/interfacespec/extract_module_top_io_table.py \
  --io-csv "$par_run_dir/results/19_${parent}_query_io_table_xhier.csv" \
  --module "$module" \
  --port-decls "${gen_dir}/${module}.port_decls.v" \
  --out-csv "$mod_run_dir/results/18_${module}_top_io_table.csv"

# Resolve module xhier using parent outputs
python3 scripts/interfacespec/enrich_cross_hierarchy.py \
  --io-csv "$mod_run_dir/results/18_${module}_top_io_table.csv" \
  --module "$module" \
  --parent "${parent}:$par_run_dir/results/07_${parent}_connectivity_query_view.csv" \
  --parent "${parent}:$par_run_dir/results/12_${parent}_query_io_table.csv" \
  --out-csv "$mod_run_dir/results/20_${module}_pseudo_top_enriched.csv"

echo "Enriched output: $mod_run_dir/results/20_${module}_pseudo_top_enriched.csv"
awk -F',' 'NR==1 || $3=="NONE" || $4=="NONE" {print}' "$mod_run_dir/results/18_${module}_top_io_table.csv"
```

Quick-run wrapper (compact version using `run_cluster_pipeline.py`):

```tcsh
set ts=`date +%Y%m%d_%H%M%S`
set module=<module>           # e.g., stsr
set parent=<parent_cluster>   # e.g., msid
set gparent=<grandparent>     # e.g., fe
set mod_top_v=<module_top_v>  # e.g., target/fe/gen/stsr.v
set par_top_v=<parent_top_v>  # e.g., target/fe/gen/msid.v
set gp_top_v=<gp_top_v>       # e.g., target/fe/gen/fe.v
set gen_dir=<gen_dir>         # e.g., target/fe/gen
set gp_gen_dir=<gp_gen_dir>   # e.g., target/fe/gen

# 1. Module pipeline
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster "$module" --top-v "$mod_top_v" --gen-dir "$gen_dir" \
  --ts "$ts"

# 2. Parent cluster pipeline
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster "$parent" --top-v "$par_top_v" --gen-dir "$gen_dir" \
  --ts "$ts"

# 3. Grandparent cluster pipeline
python3 scripts/interfacespec/run_cluster_pipeline.py \
  --cluster "$gparent" --top-v "$gp_top_v" --gen-dir "$gp_gen_dir" \
  --ts "$ts"

# 4. Enrich parent using grandparent
python3 scripts/interfacespec/enrich_cross_hierarchy.py \
  --io-csv "${gen_dir}/InterfaceSpecAgent/${parent}_pipeline_${ts}/results/12_${parent}_query_io_table.csv" \
  --module "$parent" \
  --parent "${gparent}:${gp_gen_dir}/InterfaceSpecAgent/${gparent}_pipeline_${ts}/results/12_${gparent}_query_io_table.csv" \
  --out-csv "${gen_dir}/InterfaceSpecAgent/${parent}_pipeline_${ts}/results/19_${parent}_query_io_table_xhier.csv"

# 5. Extract module top I/O from enriched parent
python3 scripts/interfacespec/extract_module_top_io_table.py \
  --io-csv "${gen_dir}/InterfaceSpecAgent/${parent}_pipeline_${ts}/results/19_${parent}_query_io_table_xhier.csv" \
  --module "$module" \
  --port-decls "${gen_dir}/${module}.port_decls.v" \
  --out-csv "${gen_dir}/InterfaceSpecAgent/${module}_pipeline_${ts}/results/18_${module}_top_io_table.csv"

# 6. Enrich module with cross-hierarchy
python3 scripts/interfacespec/enrich_cross_hierarchy.py \
  --io-csv "${gen_dir}/InterfaceSpecAgent/${module}_pipeline_${ts}/results/18_${module}_top_io_table.csv" \
  --module "$module" \
  --parent "${parent}:${gen_dir}/InterfaceSpecAgent/${parent}_pipeline_${ts}/results/07_${parent}_connectivity_query_view.csv" \
  --parent "${parent}:${gen_dir}/InterfaceSpecAgent/${parent}_pipeline_${ts}/results/12_${parent}_query_io_table.csv" \
  --out-csv "${gen_dir}/InterfaceSpecAgent/${module}_pipeline_${ts}/results/20_${module}_pseudo_top_enriched.csv"
```

### Intent: Generate interface specification for a module

Use when the user asks to generate an interface spec, interface document,
or signal classification for a module.

Action:
1. Ensure `18_<module>_top_io_table.csv` exists (extracted from enriched parent `19_*`).
2. Run `generate_interface_spec.py` with the enriched `18_*` as input.
3. Review the generated markdown (interface summary + per-interface tables).

Rules reference: `interface_spec_rules.md`
Skill reference: `SKILL.md`

Generic example:

```tcsh
python3 scripts/interfacespec/generate_interface_spec.py \
  --io-csv target/<gen_dir>/InterfaceSpecAgent/<module>_pipeline_<ts>/results/18_<module>_top_io_table.csv \
  --module <module> \
  --cluster <parent_cluster> \
  --subgroup-threshold 10
```

Output: `<module>_interface_spec.md` in the same results directory.

### Intent: Find tied-off threaded signals

Use when the user asks for tied-off ports/signals involving THREAD dimensions.

Action:
1. Use query view CSV as input.
2. Run `find_tiedoff_threaded_signals.py`.
3. Consume output reduced-schema CSV.

Generic example:

```tcsh
python3 scripts/interfacespec/find_tiedoff_threaded_signals.py \
  --query-csv target/${cluster}/gen/InterfaceSpecAgent/${cluster}_pipeline_<timestamp>/results/07_${cluster}_connectivity_query_view.csv \
  --out-csv target/${cluster}/gen/InterfaceSpecAgent/${cluster}_pipeline_<timestamp>/results/11_${cluster}_thread_tiedoff_mincols.csv
```

### Intent: Tell me about signal `<sig>`

Use when the user asks for details about one signal.

Preferred data source:
- First choice: guard-biased query view CSV (if available)
- Second choice: typed connectivity CSV
- Second choice: port directions CSV
- Third choice: module I/O table if the question is module-centric

Action:
1. Search typed connectivity for exact matches in `port_name`, `signal_name_normalized`, and `connected_expr`.
2. Report:
   - cluster/top/module/instance occurrences
   - port direction and edge role
   - source file and source line
   - preserved type and dimensions if present
   - whether it is conditional and the guard expression
3. If the signal appears in a module I/O table, summarize the producer and consumer units.
4. If guard alternatives exist, cite the anomaly row and mention that query selection is biased to `INTEL_INST_ON`.

Optional guard-bias preprocessing command:

```tcsh
python3 scripts/interfacespec/07_build_query_view_with_guard_bias.py \
  --typed-csv "$typed_csv" \
  --out-query-csv "$run_dir/results/07_${cluster}_connectivity_query_view.csv" \
  --out-anomalies-csv "$run_dir/results/07_${cluster}_guard_alternatives_anomalies.csv" \
  --preferred-guard-token INTEL_INST_ON
```

Suggested search patterns:

```tcsh
set typed_csv=<path_to_typed_connectivity_csv>
set directions_csv=<path_to_port_directions_csv>
rg -n "(^|,)(sigA)(,|$)" "$typed_csv"
rg -n "sigA" "$directions_csv"
```

### Intent: List all signals parameterized by `<PARAM>`

Use when the user asks for all signals involving a parameter such as `CORE_SMT_NUM_THREADS`.

Preferred data source:
- First choice: port directions CSV
- Second choice: typed connectivity CSV

Action:
1. Search `packed_width` and `unpacked_dim` for the parameter token.
2. If the user wants connectivity context, join back to typed connectivity results by `(module, port)` or use typed connectivity directly on `direction_packed_width` and `direction_unpacked_dim`.
3. Return distinct rows, grouped by module if helpful.

Suggested search patterns:

```tcsh
set directions_csv=<path_to_port_directions_csv>
set typed_csv=<path_to_typed_connectivity_csv>
rg -n "CORE_SMT_NUM_THREADS" "$directions_csv"
rg -n "CORE_SMT_NUM_THREADS" "$typed_csv"
```

### Intent: Find dimension mismatches within a cluster

Use when the user asks for signals with mismatched packed_width or unpacked_dim across units within a single cluster.

Preferred data source:
- Query I/O table CSV (`12_*_query_io_table.csv`)

Action:
1. Ensure the query I/O table CSV exists for the requested cluster.
2. Run `find_dimension_mismatches.py` with `--io-csv` and optional `--out-csv`.
3. Report mismatched signals grouped by port name.

Generic example:

```tcsh
python3 scripts/interfacespec/find_dimension_mismatches.py \
  --io-csv target/${cluster}/gen/InterfaceSpecAgent/${cluster}_pipeline_<timestamp>/results/12_${cluster}_query_io_table.csv \
  --out-csv target/${cluster}/gen/InterfaceSpecAgent/${cluster}_pipeline_<timestamp>/results/14_${cluster}_dimension_mismatches.csv
```

### Intent: Find dimension mismatches across clusters

Use when the user asks for signals with mismatched dimensions between two or more clusters (e.g., FE and MSID, or OOO and EXE).

Preferred data source:
- Query I/O table CSVs from each cluster (`12_*_query_io_table.csv`)

Action:
1. Ensure the query I/O table CSV exists for each cluster.
2. Run `find_cross_cluster_dimension_mismatches.py` with `--io-csvs` pointing to all cluster CSVs.
3. Report mismatched signals grouped by port name with cluster annotations.

Generic example (any combination of clusters):

```tcsh
# FE vs MSID
python3 scripts/interfacespec/find_cross_cluster_dimension_mismatches.py \
  --io-csvs \
    target/fe/gen/InterfaceSpecAgent/fe_pipeline_<timestamp>/results/12_fe_query_io_table.csv \
    target/fe/gen/InterfaceSpecAgent/msid_pipeline_<timestamp>/results/12_msid_query_io_table.csv \
  --out-csv target/fe/gen/InterfaceSpecAgent/14_fe_msid_cross_cluster_dimension_mismatches.csv

# OOO vs EXE
python3 scripts/interfacespec/find_cross_cluster_dimension_mismatches.py \
  --io-csvs \
    target/ooo/gen/InterfaceSpecAgent/ooo_pipeline_<timestamp>/results/12_ooo_query_io_table.csv \
    target/exe/gen/InterfaceSpecAgent/exe_pipeline_<timestamp>/results/12_exe_query_io_table.csv \
  --out-csv 14_ooo_exe_cross_cluster_dimension_mismatches.csv

# Three-way: FE vs MSID vs OOO
python3 scripts/interfacespec/find_cross_cluster_dimension_mismatches.py \
  --io-csvs \
    target/fe/gen/InterfaceSpecAgent/fe_pipeline_<timestamp>/results/12_fe_query_io_table.csv \
    target/fe/gen/InterfaceSpecAgent/msid_pipeline_<timestamp>/results/12_msid_query_io_table.csv \
    target/ooo/gen/InterfaceSpecAgent/ooo_pipeline_<timestamp>/results/12_ooo_query_io_table.csv \
  --out-csv 14_fe_msid_ooo_cross_cluster_dimension_mismatches.csv
```

### Intent: Why is this signal unresolved?

Use when the user asks about join failures.

Action:
1. Open the unresolved CSV from step 3.
2. Inspect `reason_code`, `context_module`, `context_port`, and source location.
3. Follow `suggested_action`.
4. If needed, inspect the referenced `<module>.port_decls.v` file.

## Locating Existing Outputs

Before rerunning, check whether outputs already exist for the requested cluster.

Preferred locations:
- `<gen-dir>/InterfaceSpecAgent/<cluster>_pipeline_<timestamp>/intermediate/...`
- `<gen-dir>/InterfaceSpecAgent/<cluster>_pipeline_<timestamp>/results/...`
- `<gen-dir>/InterfaceSpecAgent/<cluster>/<module>_io_units_table.csv`

The general pattern is `<gen-dir>` = `target/<cluster>/gen`. For MSID, `<gen-dir>` is `target/fe/gen` (shared with FE). `target/core/gen/` or `target/ooo/gen/` may also contain fallback RTL.

Legacy or ad hoc locations may also exist, for example directly under `target/`.

## Output Naming Convention

For new runs, prefer:
- `<gen-dir>/InterfaceSpecAgent/<cluster>_pipeline_<timestamp>/intermediate/...`
- `<gen-dir>/InterfaceSpecAgent/<cluster>_pipeline_<timestamp>/results/...`

For ad hoc module tables, prefer:
- `<gen-dir>/InterfaceSpecAgent/<cluster>/<module>_io_units_table.csv`

## Decision Heuristics

- Prefer existing outputs if they are already present and the user did not explicitly ask for a rerun.
- Prefer rerunning if the user asks for a fresh extraction, a new cluster/top module, or a new output directory.
- Prefer `generate_module_io_table.py` for any new module summary work.
- Use the preserved dimension fields when the user asks about parameterization or widths.
- Use `port_name` as the module signal identity in I/O tables. Do not use `connected_expr` as the signal identity.
- Key tracing by instance-aware rows from the connectivity CSV, not only by module type.
- If top-level RTL and `.port_decls.v` files live in different directories, set `--top-v` and `--gen-dir` independently.

## Cluster Generalization Notes

The core extraction pipeline (steps 01→02→03→07→12→13→17→19) is fully cluster-generic. However, some auxiliary scripts have FE/MSID-specific logic:

| Script | Generalization Status |
|--------|-----------------------|
| `01_extract_connectivity.py` | Fully generic |
| `02_extract_port_directions.py` | Fully generic |
| `03_join_connectivity_with_directions.py` | Fully generic |
| `07_build_query_view_with_guard_bias.py` | Fully generic |
| `find_tiedoff_threaded_signals.py` | Fully generic |
| `find_dimension_mismatches.py` | Fully generic |
| `find_cross_cluster_dimension_mismatches.py` | Fully generic |
| `extract_module_top_io_table.py` | Fully generic |
| `enrich_cross_hierarchy.py` | Fully generic |
| `run_cluster_pipeline.py` | Fully generic (new) |
| `generate_interface_spec.py` | Fully generic — rules-based interface spec from connected-to-top CSV |
| `generate_module_io_table.py` | Mostly generic — FE ICF fallback resolvers silently return empty for other clusters |
| `unsupervised_signal_classifier.py` | **Not mature — do not run.** Mostly generic but FE-biased |
| `generate_signal_descriptions.py` | **Not mature — do not run.** FE/IFU-specific; low quality for other clusters |
| `generate_interface_spec_from_io_table.py` | **Not mature — do not run.** Hybrid rules + ML classification |
| `augment_fe_msid_cross_cluster_io.py` | FE/MSID-specific — hardcoded cluster names and instance patterns; will crash for other clusters |
| `run_fe_msid_pipeline_with_cross_cluster.py` | FE/MSID-specific — hardcodes both cluster names |
| `generate_ifu_io_table.py` | Legacy — hardcoded FE paths, no CLI args; use `generate_module_io_table.py` instead |

## Practice Lab

See `scripts/interfacespec/practice_lab/README.md` for guided exercises and `scripts/interfacespec/practice_lab/run_examples.tcsh` for runnable examples.
