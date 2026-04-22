"""API tests for issue #60: optimistic-concurrency on PUT /api/notes."""
import base64
import os
import tempfile
from pathlib import Path

import pytest

# Must set the data dir env var BEFORE importing app.main so settings picks
# it up. Mirrors the pattern in test_api.py.
DATA = Path(tempfile.mkdtemp(prefix="vega-test-conc-"))
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


def _setup_note(client, path: str, body: str) -> str:
    """Create a project + an initial note. Returns the etag."""
    project = path.split("/", 1)[0]
    client.post("/api/projects", json={"name": project},
                headers={"Authorization": ADMIN})
    r = client.put("/api/notes",
                   json={"path": path, "body_md": body},
                   headers={"Authorization": ADMIN})
    assert r.status_code == 200, r.text
    return r.json()["etag"]


def test_put_returns_etag(client):
    etag = _setup_note(client, "concur1/plan.md", "# v1\n")
    assert isinstance(etag, str) and len(etag) == 64  # sha256 hex


def test_get_note_returns_etag(client):
    _setup_note(client, "concur2/plan.md", "# v1\n")
    # Discover the note id.
    notes = client.get("/api/notes",
                       headers={"Authorization": ADMIN}).json()
    nid = next(n["id"] for n in notes if n["path"] == "concur2/plan.md")
    r = client.get(f"/api/notes/{nid}", headers={"Authorization": ADMIN})
    assert r.status_code == 200
    j = r.json()
    assert "etag" in j and len(j["etag"]) == 64
    assert j["body_md"] == "# v1\n"


def test_put_with_matching_if_match_succeeds(client):
    e1 = _setup_note(client, "concur3/plan.md", "# v1\n")
    r = client.put("/api/notes",
                   json={"path": "concur3/plan.md",
                         "body_md": "# v2\n", "if_match": e1},
                   headers={"Authorization": ADMIN})
    assert r.status_code == 200, r.text
    assert r.json()["etag"] != e1


def test_put_with_stale_if_match_returns_409(client):
    e1 = _setup_note(client, "concur4/plan.md", "# v1 server\n")
    r = client.put("/api/notes",
                   json={"path": "concur4/plan.md",
                         "body_md": "# stale client write\n",
                         "if_match": "0" * 64},
                   headers={"Authorization": ADMIN})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "stale_write"
    assert detail["current_content"] == "# v1 server\n"
    assert detail["current_etag"] == e1
    # And the server file must NOT have been overwritten.
    notes = client.get("/api/notes",
                       headers={"Authorization": ADMIN}).json()
    nid = next(n["id"] for n in notes if n["path"] == "concur4/plan.md")
    g = client.get(f"/api/notes/{nid}", headers={"Authorization": ADMIN})
    assert g.json()["body_md"] == "# v1 server\n"


def test_put_with_if_match_via_header(client):
    e1 = _setup_note(client, "concur5/plan.md", "# v1\n")
    r = client.put("/api/notes",
                   json={"path": "concur5/plan.md", "body_md": "# v2\n"},
                   headers={"Authorization": ADMIN, "If-Match": e1})
    assert r.status_code == 200, r.text


def test_put_without_if_match_still_works_legacy(client):
    """Backwards compat: clients that don't send if_match keep working
    (today's frontend doesn't yet send it). The protection is opt-in
    until #60-followup ships the client-side support.
    """
    _setup_note(client, "concur6/plan.md", "# v1\n")
    r = client.put("/api/notes",
                   json={"path": "concur6/plan.md", "body_md": "# v2\n"},
                   headers={"Authorization": ADMIN})
    assert r.status_code == 200


def test_backup_created_on_overwrite(client):
    _setup_note(client, "concur7/plan.md", "# original\n")
    client.put("/api/notes",
               json={"path": "concur7/plan.md", "body_md": "# changed\n"},
               headers={"Authorization": ADMIN})
    backups = list((settings.notes_dir / ".trash" / "concur7").glob("plan.md.*.bak"))
    assert len(backups) >= 1
    # Most-recent backup must contain the pre-overwrite text.
    assert any(b.read_text() == "# original\n" for b in backups)
