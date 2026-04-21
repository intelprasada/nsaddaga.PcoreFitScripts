"""Tests for v3 endpoints: projects, RBAC, task PATCH (status round-trip)."""
import base64

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import init_db
from app.main import app

ADMIN = "Basic " + base64.b64encode(b"admin:admin").decode()


@pytest.fixture(scope="module")
def client():
    # Re-create the data dir if a prior test module wiped it; reset engine
    # so we don't cling to a deleted DB file, then re-init schema.
    settings.notes_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    import app.db as _db
    _db._engine = None
    init_db()
    with TestClient(app) as c:
        yield c


def test_create_project_and_tree(client):
    r = client.post("/api/projects", json={"name": "alpha"}, headers={"Authorization": ADMIN})
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "alpha", "role": "manager"}

    # Write a note inside the project
    md = "# Alpha\n- !task First #owner admin #status todo\n"
    r = client.put(
        "/api/notes",
        json={"path": "alpha/plan.md", "body_md": md},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text

    r = client.get("/api/tree", headers={"Authorization": ADMIN})
    tree = r.json()
    alpha = next(n for n in tree if n["project"] == "alpha")
    assert alpha["role"] == "manager"
    assert any(n["path"] == "alpha/plan.md" for n in alpha["notes"])


def test_patch_task_status_round_trips_to_md(client):
    # Find the task we just created
    r = client.get("/api/tasks?q=First", headers={"Authorization": ADMIN})
    tid = r.json()["tasks"][0]["id"]

    r = client.patch(
        f"/api/tasks/{tid}",
        json={"status": "in-progress"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in-progress"

    # The .md file on disk should reflect the new status
    on_disk = (settings.notes_dir / "alpha" / "plan.md").read_text()
    assert "#status in-progress" in on_disk


def test_invalid_project_name_rejected(client):
    r = client.post("/api/projects", json={"name": "../etc"}, headers={"Authorization": ADMIN})
    assert r.status_code == 400
    r = client.post("/api/projects", json={"name": "a/b"}, headers={"Authorization": ADMIN})
    assert r.status_code == 400


def test_duplicate_project_returns_409(client):
    r = client.post("/api/projects", json={"name": "alpha"}, headers={"Authorization": ADMIN})
    assert r.status_code == 409


def test_member_management(client):
    # Add bob as member
    r = client.put(
        "/api/projects/alpha/members",
        json={"user_name": "bob", "role": "member"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text

    r = client.get("/api/projects/alpha/members", headers={"Authorization": ADMIN})
    members = {m["user_name"]: m["role"] for m in r.json()}
    assert members.get("bob") == "member"

    # Remove bob
    r = client.delete("/api/projects/alpha/members/bob", headers={"Authorization": ADMIN})
    assert r.status_code == 200
    r = client.get("/api/projects/alpha/members", headers={"Authorization": ADMIN})
    assert all(m["user_name"] != "bob" for m in r.json())


def test_member_role_value_validated(client):
    r = client.put(
        "/api/projects/alpha/members",
        json={"user_name": "x", "role": "evil"},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 400


def test_owner_can_edit_their_task_without_project_membership(client):
    """Regression: an `@user` mention in markdown grants edit rights on that
    one task even when the user has no `ProjectMember` row for the project."""
    # Create a user with a known password but NO project membership in `alpha`.
    r = client.post(
        "/api/admin/users",
        json={"name": "ghost", "password": "pw", "is_admin": False},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code in (200, 201), r.text

    assert r.status_code in (200, 201), r.text
    GHOST = "Basic " + base64.b64encode(b"ghost:pw").decode()

    # Admin authors a task in alpha/ that mentions @ghost as the owner.
    md = (
        "# Alpha\n"
        "- !task Owned by ghost @ghost #status todo\n"
        "- !task Not theirs @admin #status todo\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "alpha/plan.md", "body_md": md},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text

    # Ghost is NOT a project member.
    r = client.get("/api/projects/alpha/members", headers={"Authorization": ADMIN})
    assert all(m["user_name"] != "ghost" for m in r.json())

    # Find the two tasks.
    r = client.get("/api/tasks?q=ghost", headers={"Authorization": ADMIN})
    owned = next(t for t in r.json()["tasks"] if t["title"] == "Owned by ghost")
    r = client.get("/api/tasks?q=Not", headers={"Authorization": ADMIN})
    others = next(t for t in r.json()["tasks"] if t["title"] == "Not theirs")

    # Ghost CAN PATCH the task they own — even without project membership.
    r = client.patch(
        f"/api/tasks/{owned['id']}",
        json={"status": "in-progress", "notes": "ghost was here"},
        headers={"Authorization": GHOST},
    )
    assert r.status_code == 200, r.text

    # Ghost CANNOT PATCH a task they don't own.
    r = client.patch(
        f"/api/tasks/{others['id']}",
        json={"status": "done"},
        headers={"Authorization": GHOST},
    )
    assert r.status_code == 403, r.text
    # Server detail should be precise so the frontend can show it.
    assert "no access" in r.json()["detail"].lower() or "own" in r.json()["detail"].lower()
