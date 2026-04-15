#!/usr/bin/env python3
"""Extract raw instance connectivity from a generated top-level RTL file.

This stage is intentionally deterministic and instance-aware. It records one row
per named port connection from the top-level generated RTL, along with
provenance needed by downstream ownership and grouping stages.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path


INSTANCE_HEADER_RE = re.compile(
    r"^\s*(?P<module>[A-Za-z_][A-Za-z0-9_$]*)\s*"
    r"(?:#\s*\((?P<params>.*?)\)\s*)?"
    r"(?P<instance>[A-Za-z_][A-Za-z0-9_$]*)\s*\(",
    re.DOTALL,
)
PORT_CONNECTION_RE = re.compile(
    r"^\s*\.\s*(?P<port>[A-Za-z_][A-Za-z0-9_$]*)\s*\((?P<expr>.*)\)\s*$",
    re.DOTALL,
)
PLAIN_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

HEADER_KEYWORDS = {
    "always",
    "always_comb",
    "always_ff",
    "always_latch",
    "assign",
    "case",
    "casex",
    "casez",
    "for",
    "function",
    "generate",
    "if",
    "initial",
    "module",
    "package",
    "primitive",
    "task",
    "typedef",
    "while",
}


def strip_line_comment(line: str) -> str:
    if "//" not in line:
        return line
    return line.split("//", 1)[0]


def normalize_whitespace(text: str) -> str:
    return " ".join(text.strip().split())


def invert_guard(guard: str) -> str:
    if guard.startswith("!"):
        return guard[1:]
    return f"!{guard}"


def update_guard_stack(stripped_line: str, guard_stack: list[str]) -> bool:
    directive = stripped_line.split()
    if not directive:
        return False

    keyword = directive[0]
    if keyword == "`ifdef" and len(directive) >= 2:
        guard_stack.append(directive[1])
        return True
    if keyword == "`ifndef" and len(directive) >= 2:
        guard_stack.append(f"!{directive[1]}")
        return True
    if keyword == "`else":
        if guard_stack:
            guard_stack[-1] = invert_guard(guard_stack[-1])
        return True
    if keyword == "`endif":
        if guard_stack:
            guard_stack.pop()
        return True
    return keyword.startswith("`")


def current_guard_expr(guard_stack: list[str]) -> str:
    return " && ".join(guard_stack)


def looks_like_instance_start(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("//") or stripped.startswith("`"):
        return False
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_$]*)\b", stripped)
    if not match:
        return False
    if match.group(1) in HEADER_KEYWORDS:
        return False
    if "(" not in stripped and "#" not in stripped:
        return False
    if "=" in stripped.split("(", 1)[0]:
        return False
    return True


def parse_connection_item(item_text: str) -> tuple[str, str] | None:
    match = PORT_CONNECTION_RE.match(item_text)
    if not match:
        return None
    return match.group("port"), normalize_whitespace(match.group("expr"))


def build_row(
    cluster: str,
    top_module: str,
    source_file: Path,
    instance_module: str,
    instance_name: str,
    port_name: str,
    connected_expr: str,
    source_line: int,
    guard_expr: str,
) -> dict[str, str | int]:
    is_unconnected = connected_expr == ""
    is_plain_identifier = bool(PLAIN_IDENTIFIER_RE.fullmatch(connected_expr)) if connected_expr else False
    signal_name_normalized = connected_expr if is_plain_identifier else ""
    return {
        "cluster": cluster,
        "top_module": top_module,
        "instance_module": instance_module,
        "instance_name": instance_name,
        "port_name": port_name,
        "connected_expr": connected_expr,
        "normalized_expr": connected_expr,
        "signal_name_normalized": signal_name_normalized,
        "is_plain_identifier": str(is_plain_identifier).lower(),
        "is_unconnected": str(is_unconnected).lower(),
        "is_conditional": str(bool(guard_expr)).lower(),
        "guard_expr": guard_expr,
        "source_file": str(source_file),
        "source_line": source_line,
    }


def extract_instance_rows(
    lines: list[str],
    start_index: int,
    cluster: str,
    top_module: str,
    source_file: Path,
    incoming_guard_stack: list[str],
) -> tuple[list[dict[str, str | int]], int, list[str]]:
    header_lines: list[str] = []
    header_line_numbers: list[int] = []
    scan_index = start_index

    while scan_index < len(lines):
        raw_line = lines[scan_index]
        stripped = raw_line.strip()
        if update_guard_stack(stripped, incoming_guard_stack):
            scan_index += 1
            continue

        header_lines.append(strip_line_comment(raw_line))
        header_line_numbers.append(scan_index + 1)
        header_text = "\n".join(header_lines)
        header_match = INSTANCE_HEADER_RE.match(header_text)
        if header_match:
            break
        scan_index += 1

    if not header_lines:
        return [], start_index + 1, incoming_guard_stack

    header_text = "\n".join(header_lines)
    header_match = INSTANCE_HEADER_RE.match(header_text)
    if not header_match:
        return [], scan_index, incoming_guard_stack

    instance_module = header_match.group("module")
    instance_name = header_match.group("instance")
    header_consumed = header_text[header_match.end():]
    local_guard_stack = incoming_guard_stack.copy()
    rows: list[dict[str, str | int]] = []
    current_item: list[str] = []
    item_start_line: int | None = header_line_numbers[0]
    item_guard_expr = current_guard_expr(local_guard_stack)
    paren_depth = 0
    block_closed = False

    def flush_item() -> None:
        nonlocal current_item, item_start_line, item_guard_expr
        item_text = "".join(current_item).strip()
        current_item = []
        if not item_text:
            item_start_line = None
            item_guard_expr = current_guard_expr(local_guard_stack)
            return
        parsed = parse_connection_item(item_text)
        if not parsed:
            item_start_line = None
            item_guard_expr = current_guard_expr(local_guard_stack)
            return
        port_name, connected_expr = parsed
        rows.append(
            build_row(
                cluster=cluster,
                top_module=top_module,
                source_file=source_file,
                instance_module=instance_module,
                instance_name=instance_name,
                port_name=port_name,
                connected_expr=connected_expr,
                source_line=item_start_line or header_line_numbers[0],
                guard_expr=item_guard_expr,
            )
        )
        item_start_line = None
        item_guard_expr = current_guard_expr(local_guard_stack)

    def feed_chunk(chunk: str, line_number: int) -> None:
        nonlocal paren_depth, block_closed, item_start_line, item_guard_expr
        for char in chunk:
            if block_closed:
                return
            if item_start_line is None and not char.isspace():
                item_start_line = line_number
                item_guard_expr = current_guard_expr(local_guard_stack)
            if char == "(":
                paren_depth += 1
                current_item.append(char)
                continue
            if char == ")":
                if paren_depth == 0:
                    flush_item()
                    block_closed = True
                    return
                paren_depth -= 1
                current_item.append(char)
                continue
            if char == "," and paren_depth == 0:
                flush_item()
                continue
            current_item.append(char)

    if header_consumed:
        feed_chunk(header_consumed, header_line_numbers[-1])

    current_index = scan_index + 1
    while current_index < len(lines) and not block_closed:
        raw_line = lines[current_index]
        stripped = raw_line.strip()
        if update_guard_stack(stripped, local_guard_stack):
            current_index += 1
            continue
        feed_chunk(strip_line_comment(raw_line), current_index + 1)
        current_index += 1

    return rows, current_index, local_guard_stack


def extract_connectivity_rows(top_v_path: Path, cluster: str, top_module: str) -> list[dict[str, str | int]]:
    lines = top_v_path.read_text(encoding="utf-8", errors="replace").splitlines()
    guard_stack: list[str] = []
    rows: list[dict[str, str | int]] = []
    index = 0

    while index < len(lines):
        raw_line = lines[index]
        stripped = raw_line.strip()
        if update_guard_stack(stripped, guard_stack):
            index += 1
            continue
        if not looks_like_instance_start(raw_line):
            index += 1
            continue

        instance_rows, next_index, updated_guards = extract_instance_rows(
            lines=lines,
            start_index=index,
            cluster=cluster,
            top_module=top_module,
            source_file=top_v_path,
            incoming_guard_stack=guard_stack.copy(),
        )

        if instance_rows:
            rows.extend(instance_rows)
            guard_stack = updated_guards
            index = next_index
            continue

        index += 1

    return rows


def write_csv(rows: list[dict[str, str | int]], output_path: Path) -> None:
    fieldnames = [
        "cluster",
        "top_module",
        "instance_module",
        "instance_name",
        "port_name",
        "connected_expr",
        "normalized_expr",
        "signal_name_normalized",
        "is_plain_identifier",
        "is_unconnected",
        "is_conditional",
        "guard_expr",
        "source_file",
        "source_line",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract raw named-port connectivity rows from generated RTL."
    )
    parser.add_argument("--top-v", required=True, help="Path to the generated top-level RTL file")
    parser.add_argument("--out-csv", required=True, help="Path for the raw connectivity CSV")
    parser.add_argument("--cluster", required=True, help="Cluster name for output provenance")
    parser.add_argument(
        "--top-module",
        default=None,
        help="Top module name (defaults to the stem of --top-v)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    top_v_path = Path(args.top_v)
    if not top_v_path.exists():
        print(f"ERROR: top RTL file not found: {top_v_path}", file=sys.stderr)
        sys.exit(1)

    top_module = args.top_module or top_v_path.stem
    rows = extract_connectivity_rows(
        top_v_path=top_v_path,
        cluster=args.cluster,
        top_module=top_module,
    )
    write_csv(rows, Path(args.out_csv))

    conditional_rows = sum(1 for row in rows if row["is_conditional"] == "true")
    plain_rows = sum(1 for row in rows if row["is_plain_identifier"] == "true")
    print(
        "Extracted "
        f"{len(rows)} connectivity rows across "
        f"{len({(row['instance_module'], row['instance_name']) for row in rows})} instances "
        f"({plain_rows} plain identifiers, {conditional_rows} conditional rows)"
    )
    print(f"Output: {args.out_csv}")


if __name__ == "__main__":
    main()