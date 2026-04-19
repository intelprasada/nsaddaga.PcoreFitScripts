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


@dataclass(frozen=True)
class TokenSpec:
    name: str
    multi: bool = False
    # value -> normalized representation used for indexing/sorting
    normalize: Callable[[str], object] | None = None


REGISTRY: Dict[str, TokenSpec] = {
    "task":     TokenSpec("task",     multi=True),
    "eta":      TokenSpec("eta",      normalize=parse_eta),
    "priority": TokenSpec("priority", normalize=parse_priority_rank),
    "project":  TokenSpec("project",  multi=True),
    "owner":    TokenSpec("owner",    multi=True),
    "status":   TokenSpec("status"),
    "estimate": TokenSpec("estimate", normalize=parse_duration),
    "feature":  TokenSpec("feature",  multi=True),
    "link":     TokenSpec("link",     multi=True),
}


def is_known(name: str) -> bool:
    return name in REGISTRY
