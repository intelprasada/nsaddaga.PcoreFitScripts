#!/usr/bin/env python3
"""Generate natural-language functional purpose descriptions for IFU interface signals.

Uses architectural domain knowledge + RTL context + signal-name semantics to
produce human-readable descriptions like:
  "Carries the MLC snoop transaction ID from the Mid-Level Cache (MLC/DCU)
   into the IFU's snoop queue."

The generated descriptions can then be fed as TF-IDF text features into the
unsupervised ML classifier for improved clustering.

Usage:
  python3 generate_signal_descriptions.py \\
    --io-csv  00_ifu_top_interface_from_drilldown.csv \\
    --rtl-purpose-csv ifu_with_functional_purpose.csv \\
    --out-csv ifu_with_nl_descriptions.csv \\
    --workspace .
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# Domain Knowledge: x86 Front-End Architecture
# ──────────────────────────────────────────────────────────────────────────────

# Architectural block descriptions
BLOCK_DESC = {
    "BPU":      "Branch Prediction Unit",
    "BAC":      "Branch Address Calculator",
    "DSBFE":    "Decoded Stream Buffer (front-end interface)",
    "DSB":      "Decoded Stream Buffer",
    "IFU":      "Instruction Fetch Unit",
    "IFCTLC":   "IFU control logic",
    "IFCRBRTSTS": "IFU control register and status block",
    "IFMCACHE": "IFU micro-op cache (instruction cache)",
    "IFSBD":    "IFU storage buffer and data path",
    "IFTLBD":   "IFU TLB (Translation Lookaside Buffer) data",
    "IFMEMD":   "IFU memory data path",
    "IFSNPS":   "IFU snoop handler",
    "IFQUEUED": "IFU fetch queue data path",
    "IFTAGCTLS": "IFU tag control and lookup logic",
    "IFTLBCTLS": "IFU TLB control logic",
    "IFDATACTLS": "IFU data control logic",
    "IFU_ARRAYS": "IFU array/memory structures",
    "IFU_CLKS": "IFU clock generation and distribution",
    "IFDATALATCHUNKD": "IFU data latch and chunk data path",
    "IFBACS":   "IFU BAC interface handler",
    "IFBRKS":   "IFU breakpoint logic",
    "IFCREGS":  "IFU control registers",
    "IFSBS":    "IFU store buffer snoop handler",
    "IFSWPREFS": "IFU software prefetch handler",
    "FE_MISC":  "Front-End miscellaneous/power management",
    "MLC":      "Mid-Level Cache",
    "MEU":      "Memory Execution Unit",
    "MCU":      "Memory Control Unit",
    "DCU":      "Data Cache Unit",
    "PMH":      "Page Miss Handler",
    "OOO":      "Out-of-Order engine",
    "EXE":      "Execution unit",
    "MSID":     "Micro-Sequencer and Instruction Decode",
    "IL":       "Instruction Length decoder",
    "IQ":       "Instruction Queue",
    "MS":       "Micro-Sequencer",
    "ICORE":    "Core-level infrastructure",
}

# Cluster/owner to full name
CLUSTER_DESC = {
    "fe":    "Front-End",
    "meu":   "Memory Execution Unit",
    "mlc":   "Mid-Level Cache",
    "ooo":   "Out-of-Order engine",
    "exe":   "Execution unit",
    "msid":  "Micro-Sequencer/Instruction Decode",
    "icore": "Core infrastructure",
}

# Signal name token → semantic meaning
TOKEN_SEMANTICS = {
    # Snoop/coherency
    "snp": "snoop", "snpid": "snoop ID", "snpval": "snoop valid",
    "snphit": "snoop hit", "snpquery": "snoop query",
    "snpreq": "snoop request", "snpinv": "snoop invalidate",
    "coh": "coherency",
    # MLC interface
    "mli": "MLC-to-IFU", "mlc": "mid-level cache",
    "cmpl": "completion", "nack": "negative acknowledgement",
    "req": "request", "rsp": "response",
    # Branch prediction
    "bp": "branch prediction", "bpc": "branch prediction control",
    "bpb": "branch prediction buffer", "predict": "prediction",
    "taken": "branch taken", "jeclear": "JE-clear (branch misprediction flush)",
    "rsmo": "RSMO (Return Stack Mispredict Override)",
    "fetch": "instruction fetch", "biq": "Branch Info Queue",
    "brtype": "branch type", "brtarget": "branch target",
    # Pipeline control
    "stall": "pipeline stall", "nuke": "pipeline nuke/flush",
    "clear": "pipeline clear", "valid": "valid indication",
    "enable": "enable", "en": "enable", "rdy": "ready",
    "credit": "credit-based flow control", "alloc": "allocation",
    "dealloc": "deallocation",
    # TLB/memory
    "tlb": "TLB", "phyadr": "physical address", "adr": "address",
    "addr": "address", "tag": "tag", "data": "data",
    "cache": "cache", "mcache": "micro-cache",
    "icache": "instruction cache", "mem": "memory",
    "miss": "miss", "hit": "hit", "way": "cache way",
    "pmon": "performance monitor",
    # Queue/buffer
    "queue": "queue", "buf": "buffer", "fifo": "FIFO",
    "ptr": "pointer", "entry": "queue entry",
    "ifq": "IFU fetch queue",
    # Clock/power/reset
    "clk": "clock", "clock": "clock", "rst": "reset",
    "reset": "reset", "pwr": "power", "power": "power",
    "pwrdn": "power down", "pwrup": "power up",
    "pwrgood": "power good", "pm": "power management",
    # Thread/SMT
    "thread": "thread", "smt": "SMT (simultaneous multi-threading)",
    "tid": "thread ID", "thstate": "thread state",
    # DFT/debug
    "fscan": "scan chain", "jtag": "JTAG", "ijtag": "iJTAG",
    "misr": "MISR (signature register)", "bist": "BIST",
    "mbist": "memory BIST", "lbist": "logic BIST",
    "dfx": "DFX (design-for-debug)", "tdr": "test data register",
    "scan": "scan", "debug": "debug", "trace": "trace",
    "visa": "VISA debug", "tap": "TAP controller",
    # Control register
    "cr": "control register", "crbit": "control register bit",
    "chicken": "chicken bit (feature disable)",
    "msr": "MSR (model-specific register)",
    # DSB
    "dsb": "decoded stream buffer", "ds": "decoded stream",
    "insert": "insert", "sync": "synchronization",
    # Status/error
    "error": "error", "err": "error", "fault": "fault",
    "status": "status", "poison": "poison (data corruption marker)",
    "intr": "interrupt", "nmi": "NMI (non-maskable interrupt)",
    "smi": "SMI (system management interrupt)",
    "cmc": "CMC (corrected machine check)",
    # Misc
    "uri": "URI (unique resource identifier)",
    "mode": "operating mode", "priv": "privilege level",
    "bogus": "bogus (speculative and cancelled)",
    "spec": "speculative",
    "swpref": "software prefetch",
    "ltt": "LTT (Loop Trip Tracker)",
    "stew": "STEW (Store-to-Load forwarding Event Window)",
}

# Pipeline stage ranges to descriptions
STAGE_DESCRIPTIONS = {
    range(0, 100):   "early pipeline",
    range(100, 110): "fetch/decode pipeline (M1xx)",
    range(110, 125): "mid pipeline (M1xx)",
    range(125, 160): "late pipeline (M1xx)",
    range(300, 400): "deep pipeline (M3xx)",
    range(400, 500): "replay/late-stage (M4xx)",
    range(500, 600): "completion stage (M5xx)",
    range(800, 910): "retirement/nuke stage (M8xx-M9xx)",
    range(910, 999): "post-retirement (M9xx)",
}

# Producer/consumer file stems → what they implement
FILE_FUNCTION = {
    "ml2icorerpt": "MLC-to-core report path",
    "ifsnps":      "IFU snoop queue logic",
    "iftagctls":   "IFU tag array control",
    "iftlbctls":   "IFU TLB control",
    "ifdatactls":  "IFU data path control",
    "ifqueued":    "IFU fetch queue data path",
    "ifmcache":    "IFU micro-op cache",
    "ifsbd":       "IFU store buffer data",
    "ifsbs":       "IFU store buffer snoop",
    "iftlbd":      "IFU TLB data arrays",
    "ifbacs":      "IFU BAC interface",
    "ifcregs":     "IFU control registers",
    "ifbrks":      "IFU breakpoint handler",
    "ifdatachunkd": "IFU data chunk logic",
    "ifdatalatpard": "IFU data latch parity",
    "ifretainer":  "IFU pipeline retainer/stall",
    "ifswprefs":   "IFU software prefetch",
    "ifu_clks":    "IFU clock generation",
    "ifu_arrays":  "IFU array structures",
    "bpcontrols":  "branch prediction controls",
    "bpglobalm":   "global branch predictor",
    "bpmop":       "branch micro-op logic",
    "bplsdtracker": "branch LSD tracker",
    "bpindirectctrls": "indirect branch controls",
    "bacons":      "BAC control signals",
    "baaddpvd":    "BAC address provider",
    "bpcrpmons":   "BPU performance monitors",
    "dsbctls":     "DSB control logic",
    "dsbfe":       "DSB front-end interface",
    "fe_rptrs":    "FE repeaters",
    "fe_tlm":      "FE transaction-level model",
    "pmsyns":      "power management sync",
    "mosab":       "MOB/SAB interface",
    "iqmcache":    "IQ micro-cache interface",
    "iqstew":      "IQ STEW logic",
    "rolbrctls":   "ROL/BR control logic",
}

CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+")
STAGE_RE = re.compile(r"M(\d{3})H")


# ──────────────────────────────────────────────────────────────────────────────
# NLG Engine
# ──────────────────────────────────────────────────────────────────────────────

def tokenize_name(name: str) -> list[str]:
    parts = []
    for chunk in re.split(r"[^A-Za-z0-9]+", name or ""):
        if not chunk:
            continue
        parts.extend(CAMEL_RE.findall(chunk))
    return [p.lower() for p in parts if p]


def get_stage_desc(stage_num: int) -> str:
    for rng, desc in STAGE_DESCRIPTIONS.items():
        if stage_num in rng:
            return desc
    return f"pipeline stage M{stage_num:03d}H"


def get_block_desc(unit: str) -> str:
    """Get descriptive name for an architectural block."""
    u = unit.strip().upper()
    if u in BLOCK_DESC:
        return f"{BLOCK_DESC[u]} ({u})"
    return unit


def parse_chain(chain: str) -> list[str]:
    """Parse 'MEU<-MCU<-DCU' into ['MEU', 'MCU', 'DCU']."""
    return [t.strip() for t in re.split(r"<-|->", chain) if t.strip()]


def describe_data_flow(direction: str, src_units: str, peer_units: str,
                       producer_cluster: str) -> str:
    """Build a natural-language data flow description."""
    parts = []
    if direction.lower() == "input":
        if src_units and src_units != "NONE":
            chain = parse_chain(src_units)
            if len(chain) > 1:
                origin = get_block_desc(chain[-1])
                path = " via ".join(get_block_desc(c) for c in reversed(chain[:-1]))
                parts.append(f"Received from {origin} through {path}")
            elif len(chain) == 1:
                parts.append(f"Received from {get_block_desc(chain[0])}")
        if peer_units and peer_units != "NONE":
            consumers = [u.strip() for u in peer_units.split(";") if u.strip() and u.strip() != "NONE"]
            real = [u for u in consumers if not u.upper().endswith("_TLM") and not u.upper().endswith("_MON")]
            if real:
                descs = [get_block_desc(u) for u in real[:3]]
                parts.append(f"consumed by {', '.join(descs)}")
    else:  # output
        if src_units and src_units != "NONE":
            producers = [u.strip() for u in src_units.split(";") if u.strip() and u.strip() != "NONE"]
            real = [u for u in producers if not u.upper().endswith("_TLM") and not u.upper().endswith("_MON")]
            if real:
                descs = [get_block_desc(u) for u in real[:2]]
                parts.append(f"Produced by {', '.join(descs)}")
        if peer_units and peer_units != "NONE":
            targets = [u.strip() for u in peer_units.split(";") if u.strip() and u.strip() != "NONE"]
            real = [u for u in targets if not u.upper().endswith("_TLM") and not u.upper().endswith("_MON")]
            if real:
                descs = [get_block_desc(u) for u in real[:3]]
                parts.append(f"delivered to {', '.join(descs)}")

    return ". ".join(parts) if parts else ""


def describe_signal_semantics(tokens: list[str], name: str) -> str:
    """Build a semantic description from signal name tokens."""
    meanings = []
    for t in tokens:
        if t in TOKEN_SEMANTICS:
            meanings.append(TOKEN_SEMANTICS[t])

    if not meanings:
        return ""

    # Remove duplicates preserving order
    seen = set()
    unique = []
    for m in meanings:
        if m not in seen:
            seen.add(m)
            unique.append(m)

    return ", ".join(unique[:4])


def generate_description(row: dict, rtl_purpose: str = "") -> str:
    """Generate a complete natural-language functional purpose description."""
    name = row.get("port_name", "")
    direction = row.get("port_direction", "")
    src_units = row.get("source_output_units", "")
    peer_units = row.get("connected_other_units", "")
    sv_type = row.get("direction_sv_type", "")
    packed_width = row.get("direction_packed_width", "")
    unpacked_dim = row.get("direction_unpacked_dim", "")
    producer_owner = row.get("producer_cluster_owner", "")

    tokens = tokenize_name(name)
    stage_match = STAGE_RE.search(name)
    stage_num = int(stage_match.group(1)) if stage_match else -1

    sentences = []

    # ── Opening sentence: What the signal carries ──
    semantics = describe_signal_semantics(tokens, name)
    if semantics:
        if direction.lower() == "input":
            sentences.append(f"Carries {semantics} into the IFU")
        else:
            sentences.append(f"Provides {semantics} from the IFU")
    else:
        if direction.lower() == "input":
            sentences.append(f"Input signal to the IFU")
        else:
            sentences.append(f"Output signal from the IFU")

    # ── Data type/width context ──
    type_parts = []
    if sv_type and sv_type != "node":
        type_parts.append(f"structured as {sv_type}")
    if packed_width:
        type_parts.append(f"width {packed_width}")
    if unpacked_dim:
        type_parts.append(f"replicated across {unpacked_dim}")
    if type_parts:
        sentences.append(", ".join(type_parts))

    # ── Data flow description ──
    flow = describe_data_flow(direction, src_units, peer_units, producer_owner)
    if flow:
        sentences.append(flow)

    # ── Pipeline stage context ──
    if stage_num >= 0:
        stage_desc = get_stage_desc(stage_num)
        sentences.append(f"Active at {stage_desc} (M{stage_num:03d}H)")

    # ── RTL-derived context (from the existing purpose extraction) ──
    if rtl_purpose:
        # Extract the most informative part: comments, producer/consumer files
        rtl_parts = rtl_purpose.split(";")
        # Find comment-derived parts (not structural metadata)
        for part in rtl_parts:
            part = part.strip()
            if not part:
                continue
            # Skip structural metadata we already covered
            if any(part.startswith(p) for p in ["sequential", "combinational",
                    "clock_gating", "global_driver", "from:", "to:", "src:",
                    "used_by:", "produced_in:", "consumed_in:"]):
                continue
            # This is likely a real RTL comment
            if len(part) > 8:
                sentences.append(f"RTL context: {part}")
                break

        # Extract produced_in / consumed_in for extra detail
        for part in rtl_parts:
            part = part.strip()
            if part.startswith("produced_in:"):
                stem = part.split(":")[1]
                if stem in FILE_FUNCTION:
                    sentences.append(f"Produced in {FILE_FUNCTION[stem]}")
            elif part.startswith("consumed_in:"):
                stem = part.split(":")[1]
                if stem in FILE_FUNCTION:
                    sentences.append(f"Consumed in {FILE_FUNCTION[stem]}")

    # ── Cross-cluster ownership ──
    if producer_owner and producer_owner != "NONE":
        owners = [o.strip() for o in producer_owner.split(";") if o.strip() and o.strip() != "NONE"]
        if owners:
            cluster_names = [CLUSTER_DESC.get(o, o) for o in owners]
            if direction.lower() == "input":
                sentences.append(f"Sourced from {', '.join(cluster_names)} cluster")
            else:
                sentences.append(f"Owned by {', '.join(cluster_names)} cluster")

    # ── Special patterns ──
    name_upper = name.upper()
    if "_INST" in name_upper:
        sentences.append("This is a struct instance wrapper signal (instrumentation)")

    # DFT/scan
    dft_tokens = {"fscan", "jtag", "ijtag", "misr", "bist", "mbist", "lbist",
                  "scan", "dfx", "tap", "tdr"}
    if set(tokens) & dft_tokens:
        sentences.append("Part of DFT/test infrastructure")

    # Thread-replicated
    if "CORE_SMT_NUM_THREADS" in (packed_width or "") or "CORE_SMT_NUM_THREADS" in (unpacked_dim or ""):
        sentences.append("Replicated per hardware thread (SMT)")

    # Per-fetch-way
    if "FETCH_YNG" in (packed_width or "") or "FETCH_OLD" in (packed_width or ""):
        sentences.append("Indexed per fetch way (young/old)")

    # Combine into flowing prose
    result = ". ".join(s for s in sentences if s)
    if result and not result.endswith("."):
        result += "."

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--io-csv", required=True,
                        help="Input IO table CSV (the raw signal table)")
    parser.add_argument("--rtl-purpose-csv", default="",
                        help="Optional: CSV with existing RTL-extracted functional_purpose column")
    parser.add_argument("--out-csv", required=True,
                        help="Output CSV with nl_description column")
    return parser.parse_args()


def main():
    args = parse_args()

    # Load main IO table
    print(f"Loading {args.io_csv} ...")
    with open(args.io_csv) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    print(f"  {len(rows)} rows")

    # Load RTL purpose CSV if provided (keyed by port_name)
    rtl_purposes: dict[str, str] = {}
    if args.rtl_purpose_csv:
        print(f"Loading RTL purposes from {args.rtl_purpose_csv} ...")
        with open(args.rtl_purpose_csv) as f:
            for r in csv.DictReader(f):
                name = r.get("port_name", "")
                purpose = r.get("functional_purpose", "")
                if name and purpose:
                    rtl_purposes[name] = purpose
        print(f"  {len(rtl_purposes)} RTL purposes loaded")

    # Separate _inst signals from regular signals
    rows_normal = []
    rows_excluded = []
    for r in rows:
        if r.get("port_name", "").endswith("_inst"):
            rows_excluded.append(r)
        else:
            rows_normal.append(r)
    print(f"  {len(rows_normal)} signals to describe, {len(rows_excluded)} _inst signals excluded")

    # Generate descriptions for non-_inst signals only
    print("Generating natural-language descriptions ...")
    descriptions: list[str] = []
    for r in rows_normal:
        name = r.get("port_name", "")
        rtl_purpose = rtl_purposes.get(name, "")
        desc = generate_description(r, rtl_purpose)
        descriptions.append(desc)

    # Build output fieldnames: insert nl_description after port_name
    out_fieldnames = []
    for col in fieldnames:
        out_fieldnames.append(col)
        if col == "port_name":
            out_fieldnames.append("nl_description")
    if "nl_description" not in out_fieldnames:
        out_fieldnames.append("nl_description")

    # Write output: normal signals first, then excluded _inst signals at bottom
    with open(args.out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames)
        writer.writeheader()
        for r, desc in zip(rows_normal, descriptions):
            out_row = dict(r)
            out_row["nl_description"] = desc
            writer.writerow(out_row)
        for r in rows_excluded:
            out_row = dict(r)
            out_row["nl_description"] = "EXCLUDED (_inst struct wrapper)"
            writer.writerow(out_row)

    print(f"Wrote {args.out_csv}")
    print(f"  {len(rows_normal)} described + {len(rows_excluded)} excluded _inst = {len(rows)} total")

    # Print samples
    print("\n" + "=" * 80)
    print("SAMPLE DESCRIPTIONS (first 10)")
    print("=" * 80)
    for r, desc in zip(rows_normal[:10], descriptions[:10]):
        name = r.get("port_name", "")
        print(f"\n  {name}")
        print(f"    {desc}")

    # Stats
    avg_len = sum(len(d) for d in descriptions) / max(len(descriptions), 1)
    print(f"\n  Average description length: {avg_len:.0f} chars")
    print(f"  Descriptions with RTL context: {sum(1 for d in descriptions if 'RTL context:' in d)}")
    print(f"  Excluded _inst signals: {len(rows_excluded)}")


if __name__ == "__main__":
    main()
