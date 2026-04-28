"""Phase 3 gamification: badges + per-user TZ.

Drives the system through ``record_event`` directly so each test can
construct the precise event log that should (or shouldn't) earn a given
badge. The HTTP surface is exercised separately — endpoints just call
into ``list_badges`` / the TZ setter.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

DATA = Path(tempfile.mkdtemp(prefix="vega-badges-"))
os.environ["VEGANOTES_DATA_DIR"] = str(DATA)
os.environ["VEGANOTES_SERVE_STATIC"] = "false"

from app.main import app  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.models import (  # noqa: E402
    ActivityEvent, Note, Task, TaskAttr, User, UserBadge,
)
from app import badges as badges_mod  # noqa: E402

ADMIN = "Basic " + base64.b64encode(b"admin:admin").decode()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    shutil.rmtree(DATA, ignore_errors=True)


def _user_id(s: Session, name: str = "admin") -> int:
    u = s.exec(select(User).where(User.name == name)).first()
    assert u is not None
    return u.id  # type: ignore[return-value]


def _wipe(s: Session, uid: int) -> None:
    """Reset gamification state for a user between tests."""
    for ev in s.exec(select(ActivityEvent).where(ActivityEvent.user_id == uid)).all():
        s.delete(ev)
    for b in s.exec(select(UserBadge).where(UserBadge.user_id == uid)).all():
        s.delete(b)
    s.commit()


def _add_event(
    s: Session,
    uid: int,
    kind: str,
    *,
    ref: str = "",
    ts: datetime,
    meta: dict | None = None,
) -> None:
    s.add(ActivityEvent(
        user_id=uid, kind=kind, ref=ref, ts=ts,
        meta_json=json.dumps(meta, sort_keys=True) if meta else "",
    ))
    s.commit()


def _earned(s: Session, uid: int) -> set[str]:
    return {
        r.badge_key for r in s.exec(
            select(UserBadge).where(UserBadge.user_id == uid)
        ).all()
    }


# ---------------------------------------------------------------------------
# Endpoint smoke + TZ
# ---------------------------------------------------------------------------

def test_badges_endpoint_shape(client):
    r = client.get("/api/me/badges", headers={"Authorization": ADMIN})
    assert r.status_code == 200, r.text
    data = r.json()
    for k in ("earned", "locked", "hidden_locked_count", "total_count"):
        assert k in data
    assert data["total_count"] == len(badges_mod.CATALOG)
    assert isinstance(data["earned"], list)
    assert isinstance(data["locked"], list)


def test_set_tz_valid(client):
    r = client.patch(
        "/api/me/tz", json={"tz": "America/Los_Angeles"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text
    assert r.json()["tz"] == "America/Los_Angeles"
    me = client.get("/api/me", headers={"Authorization": ADMIN}).json()
    assert me["tz"] == "America/Los_Angeles"


def test_set_tz_empty_means_utc(client):
    r = client.patch(
        "/api/me/tz", json={"tz": ""},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200
    assert r.json()["tz"] == "UTC"


def test_set_tz_rejects_unknown(client):
    r = client.patch(
        "/api/me/tz", json={"tz": "Mars/Olympus"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Badge predicates — driven by direct event injection
# ---------------------------------------------------------------------------

def test_first_light_awarded_on_single_close():
    with Session(get_engine()) as s:
        uid = _user_id(s)
        _wipe(s, uid)
        _add_event(s, uid, "task.closed", ref="T-A", ts=datetime.utcnow())
        new = badges_mod.recompute_badges(s, uid)
        s.commit()
        assert "first_light" in new
        assert "first_light" in _earned(s, uid)


def test_recompute_is_idempotent():
    with Session(get_engine()) as s:
        uid = _user_id(s)
        _wipe(s, uid)
        _add_event(s, uid, "task.closed", ref="T-A", ts=datetime.utcnow())
        badges_mod.recompute_badges(s, uid)
        s.commit()
        new2 = badges_mod.recompute_badges(s, uid)
        s.commit()
        assert new2 == []  # no double-award
        owned = list(s.exec(
            select(UserBadge).where(UserBadge.user_id == uid)
        ).all())
        # First Light shows exactly once even after a second pass.
        assert sum(1 for r in owned if r.badge_key == "first_light") == 1


def test_hat_trick_three_closes_same_day():
    with Session(get_engine()) as s:
        uid = _user_id(s)
        _wipe(s, uid)
        base = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
        for i in range(3):
            _add_event(s, uid, "task.closed", ref=f"T-H{i}",
                       ts=base + timedelta(minutes=i))
        new = badges_mod.recompute_badges(s, uid)
        s.commit()
        assert "hat_trick" in new


def test_docs_day_three_creates_same_day():
    with Session(get_engine()) as s:
        uid = _user_id(s)
        _wipe(s, uid)
        base = datetime.utcnow().replace(hour=10, minute=0, second=0, microsecond=0)
        for i in range(3):
            _add_event(s, uid, "note.created", ref=f"docs-{i}.md",
                       ts=base + timedelta(minutes=i))
        new = badges_mod.recompute_badges(s, uid)
        s.commit()
        assert "docs_day" in new


def test_curator_five_edits_same_note():
    with Session(get_engine()) as s:
        uid = _user_id(s)
        _wipe(s, uid)
        base = datetime.utcnow()
        for i in range(5):
            _add_event(s, uid, "note.edited", ref="same.md",
                       ts=base + timedelta(hours=i))
        new = badges_mod.recompute_badges(s, uid)
        s.commit()
        assert "curator" in new


def test_ghost_writer_ten_edits_one_day_one_note():
    with Session(get_engine()) as s:
        uid = _user_id(s)
        _wipe(s, uid)
        base = datetime.utcnow().replace(hour=8, minute=0, second=0, microsecond=0)
        for i in range(10):
            _add_event(s, uid, "note.edited", ref="ghost.md",
                       ts=base + timedelta(minutes=i * 30))
        badges_mod.recompute_badges(s, uid)
        s.commit()
        assert "ghost_writer" in _earned(s, uid)
        # Curator also fires (same edits >=5 of same note).
        assert "curator" in _earned(s, uid)


def test_marathoner_ten_consecutive_active_days():
    with Session(get_engine()) as s:
        uid = _user_id(s)
        _wipe(s, uid)
        # Place activity on 10 consecutive days ending today (UTC).
        today_utc = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
        for i in range(10):
            _add_event(s, uid, "task.closed", ref=f"T-M{i}",
                       ts=today_utc - timedelta(days=i))
        badges_mod.recompute_badges(s, uid)
        s.commit()
        assert "marathoner" in _earned(s, uid)


def test_weekend_warrior_close_on_saturday():
    with Session(get_engine()) as s:
        uid = _user_id(s)
        _wipe(s, uid)
        # Find an upcoming Saturday at noon UTC; user TZ is whatever they
        # last set — clear it to UTC for determinism.
        u = s.exec(select(User).where(User.id == uid)).first()
        u.tz = ""
        s.add(u); s.commit()
        d = datetime(2024, 1, 6, 12, 0, 0)  # Saturday
        assert d.weekday() == 5
        _add_event(s, uid, "task.closed", ref="T-WK", ts=d)
        badges_mod.recompute_badges(s, uid)
        s.commit()
        assert "weekend_warrior" in _earned(s, uid)


def test_quiet_hours_late_night_close():
    with Session(get_engine()) as s:
        uid = _user_id(s)
        _wipe(s, uid)
        u = s.exec(select(User).where(User.id == uid)).first()
        u.tz = ""  # UTC for determinism
        s.add(u); s.commit()
        d = datetime(2024, 3, 5, 23, 30, 0)  # 23:30 UTC, Tuesday (not weekend)
        _add_event(s, uid, "task.closed", ref="T-QH", ts=d)
        badges_mod.recompute_badges(s, uid)
        s.commit()
        assert "quiet_hours" in _earned(s, uid)


def test_phoenix_close_reopen_close():
    with Session(get_engine()) as s:
        uid = _user_id(s)
        _wipe(s, uid)
        base = datetime.utcnow() - timedelta(days=2)
        _add_event(s, uid, "task.closed", ref="T-PHX", ts=base)
        _add_event(s, uid, "task.status.set", ref="T-PHX",
                   ts=base + timedelta(hours=1),
                   meta={"from": "done", "to": "todo"})
        _add_event(s, uid, "task.closed", ref="T-PHX",
                   ts=base + timedelta(hours=2))
        badges_mod.recompute_badges(s, uid)
        s.commit()
        assert "phoenix" in _earned(s, uid)


def test_centurion_requires_100_closes():
    with Session(get_engine()) as s:
        uid = _user_id(s)
        _wipe(s, uid)
        base = datetime.utcnow() - timedelta(days=200)
        for i in range(99):
            _add_event(s, uid, "task.closed", ref=f"T-C{i}",
                       ts=base + timedelta(hours=i))
        badges_mod.recompute_badges(s, uid)
        s.commit()
        assert "centurion" not in _earned(s, uid)
        _add_event(s, uid, "task.closed", ref="T-C99",
                   ts=base + timedelta(hours=200))
        badges_mod.recompute_badges(s, uid)
        s.commit()
        assert "centurion" in _earned(s, uid)


# ---------------------------------------------------------------------------
# Hook integration
# ---------------------------------------------------------------------------

def test_record_event_awards_first_light(client):
    """Closing a task via the public API should award First Light if not
    already earned."""
    with Session(get_engine()) as s:
        uid = _user_id(s)
        _wipe(s, uid)
    # Create + close a task via real endpoints.
    client.put(
        "/api/notes", json={"path": "p3-hook.md", "body_md": "# h\n"},
        headers={"Authorization": ADMIN},
    )
    r = client.post(
        "/api/tasks",
        json={"note_path": "p3-hook.md", "title": "hook me", "owners": ["admin"]},
        headers={"Authorization": ADMIN},
    )
    ref = r.json()["task_uuid"]
    client.patch(
        f"/api/tasks/{ref}", json={"status": "done"},
        headers={"Authorization": ADMIN},
    )
    bad = client.get("/api/me/badges", headers={"Authorization": ADMIN}).json()
    keys = {b["key"] for b in bad["earned"]}
    assert "first_light" in keys


# ---------------------------------------------------------------------------
# TZ-aware bucketing in compute_history / compute_stats
# ---------------------------------------------------------------------------

def test_history_respects_per_user_tz():
    """An event at 23:00 PST on day D should bucket to D in PST and to
    D+1 in UTC (since that's 07:00 UTC the next day)."""
    from app.gamify_stats import compute_history
    with Session(get_engine()) as s:
        uid = _user_id(s)
        _wipe(s, uid)
        # Pick a UTC instant a few days ago at 05:00 UTC. That's
        # 22:00 the prior day in PDT (UTC-7) and 21:00 the prior day in
        # PST (UTC-8) — both safely "yesterday" in America/Los_Angeles.
        anchor = (datetime.utcnow() - timedelta(days=3)).replace(
            hour=5, minute=0, second=0, microsecond=0,
        )
        _add_event(s, uid, "task.closed", ref="T-TZ", ts=anchor)
        utc_day = anchor.strftime("%Y-%m-%d")
        la_day = (anchor - timedelta(days=1)).strftime("%Y-%m-%d")
        utc_hist = {h["date"]: h["closes"] for h in
                    compute_history(s, uid, days=30, tz_name="UTC")}
        la_hist = {h["date"]: h["closes"] for h in
                   compute_history(s, uid, days=30, tz_name="America/Los_Angeles")}
        assert utc_hist.get(utc_day, 0) == 1
        assert la_hist.get(la_day, 0) == 1
