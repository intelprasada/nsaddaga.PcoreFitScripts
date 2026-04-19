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

# Intel work-week notation. Weeks run Sunday (.0) → Saturday (.6).
# WW1 is the week containing the first Saturday of the calendar year, which
# means WW1 starts on the Sunday immediately preceding that first Saturday.
# Accepts: "WW16", "ww16.3", "2026WW16", "2026ww16.0".
INTEL_WW_RE = re.compile(r"^(?:(\d{4}))?ww(\d{1,2})(?:\.(\d))?$")


def _intel_ww1_start(year: int) -> date:
    jan1 = date(year, 1, 1)
    # Python weekday(): Mon=0..Sun=6. Saturday is 5.
    days_to_first_sat = (5 - jan1.weekday()) % 7
    first_sat = jan1 + timedelta(days=days_to_first_sat)
    return first_sat - timedelta(days=6)  # the Sunday before


def parse_intel_ww(value: str, *, today: Optional[date] = None) -> Optional[str]:
    """Parse Intel work-week notation into an ISO date.

    >>> parse_intel_ww("WW17.0", today=date(2026, 4, 19))
    '2026-04-19'
    """
    if not value:
        return None
    m = INTEL_WW_RE.match(value.strip().lower())
    if not m:
        return None
    today = today or _today()
    year = int(m.group(1)) if m.group(1) else today.year
    week = int(m.group(2))
    day = int(m.group(3)) if m.group(3) is not None else 5  # default → Friday
    if not (1 <= week <= 53) or not (0 <= day <= 6):
        return None
    start = _intel_ww1_start(year)
    return (start + timedelta(days=(week - 1) * 7 + day)).isoformat()


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

    ww = parse_intel_ww(v, today=today)
    if ww:
        return ww

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
