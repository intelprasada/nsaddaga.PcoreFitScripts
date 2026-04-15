#!/usr/bin/env python3
"""Generate interface specification from a derived top interface CSV.

Generic — works for any module, not just IDQ. Classification uses signal
naming conventions, column hints (sou, cou, xhc, xhp, packed/unpacked
dimensions), and configurable rules.
"""
import argparse
import csv
import os
import re
from collections import defaultdict

parser = argparse.ArgumentParser(description="Generate interface spec from derived top interface CSV")
parser.add_argument("--io-csv", required=True, help="Path to derived top interface CSV")
parser.add_argument("--module", required=True, help="Module name (e.g. idq, ifu, stsr)")
parser.add_argument("--cluster", default="", help="Cluster description for the header")
parser.add_argument("--out-md", default=None, help="Output markdown path (default: <csv_dir>/<module>_interface_spec.md)")
parser.add_argument("--subgroup-threshold", type=int, default=10,
                    help="Sub-group interfaces with more signals than this by primary internal consumer")
parser.add_argument("--internal-csv", default=None,
                    help="Path to module's own 12_* query IO table for internal consumer/producer info")
args = parser.parse_args()

with open(args.io_csv) as fh:
    all_rows = list(csv.DictReader(fh))

# ---------------------------------------------------------------------------
# Build internal consumer/producer lookup from the module's own 12_* file.
# For each top-connected signal, collect instance_names by port_direction.
# ---------------------------------------------------------------------------
internal_consumers_map = {}   # port_name -> semicolon-joined instance names (input rows)
internal_producers_map = {}   # port_name -> semicolon-joined instance names (output rows)
if args.internal_csv:
    with open(args.internal_csv) as fh:
        internal_rows = list(csv.DictReader(fh))
    # Group by signal_name_normalized (the shared net name)
    _by_signal = defaultdict(list)
    for ir in internal_rows:
        sig = (ir.get('signal_name_normalized') or ir.get('port_name', '')).strip()
        if sig:
            _by_signal[sig].append(ir)
    # For each signal that connects to top, collect instance_names by direction
    for sig, sig_rows in _by_signal.items():
        if not any(r.get('is_connected_to_top', '') == 'true' for r in sig_rows):
            continue
        consumers = set()
        producers = set()
        for r in sig_rows:
            inst = (r.get('instance_name') or r.get('instance_module') or '').strip()
            if not inst:
                continue
            d = (r.get('port_direction') or '').strip()
            if d == 'output':
                producers.add(inst)
            elif d in ('input', 'inout'):
                consumers.add(inst)
        # Map using port_name from any connected-to-top row
        for r in sig_rows:
            if r.get('is_connected_to_top', '') == 'true':
                pn = r['port_name'].strip()
                if consumers:
                    internal_consumers_map[pn] = ';'.join(sorted(consumers))
                if producers:
                    internal_producers_map[pn] = ';'.join(sorted(producers))
                break

# ---------------------------------------------------------------------------
# Deduplication: collapse rows with the same port_name + port_direction.
# The CSV may contain one row per instance; we keep the first occurrence
# and merge distinct connected_other_units values.
# ---------------------------------------------------------------------------
_seen = {}
rows = []
for r in all_rows:
    key = (r['port_name'], r['port_direction'])
    if key in _seen:
        # Merge connected_other_units from duplicate rows
        existing = _seen[key]
        old_cou = set((existing.get('connected_other_units', '') or '').split(';'))
        new_cou = set((r.get('connected_other_units', '') or '').split(';'))
        merged = ';'.join(sorted(u for u in old_cou | new_cou if u and u != 'NONE')) or 'NONE'
        existing['connected_other_units'] = merged
        continue
    _seen[key] = r
    rows.append(r)

# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def is_clock(r):
    pn = r['port_name'].lower()
    return 'clk' in pn or pn == 'mclk' or 'clock' in pn


def is_scan_dft(r):
    """Rule 2/3: scan/fscan prefix OR dfx/mbist anywhere in name."""
    pn = r['port_name'].lower()
    return (pn.startswith('scan') or pn.startswith('fscan')
            or 'dfx' in pn or 'misr' in pn or 'jtag' in pn
            or 'safd' in pn or 'mbist' in pn)


def is_cr(r):
    pn = r['port_name']
    return pn.startswith('Cr') or pn.startswith('CR')


def is_power(r):
    """Rule 4: pwrdn/pwrdwn and spelling variations."""
    pn = r['port_name'].lower()
    return any(t in pn for t in ['pwrdn', 'pwrdwn', 'powerdis', 'stpowerdisable',
                                  'pwr_dn', 'pwrdown', 'powerdown'])


def is_lsd(r):
    """Rule 6: LSD signals form their own interface group."""
    pn = r['port_name']
    return 'LSD' in pn or 'lsd' in pn.lower()


def is_cdt(r):
    """CDT signals form their own Core Debug Trace interface group.

    Matches signal name containing 'cdt' or starting with 'trc', or signals
    whose primary internal consumer is a coredebugs unit.
    """
    pn = r['port_name'].lower()
    if 'cdt' in pn or pn.startswith('trc'):
        return True
    cou = r.get('connected_other_units', '')
    if cou and cou != 'NONE':
        units = cou.split(';')
        preferred = [u for u in units if not u.endswith('_emu')
                     and not u.endswith('_inst') and not u.endswith('_tlm')]
        if not preferred:
            preferred = units
        primary = sorted(preferred)[0]
        if 'coredebugs' in primary.lower():
            return True
    return False


def _preferred_consumer(consumers_str):
    """Rule 8: Among multiple consumers, prefer those NOT ending in _emu or _inst."""
    if not consumers_str or consumers_str == 'NONE':
        return consumers_str
    units = consumers_str.split(';')
    preferred = [u for u in units if not u.endswith('_emu') and not u.endswith('_inst')
                 and not u.endswith('_tlm')]
    if preferred:
        return preferred[0]
    return units[0]


def _normalize_external_key(unit_str):
    """Normalize external unit keys to reduce fragmentation.

    Strips cluster prefixes (e.g. 'FE<-BPU' -> 'bpu', 'MSID<-DSBE' -> 'dsbe',
    'OOO<-RS' -> 'rs') and lowercases to merge interfaces that refer to the
    same logical external partner.
    """
    if not unit_str or unit_str == 'NONE':
        return unit_str
    # Strip cluster prefix like 'FE<-', 'MSID<-', 'OOO<-'
    if '<-' in unit_str:
        unit_str = unit_str.split('<-', 1)[1]
    # Normalize compound cross-hierarchy keys like 'fe/bac' -> 'bac'
    if '/' in unit_str:
        unit_str = unit_str.split('/')[-1]
    return unit_str.lower()


def get_external_unit(r):
    """Get the primary external unit this signal connects to.

    Uses external source/consumer as primary key, with internal
    consumer/producer data to resolve NONE/UNKNOWN cases.
    """
    pn = r['port_name']
    d = r['port_direction']
    if d == 'input':
        sou = r.get('source_output_units', '')
        if sou and sou != 'NONE':
            # Skip any token that equals the target module itself — that would
            # mean the module is its own source, which is an INTEL_INST_ON
            # guard artifact where the module appears as a connected unit.
            tokens = [t for t in sou.split(';') if t and t != args.module]
            if tokens:
                return _normalize_external_key(tokens[0])
            # All tokens were the module itself — fall through to UNKNOWN
        # Fallback: infer from internal consumers if available
        if internal_consumers_map:
            ic = internal_consumers_map.get(pn, '')
            if ic:
                # Multiple internal consumers -> broadly consumed input, group by first
                return 'UNKNOWN'
        return 'UNKNOWN'
    else:
        # For outputs: prefer xhier_consumers, then connected_other_units
        xhc = r.get('xhier_consumers', '')
        if xhc and xhc != 'NONE':
            return _normalize_external_key(_preferred_consumer(xhc))
        cou = r.get('connected_other_units', '')
        if cou and cou != 'NONE':
            return _normalize_external_key(_preferred_consumer(cou))
        # Fallback: use internal producer to infer interface key
        if internal_producers_map:
            ip = internal_producers_map.get(pn, '')
            if ip:
                return '%s_OUTPUT' % _preferred_consumer(ip).upper()
        sou = r.get('source_output_units', '')
        if sou and sou != 'NONE':
            return '%s_OUTPUT' % _preferred_consumer(sou).upper()
        return 'UNKNOWN'


# Special categories that are NOT sub-grouped (rules 2, 3, 4)
# INSTRUMENTAL and UNCONNECTED are also special but rendered last.
SPECIAL_NO_SUBGROUP = {
    'CLOCKS', 'SCAN_AND_DFT', 'CONTROL_REGISTERS', 'POWER_OVERRIDES',
    'LSD', 'CORE_DEBUG_TRACE', 'INSTRUMENTAL', 'UNCONNECTED',
}

SPECIAL_ORDER = ['CLOCKS', 'SCAN_AND_DFT', 'CONTROL_REGISTERS', 'POWER_OVERRIDES', 'LSD', 'CORE_DEBUG_TRACE']

# These always render at the very end of the document.
TRAILING_SECTIONS = ['INSTRUMENTAL', 'UNCONNECTED']


def classify_interface(r):
    """Classify a signal into an interface group.

    Instrumental (_inst suffix) and unconnected signals are separated
    into their own trailing sections before any other classification runs.
    """
    pn = r['port_name']
    # Instrumental signals — always go to their own section regardless of category.
    if pn.endswith('_inst'):
        return 'INSTRUMENTAL'
    # Unconnected signals — is_unconnected column set to 'true'.
    if (r.get('is_unconnected') or '').strip().lower() == 'true':
        return 'UNCONNECTED'
    # Scan/DFT must be checked BEFORE clocks so fscan_clkungate is not
    # misclassified as a clock signal (it contains 'clk' but is scan infra).
    if is_scan_dft(r):
        return 'SCAN_AND_DFT'
    if is_clock(r):
        return 'CLOCKS'
    if is_cr(r):
        return 'CONTROL_REGISTERS'
    if is_power(r):
        return 'POWER_OVERRIDES'
    if is_cdt(r):
        return 'CORE_DEBUG_TRACE'
    if is_lsd(r):
        return 'LSD'
    return get_external_unit(r)


# --- Classify all signals ---
groups = defaultdict(list)
for r in rows:
    iface = classify_interface(r)
    groups[iface].append(r)


# --- Build display name map dynamically ---
SPECIAL_DISPLAY = {
    'CLOCKS': 'Clocks',
    'SCAN_AND_DFT': 'Scan and DFT',
    'CONTROL_REGISTERS': 'Control Registers',
    'POWER_OVERRIDES': 'Power Overrides',
    'LSD': 'LSD (Loop Stream Detector)',
    'CORE_DEBUG_TRACE': 'Core Debug Trace (CDT)',
    'UNKNOWN': 'Unresolved / Unknown',
    'INSTRUMENTAL': 'Instrumental Signals (_inst)',
    'UNCONNECTED': 'Unconnected Signals',
}


def iface_display(key):
    if key in SPECIAL_DISPLAY:
        return SPECIAL_DISPLAY[key]
    if key.endswith('_INTERNAL'):
        return '%s Internal' % key.replace('_INTERNAL', '')
    if key.endswith('_OUTPUT'):
        return '%s Outputs' % key.replace('_OUTPUT', '')
    # Format cluster/unit keys nicely
    if '/' in key:
        parts = key.split('/')
        return '%s / %s' % (parts[0].upper(), parts[1].upper())
    return key


# ---------------------------------------------------------------------------
# Signal description from naming + dimension hints (Rule 7)
# ---------------------------------------------------------------------------

def signal_description(r):
    pn = r['port_name']
    hints = []

    # Prefix-based hints
    if pn.startswith('Bp'):
        hints.append('Branch prediction')
    elif pn.startswith('RO'):
        hints.append('Retirement/ROB')
    elif pn.startswith('RA'):
        hints.append('Register allocation')
    elif re.match(r'^Je', pn):
        hints.append('Jump execution/clear')
    elif pn.lower().startswith('ds') or pn.lower().startswith('dsb'):
        hints.append('DSB decode path')
    elif pn.lower().startswith('mite'):
        hints.append('MITE decode path')
    elif pn.startswith('LSD') or 'lsd' in pn.lower():
        hints.append('Loop stream detector')
    elif pn.startswith('LVP'):
        hints.append('Load value prediction')
    elif pn.lower().startswith('ms') and not pn.lower().startswith('msid'):
        hints.append('Microcode sequencer')
    elif pn.startswith('Dis'):
        hints.append('Disable/chicken-bit')
    elif pn.startswith('Force'):
        hints.append('Force override')
    elif pn.startswith('Assert'):
        hints.append('Assert mask')
    elif pn.lower().startswith('mca'):
        hints.append('Machine check architecture')
    elif pn.lower().startswith('duop'):
        hints.append('Decoded micro-op')
    elif 'cdt' in pn.lower():
        hints.append('Core debug trace')

    if pn.endswith('_inst'):
        hints.append('instrumentation')

    # Rule 7: Dimension-based hints
    pw = r.get('direction_packed_width', '')
    ud = r.get('direction_unpacked_dim', '')
    dims = pw + ' ' + ud
    if 'THREAD' in dims:
        hints.append('per-thread')
    if 'WRITE_PORT' in dims:
        hints.append('write-port width')
    if 'READ_PORT' in dims:
        hints.append('read-port width')
    if 'AUOP' in dims or 'UOP' in dims:
        hints.append('micro-op related')
    if 'BIQ' in dims or 'TBIQ' in dims:
        hints.append('branch info queue')
    if 'MRN' in dims:
        hints.append('rename related')
    if 'LSD' in dims:
        hints.append('LSD related')

    return '; '.join(hints) if hints else ''


# ---------------------------------------------------------------------------
# Sub-grouping by primary internal consumer (Rule 5 & 8)
# ---------------------------------------------------------------------------

def primary_internal_submodule(r):
    """Pick the single best internal sub-module for sub-grouping.

    Uses internal consumer map (for inputs) or internal producer map
    (for outputs) from the module's own 12_* query IO table.
    Falls back to connected_other_units from the parent view.

    Rule 5: Don't create a group per unique combination.
    Rule 8: Prefer units NOT ending in _emu, _inst, or _tlm.
    """
    pn = r['port_name']
    d = (r.get('port_direction') or '').strip()

    # Prefer internal maps when available
    if d == 'output' and internal_producers_map:
        ip = internal_producers_map.get(pn, '')
        if ip:
            units = ip.split(';')
            preferred = [u for u in units if not u.endswith('_emu') and not u.endswith('_inst')
                         and not u.endswith('_tlm')]
            return sorted(preferred)[0] if preferred else units[0]
    if d == 'input' and internal_consumers_map:
        ic = internal_consumers_map.get(pn, '')
        if ic:
            units = ic.split(';')
            preferred = [u for u in units if not u.endswith('_emu') and not u.endswith('_inst')
                         and not u.endswith('_tlm')]
            return sorted(preferred)[0] if preferred else units[0]

    # Fallback to connected_other_units from parent view
    cou = r.get('connected_other_units', '')
    if not cou or cou == 'NONE':
        return 'OTHER'
    units = cou.split(';')
    preferred = [u for u in units if not u.endswith('_emu') and not u.endswith('_inst')
                 and not u.endswith('_tlm')]
    if not preferred:
        preferred = units
    return sorted(preferred)[0]


def emit_signal_table(lines, sigs, direction):
    if direction == 'input':
        lines.append('| Signal | Direction | Width | Source | Internal Consumers | Description |')
        lines.append('|--------|-----------|-------|-------|--------------------|-------------|')
        for s in sorted(sigs, key=lambda x: x['port_name']):
            pn = s['port_name']
            pw = s.get('direction_packed_width', '') or '1-bit'
            sou = s.get('source_output_units', 'NONE')
            # Use internal consumer lookup if available, else fall back to cou
            int_cons = internal_consumers_map.get(pn, '') if internal_consumers_map else ''
            if not int_cons:
                int_cons = s.get('connected_other_units', 'NONE')
            desc = signal_description(s)
            lines.append('| `%s` | Input | `%s` | %s | %s | %s |' % (pn, pw, sou, int_cons, desc))
    else:
        lines.append('| Signal | Direction | Width | Internal Producers | External Consumers | Description |')
        lines.append('|--------|-----------|-------|--------------------|--------------------|-------------|')
        for s in sorted(sigs, key=lambda x: x['port_name']):
            pn = s['port_name']
            pw = s.get('direction_packed_width', '') or '1-bit'
            # Use internal producer lookup if available, else fall back to sou
            int_prod = internal_producers_map.get(pn, '') if internal_producers_map else ''
            if not int_prod:
                int_prod = s.get('source_output_units', 'NONE')
            xhc = s.get('xhier_consumers', '') or ''
            cou = s.get('connected_other_units', '') or ''
            # Prefer xhier_consumers; fall back to connected_other_units
            ext = xhc if xhc and xhc != 'NONE' else (cou if cou and cou != 'NONE' else 'NONE')
            desc = signal_description(s)
            lines.append('| `%s` | Output | `%s` | %s | %s | %s |' % (pn, pw, int_prod, ext, desc))
    lines.append('')


# ---------------------------------------------------------------------------
# Generate markdown
# ---------------------------------------------------------------------------
lines = []
lines.append('# %s Interface Specification' % args.module.upper())
lines.append('')
lines.append('Auto-generated from `%s` using interface grouping rules.' % os.path.basename(args.io_csv))
lines.append('')
lines.append('**Module**: `%s`' % args.module)
if args.cluster:
    lines.append('**Cluster**: %s' % args.cluster)
lines.append('**Total Signals**: %d (Inputs: %d, Outputs: %d)' % (
    len(rows),
    sum(1 for r in rows if r['port_direction'] == 'input'),
    sum(1 for r in rows if r['port_direction'] == 'output'),
))
lines.append('')

# Summary table
lines.append('## Interface Summary')
lines.append('')
lines.append('| # | Interface | Inputs | Outputs | Total |')
lines.append('|---|-----------|--------|---------|-------|')

ordered_keys = []
for k in SPECIAL_ORDER:
    if k in groups:
        ordered_keys.append(k)
remaining = sorted(
    [k for k in groups if k not in SPECIAL_ORDER and k not in TRAILING_SECTIONS],
    key=lambda k: -len(groups[k])
)
ordered_keys.extend(remaining)
# Force trailing sections to the very end of the document.
for k in TRAILING_SECTIONS:
    if k in groups:
        ordered_keys.append(k)

for i, k in enumerate(ordered_keys, 1):
    sigs = groups[k]
    inp = sum(1 for s in sigs if s['port_direction'] == 'input')
    out = sum(1 for s in sigs if s['port_direction'] == 'output')
    lines.append('| %d | %s | %d | %d | %d |' % (i, iface_display(k), inp, out, len(sigs)))

lines.append('')

# Detail tables
for k in ordered_keys:
    sigs = groups[k]
    name = iface_display(k)
    lines.append('---')
    lines.append('')
    lines.append('## %s' % name)
    lines.append('')

    if k in SPECIAL_NO_SUBGROUP:
        lines.append('**Category**: Special — %s' % name)
    else:
        lines.append('**Connected Unit**: `%s`' % k)

    inp_sigs = [s for s in sigs if s['port_direction'] == 'input']
    out_sigs = [s for s in sigs if s['port_direction'] == 'output']
    lines.append('**Signal Count**: %d (Inputs: %d, Outputs: %d)' % (len(sigs), len(inp_sigs), len(out_sigs)))
    lines.append('')

    if inp_sigs:
        lines.append('### Inputs')
        lines.append('')
        emit_signal_table(lines, inp_sigs, 'input')

    if out_sigs:
        lines.append('### Outputs')
        lines.append('')
        emit_signal_table(lines, out_sigs, 'output')

out_path = args.out_md or os.path.join(os.path.dirname(args.io_csv), '%s_interface_spec.md' % args.module)
with open(out_path, 'w') as fh:
    fh.write('\n'.join(lines))
print('WROTE=%s' % out_path)
print('INTERFACES=%d' % len(ordered_keys))
print('SIGNALS=%d' % len(rows))
