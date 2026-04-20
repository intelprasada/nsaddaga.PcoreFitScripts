"""Pure helpers that mutate the markdown source for round-trip features.

These are line-oriented edits — keep dependencies on the parser minimal so
callers can use them without importing SQLModel.
"""
from __future__ import annotations

import re
import secrets
from typing import Optional

from .parser.tokens import is_known


_STATUS_RE = re.compile(r"#status\s+(.+?)(?=\s+#|\s*$)", re.IGNORECASE)
_WW_NUM_RE = re.compile(r"(?i)ww(\d+)(\.\d+)?")
_TASK_LINE_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?!(task|ar)\b", re.IGNORECASE)
# Match `!task <title>` capturing the bang-token, the title, and any trailing
# attrs / comments on the line. Title runs up to the first `#` token, the
# end of line, or two-or-more spaces (the latter is unusual).
_TASK_LINE_FULL = re.compile(
    r"^(?P<lead>\s*(?:[-*+]\s+|\d+[.)]\s+)?)"     # bullet
    r"!(?P<bang>task|AR)\s+"                        # !task or !AR
    r"(?P<title>[^#\n]*?)"                          # title (non-greedy, no #)
    r"(?P<rest>(?:\s+#[^\n]*)?)\s*$",                # rest (attrs)
    re.IGNORECASE,
)
_ID_TOKEN_RE = re.compile(r"#id\s+(\S+)", re.IGNORECASE)
# Crockford-style base32 alphabet (no I, L, O, U for unambiguity).
_ID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


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


def roll_to_next_week(md: str, basename: str) -> tuple[str, str, int, int, str]:
    """Roll an Intel-WW-tagged note to the next week.

    - Strips done tasks/ARs (with their nested items / continuation lines).
    - Ensures every surviving `!task`/`!AR` line in the SOURCE has a stable
      `#id <ID>` token (mutating ``md`` if needed); returns the patched
      source as the 5th element so the caller can write it back.
    - Bumps every occurrence of the current WW number (matching the filename's
      WW) by +1 in the basename and in any plain prose `wwN[.x]` references in
      the body — but **never** in `#eta` values.
    - Rewrites the surviving `!task`/`!AR` lines into agenda **reference rows**
      `- #task <ID> <title> ...` so the next-week note does not redeclare
      tasks (avoiding duplicate Task rows in the index).

    Returns ``(new_md, new_basename, current_ww, next_ww, patched_source_md)``.
    Raises ``ValueError`` if the basename does not contain a `wwN` token.
    """
    cur = detect_ww(basename)
    if cur is None:
        raise ValueError("filename must contain a 'wwN' token (e.g. 'ww16')")
    nxt = cur + 1
    # 1. Inject missing IDs into the source first so both source and rolled
    #    file share the same canonical IDs.
    patched_source, _ = inject_missing_ids(md)
    # 2. Strip done tasks (using the patched source so IDs survive too).
    pruned = strip_done_tasks(patched_source)
    # 3. Bump prose ww references (but not #eta values).
    bumped = _bump_ww_outside_eta(pruned, cur, nxt)
    # 4. Rewrite remaining !task/!AR declarations as #task <ID> reference rows.
    new_body = rewrite_tasks_as_refs(bumped)
    new_base = bump_ww(basename, cur, nxt)
    return new_body, new_base, cur, nxt, patched_source


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


def generate_task_id(existing: set[str] | None = None) -> str:
    """Return a fresh, short, URL-safe task ID like ``T-A3F9K2``.

    6 Crockford-base32 chars → ~32-bit space (~1 in 1e9 collision per pair).
    If ``existing`` is provided, the result is guaranteed not to collide.
    """
    existing = existing or set()
    for _ in range(64):
        body = "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
        cand = f"T-{body}"
        if cand not in existing:
            return cand
    # Fall through with extra length on extreme collision (effectively never).
    body = "".join(secrets.choice(_ID_ALPHABET) for _ in range(10))
    return f"T-{body}"


def existing_ids(md: str) -> set[str]:
    """Return the set of `#id <ID>` values present anywhere in ``md``."""
    return {m.group(1) for m in _ID_TOKEN_RE.finditer(md)}


def inject_missing_ids(md: str) -> tuple[str, dict[int, str]]:
    """Walk ``md`` and append `#id <ID>` to every `!task`/`!AR` line that
    doesn't already have an ``#id`` token.

    Returns ``(new_md, ids_added)`` where ``ids_added`` maps the 0-based
    line number to the freshly minted ID. Lines already carrying an ID are
    untouched.
    """
    ids = existing_ids(md)
    lines = md.splitlines(keepends=True)
    added: dict[int, str] = {}
    for i, raw in enumerate(lines):
        if not _TASK_LINE_RE.match(raw):
            continue
        if _ID_TOKEN_RE.search(raw):
            continue
        new_id = generate_task_id(ids)
        ids.add(new_id)
        added[i] = new_id
        # Append `#id <ID>` before the trailing newline, with a space sep.
        body = raw
        nl = ""
        while body.endswith("\n") or body.endswith("\r"):
            nl = body[-1] + nl
            body = body[:-1]
        sep = "" if not body or body.endswith(" ") else " "
        lines[i] = f"{body}{sep}#id {new_id}{nl}"
    return "".join(lines), added


def _extract_task_id(line: str) -> Optional[str]:
    m = _ID_TOKEN_RE.search(line)
    return m.group(1) if m else None


def rewrite_tasks_as_refs(md: str) -> str:
    """Convert every surviving `!task <title> ... #id <ID>` line into a
    reference row ``- #task <ID> <title>`` preserving any sibling attrs
    (status/eta/etc) appended after the title.

    Lines without an ``#id`` token are left as-is (the caller is expected
    to run :func:`inject_missing_ids` first).
    """
    lines = md.splitlines(keepends=True)
    out: list[str] = []
    for raw in lines:
        m = _TASK_LINE_FULL.match(raw)
        if not m:
            out.append(raw)
            continue
        tid = _extract_task_id(raw)
        if tid is None:
            out.append(raw)
            continue
        body = raw
        nl = ""
        while body.endswith("\n") or body.endswith("\r"):
            nl = body[-1] + nl
            body = body[:-1]
        lead = m.group("lead")
        title = m.group("title").strip()
        rest = m.group("rest") or ""
        # Strip the `#id <ID>` token from rest (it lives only on the source
        # `!task` line — refs reference the ID directly).
        rest_clean = _ID_TOKEN_RE.sub("", rest).strip()
        rest_clean = re.sub(r"\s{2,}", " ", rest_clean).strip()
        pieces = [f"{lead}#task {tid}", title]
        if rest_clean:
            pieces.append(rest_clean)
        out.append(" ".join(p for p in pieces if p) + nl)
    return "".join(out)



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
