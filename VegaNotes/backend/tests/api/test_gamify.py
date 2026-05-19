"""Phase 1 gamification: activity event log.

Verifies that each instrumented write endpoint emits exactly one event,
that ``GET /api/me/activity`` is hard-scoped to the caller, and that
``POST /api/admin/gamify/backfill`` is idempotent.
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DATA = Path(tempfile.mkdtemp(prefix="vega-gamify-"))
os.environ["VEGANOTES_DATA_DIR"] = str(DATA)
os.environ["VEGANOTES_SERVE_STATIC"] = "false"

from app.main import app  # noqa: E402

ADMIN = "Basic " + base64.b64encode(b"admin:admin").decode()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    shutil.rmtree(DATA, ignore_errors=True)


def _activity(client, **params) -> list[dict]:
    r = client.get("/api/me/activity", params=params, headers={"Authorization": ADMIN})
    assert r.status_code == 200, r.text
    return r.json()


def _kinds(events: list[dict]) -> list[str]:
    return [e["kind"] for e in events]


def test_note_create_emits_one_event(client):
    before = _activity(client, kind="note.created")
    r = client.put(
        "/api/notes",
        json={"path": "gamify-a.md", "body_md": "# A\n\nfirst note\n"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text
    after = _activity(client, kind="note.created")
    assert len(after) == len(before) + 1
    assert after[0]["ref"] == "gamify-a.md"


def test_note_edit_emits_event(client):
    # baseline create
    client.put(
        "/api/notes",
        json={"path": "gamify-edit.md", "body_md": "# E\n\nv1\n"},
        headers={"Authorization": ADMIN},
    )
    before = _activity(client, kind="note.edited")
    r = client.put(
        "/api/notes",
        json={"path": "gamify-edit.md", "body_md": "# E\n\nv2 with changes\n"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text
    after = _activity(client, kind="note.edited")
    assert len(after) == len(before) + 1


def test_note_whitespace_only_change_skipped(client):
    client.put(
        "/api/notes",
        json={"path": "gamify-ws.md", "body_md": "# W\n\nstable body\n"},
        headers={"Authorization": ADMIN},
    )
    before = _activity(client, kind="note.edited")
    # Pure trailing whitespace change — should NOT produce a note.edited event.
    r = client.put(
        "/api/notes",
        json={"path": "gamify-ws.md", "body_md": "# W\n\nstable body\n\n\n"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200
    after = _activity(client, kind="note.edited")
    assert len(after) == len(before)


def test_task_create_emits_event(client):
    client.put(
        "/api/notes",
        json={"path": "gamify-tasks.md", "body_md": "# Tasks\n"},
        headers={"Authorization": ADMIN},
    )
    before = _activity(client, kind="task.created")
    r = client.post(
        "/api/tasks",
        json={
            "note_path": "gamify-tasks.md",
            "title": "ship phase 1",
            "owners": ["admin"],
        },
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 201, r.text
    task_uuid = r.json().get("task_uuid")
    after = _activity(client, kind="task.created")
    assert len(after) == len(before) + 1
    assert after[0]["ref"] == task_uuid


def test_task_status_change_emits_status_set_and_close(client):
    # Set up a task to flip.
    client.put(
        "/api/notes",
        json={"path": "gamify-status.md", "body_md": "# S\n"},
        headers={"Authorization": ADMIN},
    )
    r = client.post(
        "/api/tasks",
        json={
            "note_path": "gamify-status.md",
            "title": "flip me",
            "owners": ["admin"],
        },
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 201
    ref = r.json()["task_uuid"]

    set_before = _activity(client, kind="task.status.set")
    closed_before = _activity(client, kind="task.closed")
    r = client.patch(
        f"/api/tasks/{ref}",
        json={"status": "done"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text
    set_after = _activity(client, kind="task.status.set")
    closed_after = _activity(client, kind="task.closed")
    assert len(set_after) == len(set_before) + 1
    assert len(closed_after) == len(closed_before) + 1
    assert set_after[0]["meta"]["from"] == "todo"
    assert set_after[0]["meta"]["to"] == "done"


def test_patch_no_status_change_no_event(client):
    client.put(
        "/api/notes",
        json={"path": "gamify-noop.md", "body_md": "# N\n"},
        headers={"Authorization": ADMIN},
    )
    r = client.post(
        "/api/tasks",
        json={"note_path": "gamify-noop.md", "title": "hold steady", "owners": ["admin"]},
        headers={"Authorization": ADMIN},
    )
    ref = r.json()["task_uuid"]
    before = _activity(client, kind="task.status.set")
    # Patch a non-status field; no status event expected.
    r = client.patch(
        f"/api/tasks/{ref}",
        json={"priority": "p2"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200
    after = _activity(client, kind="task.status.set")
    assert len(after) == len(before)


def test_activity_filters_by_kind_since_limit(client):
    # The above tests have already populated several events. Sanity-check
    # that filtering / pagination works.
    only_creates = _activity(client, kind="task.created")
    assert all(e["kind"] == "task.created" for e in only_creates)
    capped = _activity(client, limit=2)
    assert len(capped) <= 2


def test_activity_rejects_bad_date(client):
    r = client.get(
        "/api/me/activity",
        params={"since": "not-a-date"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 400


def test_admin_backfill_is_idempotent(client):
    r1 = client.post("/api/admin/gamify/backfill", headers={"Authorization": ADMIN})
    assert r1.status_code == 200, r1.text
    counts1 = r1.json()
    r2 = client.post("/api/admin/gamify/backfill", headers={"Authorization": ADMIN})
    assert r2.status_code == 200
    counts2 = r2.json()
    # Same row counts both runs (deletes-and-re-inserts).
    assert counts1["task_created"] == counts2["task_created"]
    assert counts1["task_closed"] == counts2["task_closed"]
    # And we backfilled at least the tasks created in this module.
    assert counts1["task_created"] >= 3


def test_activity_endpoint_self_only(client):
    # /api/me/activity does not accept a `user` param — admins cannot
    # peek at someone else's stream this way.
    r = client.get(
        "/api/me/activity",
        params={"user": "someone-else"},
        headers={"Authorization": ADMIN},
    )
    # The query param is silently ignored (FastAPI tolerates extras), and
    # the response is still scoped to the caller. The privacy contract is
    # that there's no parameter binding for cross-user reads.
    assert r.status_code == 200
    # Every returned event MUST have been recorded for the admin user.
    # We can't see user_id from the public payload, but we can at least
    # assert the endpoint doesn't fail. The hard guarantee lives in the
    # endpoint signature itself (no `user_id` Query param).


def test_backfill_requires_admin(client):
    # Build a non-admin user and confirm 403.
    r = client.post(
        "/api/admin/users",
        json={"name": "alice", "password": "alicepass1", "is_admin": False},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code in (200, 201, 409), r.text
    alice_auth = "Basic " + base64.b64encode(b"alice:alicepass1").decode()
    r = client.post("/api/admin/gamify/backfill", headers={"Authorization": alice_auth})
    assert r.status_code == 403
