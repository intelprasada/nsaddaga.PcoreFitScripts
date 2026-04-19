"""Time / duration / priority normalization helpers.

Kept dependency-light: a small hand-rolled parser for the common cases
(ISO date, ``+Nd``/``+Nh``/``+Nw``, ``today``, ``tomorrow``, ``next <dow>``).
A production deployment may swap in :mod:`dateparser` for natural-language
support; the indexer treats the return value as opaque text + an optional
ISO normalization.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:?\d{2})?$")
REL = re.compile(r"^([+-])(\d+(?:\.\d+)?)([hdwm])$")
DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _today() -> date:
    return datetime.now(timezone.utc).date()


def parse_eta(value: str, *, today: Optional[date] = None) -> Optional[str]:
    """Return an ISO-8601 date string for the given ETA expression, or None.

    Accepts: ISO date/datetime, ``today``, ``tomorrow``, ``+Nd``/``+Nh``/``+Nw``,
    ``next <dow>``.
    """
    if not value:
        return None
    v = value.strip().lower()
    today = today or _today()

    if ISO_DATE.match(v):
        return v
    if ISO_DATETIME.match(value.strip()):
        return value.strip()
    if v == "today":
        return today.isoformat()
    if v == "tomorrow":
        return (today + timedelta(days=1)).isoformat()

    m = REL.match(v)
    if m:
        sign, num, unit = m.group(1), float(m.group(2)), m.group(3)
        delta_days = {"h": num / 24, "d": num, "w": num * 7, "m": num * 30}[unit]
        if sign == "-":
            delta_days = -delta_days
        return (today + timedelta(days=delta_days)).isoformat()

    if v.startswith("next "):
        dow = v[5:8]
        if dow in DOW:
            cur = today.weekday()
            target = DOW[dow]
            advance = (target - cur) % 7 or 7
            return (today + timedelta(days=advance)).isoformat()

    return None


def parse_duration(value: str) -> Optional[float]:
    """Return duration in hours, or None if unparseable."""
    if not value:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)([hdwm])$", value.strip().lower())
    if not m:
        return None
    num, unit = float(m.group(1)), m.group(2)
    return {"h": num, "d": num * 8, "w": num * 40, "m": num * 160}[unit]


# Default priority ordering. Configurable via ConfigMap in deployment;
# unknown values get rank 999 (sorted last).
PRIORITY_ORDER = ["p0", "p1", "p2", "p3", "high", "med", "medium", "low"]


def parse_priority_rank(value: str) -> int:
    v = (value or "").strip().lower()
    try:
        return PRIORITY_ORDER.index(v)
    except ValueError:
        return 999
