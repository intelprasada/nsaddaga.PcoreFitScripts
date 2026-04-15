# InterfaceSpec Pipeline GUI

A desktop GUI wrapper for the InterfaceSpec pipeline and analysis tools.

## Requirements

- Python 3.11+ (tkinter Tk 8.6 included)
- pandas (available in the model environment)
- An X11/VNC display (`DISPLAY` must be set)

No additional pip installs are required.

---

## Launch

From the model root:

```tcsh
setenv DISPLAY :1   # or your VNC display
python3 scripts/interfacespec/qtgui/main.py
```

Or as a module:

```tcsh
python3 -m scripts.interfacespec.qtgui.main
```

---

## Window Layout

```
┌──────────────────────────────────────────────────┐
│  InterfaceSpec Pipeline Tool                     │
├──────────┬──────────────────┬────────────────────┤
│ Pipeline │  Interface Spec  │  Results           │
├──────────┴──────────────────┴────────────────────┤
│  <active tab content>                            │
└──────────────────────────────────────────────────┘
```

### Tab 1 — Pipeline

1. Set **Model root** (e.g. `/nfs/site/disks/nsaddaga_wa/MT1_enab8`).
2. Choose a **Cluster** from the dropdown.  
   `Top .v` and `Gen dir` are auto-filled — edit only if needed.
3. Click **▶ Run Pipeline**.  
   The live log streams all pipeline steps.  
   When done, analysis scripts (dim mismatches, threaded tie-offs, cross-cluster)  
   run automatically. Results are loaded into the **Results** tab.

### Tab 2 — Interface Spec

1. **Result dir** is auto-filled after a pipeline run; or Browse to any
   existing pipeline results directory.
2. Enter a **Module name** (e.g. `ifu`, `idq`, `stsr`).
3. **ICF glob** is auto-suggested from the cluster; edit if needed.
4. Click **▶ Generate Spec**.  
   The generated CSV is loaded into a new sub-tab in the **Results** tab.

### Tab 3 — Results

Sub-tabs appear as results are loaded:

| Sub-tab | Source |
|---------|--------|
| Dim Mismatches | `dim_mismatches_{cluster}.csv` |
| Cross-Cluster Dims | `dim_mismatches_cross_*.csv` |
| Threaded Tie-Offs | `tiedoff_threaded_{cluster}.csv` |
| Unresolved | `03_{cluster}_connectivity_unresolved.csv` |
| Spec: \<module\> | `interface_spec_{module}.csv` |

**Filtering:** type in the filter box above any column to filter rows (200 ms debounce).  
**Sorting:** click a column header to sort; click again to reverse.  
**Export:** click **Export CSV** (bottom-right) to save the current filtered view.

---

## Settings Persistence

The last-used model root, cluster, module name, result dir, and ICF glob
are saved to `~/.copilot/interfacespec_gui_settings.json` and restored on
the next launch.

---

## File Structure

```
scripts/interfacespec/qtgui/
    main.py                  # Entry point
    app.py                   # MainWindow
    config.py                # Cluster→path mappings
    utils.py                 # Helpers: find_latest_result_dir, load_csv, settings
    tabs/
        pipeline_tab.py      # Tab 1
        spec_tab.py          # Tab 2
        results_tab.py       # Tab 3 (inner Notebook)
    widgets/
        path_entry.py        # LabeledPathEntry widget
        log_panel.py         # Live log widget
        filtered_table.py    # Sortable + filterable Treeview
    runner/
        pipeline_runner.py   # Thread: runs run_cluster_pipeline.py
        analysis_runner.py   # Thread: runs dim/tiedoff/cross scripts
        spec_runner.py       # Thread: runs generate_module_io_table.py
```

## Backend Scripts

The GUI calls the following existing scripts (no modifications needed):

| Script | Purpose |
|--------|---------|
| `scripts/interfacespec/run_cluster_pipeline.py` | Full pipeline |
| `scripts/interfacespec/find_dimension_mismatches.py` | Per-cluster dim mismatches |
| `scripts/interfacespec/find_tiedoff_threaded_signals.py` | Threaded tie-offs |
| `scripts/interfacespec/find_cross_cluster_dimension_mismatches.py` | Cross-cluster dims |
| `scripts/interfacespec/generate_module_io_table.py` | Interface spec CSV |
