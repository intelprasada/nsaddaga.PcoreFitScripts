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
_BANG_KEYWORD_RE = re.compile(r"^(?P<lead>\s*(?:[-*+]\s+|\d+[.)]\s+)?!(?:task|AR)\b)", re.IGNORECASE)
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

    Note: ``_rollup_to_parents`` intentionally keeps a parent alive when it is
    marked ``#status done`` but still has non-done AR children — the open ARs
    act as a reminder. Those tasks are stripped once ALL their AR children are
    done (either inline or via continuation ``#status done``).
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
        # Insert `#id <ID>` immediately after the !task/!AR keyword so it is
        # the first visible attribute, co-located with the declaration.
        # Input:  "  !task Buy milk #priority p1\n"
        # Output: "  !task #id T-XXXX Buy milk #priority p1\n"
        m = _BANG_KEYWORD_RE.match(raw)
        if m:
            after_kw = raw[m.end():]          # " Buy milk #priority p1\n"
            # Always insert a space after the keyword, then "#id <ID> ".
            # after_kw starts with a space or content; strip its leading space
            # to avoid double-space.
            after_kw_stripped = after_kw.lstrip(" ")
            space_after = " " if after_kw_stripped else ""
            lines[i] = f"{m.group()} #id {new_id}{space_after}{after_kw_stripped}"
        else:
            # Fallback (shouldn't happen): append at end.
            body = raw
            nl = ""
            while body.endswith("\n") or body.endswith("\r"):
                nl = body[-1] + nl
                body = body[:-1]
            sep2 = "" if not body or body.endswith(" ") else " "
            lines[i] = f"{body}{sep2}#id {new_id}{nl}"
    return "".join(lines), added


def _extract_task_id(line: str) -> Optional[str]:
    m = _ID_TOKEN_RE.search(line)
    return m.group(1) if m else None


def rewrite_tasks_as_refs(md: str) -> str:
    """Convert every surviving ``!task``/``!AR`` declaration line (that has an
    ``#id`` token) into a reference row, preserving any sibling attrs.

    - ``!task`` lines  → ``- #task <ID> <title> [attrs]``
    - ``!AR`` lines    → ``- #AR <ID> <title> [attrs]``

    Lines without an ``#id`` token are left as-is (the caller is expected
    to run :func:`inject_missing_ids` first).
    """
    lines = md.splitlines(keepends=True)
    out: list[str] = []
    for raw in lines:
        # Identify the line as a !task/!AR declaration first.
        m_kw = _BANG_KEYWORD_RE.match(raw)
        if not m_kw:
            out.append(raw)
            continue
        tid = _extract_task_id(raw)
        if tid is None:
            out.append(raw)
            continue

        # Preserve trailing newline.
        body = raw
        nl = ""
        while body.endswith("\n") or body.endswith("\r"):
            nl = body[-1] + nl
            body = body[:-1]

        # Determine bang keyword from the match (group captures up to !TASK/!AR).
        kw_str = m_kw.group()  # e.g. "  - !task" or "!AR"
        bang = "AR" if kw_str.upper().endswith("!AR") else "TASK"
        ref_keyword = "#AR" if bang == "AR" else "#task"

        # Reconstruct lead from _TASK_LINE_FULL if available, else derive.
        m_full = _TASK_LINE_FULL.match(raw)
        if m_full:
            lead = m_full.group("lead")
            title = m_full.group("title").strip()
            rest = (m_full.group("rest") or "").strip()
        else:
            # With new placement (#id right after !task), _TASK_LINE_FULL fails
            # because title=[^#]* can't match past the #id token.
            # Derive lead as everything up to and including the !keyword,
            # then treat everything after the keyword as raw_rest.
            lead_m = re.match(r"^(\s*(?:[-*+]\s+|\d+[.)]\s+)?)", raw)
            lead = lead_m.group(1) if lead_m else ""
            # raw_rest is everything after the !task/!AR keyword.
            raw_rest = raw[m_kw.end():]  # may start with space + #id ...
            title = ""
            rest = raw_rest.strip()

        # Strip the `#id <ID>` token from rest/title (it goes into ref_keyword).
        rest_clean = _ID_TOKEN_RE.sub("", rest).strip()
        # Also clean from title in case it got there.
        title_clean = _ID_TOKEN_RE.sub("", title).strip()
        rest_clean = re.sub(r"\s{2,}", " ", rest_clean).strip()

        # Build: "LEAD #task|#AR TID TITLE ATTRS"
        pieces = [f"{lead}{ref_keyword} {tid}"]
        if title_clean:
            pieces.append(title_clean)
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


def remove_attr(md: str, line_no: int, key: str) -> str:
    """Strip every ``#key <value>`` token (or ``@value`` for owner) from line."""
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
    body = re.sub(rf"\s*#{re.escape(key)}\s+\S+", "", body, flags=re.IGNORECASE)
    if key == "owner":
        body = re.sub(r"\s*@[a-zA-Z][\w.-]*", "", body)
    body = re.sub(r"\s{2,}", " ", body).rstrip()
    lines[line_no] = body + nl
    return "".join(lines)


def _continuation_pad(lines: list[str], task_line_no: int) -> str:
    """Indent string for a continuation line written under the task at
    ``lines[task_line_no]``.

    Strategy (in order):

    1. **Mirror an existing sibling continuation** of this same task — any
       line below the task whose indent is strictly greater than the task's
       and whose first non-whitespace char is ``#`` or ``@``. Copy its
       leading whitespace verbatim.
    2. **Mirror any other task's continuation** in the same file — compute
       the *delta* (extra leading whitespace beyond the task's own) and
       apply that delta to the target task. Falls back to step 3 if none
       found.
    3. **Default**: tab-style (``\\t`` in the task's whitespace) → add one
       more ``\\t``. Space-style → add ``  `` (two spaces). Root-level
       task → ``  ``.

    This avoids the previous bug where deeply space-indented tasks got an
    even deeper run of spaces (+8 on an 8-space task), placing notes two
    tab-stops below the task instead of one indent step deeper.
    """
    task_line = lines[task_line_no]
    task_ws_len = len(task_line) - len(task_line.lstrip(" \t"))
    task_ws = task_line[:task_ws_len]

    # 1. Existing NON-note continuation under THIS task wins. We deliberately
    #    skip sibling `#note` lines because we're about to rewrite them — and
    #    if a previous buggy save left them at the wrong indent we'd happily
    #    perpetuate it.
    for j in range(task_line_no + 1, len(lines)):
        raw = lines[j]
        stripped = raw.lstrip(" \t")
        ws_len = len(raw) - len(stripped)
        if ws_len <= task_ws_len:
            break
        if stripped[:1] in ("#", "@") and not re.match(r"#note(\s|$)", stripped, re.IGNORECASE):
            return raw[:ws_len]

    # 2. Learn the convention from any other task's continuation in the file.
    last_task_ws: str | None = None
    for raw in lines:
        stripped = raw.lstrip(" \t")
        ws_len = len(raw) - len(stripped)
        ws = raw[:ws_len]
        if stripped.startswith(("- !task", "* !task", "- !AR", "* !AR",
                                 "!task", "!AR")):
            last_task_ws = ws
            continue
        if last_task_ws is None:
            continue
        if ws_len > len(last_task_ws) and stripped[:1] in ("#", "@") \
                and not re.match(r"#note(\s|$)", stripped, re.IGNORECASE):
            delta = ws[len(last_task_ws):]
            if delta:
                return task_ws + delta

    # 3. Defaults.
    if not task_ws:
        return "  "
    if "\t" in task_ws:
        return task_ws + "\t"
    return task_ws + "  "


def replace_notes(md: str, task_line_no: int, task_indent: int, new_notes: str) -> str:
    """Rewrite the multi-line ``#note`` continuation block under a task.

    The notes block is the contiguous run of lines immediately after
    ``task_line_no`` whose stripped text begins with ``#note `` *and* whose
    indent is strictly greater than ``task_indent``. (Other indented
    continuation lines such as ``#status``/``@user`` are left untouched.)

    ``new_notes`` is split on newlines; each non-empty line is emitted as
    ``<task_indent+2 spaces>#note <text>`` immediately after the task line.
    Passing an empty string deletes the block.
    """
    lines = md.splitlines(keepends=True)
    if task_line_no < 0 or task_line_no >= len(lines):
        raise ValueError(f"line {task_line_no} out of range")

    # Find the existing notes block right after the task line.
    block_start = task_line_no + 1
    block_end = block_start
    while block_end < len(lines):
        raw = lines[block_end]
        stripped_left = raw.lstrip(" \t")
        ind = len(raw) - len(stripped_left)
        if ind <= task_indent:
            break
        if not re.match(r"#note(\s|$)", stripped_left, re.IGNORECASE):
            break
        block_end += 1

    new_block: list[str] = []
    # Mirror the task line's whitespace style so generated #note lines align
    # with hand-authored continuation lines (e.g. tab-indented #status). The
    # legacy `task_indent` integer is intentionally ignored here because it
    # collapses tabs and spaces into a single character count, which produced
    # visibly misaligned output (see issue #54).
    pad = _continuation_pad(lines, task_line_no)
    for raw_line in (new_notes or "").split("\n"):
        text = raw_line.strip()
        if not text:
            continue
        new_block.append(f"{pad}#note {text}\n")

    # If the file didn't end with a newline at task_line_no, ensure we don't
    # break joining; splitlines(keepends=True) already preserved that.
    return "".join(lines[:block_start] + new_block + lines[block_end:])


def append_note(md: str, task_line_no: int, text: str) -> str:
    """Append one or more new ``#note`` continuation lines under a task.

    Unlike :func:`replace_notes` (which rewrites the whole block), this is the
    journaling primitive: it leaves every existing ``#note`` line in place and
    inserts the new entries **after** the last existing one, so each save adds
    a row instead of overwriting prior history.

    ``text`` may contain newlines — each non-empty line becomes one entry.
    The emitted line shape is the bare ``<pad>#note <text>`` form; no
    timestamp or author is auto-prefixed (users keep their notes clean).
    """
    if not text or not text.strip():
        return md
    lines = md.splitlines(keepends=True)
    if task_line_no < 0 or task_line_no >= len(lines):
        raise ValueError(f"line {task_line_no} out of range")

    # Locate the end of any existing #note continuation block under this task.
    task_raw = lines[task_line_no]
    task_ws_len = len(task_raw) - len(task_raw.lstrip(" \t"))
    insert_at = task_line_no + 1
    while insert_at < len(lines):
        raw = lines[insert_at]
        stripped = raw.lstrip(" \t")
        ind = len(raw) - len(stripped)
        if ind <= task_ws_len:
            break
        if not re.match(r"#note(\s|$)", stripped, re.IGNORECASE):
            break
        insert_at += 1

    pad = _continuation_pad(lines, task_line_no)

    new_block: list[str] = []
    for raw_line in text.split("\n"):
        body = raw_line.strip()
        if not body:
            continue
        new_block.append(f"{pad}#note {body}\n")

    # Edge case: the task line might not end with a newline (last line of file
    # with no trailing newline). Make sure we don't merge the task line and the
    # first appended note.
    if insert_at == task_line_no + 1 and not lines[task_line_no].endswith(("\n", "\r")):
        lines[task_line_no] = lines[task_line_no] + "\n"

    return "".join(lines[:insert_at] + new_block + lines[insert_at:])


def replace_multi_attr(md: str, line_no: int, key: str, values: list[str]) -> str:
    """Replace every occurrence of a multi-valued attr on ``line_no`` with ``values``.

    For ``owner``, the ``@user`` sugar is used (and any existing ``#owner``
    tokens are stripped); for other multi-valued keys, ``#key value`` tokens
    are appended.
    """
    if not is_known(key):
        raise ValueError(f"unknown token '{key}'")
    md = remove_attr(md, line_no, key)
    if not values:
        return md
    lines = md.splitlines(keepends=True)
    raw = lines[line_no]
    nl = ""
    body = raw
    while body.endswith("\n") or body.endswith("\r"):
        nl = body[-1] + nl
        body = body[:-1]
    sep = "" if not body or body.endswith(" ") else " "
    if key == "owner":
        suffix = " ".join(f"@{v.lstrip('@')}" for v in values)
    else:
        suffix = " ".join(f"#{key} {v}" for v in values)
    body = f"{body}{sep}{suffix}"
    lines[line_no] = body + nl
    return "".join(lines)
