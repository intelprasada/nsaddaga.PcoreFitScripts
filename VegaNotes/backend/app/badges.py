"""Gamification Phase 3: badge catalog + award engine.

Each badge is a pure-function predicate over a pre-computed
``BadgeContext`` (the calling user's activity event log + memoised task
lookups). The catalog is small and curated — code changes ship new
badges, not user input.

``recompute_badges(s, user_id)`` is called after every ``record_event``
write. It scans the user's events once and inserts any newly earned
badges (idempotent via the ``ix_userbadge_user_key`` UNIQUE index).
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from .gamify_stats import (
    _is_on_time, _project_for_path, _task_attr, _user_tz_name,
    compute_streak, event_local_date, resolve_tz,
)
from .models import ActivityEvent, Note, Task, UserBadge

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

@dataclass
class BadgeContext:
    s: Session
    user_id: int
    tz_name: str
    today: date
    events: list[ActivityEvent]
    closes: list[ActivityEvent]
    note_creates: list[ActivityEvent]
    note_edits: list[ActivityEvent]
    status_sets: list[ActivityEvent]
    closes_by_day: dict[date, int]
    creates_by_day: dict[date, int]
    edits_by_note: dict[str, int]
    edits_by_day_per_note: dict[tuple[date, str], int]
    active_days: set[date]
    _task_cache: dict[str, Optional[Task]] = field(default_factory=dict)

    def task(self, ref: str) -> Optional[Task]:
        if not ref:
            return None
        if ref not in self._task_cache:
            self._task_cache[ref] = self.s.exec(
                select(Task).where(Task.task_uuid == ref)
            ).first()
        return self._task_cache[ref]

    def project_for(self, ref: str) -> Optional[str]:
        t = self.task(ref)
        if t is None:
            return None
        n = self.s.get(Note, t.note_id)
        return _project_for_path(n.path) if n else None

    def task_age_days(self, ev: ActivityEvent) -> Optional[int]:
        t = self.task(ev.ref)
        if t is None:
            return None
        return (ev.ts - t.created_at).days

    def close_was_on_time(self, ev: ActivityEvent) -> Optional[bool]:
        t = self.task(ev.ref)
        if t is None or t.id is None:
            return None
        eta = _task_attr(self.s, t.id, "eta")
        if not eta:
            return None
        local_d = event_local_date(ev.ts, self.tz_name)
        return _is_on_time(local_d, eta)


def _build_context(s: Session, user_id: int) -> BadgeContext:
    tz_name = _user_tz_name(s, user_id)
    today = datetime.now(tz=timezone.utc).astimezone(resolve_tz(tz_name)).date()
    events = list(s.exec(
        select(ActivityEvent)
        .where(ActivityEvent.user_id == user_id)
        .order_by(ActivityEvent.ts.asc())
    ).all())

    closes = [e for e in events if e.kind == "task.closed"]
    creates = [e for e in events if e.kind == "note.created"]
    edits = [e for e in events if e.kind == "note.edited"]
    status_sets = [e for e in events if e.kind == "task.status.set"]

    closes_by_day: dict[date, int] = defaultdict(int)
    creates_by_day: dict[date, int] = defaultdict(int)
    edits_by_note: dict[str, int] = defaultdict(int)
    edits_by_day_per_note: dict[tuple[date, str], int] = defaultdict(int)
    active_days: set[date] = set()

    for ev in closes:
        d = event_local_date(ev.ts, tz_name)
        closes_by_day[d] += 1
        active_days.add(d)
    for ev in creates:
        d = event_local_date(ev.ts, tz_name)
        creates_by_day[d] += 1
        active_days.add(d)
    for ev in edits:
        d = event_local_date(ev.ts, tz_name)
        edits_by_note[ev.ref] += 1
        edits_by_day_per_note[(d, ev.ref)] += 1
        active_days.add(d)

    return BadgeContext(
        s=s, user_id=user_id, tz_name=tz_name, today=today, events=events,
        closes=closes, note_creates=creates, note_edits=edits,
        status_sets=status_sets,
        closes_by_day=closes_by_day, creates_by_day=creates_by_day,
        edits_by_note=edits_by_note, edits_by_day_per_note=edits_by_day_per_note,
        active_days=active_days,
    )


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------

def _first_light(ctx: BadgeContext) -> bool:
    return len(ctx.closes) >= 1


def _hat_trick(ctx: BadgeContext) -> bool:
    return any(c >= 3 for c in ctx.closes_by_day.values())


def _docs_day(ctx: BadgeContext) -> bool:
    return any(c >= 3 for c in ctx.creates_by_day.values())


def _ghost_writer(ctx: BadgeContext) -> bool:
    return any(c >= 10 for c in ctx.edits_by_day_per_note.values())


def _curator(ctx: BadgeContext) -> bool:
    return any(c >= 5 for c in ctx.edits_by_note.values())


def _centurion(ctx: BadgeContext) -> bool:
    return len(ctx.closes) >= 100


def _marathoner(ctx: BadgeContext) -> bool:
    s = compute_streak(ctx.active_days, ctx.today)
    return max(s["current"], s["longest"]) >= 10


def _on_time(ctx: BadgeContext) -> bool:
    hits = 0
    for ev in ctx.closes:
        if ctx.close_was_on_time(ev):
            hits += 1
            if hits >= 10:
                return True
    return False


def _polyglot(ctx: BadgeContext) -> bool:
    by_day_proj: dict[date, set[str]] = defaultdict(set)
    for ev in ctx.closes:
        proj = ctx.project_for(ev.ref)
        if proj:
            by_day_proj[event_local_date(ev.ts, ctx.tz_name)].add(proj)
    if not by_day_proj:
        return False
    for anchor in sorted(by_day_proj.keys()):
        union: set[str] = set()
        for d, ps in by_day_proj.items():
            if anchor <= d <= anchor + timedelta(days=6):
                union |= ps
                if len(union) >= 5:
                    return True
    return False


def _cleanup_crew(ctx: BadgeContext) -> bool:
    aged_dates: list[date] = []
    for ev in ctx.closes:
        age = ctx.task_age_days(ev)
        if age is not None and age >= 30:
            aged_dates.append(event_local_date(ev.ts, ctx.tz_name))
    aged_dates.sort()
    for anchor in aged_dates:
        if sum(1 for d in aged_dates if anchor <= d <= anchor + timedelta(days=6)) >= 5:
            return True
    return False


def _weekend_warrior(ctx: BadgeContext) -> bool:
    return any(
        event_local_date(ev.ts, ctx.tz_name).weekday() >= 5
        for ev in ctx.closes
    )


def _quiet_hours(ctx: BadgeContext) -> bool:
    tz = resolve_tz(ctx.tz_name)
    for ev in ctx.closes:
        ts = ev.ts.replace(tzinfo=timezone.utc) if ev.ts.tzinfo is None else ev.ts
        local_t = ts.astimezone(tz).time()
        if local_t >= time(22, 0) or local_t < time(6, 0):
            return True
    return False


def _phoenix(ctx: BadgeContext) -> bool:
    closed_count: dict[str, int] = defaultdict(int)
    seen_reopen: set[str] = set()
    for ev in sorted(ctx.events, key=lambda e: e.ts):
        if ev.kind == "task.closed":
            closed_count[ev.ref] += 1
            if ev.ref in seen_reopen and closed_count[ev.ref] >= 2:
                return True
        elif ev.kind == "task.status.set":
            try:
                meta = json.loads(ev.meta_json) if ev.meta_json else {}
            except Exception:
                meta = {}
            if (str(meta.get("from") or "").lower() == "done"
                    and str(meta.get("to") or "").lower() != "done"
                    and closed_count.get(ev.ref, 0) >= 1):
                seen_reopen.add(ev.ref)
    return False


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Badge:
    key: str
    title: str
    description: str
    hidden: bool
    check: Callable[[BadgeContext], bool]
    progress: Optional[Callable[[BadgeContext], str]] = None


def _prog_count(target: int, getter: Callable[[BadgeContext], int]):
    def fn(ctx: BadgeContext) -> str:
        n = getter(ctx)
        return f"{min(n, target)}/{target}"
    return fn


def _prog_streak(target: int):
    def fn(ctx: BadgeContext) -> str:
        st = compute_streak(ctx.active_days, ctx.today)
        cur = max(st["current"], st["longest"])
        return f"{min(cur, target)}/{target} day(s)"
    return fn


CATALOG: tuple[Badge, ...] = (
    Badge("first_light", "First Light",
          "Close your first task.", False, _first_light,
          _prog_count(1, lambda c: len(c.closes))),
    Badge("hat_trick", "Hat Trick",
          "Close 3 tasks in a single day.", False, _hat_trick,
          _prog_count(3, lambda c: max(c.closes_by_day.values(), default=0))),
    Badge("marathoner", "Marathoner",
          "Maintain a 10-day activity streak.", False, _marathoner,
          _prog_streak(10)),
    Badge("centurion", "Centurion",
          "Close 100 tasks lifetime.", False, _centurion,
          _prog_count(100, lambda c: len(c.closes))),
    Badge("on_time", "On Time",
          "Close 10 tasks on or before their ETA (ISO dates only).",
          False, _on_time),
    Badge("curator", "Curator",
          "Edit the same note 5 or more times.", False, _curator,
          _prog_count(5, lambda c: max(c.edits_by_note.values(), default=0))),
    Badge("docs_day", "Docs Day",
          "Create 3 notes in a single day.", False, _docs_day,
          _prog_count(3, lambda c: max(c.creates_by_day.values(), default=0))),
    Badge("polyglot", "Polyglot",
          "Touch 5 distinct projects via closes in one 7-day window.",
          False, _polyglot),
    Badge("cleanup_crew", "Cleanup Crew",
          "Close 5 tasks aged ≥30 days in one 7-day window.",
          False, _cleanup_crew),
    # Hidden / easter-egg badges.
    Badge("weekend_warrior", "Weekend Warrior",
          "Close a task on a Saturday or Sunday (your local time).",
          True, _weekend_warrior),
    Badge("quiet_hours", "Quiet Hours",
          "Close a task between 22:00 and 06:00 local time.",
          True, _quiet_hours),
    Badge("ghost_writer", "Ghost Writer",
          "Edit the same note 10 or more times in a single day.",
          True, _ghost_writer),
    Badge("phoenix", "Phoenix",
          "Re-open a closed task and close it again.",
          True, _phoenix),
)

CATALOG_BY_KEY: dict[str, Badge] = {b.key: b for b in CATALOG}


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def recompute_badges(s: Session, user_id: int) -> list[str]:
    """Award any newly-earned badges for ``user_id``. Returns the list of
    keys awarded *this call* so callers can announce them. Idempotent —
    existing awards are skipped (UNIQUE index also guards at the DB layer).

    Failures are swallowed: gamification must never break the parent
    request.
    """
    try:
        ctx = _build_context(s, user_id)
        owned = {
            row.badge_key for row in s.exec(
                select(UserBadge).where(UserBadge.user_id == user_id)
            ).all()
        }
        newly: list[str] = []
        for badge in CATALOG:
            if badge.key in owned:
                continue
            try:
                if badge.check(ctx):
                    s.add(UserBadge(
                        user_id=user_id, badge_key=badge.key,
                        awarded_at=datetime.utcnow(),
                    ))
                    newly.append(badge.key)
            except Exception:  # pragma: no cover
                log.exception("badge check failed: %s", badge.key)
        if newly:
            s.flush()
        return newly
    except Exception:  # pragma: no cover
        log.exception("recompute_badges failed for user_id=%s", user_id)
        return []


def list_badges(s: Session, user_id: int) -> dict[str, Any]:
    """Return earned + locked + hidden_locked counts for ``user_id``."""
    ctx = _build_context(s, user_id)
    owned_rows = s.exec(
        select(UserBadge).where(UserBadge.user_id == user_id)
    ).all()
    owned_at: dict[str, datetime] = {r.badge_key: r.awarded_at for r in owned_rows}

    earned: list[dict[str, Any]] = []
    locked: list[dict[str, Any]] = []
    hidden_locked = 0
    for badge in CATALOG:
        if badge.key in owned_at:
            earned.append({
                "key": badge.key,
                "title": badge.title,
                "description": badge.description,
                "awarded_at": owned_at[badge.key].isoformat(),
            })
            continue
        if badge.hidden:
            hidden_locked += 1
            continue
        progress = badge.progress(ctx) if badge.progress else None
        locked.append({
            "key": badge.key,
            "title": badge.title,
            "description": badge.description,
            "progress": progress,
        })
    return {
        "earned": earned,
        "locked": locked,
        "hidden_locked_count": hidden_locked,
        "total_count": len(CATALOG),
    }
