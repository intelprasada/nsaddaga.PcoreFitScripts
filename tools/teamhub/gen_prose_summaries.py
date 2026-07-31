#!/usr/bin/env python3
"""Generate prose H1 2026 summaries for each engineer, streamed to Markdown."""
import json, sys, os
from pathlib import Path

CACHE = Path(__file__).parent / ".cache" / "summaries__ALL_2026-01-01_2026-06-30.json"
OUT   = Path(__file__).parent / "H1_2026_engineer_summaries.md"

UNIT_BLURB = {
    "STSR":       "STSR RTL / formal verification",
    "IDQ":        "IDQ feature enablement and debug",
    "DSB":        "DSB coverage and cleanup",
    "DSBE":       "DSBE coverage and validation",
    "MS/MSID":    "MS / MSID feature enablement and assertion tuning",
    "IFU":        "IFU RTL and validation",
    "BPU":        "BPU RTL and validation",
    "BAC":        "BAC RTL and validation",
    "IDU":        "IDU RTL and validation",
    "IQ":         "IQ RTL and validation",
    "RAT":        "RAT RTL and validation",
    "MCA":        "MCA checker and infrastructure",
    "FV/FPV":     "Formal verification infrastructure",
    "CTE":        "CTE testbench / topology",
}

def activity_phrase(activities):
    if not activities:
        return ""
    parts = []
    for a in activities[:4]:
        parts.append(f"{a['name'].lower()} ({a['count']} TI{'s' if a['count']!=1 else ''})")
    return ", ".join(parts)

def render_engineer(e):
    name  = e["engineer"]
    t     = e["totals"]
    turns = t["turnins"]; rel = t["released"]; canc = t["cancelled"]; inf = t["in_flight"]
    by_p  = t.get("by_project", {})
    hsd_tis = t.get("hsd_tis", 0)

    proj_parts = [f"{p} ({n})" for p, n in sorted(by_p.items(), key=lambda kv: -kv[1]) if n]
    proj_str   = " and ".join(proj_parts) if proj_parts else "GFC and JNC"

    lines = []
    lines.append(f"### {name}\n")

    # Opening paragraph
    status_bits = [f"{rel} released"]
    if canc: status_bits.append(f"{canc} cancelled")
    if inf:  status_bits.append(f"{inf} in flight")
    status_str = ", ".join(status_bits)

    units = e.get("unit_sections", [])
    top_units = [u["unit"] for u in units[:4]]
    units_hint = ", ".join(top_units) if top_units else "cross-cutting infrastructure"

    lines.append(
        f"Delivered {turns} turnins in H1 2026 ({status_str}) across {proj_str}, "
        f"with {hsd_tis} turnin{'s' if hsd_tis!=1 else ''} filing HSDs. "
        f"Work spanned {units_hint}.\n"
    )

    # Per-unit paragraphs (top 6 units)
    for u in units[:6]:
        unit = u["unit"]
        n    = u["ti_count"]
        blurb = UNIT_BLURB.get(unit, f"{unit} work")
        phrase = activity_phrase(u.get("activities", []))
        sentence = f"**{blurb}** — {n} turnin{'s' if n!=1 else ''}"
        if phrase:
            sentence += f" covering {phrase}"
        sentence += "."
        lines.append(sentence + "\n")

    # Top files (compact list)
    tf = e.get("top_files", [])[:5]
    if tf:
        files_str = ", ".join(f"`{f['path'].split('/')[-1]}` ({f['count']})" for f in tf)
        lines.append(f"**Top files touched** — {files_str}.\n")

    lines.append("")  # blank line separator
    return "\n".join(lines)

def main():
    if not CACHE.exists():
        print(f"Cache not found: {CACHE}", file=sys.stderr); sys.exit(1)
    payload = json.load(open(CACHE))["payload"]
    window  = payload.get("window", {})
    engs    = sorted(payload["engineers"],
                     key=lambda e: -(e.get("totals", {}).get("turnins", 0)))

    with open(OUT, "w") as f:
        f.write("# H1 2026 Engineer Summaries\n\n")
        f.write(f"_Window: {window.get('since','?')} → {window.get('until','?')} · "
                f"Projects: GFC + JNC · Generated: {payload.get('generated_at','')}_\n\n")
        f.write(f"_{len(engs)} engineers, sorted by turnin volume._\n\n---\n\n")
        for e in engs:
            block = render_engineer(e)
            f.write(block)
            f.write("---\n\n")
            f.flush()
            print(f"  ✓ {e['engineer']} ({e['totals']['turnins']} TIs)", file=sys.stderr)

    print(f"\nWrote {OUT}", file=sys.stderr)

if __name__ == "__main__":
    main()
