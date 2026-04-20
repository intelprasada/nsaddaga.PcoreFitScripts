"""Pure helpers that mutate the markdown source for round-trip features.

These are line-oriented edits — keep dependencies on the parser minimal so
callers can use them without importing SQLModel.
"""
from __future__ import annotations

import re
from typing import Optional

from .parser.tokens import is_known


_STATUS_RE = re.compile(r"#status\s+(.+?)(?=\s+#|\s*$)", re.IGNORECASE)
_WW_NUM_RE = re.compile(r"(?i)ww(\d+)(\.\d+)?")
_TASK_LINE_RE = re.compile(r"^\s*!(task|ar)\b", re.IGNORECASE)


def _line_indent(line: str) -> int:
    n = 0
    for ch in line:
        if ch == "\t" or ch == " ":
            n += 1
        else:
            break
    return n


def _is_done_task_line(line: str) -> bool:
    if not _TASK_LINE_RE.match(line):
        return False
    m = _STATUS_RE.search(line)
    if not m:
        return False
    from .parser.tokens import normalize_status
    return normalize_status(m.group(1).strip()) == "done"


def strip_done_tasks(md: str) -> str:
    """Remove `!task`/`!ar` items whose normalized status is `done`, including
    every continuation/sub-line indented strictly deeper than them.

    Uses the structural parser to identify done tasks (their `#status done`
    may be on a continuation line, not the `!task` line itself), then drops
    that line and everything beneath it that is more deeply indented.
    """
    from .parser import parse
    parsed = parse(md)
    done_lines: set[int] = set()
    for t in parsed.get("tasks", []):
        if t.get("status") == "done" and isinstance(t.get("line"), int):
            done_lines.add(t["line"])
    lines = md.splitlines(keepends=True)
    out: list[str] = []
    skip_indent: int | None = None
    for i, line in enumerate(lines):
        if skip_indent is not None:
            if not line.strip():
                # blank lines belong to whichever block they sit between; drop
                # them while still inside a stripped subtree.
                continue
            if _line_indent(line) > skip_indent:
                continue
            skip_indent = None
        if i in done_lines:
            skip_indent = _line_indent(line)
            continue
        out.append(line)
    return "".join(out)


def bump_ww(text: str, current_ww: int, next_ww: int) -> str:
    """Replace every `wwN[.x]` token (case-insensitive) where N == ``current_ww``
    with `ww<next_ww>[.x]`, preserving the fractional part. Other ww references
    are left untouched."""
    def _sub(m: re.Match) -> str:
        if int(m.group(1)) != current_ww:
            return m.group(0)
        frac = m.group(2) or ""
        return f"ww{next_ww}{frac}"
    return _WW_NUM_RE.sub(_sub, text)


def detect_ww(name: str) -> int | None:
    m = _WW_NUM_RE.search(name)
    return int(m.group(1)) if m else None


def roll_to_next_week(md: str, basename: str) -> tuple[str, str, int, int]:
    """Roll an Intel-WW-tagged note to the next week.

    - Strips done tasks/ARs (with their nested items / continuation lines).
    - Bumps every occurrence of the current WW number (matching the filename's
      WW) by +1 in the basename and in any plain prose `wwN[.x]` references in
      the body — but **never** in `#eta` values. ETAs are intentionally left
      untouched so the user reviews each remaining task and either marks it
      done or sets a new future ETA themselves.

    Returns ``(new_md, new_basename, current_ww, next_ww)``. Raises
    ``ValueError`` if the basename does not contain a `wwN` token.
    """
    cur = detect_ww(basename)
    if cur is None:
        raise ValueError("filename must contain a 'wwN' token (e.g. 'ww16')")
    nxt = cur + 1
    pruned = strip_done_tasks(md)
    new_body = _bump_ww_outside_eta(pruned, cur, nxt)
    new_base = bump_ww(basename, cur, nxt)
    return new_body, new_base, cur, nxt


_ETA_TOKEN_RE = re.compile(r"#eta\s+\S+", re.IGNORECASE)


def _bump_ww_outside_eta(text: str, cur: int, nxt: int) -> str:
    """Bump `wwN` → `wwN+1` everywhere except inside `#eta <value>` tokens."""
    eta_spans: list[tuple[int, int]] = [m.span() for m in _ETA_TOKEN_RE.finditer(text)]

    def in_eta(pos: int) -> bool:
        for a, b in eta_spans:
            if a <= pos < b:
                return True
        return False

    def _sub(m: re.Match) -> str:
        if in_eta(m.start()):
            return m.group(0)
        if int(m.group(1)) != cur:
            return m.group(0)
        frac = m.group(2) or ""
        return f"ww{nxt}{frac}"

    return _WW_NUM_RE.sub(_sub, text)


def update_task_status(md: str, line_no: int, new_status: str) -> str:
    """Return ``md`` with the `#status` token on ``line_no`` set to ``new_status``.

    If the line has no `#status` token, append `#status <new_status>` at the
    end (preserving any trailing newline). Raises ``ValueError`` if the line
    is out of range.
    """
    from .parser.tokens import normalize_status
    new_status = normalize_status(new_status)
    lines = md.splitlines(keepends=True)
    if line_no < 0 or line_no >= len(lines):
        raise ValueError(f"line {line_no} out of range")
    raw = lines[line_no]
    nl = ""
    body = raw
    while body.endswith("\n") or body.endswith("\r"):
        nl = body[-1] + nl
        body = body[:-1]
    if _STATUS_RE.search(body):
        body = _STATUS_RE.sub(f"#status {new_status}", body, count=1)
    else:
        sep = "" if not body or body.endswith(" ") else " "
        body = f"{body}{sep}#status {new_status}"
    lines[line_no] = body + nl
    return "".join(lines)


def replace_attr(md: str, line_no: int, key: str, new_value: str) -> str:
    """Generic single-valued-attr replacement on a line. Same shape as
    :func:`update_task_status` but for any registered attr key."""
    if not is_known(key):
        raise ValueError(f"unknown token '{key}'")
    lines = md.splitlines(keepends=True)
    if line_no < 0 or line_no >= len(lines):
        raise ValueError(f"line {line_no} out of range")
    raw = lines[line_no]
    nl = ""
    body = raw
    while body.endswith("\n") or body.endswith("\r"):
        nl = body[-1] + nl
        body = body[:-1]
    pat = re.compile(rf"#{re.escape(key)}\s+(\S+)", re.IGNORECASE)
    if pat.search(body):
        body = pat.sub(f"#{key} {new_value}", body, count=1)
    else:
        sep = "" if not body or body.endswith(" ") else " "
        body = f"{body}{sep}#{key} {new_value}"
    lines[line_no] = body + nl
    return "".join(lines)
