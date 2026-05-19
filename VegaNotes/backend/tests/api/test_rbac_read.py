"""Read-side RBAC tests (#230).

Verify that GET endpoints scope results to projects the caller belongs
to: non-admin users must not see notes / tasks / search hits / agenda
rows / features that live in projects where they have no
``ProjectMember`` row. Root-level (no top folder) notes remain visible
to everyone — that mirrors the write-side rule where
``_user_role_for_project(None) == 'manager'``.
"""
import base64
import os
import tempfile
from pathlib import Path

DATA = Path(tempfile.mkdtemp(prefix="vega-test-rbac-"))
os.environ.setdefault("VEGANOTES_DATA_DIR", str(DATA))
os.environ.setdefault("VEGANOTES_SERVE_STATIC", "false")

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import init_db
from app.main import app


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


@pytest.fixture(scope="module")
def world(client):
    """Two projects + one root-level note + two non-admin users:

    - ``insider`` is a member of project ``rbac_in`` only.
    - ``outsider`` is a member of project ``rbac_other`` only.
    """
    for u in ("insider", "outsider"):
        r = client.post(
            "/api/admin/users",
            json={"name": u, "password": "pw", "is_admin": False},
            headers={"Authorization": ADMIN},
        )
        assert r.status_code in (200, 201, 409), r.text

    for proj in ("rbac_in", "rbac_other"):
        r = client.post(
            "/api/projects",
            json={"name": proj},
            headers={"Authorization": ADMIN},
        )
        assert r.status_code in (200, 409), r.text

    client.put(
        "/api/projects/rbac_in/members",
        json={"user_name": "insider", "role": "member"},
        headers={"Authorization": ADMIN},
    )
    client.put(
        "/api/projects/rbac_other/members",
        json={"user_name": "outsider", "role": "member"},
        headers={"Authorization": ADMIN},
    )

    notes = {
        "rbac_in/secret.md": (
            "# RBAC In\n"
            "- !task Insider only secret_in_task #status todo #eta 2099-01-15\n"
        ),
        "rbac_other/elsewhere.md": (
            "# RBAC Other\n"
            "- !task Outsider only secret_other_task #status todo #eta 2099-01-15\n"
        ),
        "rbac_root_note.md": (
            "# Root\n"
            "- !task Root visible secret_root_task #status todo #eta 2099-01-15\n"
        ),
    }
    for path, body in notes.items():
        r = client.put(
            "/api/notes",
            json={"path": path, "body_md": body},
            headers={"Authorization": ADMIN},
        )
        assert r.status_code == 200, (path, r.text)

    INSIDER = "Basic " + base64.b64encode(b"insider:pw").decode()
    OUTSIDER = "Basic " + base64.b64encode(b"outsider:pw").decode()
    return {"insider": INSIDER, "outsider": OUTSIDER}


def _ids_by_path(client, headers):
    r = client.get("/api/notes", headers=headers)
    assert r.status_code == 200, r.text
    return {n["path"]: n["id"] for n in r.json()}


# ---------- /notes ----------------------------------------------------------

def test_list_notes_filters_by_project_membership(client, world):
    paths_admin = set(_ids_by_path(client, {"Authorization": ADMIN}))
    assert {"rbac_in/secret.md", "rbac_other/elsewhere.md",
            "rbac_root_note.md"} <= paths_admin

    paths_in = set(_ids_by_path(client, {"Authorization": world["insider"]}))
    assert "rbac_in/secret.md" in paths_in
    assert "rbac_root_note.md" in paths_in
    assert "rbac_other/elsewhere.md" not in paths_in

    paths_out = set(_ids_by_path(client, {"Authorization": world["outsider"]}))
    assert "rbac_other/elsewhere.md" in paths_out
    assert "rbac_root_note.md" in paths_out
    assert "rbac_in/secret.md" not in paths_out


def test_get_note_403_for_non_member(client, world):
    ids = _ids_by_path(client, {"Authorization": ADMIN})
    secret_in = ids["rbac_in/secret.md"]
    secret_other = ids["rbac_other/elsewhere.md"]
    root_id = ids["rbac_root_note.md"]

    # outsider can't read insider's note...
    r = client.get(f"/api/notes/{secret_in}",
                   headers={"Authorization": world["outsider"]})
    assert r.status_code == 403, r.text

    # ...but they can read their own and the root note.
    r = client.get(f"/api/notes/{secret_other}",
                   headers={"Authorization": world["outsider"]})
    assert r.status_code == 200
    r = client.get(f"/api/notes/{root_id}",
                   headers={"Authorization": world["outsider"]})
    assert r.status_code == 200


# ---------- /tasks ----------------------------------------------------------

def _task_titles(client, headers, **params):
    r = client.get("/api/tasks", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return {t["title"] for t in r.json()["tasks"]}


def test_list_tasks_filters_by_project_membership(client, world):
    insider_titles = _task_titles(client, {"Authorization": world["insider"]})
    assert "Insider only secret_in_task" in insider_titles
    assert "Root visible secret_root_task" in insider_titles
    assert "Outsider only secret_other_task" not in insider_titles

    outsider_titles = _task_titles(client, {"Authorization": world["outsider"]})
    assert "Outsider only secret_other_task" in outsider_titles
    assert "Root visible secret_root_task" in outsider_titles
    assert "Insider only secret_in_task" not in outsider_titles


def test_list_tasks_text_filter_still_hides_other_project(client, world):
    # Even when the search term *exactly* matches a task title in a
    # project the caller can't see, it must not be returned.
    titles = _task_titles(client, {"Authorization": world["insider"]},
                          q="secret_other_task")
    assert "Outsider only secret_other_task" not in titles


def test_get_task_403_for_non_member(client, world):
    r = client.get("/api/tasks?q=secret_in_task",
                   headers={"Authorization": ADMIN})
    tid = next(t["id"] for t in r.json()["tasks"]
               if t["title"] == "Insider only secret_in_task")

    r = client.get(f"/api/tasks/{tid}",
                   headers={"Authorization": world["outsider"]})
    assert r.status_code == 403, r.text

    r = client.get(f"/api/tasks/{tid}",
                   headers={"Authorization": world["insider"]})
    assert r.status_code == 200


# ---------- /agenda ---------------------------------------------------------

def test_agenda_filters_by_project_membership(client, world):
    # Wide enough window to catch the 2099 ETAs we authored above.
    params = {"start": "2099-01-01", "end": "2099-12-31"}

    r = client.get("/api/agenda", headers={"Authorization": world["insider"]},
                   params=params)
    assert r.status_code == 200, r.text
    titles = {t["title"] for day in r.json()["by_day"].values() for t in day}
    assert "Insider only secret_in_task" in titles
    assert "Root visible secret_root_task" in titles
    assert "Outsider only secret_other_task" not in titles


# ---------- /search ---------------------------------------------------------

def test_search_filters_by_project_membership(client, world):
    r = client.get("/api/search",
                   headers={"Authorization": world["outsider"]},
                   params={"q": "secret_in_task"})
    assert r.status_code == 200, r.text
    paths = {hit["path"] for hit in r.json()}
    assert "rbac_in/secret.md" not in paths

    r = client.get("/api/search",
                   headers={"Authorization": world["insider"]},
                   params={"q": "secret_in_task"})
    paths = {hit["path"] for hit in r.json()}
    assert "rbac_in/secret.md" in paths


# ---------- /cards/{ref}/links ---------------------------------------------

def test_card_links_403_for_non_member(client, world):
    r = client.get("/api/tasks?q=secret_in_task",
                   headers={"Authorization": ADMIN})
    tid = next(t["id"] for t in r.json()["tasks"]
               if t["title"] == "Insider only secret_in_task")

    r = client.get(f"/api/cards/{tid}/links",
                   headers={"Authorization": world["outsider"]})
    assert r.status_code == 403, r.text
    r = client.get(f"/api/cards/{tid}/links",
                   headers={"Authorization": world["insider"]})
    assert r.status_code == 200
