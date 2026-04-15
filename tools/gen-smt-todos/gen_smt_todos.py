#!/usr/intel/bin/python3.11.1
"""
gen_smt_jnc_todos.py

Scans core/fe/rtl and core/msid/rtl for RTL/ICF files containing comment lines
that have both a TODO/FIXME marker AND an SMT/JNC/T1/Thread-1 keyword.

Outputs:
  smt_jnc_todos.tsv         - TSV with columns: tree, unit, path, line, text
  smt_jnc_todos_summary.txt - Per-unit/file match counts

Run from the model root (MT1_enab* workspace root).
"""

from pathlib import Path
import re
import csv
from collections import defaultdict, Counter

# Output files written to the current working directory
out_tsv     = Path('smt_jnc_todos.tsv')
out_summary = Path('smt_jnc_todos_summary.txt')

roots = [Path('core/fe/rtl'), Path('core/msid/rtl')]

# Match comment lines containing both a TODO/FIXME and an SMT/JNC/T1/Thread-1 keyword
pat = re.compile(
    r'//.*(?:'
    r'(?:TODO|FIXME).*(?:SMT|JNC|T1|Thread 1|thread 1|MT mode|multithread|multi-thread)'
    r'|(?:SMT|JNC|T1|Thread 1|thread 1|MT mode|multithread|multi-thread).*(?:TODO|FIXME)'
    r')',
    re.I
)


def unit_for(path: Path) -> str:
    name = path.stem.lower()
    if 'bac' in name:                                                    return 'bac'
    if name.startswith('bp') or 'bpu' in name:                          return 'bpu'
    if name.startswith('ifu') or 'iftlb' in name:                       return 'ifu'
    if 'dsbfe' in name:                                                  return 'dsbfe'
    if name == 'dsbe' or 'dsbe.' in path.name.lower() or name.startswith('dsbe'): return 'dsbe'
    if name.startswith(('dsbq', 'dsbr', 'dsbfill', 'dsbpri', 'dsbpfrq', 'dsbshadow')): return 'dsbq'
    if name.startswith(('idu', 'ild')):                                  return 'idu'
    if name == 'id' or name.startswith('idq'):                          return 'id'
    if name.startswith('iq'):                                            return 'iq'
    if name.startswith('stsr'):                                          return 'stsr'
    if name.startswith('ms'):                                            return 'ms'
    return 'other'


rows = []
for root in roots:
    for ext in ('*.vs', '*.sv', '*.svh', '*.vh', '*.icf'):
        for fp in sorted(root.rglob(ext)):
            try:
                lines = fp.read_text(errors='ignore').splitlines()
            except Exception:
                continue
            for i, line in enumerate(lines, 1):
                if pat.search(line):
                    rows.append({
                        'tree': 'fe' if 'core/fe/' in str(fp) else 'msid',
                        'unit': unit_for(fp),
                        'path': str(fp),
                        'line': i,
                        'text': line.strip(),
                    })

# Write TSV
with out_tsv.open('w', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['tree', 'unit', 'path', 'line', 'text'])
    for r in rows:
        w.writerow([r['tree'], r['unit'], r['path'], r['line'], r['text']])

# Write summary
unit_counts = Counter(r['unit'] for r in rows)
file_counts = Counter((r['unit'], r['path']) for r in rows)
by_unit = defaultdict(list)
for (unit, path), cnt in file_counts.items():
    by_unit[unit].append((cnt, path))

with out_summary.open('w') as f:
    f.write(f'Total matches: {len(rows)}\n\n')
    for unit in sorted(unit_counts):
        f.write(f'{unit}: {unit_counts[unit]}\n')
        for cnt, path in sorted(by_unit[unit], reverse=True)[:10]:
            f.write(f'  {cnt:3d}  {path}\n')
        f.write('\n')

print(f'Written: {out_tsv}  ({len(rows)} rows)')
print(f'Written: {out_summary}')
for unit in sorted(unit_counts):
    print(f'  {unit}: {unit_counts[unit]}')
