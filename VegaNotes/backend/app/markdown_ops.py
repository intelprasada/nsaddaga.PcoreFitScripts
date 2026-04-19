"""Pure helpers that mutate the markdown source for round-trip features.

These are line-oriented edits — keep dependencies on the parser minimal so
callers can use them without importing SQLModel.
"""
from __future__ import annotations

import re
from typing import Optional

from .parser.tokens import is_known


_STATUS_RE = re.compile(r"#status\s+(\S+)", re.IGNORECASE)


def update_task_status(md: str, line_no: int, new_status: str) -> str:
    """Return ``md`` with the `#status` token on ``line_no`` set to ``new_status``.

    If the line has no `#status` token, append `#status <new_status>` at the
    end (preserving any trailing newline). Raises ``ValueError`` if the line
    is out of range.
    """
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
