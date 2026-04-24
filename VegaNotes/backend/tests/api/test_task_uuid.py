"""Tests for Task.task_uuid: population on reindex, UUID lookup, rename stability.

Issue #64 — promote #id T-XXXXXX to Task.task_uuid column.
"""
import base64
import os
import tempfile
from pathlib import Path

import pytest

DATA = Path(tempfile.mkdtemp(prefix="vega-test-uuid-"))
os.environ.setdefault("VEGANOTES_DATA_DIR", str(DATA))
os.environ.setdefault("VEGANOTES_SERVE_STATIC", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = "Basic " + base64.b64encode(b"admin:admin").decode()


@pytest.fixture(scope="module")
def client():
    settings.notes_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    import app.db as _db
    _db._engine = None
    init_db()
    with TestClient(app) as c:
        yield c


def _setup(client) -> dict:
    """Create a project + note with a stamped task. Returns the task dict."""
    client.post("/api/projects", json={"name": "uuid-test"},
                headers={"Authorization": ADMIN})
    md = "# UUID test\n- !task Do something @admin !P1 #id T-ABCDEF\n"
    r = client.put("/api/notes",
                   json={"path": "uuid-test/plan.md", "body_md": md},
                   headers={"Authorization": ADMIN})
    assert r.status_code == 200, r.text
    r = client.get("/api/tasks?q=Do+something", headers={"Authorization": ADMIN})
    tasks = r.json()["tasks"]
    assert tasks, "task not indexed"
    return tasks[0]


def test_task_uuid_populated_on_index(client):
    task = _setup(client)
    assert task["task_uuid"] == "T-ABCDEF", (
        f"expected task_uuid='T-ABCDEF', got {task['task_uuid']!r}"
    )


def test_task_uuid_not_in_attrs(client):
    """The #id token must NOT appear as a TaskAttr key — it's a first-class column now."""
    task = _setup(client)
    assert "id" not in task["attrs"], (
        f"'id' key must not appear in attrs, got: {task['attrs']}"
    )


def test_patch_by_int_id(client):
    task = _setup(client)
    r = client.patch(
        f"/api/tasks/{task['id']}",
        json={"status": "in-progress"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in-progress"
    # Reset
    client.patch(f"/api/tasks/{task['id']}", json={"status": "todo"},
                 headers={"Authorization": ADMIN})


def test_patch_by_uuid(client):
    """PATCH /api/tasks/T-ABCDEF resolves the same task as using the int PK."""
    task = _setup(client)
    uuid = task["task_uuid"]
    assert uuid == "T-ABCDEF"

    r = client.patch(
        f"/api/tasks/{uuid}",
        json={"status": "blocked"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "blocked"
    assert data["id"] == task["id"], "UUID lookup must resolve to same int PK"
    assert data["task_uuid"] == uuid
    # Reset
    client.patch(f"/api/tasks/{task['id']}", json={"status": "todo"},
                 headers={"Authorization": ADMIN})


def test_card_links_by_uuid(client):
    """GET /api/cards/T-ABCDEF/links resolves without error."""
    task = _setup(client)
    uuid = task["task_uuid"]
    r = client.get(f"/api/cards/{uuid}/links", headers={"Authorization": ADMIN})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["task_id"] == task["id"]
    assert data["task_uuid"] == uuid


def test_slug_change_does_not_affect_uuid(client):
    """Rewriting the title (which changes the slug) must leave task_uuid stable."""
    task = _setup(client)
    uuid = task["task_uuid"]
    int_id = task["id"]

    # Rewrite the note with a different title but the same #id token.
    md = "# UUID test\n- !task Renamed task @admin !P1 #id T-ABCDEF\n"
    r = client.put("/api/notes",
                   json={"path": "uuid-test/plan.md", "body_md": md},
                   headers={"Authorization": ADMIN})
    assert r.status_code == 200, r.text

    # Look up by uuid — must still work despite new slug.
    r = client.patch(
        f"/api/tasks/{uuid}",
        json={"status": "done"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["task_uuid"] == uuid
    assert data["slug"] != task["slug"], "slug should have changed after rename"


def test_task_without_id_has_null_uuid(client):
    """Tasks that were created without an #id token get task_uuid=None."""
    client.post("/api/projects", json={"name": "no-id-proj"},
                headers={"Authorization": ADMIN})
    md = "# No ID\n- !task No id here @admin\n"
    client.put("/api/notes",
               json={"path": "no-id-proj/plan.md", "body_md": md},
               headers={"Authorization": ADMIN})
    r = client.get("/api/tasks?q=No+id+here", headers={"Authorization": ADMIN})
    tasks = r.json()["tasks"]
    assert tasks
    assert tasks[0]["task_uuid"] is None


def test_patch_unknown_uuid_returns_404(client):
    r = client.patch(
        "/api/tasks/T-ZZZZZZ",
        json={"status": "done"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 404, r.text


def test_get_by_uuid(client):
    """GET /api/tasks/T-ABCDEF returns the task dict (mirrors PATCH lookup)."""
    task = _setup(client)
    uuid = task["task_uuid"]

    r = client.get(f"/api/tasks/{uuid}", headers={"Authorization": ADMIN})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["id"] == task["id"]
    assert data["task_uuid"] == uuid

    # int PK should also work.
    r = client.get(f"/api/tasks/{task['id']}", headers={"Authorization": ADMIN})
    assert r.status_code == 200, r.text
    assert r.json()["task_uuid"] == uuid


def test_get_unknown_returns_404(client):
    r = client.get("/api/tasks/T-ZZZZZZ", headers={"Authorization": ADMIN})
    assert r.status_code == 404, r.text
