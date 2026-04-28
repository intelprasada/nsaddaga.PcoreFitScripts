"""Phase 2 gamification: personal stats endpoints + pure compute_streak.

Strategy: drive the backend through its real HTTP surface (so we test the
hooks + stats math together), then directly unit-test ``compute_streak``
for edge cases that are awkward to reach via the API.
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DATA = Path(tempfile.mkdtemp(prefix="vega-stats-"))
os.environ["VEGANOTES_DATA_DIR"] = str(DATA)
os.environ["VEGANOTES_SERVE_STATIC"] = "false"

from app.main import app  # noqa: E402
from app.gamify_stats import compute_streak  # noqa: E402

ADMIN = "Basic " + base64.b64encode(b"admin:admin").decode()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    shutil.rmtree(DATA, ignore_errors=True)


def _create_task(client, note_path: str, title: str, **extra) -> str:
    client.put(
        "/api/notes",
        json={"path": note_path, "body_md": "# t\n"},
        headers={"Authorization": ADMIN},
    )
    body = {"note_path": note_path, "title": title, "owners": ["admin"]}
    body.update(extra)
    r = client.post("/api/tasks", json=body, headers={"Authorization": ADMIN})
    assert r.status_code == 201, r.text
    return r.json()["task_uuid"]


def _close(client, ref: str):
    r = client.patch(
        f"/api/tasks/{ref}",
        json={"status": "done"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Endpoint smoke tests
# ---------------------------------------------------------------------------

def test_stats_endpoint_basic_shape(client):
    r = client.get("/api/me/stats", headers={"Authorization": ADMIN})
    assert r.status_code == 200, r.text
    data = r.json()
    for key in (
        "as_of", "tasks_closed", "notes_touched",
        "current_streak_days", "longest_streak_days", "rest_tokens_remaining",
        "on_time_eta_rate_30d", "on_time_sample_30d", "favorite_project_30d",
        "by_kind",
    ):
        assert key in data
    for k in ("today", "week", "month", "lifetime"):
        assert k in data["tasks_closed"]


def test_close_increments_today_and_lifetime(client):
    before = client.get("/api/me/stats", headers={"Authorization": ADMIN}).json()
    ref = _create_task(client, "stats-incr.md", "incr me")
    _close(client, ref)
    after = client.get("/api/me/stats", headers={"Authorization": ADMIN}).json()
    assert after["tasks_closed"]["today"] == before["tasks_closed"]["today"] + 1
    assert after["tasks_closed"]["lifetime"] == before["tasks_closed"]["lifetime"] + 1
    assert after["current_streak_days"] >= 1
    assert after["by_kind"].get("task", 0) >= 1


def test_streak_endpoint_compact_shape(client):
    r = client.get("/api/me/streak", headers={"Authorization": ADMIN})
    assert r.status_code == 200
    keys = set(r.json().keys())
    assert keys == {
        "current_streak_days", "longest_streak_days",
        "rest_tokens_remaining", "as_of",
    }


def test_history_default_30_days(client):
    r = client.get("/api/me/history", headers={"Authorization": ADMIN})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 30
    for row in rows:
        assert set(row.keys()) == {"date", "closes", "edits"}


def test_history_custom_window(client):
    r = client.get("/api/me/history?days=7", headers={"Authorization": ADMIN})
    assert r.status_code == 200
    assert len(r.json()) == 7


def test_history_rejects_out_of_range(client):
    r = client.get("/api/me/history?days=0", headers={"Authorization": ADMIN})
    assert r.status_code == 422
    r = client.get("/api/me/history?days=400", headers={"Authorization": ADMIN})
    assert r.status_code == 422


def test_on_time_rate_when_eta_is_iso_date(client):
    """Closing a task before its ETA bumps the on-time hit count."""
    today_iso = date.today().isoformat()
    _create_task(client, "stats-ontime.md", "ahead of time", eta=today_iso)
    # The fresh stats call should now have at least one on-time sample.
    data = client.get("/api/me/stats", headers={"Authorization": ADMIN}).json()
    # Find the task_uuid we just made by walking activity.
    acts = client.get(
        "/api/me/activity?kind=task.created&limit=5",
        headers={"Authorization": ADMIN},
    ).json()
    ref = acts[0]["ref"]
    _close(client, ref)
    after = client.get("/api/me/stats", headers={"Authorization": ADMIN}).json()
    assert after["on_time_sample_30d"] >= 1
    # We closed today, eta is today → on time.
    assert after["on_time_eta_rate_30d"] is not None


def test_favorite_project_30d_picks_top_folder(client):
    # Create+close two tasks under a single project folder.
    _create_task(client, "famproj/notes.md", "p-task-1")
    acts = client.get(
        "/api/me/activity?kind=task.created&limit=5",
        headers={"Authorization": ADMIN},
    ).json()
    _close(client, acts[0]["ref"])
    _create_task(client, "famproj/notes.md", "p-task-2")
    acts = client.get(
        "/api/me/activity?kind=task.created&limit=5",
        headers={"Authorization": ADMIN},
    ).json()
    _close(client, acts[0]["ref"])

    data = client.get("/api/me/stats", headers={"Authorization": ADMIN}).json()
    # famproj should be a candidate; other tests close root-level tasks
    # which produce no project attribution.
    assert data["favorite_project_30d"] == "famproj"


# ---------------------------------------------------------------------------
# Pure compute_streak unit tests
# ---------------------------------------------------------------------------

def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def test_streak_zero_when_no_activity():
    out = compute_streak(set(), _d("2026-04-27"))
    assert out["current"] == 0 and out["longest"] == 0
    assert out["rest_tokens_remaining"] == 2


def test_streak_consecutive_days():
    days = {_d("2026-04-25"), _d("2026-04-26"), _d("2026-04-27")}
    out = compute_streak(days, _d("2026-04-27"))
    assert out["current"] == 3
    assert out["longest"] == 3


def test_streak_uses_one_rest_token_for_a_gap():
    # Active: today, two days ago. One inactive day yesterday burns a token.
    days = {_d("2026-04-25"), _d("2026-04-27")}
    out = compute_streak(days, _d("2026-04-27"))
    assert out["current"] == 2
    assert out["rest_tokens_remaining"] == 1


def test_streak_breaks_after_three_inactive_days_in_window():
    # Active: today, then 4 days ago. Three inactive days = exhausts both
    # tokens and one more → break.
    days = {_d("2026-04-23"), _d("2026-04-27")}
    out = compute_streak(days, _d("2026-04-27"))
    assert out["current"] == 1  # only today survives


def test_streak_includes_today_inactive_with_token_burn():
    # Today inactive but yesterday active → still on a streak (tokens cover today).
    days = {_d("2026-04-26")}
    out = compute_streak(days, _d("2026-04-27"))
    assert out["current"] == 1
    assert out["rest_tokens_remaining"] == 1


def test_longest_streak_separate_from_current():
    # An old long streak that has since lapsed still shows up as longest.
    days = (
        {_d("2026-01-01") + timedelta(days=i) for i in range(10)}  # 10-day run
        | {_d("2026-04-27")}                                        # today
    )
    out = compute_streak(days, _d("2026-04-27"))
    assert out["longest"] >= 10
    assert out["current"] == 1
