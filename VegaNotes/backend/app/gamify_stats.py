"""Gamification Phase 2: read-only personal stats.

Pure-Python summarisers over the ``ActivityEvent`` log written by Phase 1.
All times are in **UTC** (per-user TZ is a future enhancement; documented
in the plan). All reads are caller-scoped at the endpoint layer — this
module never reaches across users.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import text as _text
from sqlmodel import Session, select

from .models import ActivityEvent, Note, Task, TaskAttr


# ---- date helpers ---------------------------------------------------------

def _today_utc() -> date:
    return datetime.utcnow().date()


def _project_for_path(rel_path: str) -> Optional[str]:
    parts = Path(rel_path).parts
    return parts[0] if len(parts) >= 2 else None


# ---- pure streak computation ---------------------------------------------

def compute_streak(
    active_days: set[date],
    today: date,
    *,
    rest_tokens_per_window: int = 2,
    window_days: int = 14,
) -> dict[str, int]:
    """Compute current and longest streak over a set of active days.

    Walks backward from ``today``: each active day extends the streak;
    each inactive day is held in a *buffer* that is only committed as a
    consumed rest token if a later active day actually bridges across it.
    The streak ends when buffered + already-consumed tokens would exceed
    ``rest_tokens_per_window`` within any trailing ``window_days`` window.

    Tokens held speculatively (i.e. never bridged) do not count against
    ``rest_tokens_remaining`` — so a long lapse beyond the streak's true
    end doesn't punish you.

    Longest streak: same algorithm pinned at every active day in turn.
    O(n²) worst case but n is small in practice (one row per active day).
    """
    if not active_days:
        return {"current": 0, "longest": 0, "rest_tokens_remaining": rest_tokens_per_window}

    def _walk_back(start: date) -> tuple[int, list[date]]:
        """Returns (streak_length, committed_token_dates)."""
        current = 0
        committed: list[date] = []
        buffer: list[date] = []
        cursor = start
        # Hard guard against pathological inputs.
        guard = 0
        while guard < 366 * 5:
            guard += 1
            if cursor in active_days:
                # Commit any buffered inactive days as token uses.
                for d in buffer:
                    committed.append(d)
                buffer = []
                current += 1
            else:
                # Drop committed tokens that have aged out of the trailing window.
                cutoff = cursor - timedelta(days=window_days - 1)
                committed_in_window = [d for d in committed if d >= cutoff]
                if len(committed_in_window) + len(buffer) + 1 > rest_tokens_per_window:
                    break
                buffer.append(cursor)
            cursor -= timedelta(days=1)
        # Filter committed tokens to those still inside the trailing window
        # *as of `start`* — that's what "remaining" means at this anchor.
        cutoff = start - timedelta(days=window_days - 1)
        committed_recent = [d for d in committed if d >= cutoff]
        return current, committed_recent

    current, committed_recent = _walk_back(today)
    longest = current
    for d in active_days:
        if d > today:
            continue
        c, _ = _walk_back(d)
        if c > longest:
            longest = c

    return {
        "current": current,
        "longest": longest,
        "rest_tokens_remaining": max(0, rest_tokens_per_window - len(committed_recent)),
    }


# ---- compute pipeline -----------------------------------------------------

def _events_for(s: Session, user_id: int, since: Optional[date] = None) -> list[ActivityEvent]:
    q = select(ActivityEvent).where(ActivityEvent.user_id == user_id)
    if since is not None:
        q = q.where(ActivityEvent.ts >= datetime.combine(since, datetime.min.time()))
    return list(s.exec(q.order_by(ActivityEvent.ts.asc())).all())


def _task_by_uuid(s: Session, task_uuid: str) -> Optional[Task]:
    return s.exec(select(Task).where(Task.task_uuid == task_uuid)).first()


def _task_attr(s: Session, task_id: int, key: str) -> Optional[str]:
    row = s.exec(
        select(TaskAttr).where(TaskAttr.task_id == task_id, TaskAttr.key == key)
    ).first()
    return row.value if row else None


def _is_on_time(close_ts: datetime, eta_value: str) -> Optional[bool]:
    """Return True if closed on or before ``eta_value``, False if late.

    Returns None when ``eta_value`` is in a format we don't yet parse
    (e.g. ``ww18``); the caller excludes those from the rate.
    """
    if not eta_value:
        return None
    try:
        eta_d = datetime.fromisoformat(eta_value.strip()).date()
    except ValueError:
        return None
    return close_ts.date() <= eta_d


def compute_stats(
    s: Session,
    user_id: int,
    *,
    today: Optional[date] = None,
) -> dict[str, Any]:
    today = today or _today_utc()
    week_start = today - timedelta(days=6)         # last 7 calendar days incl today
    month_start = today - timedelta(days=29)       # last 30 days incl today
    thirty_days_ago_dt = datetime.combine(month_start, datetime.min.time())

    events = _events_for(s, user_id)

    closes_today = closes_week = closes_month = closes_lifetime = 0
    notes_week = notes_month = 0
    by_kind: dict[str, int] = defaultdict(int)
    project_counts: dict[str, int] = defaultdict(int)
    on_time_hits = on_time_total = 0
    active_days: set[date] = set()

    for ev in events:
        d = ev.ts.date()
        if ev.kind == "task.closed":
            closes_lifetime += 1
            if d == today:
                closes_today += 1
            if d >= week_start:
                closes_week += 1
            if d >= month_start:
                closes_month += 1
            active_days.add(d)
            # Resolve the task once for kind / project / on-time math.
            if ev.ref:
                t = _task_by_uuid(s, ev.ref)
                if t is not None:
                    by_kind[(t.kind or "task")] += 1
                    if ev.ts >= thirty_days_ago_dt:
                        note = s.get(Note, t.note_id)
                        if note:
                            proj = _project_for_path(note.path)
                            if proj:
                                project_counts[proj] += 1
                        eta = _task_attr(s, t.id, "eta") if t.id is not None else None
                        verdict = _is_on_time(ev.ts, eta or "")
                        if verdict is not None:
                            on_time_total += 1
                            if verdict:
                                on_time_hits += 1
        elif ev.kind in ("note.created", "note.edited"):
            if d >= week_start:
                notes_week += 1
            if d >= month_start:
                notes_month += 1
            active_days.add(d)

    streak = compute_streak(active_days, today)
    favorite_project = (
        max(project_counts.items(), key=lambda kv: kv[1])[0]
        if project_counts else None
    )
    on_time_rate = (on_time_hits / on_time_total) if on_time_total else None

    return {
        "as_of": today.isoformat(),
        "tasks_closed": {
            "today": closes_today,
            "week": closes_week,
            "month": closes_month,
            "lifetime": closes_lifetime,
        },
        "notes_touched": {"week": notes_week, "month": notes_month},
        "current_streak_days": streak["current"],
        "longest_streak_days": streak["longest"],
        "rest_tokens_remaining": streak["rest_tokens_remaining"],
        "on_time_eta_rate_30d": round(on_time_rate, 4) if on_time_rate is not None else None,
        "on_time_sample_30d": on_time_total,
        "favorite_project_30d": favorite_project,
        "by_kind": dict(by_kind),
    }


def compute_history(
    s: Session,
    user_id: int,
    *,
    days: int = 30,
    today: Optional[date] = None,
) -> list[dict[str, Any]]:
    today = today or _today_utc()
    start = today - timedelta(days=days - 1)
    start_dt = datetime.combine(start, datetime.min.time())

    closes: dict[date, int] = defaultdict(int)
    edits: dict[date, int] = defaultdict(int)
    rows = s.exec(
        select(ActivityEvent)
        .where(ActivityEvent.user_id == user_id)
        .where(ActivityEvent.ts >= start_dt)
    ).all()
    for ev in rows:
        d = ev.ts.date()
        if ev.kind == "task.closed":
            closes[d] += 1
        elif ev.kind in ("note.created", "note.edited"):
            edits[d] += 1

    out: list[dict[str, Any]] = []
    for i in range(days):
        d = start + timedelta(days=i)
        out.append({
            "date": d.isoformat(),
            "closes": closes.get(d, 0),
            "edits": edits.get(d, 0),
        })
    return out
