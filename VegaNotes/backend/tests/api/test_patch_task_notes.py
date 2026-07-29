"""Tests for #333 — PATCH /tasks/{ref} edit_note / delete_note.

Confirms notes become first-class (editable + deletable) like ARs:
- add several notes, then edit one by index (with `expect` guard).
- delete one by index; the others and their order survive.
- `expect` mismatch -> 409 (never touches the wrong note).
- out-of-range index -> 404.
- note_history in the index reflects the mutation after reindex.
- ref-row propagation: editing/deleting the canonical note rewrites the
  cross-file `#task` ref row's mirrored note by text match.
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


DATA = Path(tempfile.mkdtemp(prefix="vega-333-notes-"))
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


def _put_note(c, path, body):
    r = c.put("/api/notes", json={"path": path, "body_md": body},
              headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200, r.text


def _get_task(c, ref):
    r = c.get(f"/api/tasks/{ref}", headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200, r.text
    return r.json()


def _patch(c, ref, patch):
    r = c.patch(f"/api/tasks/{ref}", json=patch, headers={"Authorization": AUTH_ADMIN})
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else {})


def _read(rel):
    return (settings.notes_dir / rel).read_text(encoding="utf-8")


def _seed_three_notes(c, path, ref):
    _put_note(c, path, f"# t\n!task #id {ref} Ship @admin\n")
    for n in ("first note", "second note", "third note"):
        st, _ = _patch(c, ref, {"add_note": n})
        assert st == 200
    hist = _get_task(c, ref)["note_history"]
    assert hist == ["first note", "second note", "third note"], hist


def test_edit_note_by_index(client):
    path, ref = "n333-edit/w1.md", "T-NOTE001"
    _seed_three_notes(client, path, ref)
    st, _ = _patch(client, ref, {"edit_note": {"index": 1, "text": "SECOND edited", "expect": "second note"}})
    assert st == 200
    assert _get_task(client, ref)["note_history"] == ["first note", "SECOND edited", "third note"]
    disk = _read(path)
    assert "#note SECOND edited" in disk
    assert "#note second note" not in disk


def test_delete_note_by_index(client):
    path, ref = "n333-del/w1.md", "T-NOTE002"
    _seed_three_notes(client, path, ref)
    st, _ = _patch(client, ref, {"delete_note": {"index": 0, "expect": "first note"}})
    assert st == 200
    assert _get_task(client, ref)["note_history"] == ["second note", "third note"]
    assert "#note first note" not in _read(path)


def test_expect_mismatch_returns_409(client):
    path, ref = "n333-conflict/w1.md", "T-NOTE003"
    _seed_three_notes(client, path, ref)
    st, _ = _patch(client, ref, {"edit_note": {"index": 0, "text": "x", "expect": "WRONG"}})
    assert st == 409
    st, _ = _patch(client, ref, {"delete_note": {"index": 0, "expect": "WRONG"}})
    assert st == 409
    # unchanged
    assert _get_task(client, ref)["note_history"] == ["first note", "second note", "third note"]


def test_out_of_range_index_returns_404(client):
    path, ref = "n333-oor/w1.md", "T-NOTE004"
    _seed_three_notes(client, path, ref)
    st, _ = _patch(client, ref, {"delete_note": {"index": 9, "expect": "first note"}})
    assert st == 404


def test_edit_note_empty_text_rejected(client):
    path, ref = "n333-empty/w1.md", "T-NOTE005"
    _seed_three_notes(client, path, ref)
    st, _ = _patch(client, ref, {"edit_note": {"index": 0, "text": "  ", "expect": "first note"}})
    assert st == 400


def test_edit_note_ar_guardrail(client):
    path, ref = "n333-guard/w1.md", "T-NOTE006"
    _seed_three_notes(client, path, ref)
    st, _ = _patch(client, ref, {"edit_note": {"index": 0, "text": "!AR sneaky", "expect": "first note"}})
    assert st == 400


def test_ref_row_note_propagation(client):
    """Editing then deleting the canonical note rewrites the cross-file ref row."""
    canon, ref = "n333-ref/canon.md", "T-NOTE007"
    weekly = "n333-ref/weekly.md"
    _put_note(client, canon, f"# canon\n!task #id {ref} Ship @admin\n")
    _patch(client, ref, {"add_note": "shared note"})
    # A weekly note that references the task and mirrors the note.
    _put_note(client, weekly, f"# weekly\n- #task {ref} Ship @admin\n\t#note shared note\n")

    st, _ = _patch(client, ref, {"edit_note": {"index": 0, "text": "shared note v2", "expect": "shared note"}})
    assert st == 200
    assert "#note shared note v2" in _read(weekly)
    assert "#note shared note\n" not in _read(weekly)

    st, _ = _patch(client, ref, {"delete_note": {"index": 0, "expect": "shared note v2"}})
    assert st == 200
    assert "#note shared note v2" not in _read(weekly)
