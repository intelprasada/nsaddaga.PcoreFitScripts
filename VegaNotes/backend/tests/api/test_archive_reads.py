"""Integration tests for #304 PR 4: archive read endpoints.

- GET /api/archive/notes                — list user-archived notes
- GET /api/archive/notes/{note_id}      — details + task rows
- GET /api/archive/tasks                — filtered flat list
- GET /api/archive/tasks/{task_uuid}    — single task by uuid
- GET /api/archive/projects             — projects flagged archived
- GET /api/archive/summary              — aggregates
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

DATA = Path(tempfile.mkdtemp(prefix="vega-archive-reads-"))
os.environ["VEGANOTES_DATA_DIR"] = str(DATA)
os.environ["VEGANOTES_SERVE_STATIC"] = "false"

from app.main import app  # noqa: E402
from app.config import settings  # noqa: E402
import app.db as _db_mod  # noqa: E402
from app.models import Note, Project  # noqa: E402


AUTH_ADMIN = "Basic " + base64.b64encode(b"admin:admin").decode()


@pytest.fixture(scope="module")
def client():
    saved_data_dir = settings.data_dir
    saved_engine = _db_mod._engine
    saved_archive_engine = _db_mod._archive_engine

    settings.data_dir = DATA
    _db_mod._engine = None
    _db_mod._archive_engine = None
    _db_mod.init_db()

    with TestClient(app) as c:
        yield c

    settings.data_dir = saved_data_dir
    _db_mod._engine = saved_engine
    _db_mod._archive_engine = saved_archive_engine
    shutil.rmtree(DATA, ignore_errors=True)


def _put_note(c: TestClient, path: str, body: str):
    r = c.put(
        "/api/notes",
        json={"path": path, "body_md": body},
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _archive_note(c: TestClient, note_id: int):
    r = c.post(
        f"/api/notes/{note_id}/archive",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _find_note_id(path: str) -> int:
    with Session(_db_mod.get_engine()) as s:
        n = s.exec(select(Note).where(Note.path == path)).first()
        assert n is not None
        return n.id


def test_list_archived_notes_empty_initially(client):
    r = client.get("/api/archive/notes", headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200
    # There may be leftovers from other tests using same DATA — we just
    # want to be sure the endpoint is reachable + shape is correct.
    assert isinstance(r.json(), list)


def test_archive_and_list_shows_note(client):
    _put_note(
        client, "read-p1/w1.md",
        "# W1\n!task alpha #owner admin !p !high\n"
        "!task beta #owner admin\n",
    )
    nid = _find_note_id("read-p1/w1.md")
    _archive_note(client, nid)

    r = client.get("/api/archive/notes", headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200
    items = r.json()
    paths = {it["path"]: it for it in items}
    assert "read-p1/w1.md" in paths
    entry = paths["read-p1/w1.md"]
    assert entry["project"] == "read-p1"
    assert entry["task_count"] == 2


def test_get_archived_note_returns_tasks(client):
    _put_note(
        client, "read-p2/w2.md",
        "# W2\n!task uno #owner admin\n!task dos #owner admin !p !low\n",
    )
    nid = _find_note_id("read-p2/w2.md")
    _archive_note(client, nid)

    r = client.get(
        f"/api/archive/notes/{nid}",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["path"] == "read-p2/w2.md"
    assert data["project"] == "read-p2"
    assert len(data["tasks"]) == 2
    titles = {t["title"] for t in data["tasks"]}
    assert titles == {"uno", "dos"}
    # Owners resolved via main-db user table.
    for t in data["tasks"]:
        assert "admin" in t["owners"]


def test_list_archived_tasks_filters(client):
    _put_note(
        client, "read-p3/w3.md",
        "# W3\n"
        "!task filter-me #owner admin !p !high\n"
        "!task other-priority #owner admin !p !low\n"
        "!task done-one #owner admin #status done\n",
    )
    nid = _find_note_id("read-p3/w3.md")
    _archive_note(client, nid)

    r = client.get(
        "/api/archive/tasks?project=read-p3",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 3
    titles = {t["title"] for t in data["tasks"]}
    assert {"filter-me", "other-priority", "done-one"}.issubset(titles)

    r = client.get(
        "/api/archive/tasks?project=read-p3&status=done",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200
    d2 = r.json()
    assert all(t["status"] == "done" for t in d2["tasks"])
    assert d2["total"] >= 1

    r = client.get(
        "/api/archive/tasks?project=read-p3&owner=admin",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200
    assert r.json()["total"] >= 3


def test_get_archived_task_by_uuid(client):
    _put_note(
        client, "read-p4/w4.md",
        "# W4\n!task #id T-RRR0001 tracked-one #owner admin\n",
    )
    nid = _find_note_id("read-p4/w4.md")
    _archive_note(client, nid)

    r = client.get(
        f"/api/archive/tasks/T-RRR0001",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["task_uuid"] == "T-RRR0001"
    assert data["title"] == "tracked-one"
    assert "admin" in data["owners"]


def test_archived_projects_list(client):
    _put_note(
        client, "read-p5/w5.md",
        "# W5\n!task first #owner admin\n",
    )
    # Archive at project level.
    r = client.post(
        "/api/projects/read-p5/archive",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text

    r = client.get(
        "/api/archive/projects",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200
    items = {it["name"]: it for it in r.json()}
    assert "read-p5" in items
    entry = items["read-p5"]
    assert entry["archived"] is True
    assert entry["task_count"] >= 1
    assert entry["note_count"] >= 1


def test_archive_summary(client):
    _put_note(
        client, "read-p6/w6.md",
        "# W6\n"
        "!task todo-a #owner admin\n"
        "!task todo-b #owner admin\n"
        "!task done-a #owner admin #status done\n",
    )
    nid = _find_note_id("read-p6/w6.md")
    _archive_note(client, nid)

    r = client.get(
        "/api/archive/summary?project=read-p6",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total_tasks"] >= 3
    assert data["by_status"].get("todo", 0) >= 2
    assert data["by_status"].get("done", 0) >= 1
    assert data["by_project"].get("read-p6", 0) >= 3
    owners = {o["name"]: o["count"] for o in data["top_owners"]}
    assert owners.get("admin", 0) >= 3


def test_archived_note_404_when_not_archived(client):
    _put_note(client, "read-p7/live.md", "# Live\n!task x #owner admin\n")
    nid = _find_note_id("read-p7/live.md")
    r = client.get(
        f"/api/archive/notes/{nid}",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 404
