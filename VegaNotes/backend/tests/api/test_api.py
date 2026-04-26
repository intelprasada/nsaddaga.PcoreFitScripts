import base64
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DATA = Path(tempfile.mkdtemp(prefix="vega-test-"))
os.environ["VEGANOTES_DATA_DIR"] = str(DATA)
os.environ["VEGANOTES_SERVE_STATIC"] = "false"

from app.main import app  # noqa: E402

AUTH = "Basic " + base64.b64encode(b"admin:admin").decode()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    shutil.rmtree(DATA, ignore_errors=True)


def test_health(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_auth_required(client):
    r = client.get("/api/notes")
    assert r.status_code == 401


def test_create_and_query(client):
    body = (Path(__file__).parent.parent / "fixtures" / "sprint14.md").read_text()
    r = client.put(
        "/api/notes",
        json={"path": "sprint14.md", "body_md": body},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    # Tasks for alice (hide done)
    r = client.get("/api/tasks?owner=alice&hide_done=1", headers={"Authorization": AUTH})
    assert r.status_code == 200
    tasks = r.json()["tasks"]
    titles = sorted(t["title"] for t in tasks)
    assert titles == ["Add OAuth callback", "Add login screen", "Wire up SSO"]

    # Agenda window covering 2027-04-24 (use big window).
    # "Wire up SSO" (alice, eta 04-22) rolls up to 04-24 (max child ETA).
    # "Add login screen" (bob, inherited alice) doesn't surface in agenda
    # since the agenda JOIN requires explicit owner row — pre-existing known gap.
    r = client.get("/api/agenda?owner=alice&days=3650", headers={"Authorization": AUTH})
    days = r.json()["by_day"]
    assert "2027-04-24" in days

    # Feature aggregation
    r = client.get("/api/features/search-rewrite/tasks", headers={"Authorization": AUTH})
    j = r.json()
    assert j["aggregations"]["owners"] == ["alice"]
    assert "Migrate index" in [t["title"] for t in j["tasks"]]

    # Bidirectional links: migrate-index -> wire-up-sso
    r = client.get("/api/tasks?q=Migrate", headers={"Authorization": AUTH})
    migrate_id = r.json()["tasks"][0]["id"]
    r = client.get(f"/api/cards/{migrate_id}/links", headers={"Authorization": AUTH})
    links = r.json()["links"]
    assert any(l["other_slug"] == "wire-up-sso" and l["direction"] == "out" for l in links)
    # Reverse direction on the target card
    r = client.get("/api/tasks?q=Wire", headers={"Authorization": AUTH})
    wire_id = r.json()["tasks"][0]["id"]
    r = client.get(f"/api/cards/{wire_id}/links", headers={"Authorization": AUTH})
    links = r.json()["links"]
    assert any(l["other_slug"] == "migrate-index" and l["direction"] == "in" for l in links)


# ---------------------------------------------------------------------------
# RBAC: last-manager protection (#81)
# ---------------------------------------------------------------------------

def _create_user(client, name: str, password: str = "pw") -> None:
    r = client.post(
        "/api/admin/users",
        json={"name": name, "password": password, "is_admin": False},
        headers={"Authorization": AUTH},
    )
    assert r.status_code in (200, 201, 409), r.text  # 201 created, 409 = already exists


def _admin_auth(name: str, password: str = "admin") -> str:
    return "Basic " + base64.b64encode(f"{name}:{password}".encode()).decode()


def test_last_manager_protection(client):
    """Removing or demoting the sole manager of a project must be rejected."""
    _create_user(client, "mgr1")
    _create_user(client, "mem1")

    # Create project as admin (admin auto-becomes manager via create_project).
    r = client.post(
        "/api/projects",
        json={"name": "test-rbac-proj"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code in (200, 409), r.text

    # Add mgr1 as manager, mem1 as member.
    client.put(
        "/api/projects/test-rbac-proj/members",
        json={"user_name": "mgr1", "role": "manager"},
        headers={"Authorization": AUTH},
    )
    client.put(
        "/api/projects/test-rbac-proj/members",
        json={"user_name": "mem1", "role": "member"},
        headers={"Authorization": AUTH},
    )

    # Remove admin from managers — now mgr1 is the only manager.
    r = client.delete(
        "/api/projects/test-rbac-proj/members/admin",
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200

    # Trying to demote mgr1 to member (sole manager) must return 400.
    r = client.put(
        "/api/projects/test-rbac-proj/members",
        json={"user_name": "mgr1", "role": "member"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 400
    assert "last manager" in r.json()["detail"].lower()

    # Trying to delete mgr1 (sole manager) must return 400.
    r = client.delete(
        "/api/projects/test-rbac-proj/members/mgr1",
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 400
    assert "last manager" in r.json()["detail"].lower()

    # Promoting mem1 to manager first — now removal/demotion of mgr1 is allowed.
    r = client.put(
        "/api/projects/test-rbac-proj/members",
        json={"user_name": "mem1", "role": "manager"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200

    r = client.delete(
        "/api/projects/test-rbac-proj/members/mgr1",
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Filesystem project bootstrap (#82)
# ---------------------------------------------------------------------------

def test_fs_project_bootstrap(client):
    """Projects created by dropping folders on disk get auto-assigned admin manager."""
    import shutil as _shutil
    proj_dir = DATA / "notes" / "fs-only-project"
    proj_dir.mkdir(parents=True, exist_ok=True)
    note = proj_dir / "readme.md"
    note.write_text("# FS-only project\n\n!task #title Hello #owner alice\n")

    # Trigger a reindex via the admin reindex endpoint.
    r = client.post("/api/admin/reindex", headers={"Authorization": AUTH})
    assert r.status_code == 200

    # The project should now be visible via /api/projects.
    r = client.get("/api/projects", headers={"Authorization": AUTH})
    names = [p["name"] for p in r.json()]
    assert "fs-only-project" in names

    # Admin must be listed as manager.
    r = client.get("/api/projects/fs-only-project/members", headers={"Authorization": AUTH})
    assert r.status_code == 200
    members = r.json()
    admin_row = next((m for m in members if m["user_name"] == "admin"), None)
    assert admin_row is not None
    assert admin_row["role"] == "manager"

    # Cleanup
    _shutil.rmtree(proj_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Self-service password change (#66)
# ---------------------------------------------------------------------------

def test_change_own_password(client):
    """Any user can change their own password; wrong current password is rejected."""
    _create_user(client, "pw-user", "oldpass")
    user_auth = "Basic " + base64.b64encode(b"pw-user:oldpass").decode()

    # Wrong current password → 403.
    r = client.patch(
        "/api/me/password",
        json={"current_password": "wrongpass", "new_password": "newpass"},
        headers={"Authorization": user_auth},
    )
    assert r.status_code == 403

    # Empty new password → 400.
    r = client.patch(
        "/api/me/password",
        json={"current_password": "oldpass", "new_password": ""},
        headers={"Authorization": user_auth},
    )
    assert r.status_code == 400

    # Correct change → 200.
    r = client.patch(
        "/api/me/password",
        json={"current_password": "oldpass", "new_password": "newpass"},
        headers={"Authorization": user_auth},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    # Old credentials now rejected.
    r = client.get("/api/me", headers={"Authorization": user_auth})
    assert r.status_code == 401

    # New credentials work.
    new_auth = "Basic " + base64.b64encode(b"pw-user:newpass").decode()
    r = client.get("/api/me", headers={"Authorization": new_auth})
    assert r.status_code == 200
    assert r.json()["name"] == "pw-user"


def test_admin_can_reset_any_password(client):
    """Admin can reset another user's password via PATCH /admin/users/{name}."""
    _create_user(client, "reset-target", "original")

    # Admin resets to "forced".
    r = client.patch(
        "/api/admin/users/reset-target",
        json={"password": "forced"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200

    # New password works.
    new_auth = "Basic " + base64.b64encode(b"reset-target:forced").decode()
    r = client.get("/api/me", headers={"Authorization": new_auth})
    assert r.status_code == 200
    assert r.json()["name"] == "reset-target"


# ---------------------------------------------------------------------------
# Ref-row propagation (#92)
# ---------------------------------------------------------------------------

def test_patch_propagates_to_ref_rows(client):
    """PATCH /tasks/{id} must update #task T-XXXX ref rows in other files too."""
    # 1. Create the canonical note with a stamped task.
    canonical_md = (
        "# Sprint\n"
        "- !task #id T-PROP01 Fix cache #status todo @alice\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "canonical-prop.md", "body_md": canonical_md},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text
    canonical_note_id = r.json()["id"]

    # 2. Create a weekly note that references the task with override attrs.
    ref_md = (
        "# Weekly\n"
        "- #task T-PROP01 Fix cache #status todo @alice\n"
        "- #task T-PROP01 Fix cache copy two #status todo\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "weekly-prop.md", "body_md": ref_md},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text
    ref_note_id = r.json()["id"]

    # 3. Resolve the task ID from the index.
    r = client.get("/api/tasks?q=Fix+cache", headers={"Authorization": AUTH})
    tasks = r.json()["tasks"]
    task = next(t for t in tasks if t.get("task_uuid") == "T-PROP01")
    task_id = task["id"]

    # 4. PATCH status + eta.
    r = client.patch(
        f"/api/tasks/{task_id}",
        json={"status": "done", "eta": "2026-W20"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"

    # 5. The canonical note must reflect the patch.
    r = client.get(f"/api/notes/{canonical_note_id}", headers={"Authorization": AUTH})
    canonical_body = r.json()["body_md"]
    assert "#status done" in canonical_body
    assert "#eta 2026-W20" in canonical_body

    # 6. The weekly (ref-row) note must also be updated on disk.
    r = client.get(f"/api/notes/{ref_note_id}", headers={"Authorization": AUTH})
    ref_body = r.json()["body_md"]
    # Both ref rows updated.
    assert ref_body.count("#status done") == 2
    assert ref_body.count("#eta 2026-W20") == 2
    # Old status gone.
    assert "#status todo" not in ref_body
