"""Tests for #333 — PATCH /tasks/{ref} add_tag / remove_tag."""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


DATA = Path(tempfile.mkdtemp(prefix="vega-333-tags-"))
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


def _put(c, path, body):
    r = c.put("/api/notes", json={"path": path, "body_md": body}, headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200, r.text


def _get(c, ref):
    r = c.get(f"/api/tasks/{ref}", headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200, r.text
    return r.json()


def _patch(c, ref, patch):
    r = c.patch(f"/api/tasks/{ref}", json=patch, headers={"Authorization": AUTH_ADMIN})
    return r.status_code


def _read(rel):
    return (settings.notes_dir / rel).read_text(encoding="utf-8")


def _tags(task):
    """Keys of free-tag attrs (exclude the structured/reserved ones)."""
    reserved = {"id", "priority", "eta", "owner", "project", "feature",
                "status", "task", "ar", "link", "note", "url", "hsd", "jira", "pr", "progress"}
    return {k for k in (task.get("attrs") or {}) if k.lower() not in reserved}


def test_add_tag(client):
    path, ref = "t333-add/w1.md", "T-TAG001"
    _put(client, path, f"# t\n!task #id {ref} Ship @admin #status todo\n")
    assert _patch(client, ref, {"add_tag": "urgent"}) == 200
    assert "#urgent" in _read(path)
    assert "urgent" in _tags(_get(client, ref))


def test_remove_tag(client):
    path, ref = "t333-rm/w1.md", "T-TAG002"
    _put(client, path, f"# t\n!task #id {ref} Ship @admin #urgent #status todo\n")
    assert "urgent" in _tags(_get(client, ref))
    assert _patch(client, ref, {"remove_tag": {"key": "urgent"}}) == 200
    assert "#urgent" not in _read(path)
    assert "urgent" not in _tags(_get(client, ref))


def test_add_tag_rejects_reserved(client):
    path, ref = "t333-reserved/w1.md", "T-TAG003"
    _put(client, path, f"# t\n!task #id {ref} Ship @admin\n")
    assert _patch(client, ref, {"add_tag": "status"}) == 400


def test_add_tag_rejects_invalid(client):
    path, ref = "t333-invalid/w1.md", "T-TAG004"
    _put(client, path, f"# t\n!task #id {ref} Ship @admin\n")
    assert _patch(client, ref, {"add_tag": "two words"}) == 400


def test_add_tag_idempotent(client):
    path, ref = "t333-idem/w1.md", "T-TAG005"
    _put(client, path, f"# t\n!task #id {ref} Ship @admin\n")
    assert _patch(client, ref, {"add_tag": "hot"}) == 200
    assert _patch(client, ref, {"add_tag": "hot"}) == 200
    # exactly one occurrence
    assert _read(path).count("#hot") == 1


def test_tag_ref_row_propagation(client):
    canon, ref = "t333-ref/canon.md", "T-TAG006"
    weekly = "t333-ref/weekly.md"
    _put(client, canon, f"# canon\n!task #id {ref} Ship @admin\n")
    _put(client, weekly, f"# weekly\n- #task {ref} Ship @admin\n")
    assert _patch(client, ref, {"add_tag": "blocker"}) == 200
    assert "#blocker" in _read(weekly)
    assert _patch(client, ref, {"remove_tag": {"key": "blocker"}}) == 200
    assert "#blocker" not in _read(weekly)
