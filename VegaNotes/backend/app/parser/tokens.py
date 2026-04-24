"""Token registry for VegaNotes inline markdown tokens.

Each token kind is registered with a short name (e.g. ``eta``), a value
parser, and ``multi`` flag indicating whether multiple occurrences of the
token on the same task should accumulate into a list.

The two parsers (Python here, TypeScript in ``packages/parser-ts``) are kept
in lock-step using the shared golden fixtures under ``tests/fixtures``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

from .time_parse import parse_eta, parse_duration, parse_priority_rank


# Canonical task statuses. ``normalize_status`` first tries an exact alias
# match (case-insensitive); if that fails, it scans the raw value for any
# trigger word from each canonical status's vocabulary, in priority order.
# This means free-form values like "blocked by hsd approval" or "done but
# waiting on QA" still bucket into the right kanban column while keeping
# their original wording on disk.
#
# Order matters for ambiguous strings — earlier canonicals win:
#   blocked > done > in-progress > todo
# Rationale: "blocked" is the most actionable signal (call attention to
# it); a "done" qualifier overrides "in progress"; "todo" is the default
# fallback so it goes last.
_STATUS_ALIASES: Dict[str, str] = {
    "in progress": "in-progress",
    "in_progress": "in-progress",
    "inprogress": "in-progress",
    "wip": "in-progress",
    "doing": "in-progress",
    "working": "in-progress",
    "complete": "done",
    "completed": "done",
    "finished": "done",
    "closed": "done",
    "open": "todo",
    "pending": "todo",
    "to-do": "todo",
    "todo": "todo",
    "block": "blocked",
    "blocked": "blocked",
    "stuck": "blocked",
}

# Trigger words scanned with word boundaries inside free-form values.
# Listed canonical-by-canonical in priority order.
_STATUS_TRIGGERS: list[tuple[str, list[str]]] = [
    ("blocked",     ["blocked", "block", "stuck", "waiting", "hold", "on-hold", "on_hold"]),
    ("done",        ["done", "complete", "completed", "finished", "closed", "shipped", "merged"]),
    ("in-progress", ["wip", "doing", "working", "inprogress", "in-progress", "in_progress", "started", "ongoing"]),
    ("todo",        ["todo", "to-do", "pending", "open", "new", "queued", "backlog"]),
]
# Pre-compile a single regex per canonical for fast scanning.
import re as _re
_STATUS_TRIGGER_RES: list[tuple[str, "_re.Pattern[str]"]] = [
    (canon, _re.compile(r"\b(?:" + "|".join(_re.escape(w) for w in words) + r")\b", _re.IGNORECASE))
    for canon, words in _STATUS_TRIGGERS
]


def normalize_status(value: str) -> str:
    if not value:
        return "todo"
    v = value.strip().lower()
    # 1. Exact alias hit (covers single-word inputs like "wip", "complete").
    if v in _STATUS_ALIASES:
        return _STATUS_ALIASES[v]
    # 2. If the value is exactly one of the canonical buckets, keep it.
    if v in {"todo", "in-progress", "blocked", "done"}:
        return v
    # 3. Free-form value — scan for trigger words, in canonical priority
    #    order (blocked > done > in-progress > todo).
    for canon, pat in _STATUS_TRIGGER_RES:
        if pat.search(v):
            return canon
    # 4. Nothing matched — preserve the original (lower-cased) so we never
    #    silently lose information; the kanban will still bucket it under
    #    "todo" (fallback) but the literal value remains in the .md file.
    return v


@dataclass(frozen=True)
class TokenSpec:
    name: str
    multi: bool = False
    # value -> normalized representation used for indexing/sorting
    normalize: Callable[[str], object] | None = None


REGISTRY: Dict[str, TokenSpec] = {
    "task":     TokenSpec("task",     multi=True),
    "ar":       TokenSpec("ar",       multi=True),   # #AR <ID> reference rows
    "id":       TokenSpec("id",       multi=False),
    "eta":      TokenSpec("eta",      normalize=parse_eta),
    "priority": TokenSpec("priority", normalize=parse_priority_rank),
    "project":  TokenSpec("project",  multi=True),
    "owner":    TokenSpec("owner",    multi=True),
    "status":   TokenSpec("status",   normalize=normalize_status),
    "estimate": TokenSpec("estimate", normalize=parse_duration),
    "feature":  TokenSpec("feature",  multi=True),
    "link":     TokenSpec("link",     multi=True),
    "note":     TokenSpec("note",     multi=True),
}


def is_known(name: str) -> bool:
    return name in REGISTRY
