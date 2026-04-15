#!/usr/bin/env python3
"""Build a module-port direction database from generated .port_decls.v files.

This stage consumes either:
- A connectivity/modules CSV (with an instance_module or module column), or
- A top-level RTL file (from which instantiated module names are extracted),
then parses each matching <module>.port_decls.v file under a generated RTL
folder and emits a deterministic direction map for downstream joins.
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

PORT_DECL_RE = re.compile(r"^\s*(?P<direction>input|output|inout)\b(?P<body>[^;]*);")
PORT_DECL_DETAIL_RE = re.compile(
    r"^\s*"
    r"(?P<direction>input|output|inout)\s+"
    r"(?P<sv_type>[A-Za-z_][A-Za-z0-9_$]*)\s+"
    r"(?P<packed_width>(?:\[[^\]]+\]\s*)+)?"
    r"(?P<signal_name>[A-Za-z_][A-Za-z0-9_$]*)"
    r"\s*(?P<unpacked_dim>(?:\[[^\]]+\]\s*)+)?"
    r"\s*;"
)
TRAILING_DIMS_RE = re.compile(r"\s*(\[[^\]]+\]\s*)*$")
SIGNAL_NAME_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_$]*)$")


def strip_line_comment(line: str) -> str:
    if "//" not in line:
        return line
    return line.split("//", 1)[0]


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


def collect_modules_from_top_v(top_v_path: Path) -> set[str]:
    lines = top_v_path.read_text(encoding="utf-8", errors="replace").splitlines()
    modules: set[str] = set()
    index = 0

    while index < len(lines):
        raw_line = lines[index]
        if not looks_like_instance_start(raw_line):
            index += 1
            continue

        header_lines: list[str] = []
        scan_index = index
        while scan_index < len(lines):
            line = lines[scan_index].strip()
            if line.startswith("`"):
                scan_index += 1
                continue
            header_lines.append(strip_line_comment(lines[scan_index]))
            header_text = "\n".join(header_lines)
            header_match = INSTANCE_HEADER_RE.match(header_text)
            if header_match:
                modules.add(header_match.group("module"))
                break
            scan_index += 1

        index += 1

    return modules


def collect_modules_from_csv(modules_csv: Path) -> set[str]:
    modules: set[str] = set()
    with modules_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return modules

        # Prefer connectivity output column names.
        module_col = None
        for candidate in ("instance_module", "module", "module_name"):
            if candidate in reader.fieldnames:
                module_col = candidate
                break

        if module_col is None:
            raise ValueError(
                "modules CSV must include one of: instance_module, module, module_name"
            )

        for row in reader:
            module = (row.get(module_col) or "").strip()
            if module:
                modules.add(module)

    return modules


def resolve_port_decls_path(module: str, gen_dir: Path, fallback_gen_dir: Path | None) -> Path | None:
    primary = gen_dir / f"{module}.port_decls.v"
    if primary.exists():
        return primary

    if fallback_gen_dir is not None:
        secondary = fallback_gen_dir / f"{module}.port_decls.v"
        if secondary.exists():
            return secondary

    return None


def extract_signal_name_from_decl_body(body: str) -> str | None:
    no_dims = TRAILING_DIMS_RE.sub("", body)
    match = SIGNAL_NAME_RE.search(no_dims.strip())
    if not match:
        return None
    return match.group(1)


def parse_port_decls_file(module: str, port_decls_path: Path) -> tuple[list[dict[str, str | int]], int]:
    rows: list[dict[str, str | int]] = []
    skipped_lines = 0
    with port_decls_path.open(encoding="utf-8", errors="replace") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = strip_line_comment(raw_line).strip()
            if not line or line.startswith("`"):
                continue

            match = PORT_DECL_RE.match(line)
            if not match:
                continue

            direction = match.group("direction")
            sv_type = ""
            packed_width = ""
            unpacked_dim = ""

            detail_match = PORT_DECL_DETAIL_RE.match(line)
            if detail_match:
                signal_name = detail_match.group("signal_name")
                sv_type = (detail_match.group("sv_type") or "").strip()
                packed_width = (detail_match.group("packed_width") or "").strip()
                unpacked_dim = (detail_match.group("unpacked_dim") or "").strip()
            else:
                body = match.group("body")
                signal_name = extract_signal_name_from_decl_body(body)
                if signal_name is None:
                    skipped_lines += 1
                    continue

            rows.append(
                {
                    "module": module,
                    "port": signal_name,
                    "direction": direction,
                    "sv_type": sv_type,
                    "packed_width": packed_width,
                    "unpacked_dim": unpacked_dim,
                    "decl_line": line_number,
                    "source_file": str(port_decls_path),
                }
            )

    return rows, skipped_lines


def write_csv(rows: list[dict[str, str | int]], out_csv: Path) -> None:
    fieldnames = [
        "module",
        "port",
        "direction",
        "sv_type",
        "packed_width",
        "unpacked_dim",
        "decl_line",
        "source_file",
    ]
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract (module, port, direction) rows from generated .port_decls.v files"
    )

    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--modules-csv",
        help="CSV containing module names (preferably connectivity output with instance_module)",
    )
    source_group.add_argument(
        "--top-v",
        help="Top-level generated RTL file used to discover instantiated modules",
    )

    parser.add_argument(
        "--gen-dir",
        required=True,
        help="Primary generated RTL directory containing *.port_decls.v",
    )
    parser.add_argument(
        "--fallback-gen-dir",
        default=None,
        help="Optional fallback generated RTL directory (useful for msid->fe lookup)",
    )
    parser.add_argument("--out-csv", required=True, help="Output CSV path for direction DB")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    gen_dir = Path(args.gen_dir)
    out_csv = Path(args.out_csv)
    fallback_gen_dir = Path(args.fallback_gen_dir) if args.fallback_gen_dir else None

    if not gen_dir.exists():
        print(f"ERROR: --gen-dir not found: {gen_dir}", file=sys.stderr)
        sys.exit(1)

    if args.modules_csv:
        modules_csv = Path(args.modules_csv)
        if not modules_csv.exists():
            print(f"ERROR: --modules-csv not found: {modules_csv}", file=sys.stderr)
            sys.exit(1)
        try:
            modules = collect_modules_from_csv(modules_csv)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        module_source = str(modules_csv)
    else:
        top_v_path = Path(args.top_v)
        if not top_v_path.exists():
            print(f"ERROR: --top-v not found: {top_v_path}", file=sys.stderr)
            sys.exit(1)
        modules = collect_modules_from_top_v(top_v_path)
        module_source = str(top_v_path)

    modules_sorted = sorted(modules)
    all_rows: list[dict[str, str | int]] = []
    missing_modules: list[str] = []
    skipped_decl_lines = 0

    for module in modules_sorted:
        port_decls_path = resolve_port_decls_path(module, gen_dir, fallback_gen_dir)
        if port_decls_path is None:
            missing_modules.append(module)
            continue
        rows, skipped = parse_port_decls_file(module, port_decls_path)
        skipped_decl_lines += skipped
        all_rows.extend(rows)

    all_rows.sort(
        key=lambda row: (
            str(row["module"]),
            str(row["port"]),
            str(row["direction"]),
            str(row["packed_width"]),
            str(row["unpacked_dim"]),
            int(row["decl_line"]),
            str(row["source_file"]),
        )
    )
    write_csv(all_rows, out_csv)

    input_count = sum(1 for row in all_rows if row["direction"] == "input")
    output_count = sum(1 for row in all_rows if row["direction"] == "output")
    inout_count = sum(1 for row in all_rows if row["direction"] == "inout")

    print(f"Module source: {module_source}")
    print(f"Discovered modules: {len(modules_sorted)}")
    print(f"Missing .port_decls.v files: {len(missing_modules)}")
    if missing_modules:
        print("Missing modules:")
        for module in missing_modules:
            print(f"  - {module}")
    print(
        "Extracted "
        f"{len(all_rows)} direction rows "
        f"({input_count} input, {output_count} output, {inout_count} inout)"
    )
    print(f"Skipped malformed decl lines: {skipped_decl_lines}")
    print(f"Output: {out_csv}")


if __name__ == "__main__":
    main()
