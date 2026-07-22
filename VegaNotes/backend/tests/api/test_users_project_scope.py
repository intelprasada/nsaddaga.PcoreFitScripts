"""Regression tests for #312: /api/users project scope + archive refresh.

Confirms:
- ``GET /api/users`` (default) returns every User row.
- ``GET /api/users?with_display=1`` returns only users with active tasks
  (JOIN on TaskOwner already excludes users with 0 tasks in main.db).
- ``GET /api/users?project=<name>`` restricts to users with tasks in
  that project — even when they own tasks in other projects.
- Both flags composable: ``?with_display=1&project=<name>``.
- After archiving a project, its users drop from the project-scoped
  list AND drop from the with_display list if they own no other tasks
  anywhere (because archive.db is a separate engine).
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


DATA = Path(tempfile.mkdtemp(prefix="vega-312-users-project-"))
os.environ["VEGANOTES_DATA_DIR"] = str(DATA)
os.environ["VEGANOTES_SERVE_STATIC"] = "false"

from app.main import app  # noqa: E402
from app.config import settings  # noqa: E402
import app.db as _db_mod  # noqa: E402


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


def _get(c: TestClient, url: str):
    r = c.get(url, headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200, r.text
    return r.json()


def _users(c: TestClient, project: str | None = None) -> list[str]:
    url = "/api/users"
    if project:
        url += f"?project={project}"
    return _get(c, url)


def _users_display(c: TestClient, project: str | None = None) -> list[str]:
    url = "/api/users?with_display=1"
    if project:
        url += f"&project={project}"
    return [row["name"] for row in _get(c, url)]


def test_users_default_returns_all_user_rows(client):
    _put_note(client, "p312a-alpha/w1.md", "# a\n!task Ship @u312a\n")
    all_users = _users(client)
    assert "u312a" in all_users


def test_users_with_display_excludes_users_with_zero_tasks(client):
    """The JOIN on TaskOwner naturally drops users whose only tasks were
    archived (moved to archive.db, which this endpoint doesn't consult).
    """
    _put_note(client, "p312b/w1.md", "# b\n!task #id T-U312B0001 Do @u312b\n")

    disp = _users_display(client)
    assert "u312b" in disp, "user with an active task must appear"

    # Archive the project -> u312b's only task moves to archive.db.
    r = client.post(
        "/api/projects/p312b/archive",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text

    disp = _users_display(client)
    assert "u312b" not in disp, (
        "user whose only active task was archived must drop from "
        "the with_display list"
    )


def test_users_project_scope_restricts_to_that_project(client):
    """A user who owns tasks in project X but not project Y must appear
    only in ``?project=X`` and not in ``?project=Y``.
    """
    _put_note(client, "p312c-x/w1.md", "# x\n!task Do @u312c\n")
    _put_note(client, "p312c-y/w1.md", "# y\n!task Do @u312d\n")

    x_users = _users_display(client, project="p312c-x")
    y_users = _users_display(client, project="p312c-y")

    assert "u312c" in x_users
    assert "u312c" not in y_users
    assert "u312d" in y_users
    assert "u312d" not in x_users


def test_users_project_scope_without_display_flag(client):
    """The ``project`` param must also work when ``with_display`` is
    unset (returns plain list of names).
    """
    _put_note(client, "p312e/w1.md", "# e\n!task Go @u312e\n")

    names = _users(client, project="p312e")
    assert "u312e" in names

    other = _users(client, project="p312c-x")  # from previous test
    assert "u312e" not in other


def test_users_project_scope_drops_owners_of_archived_project(client):
    """Real end-to-end: create a user's only task inside project P,
    archive P, and confirm the user disappears from every /users query
    that has visibility of P.
    """
    _put_note(client, "p312f/w1.md", "# f\n!task #id T-U312F0001 Ship @u312f\n")
    assert "u312f" in _users_display(client)
    assert "u312f" in _users_display(client, project="p312f")

    r = client.post(
        "/api/projects/p312f/archive",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text

    assert "u312f" not in _users_display(client)
    # Project-scoped query still works (returns empty list, no error).
    assert "u312f" not in _users_display(client, project="p312f")


def test_users_project_scope_survives_user_owning_tasks_in_two_projects(client):
    """Guard: a user who owns tasks in both P and Q must remain visible
    in the with_display list when P is archived (still has a task in Q).
    """
    _put_note(client, "p312g/w1.md", "# g\n!task Do @u312g\n")
    _put_note(client, "p312h/w1.md", "# h\n!task Do @u312g\n")

    assert "u312g" in _users_display(client)

    r = client.post(
        "/api/projects/p312g/archive",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200

    # Global list still includes u312g because Q's task keeps them active.
    assert "u312g" in _users_display(client)
    # But p312g scope no longer includes them (its tasks are gone).
    assert "u312g" not in _users_display(client, project="p312g")
    # p312h scope still includes them.
    assert "u312g" in _users_display(client, project="p312h")
