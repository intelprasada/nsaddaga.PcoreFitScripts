"""Regression tests for #310: archived projects still appear in sidebar tree
and project dropdowns.

- ``GET /api/projects`` must omit projects with ``Project.archived == True``.
- ``GET /api/projects?include_archived=1`` must include them again.
- ``GET /api/tree`` must omit archived projects at the top level.
- ``GET /api/tree?include_archived=1`` must include them again.
- ``GET /api/tree`` must NOT silently resurrect Note rows for notes that
  belong to an archived project (whose task rows were evicted to
  ``archive.db``). This is the data-consistency regression called out in
  the issue: the archived project would otherwise be visible AND its
  eviction would be undone on the next tree render.
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select


DATA = Path(tempfile.mkdtemp(prefix="vega-310-hide-archived-"))
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


from app.models import Note, Project  # noqa: E402


def _put_note(c: TestClient, path: str, body: str):
    r = c.put(
        "/api/notes",
        json={"path": path, "body_md": body},
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _archive_project(c: TestClient, project: str):
    r = c.post(
        f"/api/projects/{project}/archive",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _project_names(c: TestClient, include_archived: bool = False) -> list[str]:
    url = "/api/projects" + ("?include_archived=1" if include_archived else "")
    r = c.get(url, headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200, r.text
    return [p["name"] for p in r.json()]


def _tree_project_names(c: TestClient, include_archived: bool = False) -> list[str]:
    url = "/api/tree" + ("?include_archived=1" if include_archived else "")
    r = c.get(url, headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200, r.text
    # Skip the loose "no project" bucket (project=None).
    return [n["project"] for n in r.json() if n.get("project") is not None]


def test_list_projects_hides_archived_projects(client):
    _put_note(client, "p310-alpha/w1.md", "# alpha\n!task Ship @a\n")
    _put_note(client, "p310-beta/w1.md", "# beta\n!task Plan @b\n")

    # Baseline: both visible.
    names = _project_names(client)
    assert "p310-alpha" in names
    assert "p310-beta" in names

    _archive_project(client, "p310-alpha")

    # Archived project drops out.
    names = _project_names(client)
    assert "p310-alpha" not in names
    assert "p310-beta" in names


def test_list_projects_include_archived_flag_surfaces_them(client):
    _put_note(client, "p310-gamma/w1.md", "# gamma\n!task Do @c\n")
    _archive_project(client, "p310-gamma")

    assert "p310-gamma" not in _project_names(client)
    assert "p310-gamma" in _project_names(client, include_archived=True)


def test_tree_hides_archived_projects(client):
    _put_note(client, "p310-delta/w1.md", "# delta\n!task Go @d\n")
    _put_note(client, "p310-epsilon/w1.md", "# eps\n!task Stay @e\n")

    names = _tree_project_names(client)
    assert "p310-delta" in names
    assert "p310-epsilon" in names

    _archive_project(client, "p310-delta")

    names = _tree_project_names(client)
    assert "p310-delta" not in names
    assert "p310-epsilon" in names


def test_tree_include_archived_flag_surfaces_them(client):
    _put_note(client, "p310-zeta/w1.md", "# zeta\n!task Yo @f\n")
    _archive_project(client, "p310-zeta")

    assert "p310-zeta" not in _tree_project_names(client)
    assert "p310-zeta" in _tree_project_names(client, include_archived=True)


def test_tree_does_not_resurrect_archived_project_notes(client):
    """Even though the .md file lives on disk, ``tree()`` must not lazily
    reindex it back into main.db when the project is archived. Otherwise
    the eviction performed by ``archive_notes()`` gets silently undone on
    every sidebar render.
    """
    project = "p310-eta"
    path = f"{project}/w1.md"
    _put_note(client, path, "# eta\n!task #id T-ETA0001 Ship @g\n")

    # Sanity: task row present in main pre-archive.
    with Session(_db_mod.get_engine()) as s:
        n = s.exec(select(Note).where(Note.path == path)).first()
        assert n is not None
        row = s.exec(
            text("SELECT COUNT(*) FROM task WHERE note_id = :i").bindparams(i=n.id)
        ).first()
        assert int(row[0]) == 1

    _archive_project(client, project)

    # Post-archive: Note.archived flag set; task rows evicted from main.
    with Session(_db_mod.get_engine()) as s:
        n = s.exec(select(Note).where(Note.path == path)).first()
        assert n is not None
        assert n.archived is True
        row = s.exec(
            text("SELECT COUNT(*) FROM task WHERE note_id = :i").bindparams(i=n.id)
        ).first()
        assert int(row[0]) == 0

    # Render the tree. Before the fix this would iterate the archived
    # project's files and call reindex_file, re-creating task rows.
    r = client.get("/api/tree", headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200
    assert project not in [n["project"] for n in r.json() if n.get("project")]

    # Confirm no resurrection happened.
    with Session(_db_mod.get_engine()) as s:
        n = s.exec(select(Note).where(Note.path == path)).first()
        assert n is not None
        row = s.exec(
            text("SELECT COUNT(*) FROM task WHERE note_id = :i").bindparams(i=n.id)
        ).first()
        assert int(row[0]) == 0, "tree() must not resurrect archived-project tasks"


def test_project_row_marked_archived_after_archive(client):
    """Guard against a schema drift regression: the archive_project
    endpoint must actually set ``Project.archived = True`` — the fix in
    ``list_projects`` / ``tree`` depends on this flag being persistent.
    """
    _put_note(client, "p310-theta/w1.md", "# theta\n!task Go @h\n")
    _archive_project(client, "p310-theta")
    with Session(_db_mod.get_engine()) as s:
        proj = s.exec(select(Project).where(Project.name == "p310-theta")).first()
        assert proj is not None
        assert proj.archived is True
