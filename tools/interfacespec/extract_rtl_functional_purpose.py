#!/usr/bin/env python3
"""Extract functional purpose of each signal from RTL source files.

For each signal in the IO table CSV, searches the RTL files referenced by
source_output_units and connected_other_units, extracts surrounding comments
and usage context, and produces a one-line functional purpose description.

The functional purpose is inserted as a new column in the output CSV.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

# Map unit names (lowercase) to RTL directories/file patterns
UNIT_TO_RTL_DIRS: dict[str, list[str]] = {
    # FE children
    "ifctlc":       ["core/fe/rtl"],
    "ifcrbrtsts":   ["core/fe/rtl"],
    "ifmcache":     ["core/fe/rtl"],
    "ifsbd":        ["core/fe/rtl"],
    "iftlbd":       ["core/fe/rtl"],
    "ifmemd":       ["core/fe/rtl"],
    "ifu_arrays":   ["core/fe/rtl"],
    "ifu_clks":     ["core/fe/rtl"],
    "ifdatalatchunkd": ["core/fe/rtl"],
    "bpu":          ["core/fe/rtl"],
    "bac":          ["core/fe/rtl"],
    "dsbfe":        ["core/fe/rtl"],
    "fe_misc":      ["core/fe/rtl"],
    # Cross-cluster
    "meu":          ["core/meu/rtl"],
    "dcu":          ["core/meu/rtl"],
    "mcu":          ["core/meu/rtl"],
    "pmh":          ["core/meu/rtl"],
    "mlc":          ["core/mlc/rtl"],
    "ooo":          ["core/ooo/rtl"],
    "exe":          ["core/exe/rtl"],
    "msid":         ["core/msid/rtl"],
}

# File extensions to search
RTL_EXTENSIONS = {".vs", ".icf", ".svh", ".sv", ".vh"}

# Patterns that indicate how a signal is used
USAGE_PATTERNS = [
    (re.compile(r"`CORE_MSFF\s*\(\s*(\S+)\s*,\s*(\S+)\s*,"), "flop"),
    (re.compile(r"`CORE_MSFF_EN\s*\(\s*(\S+)\s*,\s*(\S+)\s*,"), "flop_en"),
    (re.compile(r"`CORE_ICG\w*\s*\("), "clock_gate"),
    (re.compile(r"\bassign\b"), "assign"),
    (re.compile(r"\balways_comb\b"), "comb_logic"),
    (re.compile(r"\balways_ff\b"), "seq_logic"),
    (re.compile(r"\bglbdrvff\b"), "global_driver_ff"),
    (re.compile(r"output\s+"), "port_output"),
    (re.compile(r"input\s+"), "port_input"),
]

# Comment-line pattern
COMMENT_RE = re.compile(r"^\s*//(.*)$")
BLOCK_COMMENT_START = re.compile(r"/\*")
BLOCK_COMMENT_END = re.compile(r"\*/")

# Signal-name prefix to functional domain hints
PREFIX_HINTS = {
    "ML": "MLC interface",
    "BP": "branch prediction",
    "BA": "BAC interface",
    "DSB": "decoded stream buffer",
    "PM": "power management",
    "IF": "IFU internal",
    "GQ": "global/power",
    "Clk": "clock",
    "Rst": "reset",
    "Fscan": "DFT scan",
}


# ──────────────────────────────────────────────────────────────────────────────
# RTL Index Builder
# ──────────────────────────────────────────────────────────────────────────────

class RTLIndex:
    """Builds an in-memory index of signal occurrences across RTL files.

    Reads each RTL file once, stores per-signal context lines.
    """

    def __init__(self, workspace_root: str, signal_names: set[str]):
        self.root = Path(workspace_root)
        self.signals = signal_names
        # signal_name -> list of (file, line_no, line_text, context_before)
        self.occurrences: dict[str, list[dict]] = defaultdict(list)
        self._dirs_scanned: set[str] = set()

    def scan_directory(self, rel_dir: str):
        """Scan all RTL files in a directory for signal occurrences."""
        if rel_dir in self._dirs_scanned:
            return
        self._dirs_scanned.add(rel_dir)
        dir_path = self.root / rel_dir
        if not dir_path.is_dir():
            return

        for fpath in sorted(dir_path.iterdir()):
            if not fpath.is_file():
                continue
            if fpath.suffix not in RTL_EXTENSIONS:
                continue
            self._scan_file(fpath)

    def _scan_file(self, fpath: Path):
        """Scan a single file for signal name occurrences."""
        try:
            lines = fpath.read_text(errors="replace").splitlines()
        except (OSError, PermissionError):
            return

        rel_path = str(fpath.relative_to(self.root))

        for i, line in enumerate(lines):
            for sig in self.signals:
                if sig in line:
                    # Collect context: up to 5 lines before
                    ctx_start = max(0, i - 5)
                    context_before = lines[ctx_start:i]
                    # Collect 2 lines after
                    context_after = lines[i + 1:min(len(lines), i + 3)]
                    self.occurrences[sig].append({
                        "file": rel_path,
                        "line_no": i + 1,
                        "line": line,
                        "context_before": context_before,
                        "context_after": context_after,
                    })

    def scan_for_units(self, units: list[str]):
        """Scan RTL directories relevant to the given unit names."""
        for unit in units:
            unit_lower = unit.strip().lower()
            # Handle chain notation like "MEU<-MCU<-DCU"
            for token in re.split(r"<-|->", unit_lower):
                token = token.strip()
                if token in UNIT_TO_RTL_DIRS:
                    for d in UNIT_TO_RTL_DIRS[token]:
                        self.scan_directory(d)


# ──────────────────────────────────────────────────────────────────────────────
# Functional Purpose Inference
# ──────────────────────────────────────────────────────────────────────────────

def extract_nearby_comments(occ: dict) -> list[str]:
    """Extract comment lines from context before a signal occurrence."""
    comments = []
    for line in reversed(occ["context_before"]):
        m = COMMENT_RE.match(line)
        if m:
            txt = m.group(1).strip()
            if txt and len(txt) > 3:
                comments.insert(0, txt)
        elif line.strip():
            break  # Stop at non-comment, non-empty line
    return comments


def extract_inline_desc(occ: dict) -> str:
    """Extract desc("...") or inline // comment from the signal line."""
    line = occ["line"]
    # desc("...") pattern  (ironchef)
    m = re.search(r'desc\(\s*"([^"]+)"\s*\)', line)
    if m:
        return m.group(1).strip()
    # Inline comment after code
    m = re.search(r'//\s*(.+)$', line)
    if m:
        txt = m.group(1).strip()
        # Skip auto-generated connection comments like "ifctlc ifctlc,input"
        if not re.match(r'^\w+ \w+,(input|output)', txt):
            return txt
    return ""


def classify_usage(occ: dict) -> str:
    """Classify how the signal is used in this occurrence."""
    line = occ["line"]
    for pattern, label in USAGE_PATTERNS:
        if pattern.search(line):
            return label
    return "reference"


def infer_purpose(signal_name: str, occurrences: list[dict],
                  direction: str, src_units: str, peer_units: str) -> str:
    """Infer a one-line functional purpose from RTL occurrences and metadata."""
    if not occurrences:
        return _fallback_purpose(signal_name, direction, src_units, peer_units)

    # Prioritized comment collection
    inline_descs: list[str] = []
    block_comments: list[str] = []
    usage_types: set[str] = set()
    producer_file = ""
    consumer_file = ""

    for occ in occurrences:
        # Inline desc("...") or meaningful inline comment
        idesc = extract_inline_desc(occ)
        if idesc:
            inline_descs.append(idesc)

        # Block comments above the signal
        comments = extract_nearby_comments(occ)
        block_comments.extend(comments)

        usage = classify_usage(occ)
        usage_types.add(usage)

        # Track producer vs consumer files
        if "flop" in usage or usage == "assign" or usage == "global_driver_ff":
            line = occ["line"]
            m = re.search(r"`CORE_MSFF\w*\s*\(\s*(\S+?)\s*[,\[]", line)
            if m:
                flop_out = m.group(1).split("[")[0].split(".")[0]
                if signal_name in flop_out or flop_out.startswith(signal_name.split("[")[0]):
                    producer_file = occ["file"]
                else:
                    consumer_file = occ["file"]
            elif "assign" in usage:
                if re.search(rf"\bassign\s+.*\b{re.escape(signal_name)}\b", line):
                    producer_file = occ["file"]
                else:
                    consumer_file = occ["file"]

        if usage == "port_output":
            producer_file = producer_file or occ["file"]
        elif usage == "port_input":
            consumer_file = consumer_file or occ["file"]

    # Build purpose - prioritize desc() > block comments > fallback
    parts = []

    # Best description
    unique_descs = list(dict.fromkeys(inline_descs))
    unique_comments = list(dict.fromkeys(block_comments))

    # Filter out noisy comments
    noise_patterns = [
        re.compile(r'^-+$'),              # dashes
        re.compile(r'^=+$'),              # equals
        re.compile(r'^\*+$'),             # stars
        re.compile(r'^(begin|end)\b'),    # begin/end labels
        re.compile(r'^[A-Z_]+:?\s*$'),    # just a label
        re.compile(r'^\s*$'),             # empty
        re.compile(r'^ifdef|^endif'),     # preprocessor
        re.compile(r'^Alexey signals'),   # dev note
        re.compile(r'^node\s'),           # declaration echo
        re.compile(r'^`'),                # macro echo
    ]
    def is_useful_comment(c: str) -> bool:
        if len(c) < 5:
            return False
        return not any(p.search(c) for p in noise_patterns)

    good_descs = [d for d in unique_descs if is_useful_comment(d)]
    good_comments = [c for c in unique_comments if is_useful_comment(c)]

    if good_descs:
        best = good_descs[0]
        if len(best) > 100:
            best = best[:97] + "..."
        parts.append(best)
    elif good_comments:
        best = good_comments[0]
        if len(best) > 100:
            best = best[:97] + "..."
        parts.append(best)

    # Usage type
    usage_str = []
    if "flop" in usage_types or "flop_en" in usage_types or "seq_logic" in usage_types:
        usage_str.append("sequential")
    if "comb_logic" in usage_types or "assign" in usage_types:
        usage_str.append("combinational")
    if "clock_gate" in usage_types:
        usage_str.append("clock_gating")
    if "global_driver_ff" in usage_types:
        usage_str.append("global_driver")
    if usage_str:
        parts.append("/".join(usage_str))

    # Source/destination
    if src_units and src_units != "NONE":
        clean_src = src_units.replace("<-", "→")
        if direction.lower() == "input":
            parts.append(f"from:{clean_src}")
        else:
            parts.append(f"src:{clean_src}")
    if peer_units and peer_units != "NONE":
        if direction.lower() == "output":
            parts.append(f"to:{peer_units}")
        else:
            parts.append(f"used_by:{peer_units}")

    # Producer/consumer RTL files (just the stem, most informative)
    if producer_file:
        parts.append(f"produced_in:{Path(producer_file).stem}")
    if consumer_file and consumer_file != producer_file:
        parts.append(f"consumed_in:{Path(consumer_file).stem}")

    if not parts:
        return _fallback_purpose(signal_name, direction, src_units, peer_units)

    return "; ".join(parts)


def _fallback_purpose(signal_name: str, direction: str,
                      src_units: str, peer_units: str) -> str:
    """Generate a minimal purpose when no RTL context is found."""
    parts = []
    # Try prefix hints
    for prefix, hint in PREFIX_HINTS.items():
        if signal_name.startswith(prefix):
            parts.append(hint)
            break
    if direction:
        parts.append(direction)
    if src_units and src_units != "NONE":
        parts.append(f"from:{src_units}")
    if peer_units and peer_units != "NONE":
        parts.append(f"to:{peer_units}")
    return "; ".join(parts) if parts else "unknown"


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def split_units(value: str) -> list[str]:
    raw = (value or "").strip()
    if not raw or raw == "NONE":
        return []
    return [u.strip() for u in raw.split(";") if u.strip() and u.strip() != "NONE"]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--io-csv", required=True,
                        help="Input IO table CSV")
    parser.add_argument("--out-csv", required=True,
                        help="Output CSV with functional_purpose column")
    parser.add_argument("--workspace", default=".",
                        help="Workspace root directory")
    parser.add_argument("--extra-rtl-dirs", nargs="*", default=[],
                        help="Additional RTL directories to scan")
    return parser.parse_args()


def main():
    args = parse_args()
    workspace = Path(args.workspace).resolve()

    # Load CSV
    print(f"Loading {args.io_csv} ...")
    with open(args.io_csv) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    print(f"  {len(rows)} rows")

    # Collect all signal names and all referenced units
    signal_names: set[str] = set()
    all_units: set[str] = set()
    for r in rows:
        name = r.get("port_name", "").strip()
        if name:
            signal_names.add(name)
        for field in ["source_output_units", "connected_other_units"]:
            for u in split_units(r.get(field, "")):
                all_units.add(u)

    print(f"  {len(signal_names)} unique signals, {len(all_units)} unique units")

    # Build RTL index - scan once
    print("Scanning RTL files ...")
    idx = RTLIndex(str(workspace), signal_names)

    # Always scan FE RTL (where IFU lives)
    idx.scan_directory("core/fe/rtl")

    # Scan directories for all referenced units
    idx.scan_for_units(list(all_units))

    # Scan any extra dirs
    for d in args.extra_rtl_dirs:
        idx.scan_directory(d)

    total_hits = sum(len(v) for v in idx.occurrences.values())
    signals_found = sum(1 for v in idx.occurrences.values() if v)
    print(f"  Found {total_hits} RTL occurrences across {signals_found}/{len(signal_names)} signals")

    # Infer functional purpose for each row
    print("Inferring functional purposes ...")
    purposes: list[str] = []
    for r in rows:
        name = r.get("port_name", "").strip()
        direction = r.get("port_direction", "")
        src_units = r.get("source_output_units", "")
        peer_units = r.get("connected_other_units", "")
        occs = idx.occurrences.get(name, [])
        purpose = infer_purpose(name, occs, direction, src_units, peer_units)
        purposes.append(purpose)

    # Write output CSV with functional_purpose inserted after port_name
    out_fieldnames = []
    for col in fieldnames:
        out_fieldnames.append(col)
        if col == "port_name":
            out_fieldnames.append("functional_purpose")

    # Safety: if port_name wasn't in fieldnames, append at end
    if "functional_purpose" not in out_fieldnames:
        out_fieldnames.append("functional_purpose")

    with open(args.out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames)
        writer.writeheader()
        for r, purpose in zip(rows, purposes):
            out_row = dict(r)
            out_row["functional_purpose"] = purpose
            writer.writerow(out_row)

    print(f"Wrote {args.out_csv}")
    print(f"  {len(rows)} rows, functional_purpose column inserted after port_name")

    # Print sample
    print("\nSample purposes (first 15):")
    for r, purpose in zip(rows[:15], purposes[:15]):
        name = r.get("port_name", "")
        print(f"  {name:40s} → {purpose[:100]}")


if __name__ == "__main__":
    main()
