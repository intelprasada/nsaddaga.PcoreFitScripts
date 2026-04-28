"""Gamification event log — Phase 1 foundation.

This module owns the *write side* of the gamification subsystem: a single
``record_event`` helper that API write-handlers call after a successful
mutation. Reads (stats, badges, streaks) live in higher phases.

Design notes
------------
- All writes are **best-effort**: an exception inside ``record_event`` MUST
  NOT fail the parent request. We swallow and log instead. Gamification is
  decoration, never a correctness concern.
- The actor is the *authenticated user*, not the task owner. "What I did"
  semantics — closing someone else's task still counts as my close.
- Events are append-only. There is no update path. ``vn me reset`` will
  delete-by-user_id (Phase 4).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from .models import ActivityEvent, User

log = logging.getLogger(__name__)


# Event kind constants. Centralised so callers + tests share spelling.
TASK_CREATED = "task.created"
TASK_STATUS_SET = "task.status.set"
TASK_CLOSED = "task.closed"
NOTE_CREATED = "note.created"
NOTE_EDITED = "note.edited"


def record_event(
    s: Session,
    user_name: str,
    kind: str,
    ref: str = "",
    meta: Optional[dict[str, Any]] = None,
    *,
    ts: Optional[datetime] = None,
) -> list[str]:
    """Append one ActivityEvent for ``user_name`` and run badge
    recompute. Failure is swallowed.

    ``ref`` is a free-form subject identifier (task uuid, note path, …).
    ``meta`` is JSON-encoded into ``meta_json``.

    Returns the list of badge keys newly awarded by this event (empty if
    none — also empty on any internal failure, since gamification is
    decoration, never a correctness concern).
    """
    try:
        u = s.exec(select(User).where(User.name == user_name)).first()
        if u is None or u.id is None:
            return []
        ev = ActivityEvent(
            user_id=u.id,
            kind=kind,
            ref=ref or "",
            ts=ts or datetime.utcnow(),
            meta_json=json.dumps(meta, sort_keys=True) if meta else "",
        )
        s.add(ev)
        s.flush()
        # Recompute badges on every event. Cheap (one user's events) and
        # keeps awards close to the action that earned them.
        from . import badges as _badges  # local import: avoid circular
        return _badges.recompute_badges(s, u.id)
    except Exception:  # pragma: no cover - defensive
        log.exception("record_event failed (kind=%s ref=%s)", kind, ref)
        return []


def backfill(s: Session) -> dict[str, int]:
    """One-shot reconstruction of historical events from existing rows.

    Idempotent: removes any prior backfill rows (``meta.source == 'backfill'``)
    before re-emitting, so re-running after data fixes is safe.

    Strategy (intentionally minimal — historical actor is unknown):
      * For every Task with at least one owner, emit ``task.created`` per
        (task, owner) at ``task.created_at``.
      * For every Task whose status is ``done``, additionally emit
        ``task.closed`` per (task, owner) at ``task.updated_at``.
      * Notes have no recorded author and are NOT backfilled.

    Returns counts for observability.
    """
    from sqlalchemy import text as _text  # local: avoid top-level coupling

    s.exec(
        _text(
            "DELETE FROM activityevent "
            "WHERE meta_json LIKE '%\"source\": \"backfill\"%'"
        )
    )
    from .models import Task, TaskOwner  # local to avoid circular at import

    created = 0
    closed = 0
    rows = s.exec(
        select(Task, TaskOwner, User)
        .join(TaskOwner, TaskOwner.task_id == Task.id)
        .join(User, User.id == TaskOwner.user_id)
    ).all()
    for t, _own, u in rows:
        if u.id is None:
            continue
        meta_seed = {"source": "backfill"}
        if t.task_uuid:
            meta_seed["task_uuid"] = t.task_uuid
        s.add(ActivityEvent(
            user_id=u.id,
            kind=TASK_CREATED,
            ref=t.task_uuid or f"task#{t.id}",
            ts=t.created_at,
            meta_json=json.dumps(meta_seed, sort_keys=True),
        ))
        created += 1
        if (t.status or "").lower() == "done":
            close_meta = dict(meta_seed)
            close_meta["from"] = ""
            close_meta["to"] = "done"
            s.add(ActivityEvent(
                user_id=u.id,
                kind=TASK_CLOSED,
                ref=t.task_uuid or f"task#{t.id}",
                ts=t.updated_at,
                meta_json=json.dumps(close_meta, sort_keys=True),
            ))
            closed += 1
    s.flush()
    return {"task_created": created, "task_closed": closed}
