#!/usr/bin/env python3
"""
Generate I/O tables from typed/query connectivity CSV data.

Mode 1 (existing):
- Aggregate per-port for one module.

Mode 2 (new):
- For every row in a query-view CSV, add `source_output_units` and
    `connected_other_units` / `connected_tlm_units` columns.

Usage:
        # Module aggregate table
        python3 generate_module_io_table.py --typed-csv <file> --module <name> --out-csv <file>

        # All-lines query-view I/O table
        python3 generate_module_io_table.py --query-csv <file> --all-lines-io-table --out-csv <file>
"""

import argparse
import csv
import difflib
import re
import sys
from pathlib import Path
from collections import defaultdict
from collections import deque


# Regex patterns for parsing connection expressions
IDENTIFIER_RE = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_$]*)\b')
LITERAL_RE = re.compile(r"\b\d+'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ_]+\b|'[01xXzZ]")
PORT_DECL_RE = re.compile(r"^\s*(input|output|inout)\b(?P<body>[^;]*);")
ICF_DECL_RE = re.compile(r"^\s*(input|output)\s+\S+\s+(?P<body>.*)$")
ICF_NODE_RE = re.compile(r"^\s*node\b\s+(?P<body>.*)$")
ICF_PIN_FE_CONST_RE = re.compile(
    r"^\s*pin\s*\{fe\}\s*input\b.*\((?P<expr>[^)]*)\)",
    re.IGNORECASE,
)
TRAILING_DIMS_RE = re.compile(r"\s*(\[[^\]]+\]\s*)*$")
SIGNAL_NAME_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_$]*)$")
HIER_MODULE_START_RE = re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\s*;")
HIER_INSTANCE_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s+([A-Za-z_][A-Za-z0-9_$]*)\s*(?:\[[^\]]+\])?\s*;"
)
MODULE_DISPLAY_ALIAS = {
    "MEC": "MCU",
}

HIER_GRAPH_CACHE = {}


def normalize_alias_key(signal_name: str) -> str:
    """Normalize signal name for near-name alias comparisons."""
    if not signal_name:
        return ""
    return re.sub(r"[^a-z0-9]", "", signal_name.lower())


def collect_alias_candidates(
    query_signal: str,
    owner_index: dict,
    owner_signals: list[str],
    max_candidates: int = 5,
    min_ratio: float = 0.72,
) -> list[dict]:
    """Find near-name candidate producer signals for manual review only."""
    query_key = normalize_alias_key(query_signal)
    if not query_key:
        return []

    scored = []
    for candidate_signal in owner_signals:
        cand_key = normalize_alias_key(candidate_signal)
        if not cand_key or cand_key == query_key:
            continue
        ratio = difflib.SequenceMatcher(None, query_key, cand_key).ratio()
        if ratio < min_ratio:
            continue
        entry = owner_index.get(candidate_signal, {})
        owners = sorted(entry.get("outputs", set()))
        evidence = sorted(entry.get("output_evidence", set()))
        scored.append(
            {
                "candidate_signal": candidate_signal,
                "match_score": f"{ratio:.3f}",
                "candidate_owner_clusters": ";".join(owners) if owners else "NONE",
                "candidate_owner_evidence": ";".join(evidence) if evidence else "NONE",
            }
        )

    scored.sort(
        key=lambda x: (
            -float(x["match_score"]),
            x["candidate_signal"],
        )
    )
    return scored[:max_candidates]


def best_alias_output_owner(
    query_signal: str,
    owner_index: dict,
    owner_signals: list[str],
    row_direction: str,
) -> tuple[str, str, str] | None:
    """Return best alias output-owner triple (units, owners, evidence) for query signal."""
    candidates = collect_alias_candidates(
        query_signal=query_signal,
        owner_index=owner_index,
        owner_signals=owner_signals,
        max_candidates=20,
    )

    filtered = []
    for candidate in candidates:
        candidate_signal = candidate["candidate_signal"]
        match_score = float(candidate["match_score"])
        if not (
            is_t_only_mismatch(query_signal, candidate_signal)
            or is_repeater_variant(query_signal, candidate_signal)
            or match_score >= 0.82
        ):
            continue
        candidate_entry = owner_index.get(candidate_signal, {})
        owners = set(candidate_entry.get("outputs", set()))
        evidence = set(candidate_entry.get("output_evidence", set()))
        if not owners:
            continue
        filtered.append((match_score, candidate_signal, owners, evidence))

    if not filtered:
        return None

    filtered.sort(key=lambda x: (-x[0], x[1]))
    _, _, owners, evidence = filtered[0]
    owner_units = extract_owner_units_from_evidence(evidence, row_direction)

    def is_specific_owner_chain(value: str) -> bool:
        tokens = [t for t in re.split(r"<-|->|/", value) if t]
        return len(tokens) >= 2 and tokens[0] != tokens[-1]

    specific_owner_units = {x for x in owner_units if is_specific_owner_chain(x)}
    preferred_owner_units = specific_owner_units if specific_owner_units else owner_units

    units_value = ";".join(sorted(preferred_owner_units)) if preferred_owner_units else "NONE"
    owners_value = ";".join(sorted(owners)) if owners else "NONE"
    evidence_value = ";".join(sorted(evidence)) if evidence else "NONE"
    return units_value, owners_value, evidence_value


def is_t_only_mismatch(query_signal: str, candidate_signal: str) -> bool:
    """Return True when query/candidate differ only by inserted/removed 'T' chars."""
    query_key = normalize_alias_key(query_signal)
    cand_key = normalize_alias_key(candidate_signal)
    if not query_key or not cand_key or query_key == cand_key:
        return False
    return query_key.replace("t", "") == cand_key.replace("t", "")


def canonicalize_repeater_name(signal_name: str) -> str:
    """Normalize repeater-style names by removing _rpt* segments."""
    if not signal_name:
        return ""
    return re.sub(r"_rpt[0-9a-z_]*", "", signal_name.lower())


def is_repeater_variant(query_signal: str, candidate_signal: str) -> bool:
    """Return True when query/candidate map to same base signal across _rpt* names."""
    query_base = canonicalize_repeater_name(query_signal)
    cand_base = canonicalize_repeater_name(candidate_signal)
    if not query_base or not cand_base:
        return False
    if query_base != cand_base:
        return False
    return ("_rpt" in query_signal.lower()) or ("_rpt" in candidate_signal.lower())


def extract_owner_units_from_evidence(evidence: set[str], row_direction: str) -> set[str]:
    """Build owner hierarchy strings from evidence entries."""
    owner_units = set()
    for evidence_entry in evidence:
        if ":" not in evidence_entry:
            continue
        cluster, path_str = evidence_entry.split(":", 1)
        unit = Path(path_str).stem
        if not cluster or not unit:
            continue
        owner_units.add(format_owner_hierarchy(cluster, unit, row_direction))
    return owner_units


def extract_identifiers(expr: str) -> list:
    """Extract unique identifiers from a connection expression."""
    if not expr:
        return []
    # Remove Verilog literal tokens so items like 1'b0 do not produce false identifiers.
    stripped_expr = LITERAL_RE.sub(" ", expr)
    tokens = IDENTIFIER_RE.findall(stripped_expr)
    return sorted(set(tokens))


def parse_bool_str(val: str) -> bool:
    """Parse string representation of boolean."""
    return (val or "").lower().strip() in ('true', '1', 'yes')


def strip_line_comment(line: str) -> str:
    """Remove Verilog line comments."""
    if "//" not in line:
        return line
    return line.split("//", 1)[0]


def extract_signal_name_from_decl_body(body: str) -> str | None:
    """Extract the declared signal name from a port declaration body."""
    no_dims = TRAILING_DIMS_RE.sub("", body).rstrip(" ;")
    match = SIGNAL_NAME_RE.search(no_dims.strip())
    if not match:
        return None
    return match.group(1)


def extract_cluster_from_path(path: Path) -> str | None:
    """Extract cluster name from paths like core/<cluster>/rtl/*.icf."""
    parts = path.as_posix().split("/")
    try:
        core_idx = parts.index("core")
    except ValueError:
        return None
    if core_idx + 1 >= len(parts):
        return None
    return parts[core_idx + 1]


def parse_hier_module_graph(hier_file: Path) -> dict[str, set[str]]:
    """Parse .hier file into module->child-module graph."""
    graph = defaultdict(set)
    module_stack = []

    for raw_line in hier_file.read_text(errors='ignore').splitlines():
        line = strip_line_comment(raw_line).strip()
        if not line:
            continue

        module_match = HIER_MODULE_START_RE.match(line)
        if module_match:
            new_module = module_match.group(1).lower()
            if module_stack:
                # Nested module declarations are structural children in .hier context.
                graph[module_stack[-1]].add(new_module)
            module_stack.append(new_module)
            continue

        if line.startswith("endmodule"):
            if module_stack:
                module_stack.pop()
            continue

        if not module_stack or line.startswith(("`", "(*", "import ")):
            continue

        inst_match = HIER_INSTANCE_RE.match(line)
        if not inst_match:
            continue
        child_module = inst_match.group(1).lower()
        graph[module_stack[-1]].add(child_module)

    return graph


def get_hier_graph_for_cluster(cluster: str) -> dict[str, set[str]] | None:
    """Load and cache top-level .hier module graph for a cluster."""
    cluster = cluster.lower()
    if cluster in HIER_GRAPH_CACHE:
        return HIER_GRAPH_CACHE[cluster]

    hier_path = Path(f"core/{cluster}/rtl/{cluster}.hier")
    if not hier_path.exists():
        HIER_GRAPH_CACHE[cluster] = None
        return None

    graph = parse_hier_module_graph(hier_path)
    HIER_GRAPH_CACHE[cluster] = graph
    return graph


def find_module_path(graph: dict[str, set[str]], start: str, target: str) -> list[str] | None:
    """Find shortest module-type path from start to target in module graph."""
    start = start.lower()
    target = target.lower()
    if start == target:
        return [start]

    queue = deque([[start]])
    visited = {start}
    while queue:
        path = queue.popleft()
        node = path[-1]
        for nxt in sorted(graph.get(node, set())):
            if nxt in visited:
                continue
            next_path = path + [nxt]
            if nxt == target:
                return next_path
            visited.add(nxt)
            queue.append(next_path)
    return None


def format_owner_hierarchy(cluster: str, unit: str, row_direction: str) -> str:
    """Format owner as hierarchy chain with directional arrows.

    input  rows: TOP<-...<-LEAF
    output rows: TOP->...->LEAF
    """
    cluster_l = cluster.lower()
    unit_l = unit.lower()
    graph = get_hier_graph_for_cluster(cluster_l)

    path = None
    if graph:
        path = find_module_path(graph, cluster_l, unit_l)

    if path:
        tokens = [MODULE_DISPLAY_ALIAS.get(p.upper(), p.upper()) for p in path]
    else:
        tokens = [cluster_l.upper(), unit_l.upper()]

    arrow = "<-" if (row_direction or "").strip().lower() == "input" else "->"
    return arrow.join(tokens)


def load_signal_owner_index(icf_glob: str) -> dict:
    """Build signal ownership index from ICF input/output node declarations."""
    index = defaultdict(
        lambda: {
            "outputs": set(),
            "inputs": set(),
            "output_evidence": set(),
            "input_evidence": set(),
        }
    )
    for icf_path in sorted(Path(".").glob(icf_glob)):
        cluster = extract_cluster_from_path(icf_path)
        if not cluster:
            continue
        for raw_line in icf_path.read_text(errors='ignore').splitlines():
            line = strip_line_comment(raw_line).strip()
            if not line:
                continue
            match = ICF_DECL_RE.match(line)
            if not match:
                continue
            direction = match.group(1)
            signal_name = extract_signal_name_from_decl_body(match.group("body"))
            if not signal_name:
                continue

            if direction == "output":
                index[signal_name]["outputs"].add(cluster)
                index[signal_name]["output_evidence"].add(f"{cluster}:{icf_path.as_posix()}")
            elif direction == "input":
                index[signal_name]["inputs"].add(cluster)
                index[signal_name]["input_evidence"].add(f"{cluster}:{icf_path.as_posix()}")
    return index


def resolve_owner_for_row(row: dict, owner_index: dict) -> tuple[str, str, str]:
    """Resolve producer owner/origin/evidence for a row from referenced identifiers."""
    owners = set()
    evidence = set()
    identifiers = get_row_identifiers(row)
    for identifier in identifiers:
        owner_entry = owner_index.get(identifier)
        if not owner_entry:
            continue
        owners.update(owner_entry["outputs"])
        evidence.update(owner_entry["output_evidence"])

    row_direction = (row.get("port_direction") or "").strip()
    owner_units = extract_owner_units_from_evidence(evidence, row_direction)

    # Prefer concrete sub-unit ownership chains over top-level duplicates.
    def is_specific_owner_chain(value: str) -> bool:
        tokens = [t for t in re.split(r"<-|->|/", value) if t]
        return len(tokens) >= 2 and tokens[0] != tokens[-1]

    specific_owner_units = {x for x in owner_units if is_specific_owner_chain(x)}
    preferred_owner_units = specific_owner_units if specific_owner_units else owner_units

    owner_value = ";".join(sorted(owners)) if owners else "NONE"
    evidence_value = ";".join(sorted(evidence)) if evidence else "NONE"
    owner_units_value = ";".join(sorted(preferred_owner_units)) if preferred_owner_units else "NONE"

    origin_hint = "NONE"
    # MLi* signals are typically MLC-origin semantics carried on MEU-owned interfaces.
    if owners and any(sig.startswith("MLi") for sig in identifiers) and "meu" in owners:
        origin_hint = "mlc_interface"

    return owner_value, origin_hint, evidence_value, owner_units_value


def apply_top_input_exact_match_fallback(
    row: dict,
    out_row: dict,
    owner_index: dict,
    top_port_directions: dict,
    fe_top_input_net_index: dict,
) -> None:
    """Backfill unresolved top-connected rows using output ownership only."""
    if out_row.get("source_output_units") != "NONE":
        return
    if (out_row.get("is_connected_to_top") or "").lower() != "true":
        return

    row_direction = (row.get("port_direction") or "").strip()
    top_matches = get_top_port_matches(row, top_port_directions)
    if not top_matches:
        return

    for signal_name in top_matches:
        candidate_identifiers = []
        mapped_net = fe_top_input_net_index.get(signal_name)
        if mapped_net:
            candidate_identifiers.append(mapped_net)
        candidate_identifiers.append(signal_name)

        for identifier in candidate_identifiers:
            owner_entry = owner_index.get(identifier)
            if not owner_entry:
                continue

            output_owners = set(owner_entry.get("outputs", set()))
            output_evidence = set(owner_entry.get("output_evidence", set()))
            if not output_owners:
                continue

            owner_units = extract_owner_units_from_evidence(output_evidence, row_direction)

            def is_specific_owner_chain(value: str) -> bool:
                tokens = [t for t in re.split(r"<-|->|/", value) if t]
                return len(tokens) >= 2 and tokens[0] != tokens[-1]

            specific_owner_units = {x for x in owner_units if is_specific_owner_chain(x)}
            preferred_owner_units = specific_owner_units if specific_owner_units else owner_units

            out_row["source_output_units"] = (
                ";".join(sorted(preferred_owner_units)) if preferred_owner_units else "TOP_INPUT_OR_EXTERNAL"
            )
            out_row["producer_cluster_owner"] = ";".join(sorted(output_owners))
            out_row["producer_origin_hint"] = "top_input_output_owner"
            out_row["producer_owner_evidence"] = (
                ";".join(sorted(output_evidence)) if output_evidence else "NONE"
            )
            return


def load_fe_top_tieoff_index(icore_icf: Path) -> dict:
    """Build signal -> tieoff info from icore FE pin mappings like (.Sig('0))."""
    index = {}
    if not icore_icf.exists():
        return index

    current_signal = None
    lines = icore_icf.read_text(errors='ignore').splitlines()
    for i, raw_line in enumerate(lines, start=1):
        line = strip_line_comment(raw_line).strip()
        if not line:
            continue

        decl_match = ICF_DECL_RE.match(line)
        if decl_match:
            current_signal = extract_signal_name_from_decl_body(decl_match.group("body"))
            continue

        node_match = ICF_NODE_RE.match(line)
        if node_match:
            current_signal = extract_signal_name_from_decl_body(node_match.group("body"))
            continue

        if current_signal is None:
            continue

        pin_match = ICF_PIN_FE_CONST_RE.match(line)
        if not pin_match:
            continue

        expr = pin_match.group("expr").strip()
        if "'" not in expr:
            continue

        index[current_signal] = {
            "unit": "TOP_TIEDOFF_CONST",
            "evidence": f"core/common/rtl/icore.icf:{i}:{expr}",
        }
    return index


def load_fe_top_input_net_index(icore_icf: Path) -> dict:
    """Build top FE input signal -> mapped net identifier from icore.icf pin mapping."""
    index = {}
    if not icore_icf.exists():
        return index

    lines = icore_icf.read_text(errors='ignore').splitlines()
    for raw_line in lines:
        line = strip_line_comment(raw_line).strip()
        if not line:
            continue

        if not ICF_PIN_FE_CONST_RE.match(line):
            continue

        node_match = re.search(r"\binput\s+node\s+([A-Za-z_][A-Za-z0-9_$]*)", line)
        if not node_match:
            continue
        top_signal_name = node_match.group(1)

        # Parse pin binding syntax like .PortName(NetName)
        bind_match = re.search(
            r"\.\s*[A-Za-z_][A-Za-z0-9_$]*\s*\(\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\)",
            line,
        )
        if not bind_match:
            continue

        net_name = bind_match.group(1)
        if "'" in net_name:
            continue

        index[top_signal_name] = net_name

    return index


def split_tlm_units(units: set) -> tuple:
    """Split connected units into non-TLM and *_tlm groups."""
    non_tlm = set()
    tlm = set()
    for unit in units:
        if unit.lower().endswith("_tlm"):
            tlm.add(unit)
        else:
            non_tlm.add(unit)
    return non_tlm, tlm


def join_units(units: set) -> str:
    """Return sorted semicolon-joined units, or NONE when empty."""
    return ";".join(sorted(units) if units else ["NONE"])


def row_key(row: dict) -> tuple:
    """Key that uniquely identifies an instance-port row in query/typed CSV."""
    return (
        (row.get("instance_module") or "").strip(),
        (row.get("instance_name") or "").strip(),
        (row.get("port_name") or "").strip(),
        (row.get("source_line") or "").strip(),
    )


def build_identifier_index(rows: list) -> dict:
    """Build identifier -> rows map using signal_name_normalized and port_name fallback."""
    by_identifier = defaultdict(list)
    for row in rows:
        signal_name_normalized = (row.get("signal_name_normalized") or "").strip()
        if signal_name_normalized:
            by_identifier[signal_name_normalized].append(row)
        # Fallback: index by port_name when signal_name_normalized is empty.
        # This handles array-indexed expressions like IDLSrcM200H[1][0][...]
        # where the connected_expr is not a plain identifier.
        port_name = (row.get("port_name") or "").strip()
        if port_name and port_name != signal_name_normalized:
            by_identifier[port_name].append(row)
    return by_identifier


def load_top_port_directions(rows: list) -> dict:
    """Load top-level port directions from inferred <top_module>.port_decls.v."""
    top_module = ""
    source_file = ""
    for row in rows:
        top_module = (row.get("top_module") or "").strip()
        source_file = (row.get("source_file") or "").strip()
        if top_module and source_file:
            break

    if not top_module or not source_file:
        return {}

    port_decls_path = Path(source_file).with_name(f"{top_module}.port_decls.v")
    if not port_decls_path.exists():
        return {}

    port_directions = {}
    for raw_line in port_decls_path.read_text(errors='ignore').splitlines():
        line = strip_line_comment(raw_line).strip()
        if not line or line.startswith("`"):
            continue

        match = PORT_DECL_RE.match(line)
        if not match:
            continue

        signal_name = extract_signal_name_from_decl_body(match.group("body"))
        if not signal_name:
            continue

        port_directions[signal_name] = match.group(1)
    return port_directions


def get_top_port_matches(row: dict, top_port_directions: dict) -> list:
    """Return declared top-level ports referenced by this row."""
    matches = []
    for identifier in get_row_identifiers(row):
        if identifier in top_port_directions:
            matches.append(identifier)
    return sorted(set(matches))


def get_row_identifiers(row: dict) -> list:
    """Extract signal identifiers for a row from normalized or expression fields."""
    if parse_bool_str(row.get("is_plain_identifier", "")) and (
        row.get("signal_name_normalized", "").strip()
    ):
        return [row.get("signal_name_normalized", "").strip()]
    # For expression-connected rows, try port_name first (matches the index
    # fallback), then fall back to extracting tokens from connected_expr.
    results = []
    port_name = (row.get("port_name") or "").strip()
    if port_name:
        results.append(port_name)
    connected_expr = (row.get("connected_expr") or "").strip()
    results.extend(extract_identifiers(connected_expr))
    return results


def classify_connected_units_for_row(
    row: dict,
    by_identifier: dict,
    parent_by_identifier: dict | None = None,
    include_self_output_as_source: bool = False,
) -> tuple[set, set]:
    """Classify connected units for one row into output sources and other peers."""
    connected_expr = (row.get("connected_expr") or "").strip()
    source_output_units = set()
    connected_other_units = set()
    this_instance = (row.get("instance_name") or row.get("instance_module") or "").strip()

    expr_identifiers = get_row_identifiers(row)

    # In row-wise IO mode, an output row is sourced by its own instance.
    if include_self_output_as_source and (row.get("port_direction") or "").strip() == "output" and this_instance:
        source_output_units.add(this_instance)

    this_key = row_key(row)

    for identifier in expr_identifiers:
        if not identifier:
            continue

        peer_rows = by_identifier.get(identifier, [])
        if not peer_rows:
            continue

        for peer_row in peer_rows:
            if row_key(peer_row) == this_key:
                continue

            peer_instance = (peer_row.get("instance_name") or peer_row.get("instance_module") or "").strip()
            peer_direction = (peer_row.get("port_direction") or "").strip()

            if not peer_instance:
                continue

            if peer_direction == "output":
                source_output_units.add(peer_instance)
            elif peer_direction in ("input", "inout"):
                connected_other_units.add(peer_instance)

    # Optional parent-level fallback: if local peers are empty, try parent query rows.
    if not connected_other_units and parent_by_identifier:
        for identifier in expr_identifiers:
            if not identifier:
                continue
            for parent_row in parent_by_identifier.get(identifier, []):
                parent_instance = (parent_row.get("instance_name") or parent_row.get("instance_module") or "").strip()
                if not parent_instance or parent_instance == this_instance:
                    continue
                connected_other_units.add(parent_instance)

    # If there are no peer units and expression uses literal ticks, mark as expression-based.
    if not source_output_units and not connected_other_units and "'" in connected_expr:
        source_output_units.add("CONSTANT_OR_EXPRESSION")

    return source_output_units, connected_other_units


def detect_connected_to_top(row: dict, top_port_directions: dict) -> bool:
    """Return True when row expression references a declared top-level port."""
    return bool(get_top_port_matches(row, top_port_directions))


def print_top_port_coverage_summary(rows: list, top_port_directions: dict) -> None:
    """Print validation summary comparing declared vs referenced top ports."""
    declared_inputs = {port for port, direction in top_port_directions.items() if direction == "input"}
    declared_outputs = {port for port, direction in top_port_directions.items() if direction == "output"}

    referenced_inputs = set()
    referenced_outputs = set()

    for row in rows:
        for port in get_top_port_matches(row, top_port_directions):
            direction = top_port_directions.get(port)
            if direction == "input":
                referenced_inputs.add(port)
            elif direction == "output":
                referenced_outputs.add(port)

    missing_inputs = sorted(declared_inputs - referenced_inputs)
    missing_outputs = sorted(declared_outputs - referenced_outputs)

    print(f"TOP_INPUT_PORTS_DECLARED={len(declared_inputs)}", file=sys.stderr)
    print(f"TOP_INPUT_PORTS_REFERENCED={len(referenced_inputs)}", file=sys.stderr)
    print(f"TOP_INPUT_PORTS_MATCH={'true' if len(declared_inputs) == len(referenced_inputs) else 'false'}", file=sys.stderr)
    print(f"TOP_OUTPUT_PORTS_DECLARED={len(declared_outputs)}", file=sys.stderr)
    print(f"TOP_OUTPUT_PORTS_REFERENCED={len(referenced_outputs)}", file=sys.stderr)
    print(f"TOP_OUTPUT_PORTS_MATCH={'true' if len(declared_outputs) == len(referenced_outputs) else 'false'}", file=sys.stderr)
    print(f"TOP_INPUT_PORTS_MISSING={';'.join(missing_inputs) if missing_inputs else 'NONE'}", file=sys.stderr)
    print(f"TOP_OUTPUT_PORTS_MISSING={';'.join(missing_outputs) if missing_outputs else 'NONE'}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Generate module or all-lines I/O tables from typed/query connectivity CSV"
    )
    parser.add_argument(
        "--typed-csv",
        help="Path to typed connectivity CSV (output of step 3)",
    )
    parser.add_argument(
        "--query-csv",
        help="Path to query-view CSV (output of step 7)",
    )
    parser.add_argument(
        "--parent-query-csv",
        help="Optional parent-level query-view CSV for fallback connected-unit lookup",
    )
    parser.add_argument(
        "--module",
        help="Module name to generate I/O table for (e.g., 'ifu', 'stsr')",
    )
    parser.add_argument(
        "--all-lines-io-table",
        action="store_true",
        help="Generate row-wise I/O table for all lines in query CSV",
    )
    parser.add_argument(
        "--out-csv",
        required=True,
        help="Output CSV file path",
    )
    parser.add_argument(
        "--owner-icf-glob",
        default="core/*/rtl/*.icf",
        help="Glob for ICF files used to resolve producer cluster ownership",
    )
    parser.add_argument(
        "--alias-review-csv",
        help="Optional CSV path for unresolved near-name alias candidates (review-only)",
    )
    args = parser.parse_args()

    if args.all_lines_io_table:
        if not args.query_csv:
            print("ERROR: --query-csv is required with --all-lines-io-table", file=sys.stderr)
            sys.exit(1)
        input_csv = Path(args.query_csv)
    else:
        if not args.typed_csv or not args.module:
            print("ERROR: --typed-csv and --module are required for module I/O table mode", file=sys.stderr)
            sys.exit(1)
        input_csv = Path(args.typed_csv)

    module_name = (args.module or "").lower()
    out_csv = Path(args.out_csv)

    if not input_csv.exists():
        print(f"ERROR: {input_csv} not found", file=sys.stderr)
        sys.exit(1)

    if args.all_lines_io_table:
        print(f"Processing all-lines IO table from {input_csv}...", file=sys.stderr)
        with open(input_csv, 'r') as f:
            reader = csv.DictReader(f)
            input_rows = list(reader)
            input_fields = list(reader.fieldnames or [])

        parent_by_identifier = None
        if args.parent_query_csv:
            parent_csv = Path(args.parent_query_csv)
            if not parent_csv.exists():
                print(f"ERROR: {parent_csv} not found", file=sys.stderr)
                sys.exit(1)
            with open(parent_csv, 'r') as pf:
                parent_reader = csv.DictReader(pf)
                parent_rows = list(parent_reader)
            parent_by_identifier = build_identifier_index(parent_rows)
            print(f"Loaded parent fallback index from {parent_csv}", file=sys.stderr)

        by_identifier = build_identifier_index(input_rows)
        top_port_directions = load_top_port_directions(input_rows)
        owner_index = load_signal_owner_index(args.owner_icf_glob)
        owner_signals = sorted(owner_index.keys())
        icore_icf = Path("core/common/rtl/icore.icf")
        fe_tieoff_index = load_fe_top_tieoff_index(icore_icf)
        fe_top_input_net_index = load_fe_top_input_net_index(icore_icf)
        alias_review_rows = []
        output_rows = []
        for row_idx, row in enumerate(input_rows, start=2):
            src_units, other_units = classify_connected_units_for_row(
                row,
                by_identifier,
                parent_by_identifier=parent_by_identifier,
                include_self_output_as_source=True,
            )
            non_tlm_other_units, tlm_other_units = split_tlm_units(other_units)
            out_row = dict(row)
            out_row["source_output_units"] = join_units(src_units)
            out_row["connected_other_units"] = join_units(non_tlm_other_units)
            out_row["connected_tlm_units"] = join_units(tlm_other_units)
            out_row["is_connected_to_top"] = "true" if detect_connected_to_top(row, top_port_directions) else "false"
            owner_value, origin_hint, owner_evidence, owner_units_value = resolve_owner_for_row(row, owner_index)
            out_row["producer_cluster_owner"] = owner_value
            out_row["producer_origin_hint"] = origin_hint
            out_row["producer_owner_evidence"] = owner_evidence

            # Backfill unresolved source_output_units with producer cluster/unit ownership.
            if out_row["source_output_units"] == "NONE" and owner_units_value != "NONE":
                out_row["source_output_units"] = owner_units_value

            # If still unresolved but top-connected, detect FE top-level tie-off constants.
            if (
                out_row["source_output_units"] == "NONE"
                and out_row["is_connected_to_top"] == "true"
            ):
                tieoff = None
                for identifier in get_row_identifiers(row):
                    if identifier in fe_tieoff_index:
                        tieoff = fe_tieoff_index[identifier]
                        break

                if tieoff:
                    out_row["source_output_units"] = tieoff["unit"]
                    out_row["producer_cluster_owner"] = "icore"
                    out_row["producer_origin_hint"] = "top_level_tieoff"
                    out_row["producer_owner_evidence"] = tieoff["evidence"]

            # SOP fallback: treat exact-name top-input ownership as resolved.
            apply_top_input_exact_match_fallback(
                row=row,
                out_row=out_row,
                owner_index=owner_index,
                top_port_directions=top_port_directions,
                fe_top_input_net_index=fe_top_input_net_index,
            )

            # Review-only alias detection for unresolved rows.
            if (
                out_row["source_output_units"] == "NONE"
                and out_row["producer_cluster_owner"] == "NONE"
            ):
                query_signals = []
                query_signals.extend(get_row_identifiers(row))
                for key in ("signal_name_normalized", "port_name"):
                    value = (row.get(key) or "").strip()
                    if value:
                        query_signals.append(value)
                query_signals = sorted(set(query_signals))

                for query_signal in query_signals:
                    # Skip alias review only when exact-name has an output owner.
                    exact_entry = owner_index.get(query_signal)
                    if exact_entry and exact_entry.get("outputs"):
                        continue

                    candidates = collect_alias_candidates(
                        query_signal=query_signal,
                        owner_index=owner_index,
                        owner_signals=owner_signals,
                    )
                    for candidate in candidates:
                        score = float(candidate["match_score"])
                        # Separate review is for T-mismatch or repeater (_rpt*) variants.
                        if not (
                            is_t_only_mismatch(query_signal, candidate["candidate_signal"])
                            or is_repeater_variant(query_signal, candidate["candidate_signal"])
                            or score >= 0.82
                        ):
                            continue
                        alias_review_rows.append(
                            {
                                "source_row_number": row_idx,
                                "instance_module": row.get("instance_module", ""),
                                "instance_name": row.get("instance_name", ""),
                                "port_name": row.get("port_name", ""),
                                "port_direction": row.get("port_direction", ""),
                                "connected_expr": row.get("connected_expr", ""),
                                "is_connected_to_top": out_row.get("is_connected_to_top", ""),
                                "alias_query_signal": query_signal,
                                "candidate_signal": candidate["candidate_signal"],
                                "match_score": candidate["match_score"],
                                "candidate_owner_clusters": candidate["candidate_owner_clusters"],
                                "candidate_owner_evidence": candidate["candidate_owner_evidence"],
                                "review_note": "candidate_only_not_applied",
                            }
                        )
            output_rows.append(out_row)

        out_csv.parent.mkdir(parents=True, exist_ok=True)
        output_fields = list(input_fields)
        insert_at = output_fields.index("port_direction") + 1 if "port_direction" in output_fields else len(output_fields)
        output_fields[insert_at:insert_at] = ["source_output_units", "connected_other_units", "connected_tlm_units", "is_connected_to_top"]
        if "producer_cluster_owner" not in output_fields:
            output_fields.extend(["producer_cluster_owner", "producer_origin_hint", "producer_owner_evidence"])
        with open(out_csv, 'w', newline='') as f:
            writer = csv.DictWriter(
                f,
                fieldnames=output_fields,
            )
            writer.writeheader()
            writer.writerows(output_rows)

        alias_review_csv = Path(args.alias_review_csv) if args.alias_review_csv else out_csv.with_name(f"{out_csv.stem}_alias_review.csv")
        with open(alias_review_csv, 'w', newline='') as af:
            aw = csv.DictWriter(
                af,
                fieldnames=[
                    "source_row_number",
                    "instance_module",
                    "instance_name",
                    "port_name",
                    "port_direction",
                    "connected_expr",
                    "is_connected_to_top",
                    "alias_query_signal",
                    "candidate_signal",
                    "match_score",
                    "candidate_owner_clusters",
                    "candidate_owner_evidence",
                    "review_note",
                ],
            )
            aw.writeheader()
            aw.writerows(alias_review_rows)
        print(f"ALIAS_REVIEW_CSV={alias_review_csv} ROWS={len(alias_review_rows)}", file=sys.stderr)

        if top_port_directions:
            print_top_port_coverage_summary(output_rows, top_port_directions)

        print(f"WROTE={out_csv} ROWS={len(output_rows)}", file=sys.stderr)
        return

    # Phase 1: Build mapping of identifiers to their connectivity rows (non-module rows)
    by_identifier = defaultdict(list)
    module_rows = []
    
    print(f"Processing {input_csv}...", file=sys.stderr)
    with open(input_csv, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            inst_module = (row.get("instance_module") or "").strip().lower()
            
            if inst_module == module_name:
                # Collect module rows for aggregation phase
                module_rows.append(row)
            else:
                # Build identifier map for non-module rows (potential producers/peers)
                signal_name_normalized = (row.get("signal_name_normalized") or "").strip()
                if signal_name_normalized:
                    # Each identifier in this row is a potential producer/peer for module signals
                    by_identifier[signal_name_normalized].append(row)

    print(
        f"Loaded {len(module_rows)} {module_name} rows and indexed {len(by_identifier)} identifiers",
        file=sys.stderr,
    )

    # Phase 2: Aggregate by module port (signal) and classify connected units
    aggregated = {}

    for row in module_rows:
        port_name = (row.get("port_name") or "").strip()
        direction = (row.get("port_direction") or "").strip()
        connected_expr = (row.get("connected_expr") or "").strip()

        if not port_name:
            continue

        if port_name not in aggregated:
            aggregated[port_name] = {
                "port_name": port_name,
                "direction": direction,
                "source_output_units": set(),
                "other_connected_units": set(),
            }

        src_units, other_units = classify_connected_units_for_row(row, by_identifier)
        aggregated[port_name]["source_output_units"].update(src_units)
        aggregated[port_name]["other_connected_units"].update(other_units)

    # Phase 3: Write aggregated table
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    with open(out_csv, 'w', newline='') as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "signal_name",
                "direction",
                "source_output_units",
                "other_connected_units",
                "tlm_units_connected",
            ],
        )
        writer.writeheader()

        for signal_name in sorted(aggregated.keys()):
            agg = aggregated[signal_name]
            non_tlm_units, tlm_units = split_tlm_units(agg["other_connected_units"])
            writer.writerow(
                {
                    "signal_name": agg["port_name"],
                    "direction": agg["direction"],
                    "source_output_units": join_units(agg["source_output_units"]),
                    "other_connected_units": join_units(non_tlm_units),
                    "tlm_units_connected": join_units(tlm_units),
                }
            )

    print(f"WROTE={out_csv} ROWS={len(aggregated)}", file=sys.stderr)


if __name__ == "__main__":
    main()
