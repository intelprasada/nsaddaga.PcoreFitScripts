"""API tests for the Focus of the Week endpoints (#266).

The focus is a free-form team / project goal of the week stored as a
single markdown file at ``<notes_dir>/_meta/focus.md``. These tests
exercise the GET / PUT pair end-to-end and assert the indexer skip
behaviour (the file must NOT appear as a Note row).
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DATA = Path(tempfile.mkdtemp(prefix="vega-test-focus-"))
os.environ["VEGANOTES_DATA_DIR"] = str(DATA)
os.environ["VEGANOTES_SERVE_STATIC"] = "false"

from app.main import app  # noqa: E402

ADMIN = {"Authorization": "Basic " + base64.b64encode(b"admin:admin").decode()}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    shutil.rmtree(DATA, ignore_errors=True)


def test_get_returns_404_when_unset(client):
    # Brand-new install: no focus file yet.
    r = client.get("/api/focus-week", headers=ADMIN)
    assert r.status_code == 404


def test_put_then_get_round_trip(client):
    md = "Ship the WW27 RTL freeze. **All P0 owners** post a cover-closure plan by Wed EOD."
    r = client.put("/api/focus-week", headers=ADMIN, json={"markdown": md})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["markdown"] == md
    assert out["path"] == "_meta/focus.md"
    assert out["updated_at"]

    r = client.get("/api/focus-week", headers=ADMIN)
    assert r.status_code == 200
    got = r.json()
    assert got["markdown"] == md
    assert got["path"] == "_meta/focus.md"


def test_put_empty_deletes_file(client):
    # Seed first
    client.put("/api/focus-week", headers=ADMIN, json={"markdown": "to be cleared"})

    # Empty / whitespace-only payload clears the banner by deleting the file.
    r = client.put("/api/focus-week", headers=ADMIN, json={"markdown": "   \n   "})
    assert r.status_code == 200
    assert r.json()["markdown"] == ""

    r = client.get("/api/focus-week", headers=ADMIN)
    assert r.status_code == 404


def test_meta_file_not_indexed_as_note(client):
    # Seed a focus file.
    client.put(
        "/api/focus-week", headers=ADMIN,
        json={"markdown": "We do not want this surfaced as a note row."},
    )
    # Force a full reindex pass — _meta/ must be skipped.
    r = client.post("/api/admin/reindex", headers=ADMIN)
    assert r.status_code == 200

    # /api/notes (and the tree) must NOT list the focus file.
    notes = client.get("/api/notes", headers=ADMIN).json()
    paths = [n["path"] for n in notes]
    assert "_meta/focus.md" not in paths
    assert not any(p.startswith("_meta/") for p in paths)

    tree = client.get("/api/tree", headers=ADMIN).json()
    # tree is a nested folder/file structure; serialize to flat string blob
    # and assert the meta segment doesn't appear.
    import json as _json
    assert "_meta" not in _json.dumps(tree)


def test_put_requires_admin(client):
    # Create a non-admin user (they won't have is_admin=True) and try PUT.
    # The admin/users endpoint requires admin so we set it up as admin first.
    client.post(
        "/api/admin/users", headers=ADMIN,
        json={"name": "viewer", "password": "viewerpass1!", "is_admin": False},
    )
    viewer = {
        "Authorization": "Basic " + base64.b64encode(b"viewer:viewerpass1!").decode(),
    }

    # GET is allowed for any authenticated user.
    r = client.get("/api/focus-week", headers=viewer)
    assert r.status_code in (200, 404)

    # PUT must be rejected with 403.
    r = client.put(
        "/api/focus-week", headers=viewer,
        json={"markdown": "non-admin trying to edit"},
    )
    assert r.status_code == 403
