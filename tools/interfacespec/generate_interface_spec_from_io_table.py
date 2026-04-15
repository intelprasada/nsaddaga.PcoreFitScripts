#!/usr/bin/env python3
"""Generate module interface spec artifacts from row-wise IO table CSV.

Outputs:
- <module>_interface_features.csv
- <module>_interface_classified.csv
- <module>_interface_by_block.csv
- <module>_interface_spec.md

The script uses a hybrid approach:
1) Deterministic feature extraction and seed labeling from signal/connectivity patterns.
2) Optional ML classification (sklearn LogisticRegression) when available and seeds are sufficient.
3) Rule fallback when sklearn is unavailable.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from collections import Counter, defaultdict
from pathlib import Path


RULE_LABELS = [
    "clock_reset_power",
    "debug_monitor_tlm",
    "snoop_coherency",
    "request_command",
    "response_completion",
    "data_payload",
    "control_flow",
    "status_error",
    "misc_unknown",
]

KEYWORD_RULES = {
    "clock_reset_power": ["clk", "clock", "reset", "rst", "pwr", "power"],
    "debug_monitor_tlm": ["tlm", "rtlsi", "debug", "assert", "event", "evt", "trace", "emon"],
    "snoop_coherency": ["snp", "snoop", "coh", "mli"],
    "request_command": ["req", "request", "cmd", "dispatch", "issue"],
    "response_completion": ["rsp", "resp", "cmpl", "ack", "nack", "done"],
    "data_payload": ["data", "payload", "id", "addr", "tag"],
    "control_flow": ["stall", "flow", "credit", "valid", "ready", "enable", "en"],
    "status_error": ["status", "error", "err", "fault", "poison", "warn"],
}

TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+")

BLOCK_NAME_RULES = [
    (re.compile(r"^mlisnp", re.IGNORECASE), "DCU", "name:mlisnp"),
    (re.compile(r"^mli", re.IGNORECASE), "DCU", "name:mli"),
    (re.compile(r"mcache", re.IGNORECASE), "MCACHE", "name:mcache"),
    (re.compile(r"ifreq|request", re.IGNORECASE), "MCACHE", "name:ifreq_request"),
    (re.compile(r"^bp", re.IGNORECASE), "BPU", "name:bp_prefix"),
    (re.compile(r"^ba", re.IGNORECASE), "BAC", "name:ba_prefix"),
    (re.compile(r"^dsb|^ds", re.IGNORECASE), "DSBFE", "name:dsb_prefix"),
    (re.compile(r"^pm|femisc|ifmisc", re.IGNORECASE), "FE_MISC", "name:pm_misc"),
]

RTL_BLOCK_HINTS = [
    ("mcache", "MCACHE"),
    ("dcu", "DCU"),
    ("mli", "DCU"),
    ("snp", "DCU"),
    ("bpu", "BPU"),
    ("bac", "BAC"),
    ("dsb", "DSBFE"),
    ("fe_misc", "FE_MISC"),
]

NOISY_BLOCKS = {
    "FE_RTLSI_MON",
    "BPU_TLM",
    "FE_EVENTS_TLM",
    "IFU_ILG_BAC_TLM",
}

DFT_DEBUG_TOKENS = {
    "fscan",
    "jtag",
    "ijtag",
    "misr",
    "lbist",
    "mbist",
    "bist",
    "scan",
    "dfx",
    "tap",
    "tdr",
}


def is_noisy_block_name(block: str | None) -> bool:
    value = (block or "").strip().upper()
    if not value:
        return False
    if value in NOISY_BLOCKS:
        return True
    return value.endswith("_TLM") or value.endswith("_MON")


def infer_hard_partition_from_units(row: dict[str, str], module: str) -> tuple[str | None, str, float]:
    """Return deterministic block partition from unit connections when unambiguous."""
    direction = (row.get("port_direction") or "").strip().lower()
    preferred_field = "source_output_units" if direction == "input" else "connected_other_units"
    fallback_fields = ["connected_other_units", "source_output_units", "connected_tlm_units"]

    preferred_blocks: list[str] = []
    for unit in split_units(row.get(preferred_field, "")):
        block = normalize_block_name(unit_leaf(unit), module)
        if block and not is_noisy_block_name(block):
            preferred_blocks.append(block)

    if preferred_blocks:
        uniq = sorted(set(preferred_blocks))
        if len(uniq) == 1:
            return uniq[0], f"hard_partition:{preferred_field}:{uniq[0]}", 1.0

    all_blocks: list[str] = []
    for field in fallback_fields:
        for unit in split_units(row.get(field, "")):
            block = normalize_block_name(unit_leaf(unit), module)
            if block and not is_noisy_block_name(block):
                all_blocks.append(block)

    if not all_blocks:
        return None, "hard_partition:none", 0.0

    uniq_all = sorted(set(all_blocks))
    if len(uniq_all) == 1:
        return uniq_all[0], f"hard_partition:all_fields:{uniq_all[0]}", 0.98

    return None, "hard_partition:ambiguous", 0.0


def infer_dft_debug_override(port_name: str) -> tuple[str | None, str, float]:
    """Force DFT/debug block for well-known test/debug naming patterns."""
    name = (port_name or "").strip()
    if not name:
        return None, "dft_debug:none", 0.0
    tokens = set(tokenize_signal_name(name))
    if tokens & DFT_DEBUG_TOKENS:
        matched = sorted(tokens & DFT_DEBUG_TOKENS)[0]
        return "DFT_DEBUG", f"name_override:dft_debug:{matched}", 1.0
    return None, "dft_debug:none", 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--io-csv", required=True, help="Input row-wise IO table CSV (12_*_query_io_table.csv)")
    parser.add_argument("--module", required=True, help="Module name to generate spec for (for example: ifu)")
    parser.add_argument("--out-dir", required=True, help="Output directory for generated artifacts")
    parser.add_argument("--min-ml-seed-rows", type=int, default=25, help="Minimum seeded rows required to train ML")
    parser.add_argument(
        "--rtl-search-files",
        nargs="*",
        default=["target/fe/gen/ifu.v", "target/fe/gen/fe.v", "target/fe/gen/fe_te.v"],
        help="Optional RTL files used for drill-down block inference when units are missing",
    )
    return parser.parse_args()


def split_units(value: str) -> list[str]:
    raw = (value or "").strip()
    if not raw or raw == "NONE":
        return []
    return [u.strip() for u in raw.split(";") if u.strip() and u.strip() != "NONE"]


def tokenize_signal_name(name: str) -> list[str]:
    parts = []
    for chunk in TOKEN_SPLIT_RE.split(name or ""):
        if not chunk:
            continue
        parts.extend(CAMEL_RE.findall(chunk))
    return [p.lower() for p in parts if p]


def build_feature_tokens(row: dict[str, str]) -> list[str]:
    tokens: list[str] = []

    tokens.extend(f"sig:{t}" for t in tokenize_signal_name(row.get("port_name", "")))
    direction = (row.get("port_direction") or "").strip().lower()
    if direction:
        tokens.append(f"dir:{direction}")

    sv_type = (row.get("direction_sv_type") or "").strip().lower()
    if sv_type:
        tokens.append(f"type:{sv_type}")

    packed = (row.get("direction_packed_width") or "").strip().lower()
    if packed:
        tokens.append(f"packed:{packed}")

    for u in split_units(row.get("source_output_units", "")):
        if is_noisy_block_name(unit_leaf(u)):
            tokens.append(f"src_weak:{u.lower()}")
        else:
            tokens.append(f"src:{u.lower()}")
    for u in split_units(row.get("connected_other_units", "")):
        if is_noisy_block_name(unit_leaf(u)):
            tokens.append(f"peer_weak:{u.lower()}")
        else:
            tokens.append(f"peer:{u.lower()}")
    for u in split_units(row.get("connected_tlm_units", "")):
        tokens.append(f"tlm_weak:{u.lower()}")

    is_top = (row.get("is_connected_to_top") or "").strip().lower()
    if is_top:
        tokens.append(f"top:{is_top}")

    owner = (row.get("producer_cluster_owner") or "").strip().lower()
    if owner and owner != "none":
        tokens.append(f"owner:{owner}")

    origin = (row.get("producer_origin_hint") or "").strip().lower()
    if origin and origin != "none":
        tokens.append(f"origin:{origin}")

    return tokens


def seed_label_from_tokens(tokens: list[str]) -> tuple[str, float, str]:
    joined = " ".join(tokens)
    score_by_label: dict[str, int] = {label: 0 for label in RULE_LABELS}

    for label, keywords in KEYWORD_RULES.items():
        for kw in keywords:
            hits = joined.count(kw)
            score_by_label[label] += hits

    best_label = max(score_by_label, key=score_by_label.get)
    best_score = score_by_label[best_label]

    if best_score == 0:
        return "misc_unknown", 0.35, "no_keyword_match"

    # Mild confidence scaling based on matched keyword count.
    confidence = min(0.55 + 0.07 * best_score, 0.92)
    return best_label, confidence, f"keyword_score:{best_score}"


def choose_counterpart_units(row: dict[str, str]) -> list[str]:
    direction = (row.get("port_direction") or "").strip().lower()
    if direction == "input":
        units = split_units(row.get("source_output_units", ""))
        if units:
            filtered = [u for u in units if not is_noisy_block_name(unit_leaf(u))]
            return filtered or units
    elif direction == "output":
        units = split_units(row.get("connected_other_units", ""))
        if units:
            filtered = [u for u in units if not is_noisy_block_name(unit_leaf(u))]
            return filtered or units

    tlm = split_units(row.get("connected_tlm_units", ""))
    if tlm:
        return tlm

    return ["TOP_OR_UNRESOLVED"]


def split_chain_tokens(unit: str) -> list[str]:
    return [t.strip() for t in re.split(r"<-|->", unit) if t.strip()]


def unit_leaf(unit: str) -> str:
    tokens = split_chain_tokens(unit)
    return tokens[-1] if tokens else unit.strip()


def normalize_block_name(raw: str, module: str) -> str | None:
    value = (raw or "").strip().upper()
    if not value:
        return None
    if value == module.upper() or value == "TOP_OR_UNRESOLVED":
        return None
    aliases = {
        "MEC": "MCU",
    }
    return aliases.get(value, value)


def infer_block_from_units(row: dict[str, str], module: str) -> tuple[str | None, str]:
    direction = (row.get("port_direction") or "").strip().lower()
    ordered_fields = (
        ["source_output_units", "connected_other_units", "connected_tlm_units"]
        if direction == "input"
        else ["connected_other_units", "source_output_units", "connected_tlm_units"]
    )
    for field in ordered_fields:
        for unit in split_units(row.get(field, "")):
            leaf = unit_leaf(unit)
            block = normalize_block_name(leaf, module)
            if block and not is_noisy_block_name(block):
                return block, f"units:{field}:{unit}"
    return None, "units:none"


def infer_block_from_name(port_name: str) -> tuple[str | None, str]:
    for pattern, block, reason in BLOCK_NAME_RULES:
        if pattern.search(port_name or ""):
            return block, reason
    return None, "name:none"


def infer_block_from_rtl_context(
    port_name: str,
    rtl_search_files: list[Path],
    cache: dict[str, tuple[str | None, str]],
) -> tuple[str | None, str]:
    signal = (port_name or "").strip()
    if not signal:
        return None, "rtl:none"
    if signal in cache:
        return cache[signal]

    for path in rtl_search_files:
        if not path.exists():
            continue
        lines = path.read_text(errors="ignore").splitlines()
        for idx, line in enumerate(lines):
            if signal not in line:
                continue
            lo = max(0, idx - 2)
            hi = min(len(lines), idx + 3)
            window = "\n".join(lines[lo:hi]).lower()
            for keyword, block in RTL_BLOCK_HINTS:
                if keyword in window:
                    result = (block, f"rtl:{path.as_posix()}:{idx + 1}:{keyword}")
                    cache[signal] = result
                    return result

    cache[signal] = (None, "rtl:none")
    return cache[signal]


def infer_sub_interface(port_name: str, block: str) -> str:
    name = (port_name or "").lower()
    if block == "IFCRBRTSTS":
        return "control_register_interface"
    if block == "DCU":
        if name.startswith("mlisnp"):
            return "snoop_interface"
        if name.startswith("mli"):
            return "mlc_response_interface"
        if name.startswith("ifreq") or "req" in name:
            return "request_interface"
        return "dcu_control_interface"

    if block == "MCACHE":
        if name.startswith("ifreq") or "req" in name:
            return "request_interface"
        if "rsp" in name or "resp" in name or "cmpl" in name or "ack" in name or "nack" in name:
            return "response_interface"
        if "steer" in name or "sel" in name:
            return "mcache_control_interface"
        if "data" in name or "id" in name or "addr" in name or "tag" in name:
            return "data_interface"
        return "mcache_interface"

    if "req" in name or "request" in name or "cmd" in name:
        return "request_interface"
    if "rsp" in name or "resp" in name or "cmpl" in name or "ack" in name or "nack" in name:
        return "response_interface"
    if "data" in name or "id" in name or "addr" in name or "tag" in name:
        return "data_interface"
    if "stall" in name or "valid" in name or "ready" in name or "enable" in name or "credit" in name:
        return "control_interface"
    if "clk" in name or "reset" in name or "rst" in name or "pwr" in name:
        return "clock_reset_interface"
    if "tlm" in name or "debug" in name or "assert" in name or "event" in name:
        return "debug_monitor_interface"
    return "misc_interface"


def infer_block_and_subinterface(
    row: dict[str, str],
    module: str,
    rtl_search_files: list[Path],
    rtl_cache: dict[str, tuple[str | None, str]],
) -> tuple[str, str, str, float]:
    dft_block, dft_reason, dft_conf = infer_dft_debug_override(row.get("port_name", ""))
    if dft_block:
        return dft_block, "debug_monitor_interface", dft_reason, dft_conf

    hard_block, hard_reason, hard_conf = infer_hard_partition_from_units(row, module)
    if hard_block:
        sub = infer_sub_interface(row.get("port_name", ""), hard_block)
        return hard_block, sub, hard_reason, hard_conf

    name_block, name_reason = infer_block_from_name(row.get("port_name", ""))
    block, reason = infer_block_from_units(row, module)
    confidence = 0.95 if block else 0.0

    # Strong semantic names should override monitor/noise-derived unit assignments.
    if name_block and is_noisy_block_name(block):
        block = name_block
        reason = f"name_override:{name_reason}"
        confidence = 0.90

    if not block:
        block, reason = name_block, name_reason
        if block:
            confidence = 0.85

    if not block:
        block, reason = infer_block_from_rtl_context(row.get("port_name", ""), rtl_search_files, rtl_cache)
        if block:
            confidence = 0.78

    if not block:
        block = "TOP_OR_UNRESOLVED"
        reason = "fallback:unresolved"
        confidence = 0.50

    sub = infer_sub_interface(row.get("port_name", ""), block)
    return block, sub, reason, confidence


def run_optional_ml(rows: list[dict[str, str]], min_seed_rows: int) -> tuple[bool, list[tuple[str, float, str]]]:
    """Try sklearn classifier. Fallback to seed labels if unavailable/insufficient."""
    feature_texts = [" ".join(build_feature_tokens(r)) for r in rows]
    seed_labels = []
    seed_conf = []
    for row in rows:
        label, conf, _ = seed_label_from_tokens(build_feature_tokens(row))
        seed_labels.append(label)
        seed_conf.append(conf)

    # Keep only reasonably strong seeds for training.
    train_idx = [i for i, conf in enumerate(seed_conf) if conf >= 0.62 and seed_labels[i] != "misc_unknown"]
    classes = {seed_labels[i] for i in train_idx}
    if len(train_idx) < min_seed_rows or len(classes) < 2:
        fallback = [(seed_labels[i], seed_conf[i], "rule_fallback") for i in range(len(rows))]
        return False, fallback

    def run_internal_nb() -> tuple[bool, list[tuple[str, float, str]]]:
        # Multinomial Naive Bayes over whitespace-tokenized features.
        class_doc_count: Counter[str] = Counter()
        class_token_count: defaultdict[str, Counter[str]] = defaultdict(Counter)
        class_total_tokens: Counter[str] = Counter()
        vocab: set[str] = set()

        for i in train_idx:
            c = seed_labels[i]
            toks = feature_texts[i].split()
            class_doc_count[c] += 1
            class_token_count[c].update(toks)
            class_total_tokens[c] += len(toks)
            vocab.update(toks)

        total_docs = sum(class_doc_count.values())
        classes_sorted = sorted(class_doc_count)
        v = max(1, len(vocab))

        def predict(text: str) -> tuple[str, float]:
            toks = text.split()
            logps = {}
            for c in classes_sorted:
                prior = (class_doc_count[c] + 1.0) / (total_docs + len(classes_sorted))
                lp = math.log(prior)
                denom = class_total_tokens[c] + v
                token_counter = class_token_count[c]
                for t in toks:
                    lp += math.log((token_counter.get(t, 0) + 1.0) / denom)
                logps[c] = lp

            best_c = max(logps, key=logps.get)
            # Convert log-probs to normalized confidence via stable softmax.
            m = max(logps.values())
            exps = {c: math.exp(logps[c] - m) for c in logps}
            z = sum(exps.values()) or 1.0
            conf = exps[best_c] / z
            return best_c, conf

        preds = []
        for text in feature_texts:
            label, conf = predict(text)
            preds.append((label, conf, "ml_nb"))
        return True, preds

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
    except Exception:
        return run_internal_nb()

    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
    x_all = vectorizer.fit_transform(feature_texts)
    x_train = x_all[train_idx]
    y_train = [seed_labels[i] for i in train_idx]

    model = LogisticRegression(max_iter=1200)
    model.fit(x_train, y_train)

    probs = model.predict_proba(x_all)
    cls = model.classes_
    out: list[tuple[str, float, str]] = []
    for i in range(x_all.shape[0]):
        best_j = int(probs[i].argmax())
        label = str(cls[best_j])
        conf = float(probs[i][best_j])
        out.append((label, conf, "ml_logreg"))

    return True, out


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown_spec(path: Path, module: str, classified_rows: list[dict[str, str]], by_block_rows: list[dict[str, str]]) -> None:
    lines: list[str] = []
    lines.append(f"## Interface Spec: {module.upper()}")
    lines.append("")
    lines.append(f"Source table: row-wise IO table for `{module}`")
    lines.append("")

    lines.append("## Functional Interfaces By Block")
    lines.append("")

    by_block_label: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for row in classified_rows:
        blk = row.get("interface_block", "TOP_OR_UNRESOLVED")
        sub = row.get("functional_interface", "misc_interface")
        by_block_label[blk][sub].append(row)

    for block in sorted(by_block_label.keys()):
        lines.append(f"### Block: {block}")
        lines.append("")
        for sub in sorted(by_block_label[block].keys()):
            lines.append(f"#### {sub}")
            lines.append("")
            lines.append("| Signal | Direction | Type | Width | Source Units | Connected Units | Top Connected | Confidence | Assign Reason |")
            lines.append("|---|---|---|---|---|---|---|---|---|")
            for row in sorted(by_block_label[block][sub], key=lambda r: (r["port_name"], r["port_direction"])):
                lines.append(
                    "| {sig} | {dir} | {typ} | {wid} | {src} | {peer} | {top} | {conf:.3f} | {reason} |".format(
                        sig=row.get("port_name", ""),
                        dir=row.get("port_direction", ""),
                        typ=row.get("direction_sv_type", ""),
                        wid=row.get("direction_packed_width", ""),
                        src=row.get("source_output_units", ""),
                        peer=row.get("connected_other_units", ""),
                        top=row.get("is_connected_to_top", ""),
                        conf=float(row.get("interface_confidence", "0") or 0),
                        reason=row.get("block_assignment_reason", ""),
                    )
                )
            lines.append("")

    lines.append("## Per-Block Interface Summary")
    lines.append("")
    lines.append("| Block | Functional Interface | Direction | Signal Count |")
    lines.append("|---|---|---|---|")
    for row in by_block_rows:
        lines.append(
            f"| {row['block']} | {row['functional_interface']} | {row['direction']} | {row['signal_count']} |"
        )
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    args = parse_args()

    io_csv = Path(args.io_csv)
    out_dir = Path(args.out_dir)
    module = args.module.strip().lower()
    if not io_csv.exists():
        raise SystemExit(f"ERROR: Input not found: {io_csv}")

    with io_csv.open(newline="") as handle:
        reader = csv.DictReader(handle)
        all_rows = list(reader)

    rtl_search_files = [Path(p) for p in args.rtl_search_files]
    rtl_cache: dict[str, tuple[str | None, str]] = {}

    rows = [r for r in all_rows if (r.get("instance_module") or "").strip().lower() == module]
    if not rows:
        raise SystemExit(f"ERROR: No rows found for module={module} in {io_csv}")

    used_ml, predictions = run_optional_ml(rows, args.min_ml_seed_rows)

    features_rows: list[dict[str, str]] = []
    classified_rows: list[dict[str, str]] = []

    for idx, row in enumerate(rows):
        tokens = build_feature_tokens(row)
        seed_label, seed_conf, seed_reason = seed_label_from_tokens(tokens)
        pred_label, pred_conf, pred_source = predictions[idx]

        out_row = dict(row)
        out_row["functional_interface"] = pred_label
        out_row["interface_confidence"] = f"{pred_conf:.4f}"
        out_row["classification_source"] = pred_source
        out_row["seed_label"] = seed_label
        out_row["seed_confidence"] = f"{seed_conf:.4f}"
        out_row["seed_reason"] = seed_reason

        block, refined_sub, block_reason, block_conf = infer_block_and_subinterface(
            row,
            module,
            rtl_search_files,
            rtl_cache,
        )
        out_row["interface_block"] = block
        out_row["functional_interface_ml"] = pred_label
        out_row["functional_interface"] = refined_sub
        out_row["block_assignment_reason"] = block_reason
        out_row["interface_confidence"] = f"{block_conf:.4f}"
        out_row["classification_source"] = "block_refined"
        classified_rows.append(out_row)

        features_rows.append(
            {
                "row_index": str(idx),
                "module": module,
                "port_name": row.get("port_name", ""),
                "port_direction": row.get("port_direction", ""),
                "feature_tokens": " ".join(tokens),
                "seed_label": seed_label,
                "seed_confidence": f"{seed_conf:.4f}",
                "classification_label": pred_label,
                "classification_confidence": f"{pred_conf:.4f}",
                "classification_source": pred_source,
                "interface_block": block,
                "refined_sub_interface": refined_sub,
                "block_assignment_reason": block_reason,
            }
        )

    # Per-block summary table.
    by_block_counter: Counter[tuple[str, str, str]] = Counter()
    for row in classified_rows:
        iface = row["functional_interface"]
        direction = (row.get("port_direction") or "").strip().lower() or "unknown"
        block = (row.get("interface_block") or "TOP_OR_UNRESOLVED").strip()
        by_block_counter[(block, iface, direction)] += 1

    by_block_rows = [
        {
            "block": k[0],
            "functional_interface": k[1],
            "direction": k[2],
            "signal_count": str(v),
        }
        for k, v in sorted(by_block_counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    features_csv = out_dir / f"{module}_interface_features.csv"
    classified_csv = out_dir / f"{module}_interface_classified.csv"
    by_block_csv = out_dir / f"{module}_interface_by_block.csv"
    spec_md = out_dir / f"{module}_interface_spec.md"

    write_csv(
        features_csv,
        features_rows,
        [
            "row_index",
            "module",
            "port_name",
            "port_direction",
            "feature_tokens",
            "seed_label",
            "seed_confidence",
            "classification_label",
            "classification_confidence",
            "classification_source",
            "interface_block",
            "refined_sub_interface",
            "block_assignment_reason",
        ],
    )

    classified_fields = list(classified_rows[0].keys())
    write_csv(classified_csv, classified_rows, classified_fields)
    write_csv(by_block_csv, by_block_rows, ["block", "functional_interface", "direction", "signal_count"])
    write_markdown_spec(spec_md, module, classified_rows, by_block_rows)

    print(f"MODULE={module}")
    print(f"INPUT_ROWS={len(rows)}")
    print(f"ML_USED={'true' if used_ml else 'false'}")
    print(f"WROTE_FEATURES={features_csv}")
    print(f"WROTE_CLASSIFIED={classified_csv}")
    print(f"WROTE_BY_BLOCK={by_block_csv}")
    print(f"WROTE_SPEC={spec_md}")


if __name__ == "__main__":
    main()
