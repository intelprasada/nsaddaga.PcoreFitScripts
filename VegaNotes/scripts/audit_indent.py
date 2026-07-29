#!/usr/bin/env python3
"""Audit note .md files for the indent-corruption signature behind T-800631.

An AR/subtask that lost a level of indentation gets reparented by the
indent-driven parser, hiding it and its siblings from the card while the text
stays in the file. This flags the tell-tale shape: an `!AR` (or `!task`) line
that is *shallower* than the continuation/sibling content directly around it in
the same block — e.g. a 1-tab `!AR` sitting between 2-tab `#note`s and 2-tab
`!AR`s. Reports candidates for human review; it does not modify anything.

Usage: python audit_indent.py <notes_dir> [<notes_dir> ...]
"""
from __future__ import annotations

import sys
from pathlib import Path


def _indent_width(line: str) -> int:
    # Tabs count as 1 "level" each for this heuristic; spaces as-is. We only
    # compare relative depth, so a consistent metric is enough.
    n = 0
    for ch in line:
        if ch == "\t":
            n += 1
        elif ch == " ":
            n += 1
        else:
            break
    return n


def _is_decl(stripped: str) -> bool:
    s = stripped.lstrip("-*+ ").lstrip()
    return s.startswith("!AR") or s.startswith("!task")


def _is_ar(stripped: str) -> bool:
    return stripped.lstrip("-*+ ").lstrip().startswith("!AR")


def _is_note(stripped: str) -> bool:
    return stripped.lstrip("-*+ ").lstrip().lower().startswith("#note")


def audit_file(path: Path) -> list[tuple[int, str]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or not _is_ar(line.lstrip()):
            continue
        ind = _indent_width(line)
        # Precise flattening signature: an AR immediately followed (allowing
        # only its own #note continuations in between) by a *sibling* AR at a
        # STRICTLY DEEPER indent. Siblings share an indent, so a shallow AR
        # sitting just above deeper ARs was flattened out of their level
        # (exactly what happened to T-W7M4KZ). We deliberately do NOT flag on a
        # deeper *preceding* #note — that note belongs to the previous sibling,
        # not to this AR (a common false positive).
        next_ar_deeper = False
        for k in range(i + 1, len(lines)):
            if not lines[k].strip():
                continue
            w = _indent_width(lines[k])
            if _is_note(lines[k]) and w > ind:
                continue  # a #note continuation, keep scanning
            if _is_ar(lines[k]) and w > ind:
                next_ar_deeper = True
            break  # first non-note, non-deeper-note line decides
        if next_ar_deeper:
            hits.append((i + 1, line))
    return hits


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv[1:]] or [Path(".")]
    total = 0
    for root in roots:
        for md in sorted(root.rglob("*.md")):
            if "/.trash/" in f"/{md}/":
                continue
            hits = audit_file(md)
            if hits:
                print(f"\n{md}")
                for ln, text in hits:
                    print(f"  L{ln}: {text[:90]}")
                    total += len(hits)
    print(f"\n=== {total} candidate line(s) flagged for review ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
