# Interface Specification Generation Rules

These rules govern how to generate an interface specification from a derived top
interface CSV. They are **module-generic** — the same rules apply regardless of
which unit's interface spec is being created (IDQ, IFU, STSR, DSBE, etc.).

## Input

The primary input is the derived top interface CSV produced by
`derive_top_interface_from_drilldown.py`. Key columns used:

| Abbreviation | Column | Usage |
|---|---|---|
| `pn` | `port_name` | Signal name — primary grouping key via prefix analysis |
| `pd` | `port_direction` | `input` or `output` — determines signal flow |
| `sou` | `source_output_units` | For inputs: the producing unit (RTL-verified via xhier) |
| `cou` | `connected_other_units` | Internal subunits that consume/produce this signal |
| `xhc` | `xhier_consumers` | Cross-hierarchy consumers (for outputs) |
| `xhp` | `xhier_producers` | Cross-hierarchy producers (for inputs) |
| `pw` | `direction_packed_width` | Bit width / packed dimensions |
| `ud` | `direction_unpacked_dim` | Unpacked dimensions (e.g., arrays) |
| `ct` | `is_connected_to_top` | Whether the signal crosses the module boundary |

## Rule 1: Primary Grouping by Connected Unit

Interfaces are **strictly divided by the external unit** the top module connects
to. This is determined by:

- **For inputs**: Use `source_output_units` (`sou`) to identify the producing unit
- **For outputs**: Use `xhier_consumers` (`xhc`) to identify the consuming unit(s).
  When multiple consumers exist, **prefer** those whose names do NOT end in
  `_emu`, `_inst`, or `_tlm` (Rule 8).

Each external unit forms its own interface group.

### Unit Hierarchy Notation

Units may appear as:
- `msid/dsbe` — subunit `dsbe` within cluster `msid`
- `ooo/ratal` — subunit `ratal` within cluster `ooo`
- `icore/fe` — unit `fe` at the `icore` level

Group by the **leaf unit** for interfaces within the same parent cluster, or by
`cluster/unit` for cross-cluster interfaces.

## Rule 2: Special Interface Categories (Override Grouping)

Before applying unit-based grouping, extract signals into these special
categories. These take priority over unit-based grouping. **Special categories
are never sub-grouped by internal consumer** — they remain flat regardless of
cou values.

### 2a. Clock Interface
- **Match**: Signal name contains `clk` or `clock` (case-insensitive), or name
  is exactly `Mclk`
- **Examples**: `Mclk`, `ClkCR3n1MH`, `ClkMsidAssert4MH`

### 2b. Scan and DFT Interface
- **Match**: Signal name **starts with** `scan` or `fscan` (case-insensitive)
- **Match**: Signal name contains `dfx` anywhere (case-insensitive)
- **Also match**: Names containing `misr`, `jtag`, or `safd`
- **Examples**: `fscan_clkungate`, `scan_byprst_b`, `msid_dfxmisrenmnnh`,
  `safd_scan_isolation`

### 2c. Control Register Interface
- **Match**: Signal name starts with `Cr` or `CR` (case-sensitive prefix)
- **Hint**: CR signals are typically configuration/chicken-bit signals
  distributed through a CR bus unit
- **Examples**: `CrDIS_LVPMnn1H`

### 2d. Power Overrides Interface
- **Match**: Signal name contains any spelling variation implying power-down or
  power-disable: `pwrdn`, `pwrdwn`, `pwr_dn`, `pwrdown`, `powerdown`,
  `powerdis`, `stpowerdisable` (case-insensitive)
- **Examples**: `PwrdnBitMnnn1H`, `IdqStPowerDisableMnnnH`,
  `DisFitPwrDwnWBiqFullMnn1H`

### 2e. LSD (Loop Stream Detector) Interface
- **Match**: Signal name contains `LSD` or `lsd` (case-insensitive)
- **Examples**: `LSDLoop_M148H`, `dsLSDEndUop_M185H`, `idLSDStallM109H`

### 2f. Core Debug Trace (CDT) Interface
- **Match**: Signal name contains `cdt` (case-insensitive)
- **Match**: Signal name **starts with** `Trc` (case-insensitive) — trace signals
- **Also match**: Signals whose primary internal consumer (from `cou`) contains
  `coredebugs` (case-insensitive). This merges debug observation signals into
  CDT rather than scattering them as sub-groups in other interfaces.
- **Examples**: `IdqCdtFilterLsdMnn1H`, `CdtAllocValidM109H`,
  `TrcIDQAryWrDatKeeperXorM205H` (Trc prefix),
  `mltaSigntrRstM10nH` (primary consumer `idqcoredebugs`)

## Rule 3: Signal Name Prefix Convention

Signal naming generally starts with an abbreviation of the **origin unit**. Use
this to confirm grouping:

| Prefix | Origin Unit | Description |
|--------|------------|-------------|
| `Bp` | BPU (Branch Prediction Unit) | Branch prediction signals from FE |
| `RO` | ROB (Reorder Buffer) | Retirement/nuke signals from OOO |
| `RA` | RAT/RATAL (Register Alias Table) | Register allocation signals from OOO |
| `Je` | JE (Jump Execution) | Jump/clear signals from OOO |
| `ds` / `Ds` / `DSB` | DSBE (Decode Stream Buffer) | DSB decode path signals |
| `mite` / `Mite` | MITE (Macro Instruction Translation Engine) | MITE decode path |
| `Id` / `ID` / `IDL` | ID / IDQ (Instruction Decode Queue) | IDQ-internal signals |
| `LSD` | LSD (Loop Stream Detector) | Loop detection signals |
| `LVP` | LVP (Load Value Prediction) | Load value prediction signals |
| `ms` / `Ms` | MS (Microcode Sequencer) | Microcode sequencer signals |
| `fscan_` / `scan_` | Scan infrastructure | DFT/scan chain signals |
| `Dis` | Disable/chicken-bit | Configuration disable signals |
| `Force` | Force override | Debug/override forcing signals |
| `Assert` | Assert mask | Error assertion mask signals |
| `mca` | MCA (Machine Check Architecture) | Error reporting signals |

## Rule 4: Use Column Hints for Functional Purpose

When a signal's function is ambiguous from its name alone, use these column hints:

1. **`sou`**: Who produces the signal for inputs. `sou = ooo/ratal` → RAT signal from OOO.

2. **`cou`**: Which internal subunits consume/produce the signal.

3. **`xhc`**: For outputs, which cross-hierarchy units consume it.

4. **`xhp`**: For inputs, the RTL-verified producer at the hierarchy level.

5. **`pw` / `ud` (packed/unpacked dimensions)**: Parameterized dimension names
   are strong functionality hints:
   - `CORE_SMT_NUM_THREADS` → per-thread signal
   - `GFC_DSB_IDQ_WRITE_PORT_SIZE` → DSB write-port-wide signal
   - `ID_MAX_DECODED_UOPS` → decoded micro-op width
   - `AUOP` or `UOP` → micro-op related
   - `BIQ` or `TBIQ` → branch info queue
   - `MRN` → rename related
   - `LSD` → loop stream detector related
   - `READ_PORT` / `WRITE_PORT` → port-width signal

## Rule 5: Sub-Grouping Within Large Interfaces

When an interface has more than the threshold number of signals (default 10),
sub-group by **primary internal consumer** from the `cou` column.

**Important**: Do NOT create a separate sub-group for every unique combination of
consumers. Instead, pick the **single primary consumer** for each signal:

1. Split `cou` into individual units
2. **Discard** units ending in `_emu`, `_inst`, or `_tlm` (Rule 8)
3. From the remaining units, pick the first alphabetically as the sub-group key
4. If all units were filtered out, use the original first unit

This ensures a reasonable number of sub-groups (one per actual internal subunit)
rather than a combinatorial explosion.

**Special categories (Clocks, Scan/DFT, CR, Power Overrides, LSD, CDT) are
never sub-grouped** — they remain flat tables regardless of how many signals
they have.

## Rule 6: Handling Signals with `_inst` Suffix

Signals ending in `_inst` are **instrumentation/observability** copies of
architectural signals. Group them in the same interface as their base signal
(without `_inst`) but mark them as instrumentation in the description.

## Rule 7: Handling Tied-Off Signals

Signals with `sou = icore/TIEDOFF_CONST` or `sou = <cluster>/TIEDOFF_CONST` are
constants tied off at a parent hierarchy level. Check `xhier_tiedoff_expr` for
the actual value. Group these by their functional category (scan, clock, etc.)
rather than by "tiedoff" as a unit.

## Rule 8: Consumer Priority

When a signal has multiple consumers in `cou` or `xhc`:
- **Prefer** consumers whose names do NOT end in `_emu`, `_inst`, or `_tlm`
- These suffixed units are emulation, instrumentation, or TLM wrappers and
  should not drive the interface grouping
- Example: if `xhc = msid/fe_latch_emu;msid/fe_macroinst_tlm;ooo/ratal`, the
  preferred consumer is `ooo/ratal`

## Rule 9: Interface Specification Output Format

### Top-Level Structure
```
# <MODULE> Interface Specification

**Module**: `<module>`
**Cluster**: <cluster>
**Total Signals**: N (Inputs: X, Outputs: Y)

## Interface Summary
| # | Interface | Inputs | Outputs | Total |
...
```

### Per-Interface Detail (small, ≤ threshold)
```
## <Interface Name>

**Connected Unit**: `<unit>`
**Signal Count**: N (Inputs: X, Outputs: Y)

### Inputs
| Signal | Width | Source | Internal Consumers | Description |
...

### Outputs
| Signal | Width | Internal Producers | External Consumers | Description |
...
```

### Per-Interface Detail (large, > threshold) — with sub-groups
```
## <Interface Name>

**Connected Unit**: `<unit>`
**Signal Count**: N (Inputs: X, Outputs: Y)

### <primary_consumer> (M signals)
| Signal | Width | Source | Internal Consumers | Description |
...

### <another_consumer> (K signals)
...
```

### Ordering
1. Special categories first: Clocks, Scan/DFT, Control Registers, Power Overrides, LSD, CDT
2. Then unit-based interfaces, ordered by signal count (largest first)
3. Within each interface, sub-groups ordered by signal count (largest first)
4. Within each sub-group, inputs before outputs, then alphabetical

## Rule 10: Internal-Only Signals

Signals where `is_connected_to_top = false` are internal to the module and do
not appear in the external interface specification. They may be listed in a
separate "Internal Signals" appendix if requested.

## Example Classification

Given signal `BpCrDisUoRFFEIPNewScheme308H`:
- Prefix `Bp` → Branch Prediction origin
- `sou = icore/fe` → Produced by FE cluster
- `cou = idqctln;idqued` → Primary consumer = `idqctln` (Rule 5)
- Classification: **FE cross-cluster Interface**, sub-group **idqctln**

Given signal `RONukeAllM907H`:
- Prefix `RO` → ROB origin
- `sou = msid/msid_misc` → Produced by MSID misc (forwarded from OOO)
- `cou = idqctln` → Single consumer
- Classification: **MSID/MSID_MISC Interface**, sub-group **idqctln**

Given signal `dsLSDEndUop_M185H`:
- Name contains `LSD` → Special category override
- Classification: **LSD Interface** (no sub-grouping)

Given signal `msid_dfxmisrenmnnh`:
- Name contains `dfx` → Special category override
- Classification: **Scan and DFT Interface** (no sub-grouping)

Given signal `IdqCdtFilterLsdMnn1H`:
- Name contains `Cdt` → Special category override
- Classification: **Core Debug Trace (CDT) Interface** (no sub-grouping)

## Generator Script

The interface specification can be auto-generated using:

```tcsh
python3 scripts/interfacespec/generate_interface_spec.py \
  --io-csv <derived_top_interface.csv> \
  --module <module_name> \
  --cluster <cluster_description> \
  --subgroup-threshold 10 \
  --out-md <output.md>
```

### CLI Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--io-csv` | Yes | — | Path to derived top interface CSV |
| `--module` | Yes | — | Module name (e.g. `idq`, `ifu`, `stsr`) |
| `--cluster` | No | `""` | Cluster description for header |
| `--subgroup-threshold` | No | `10` | Interfaces with more signals are sub-grouped by primary consumer |
| `--out-md` | No | `<csv_dir>/<module>_interface_spec.md` | Output markdown path |
