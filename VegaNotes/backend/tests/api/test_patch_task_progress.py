"""Tests for #320 PATCH /tasks/{ref} with the #progress token.

Confirms:
- PATCH sets #progress from empty (N, N/D, N/D label) shapes.
- PATCH replaces an existing value cleanly (label doesn't leak).
- Empty string clears the token from markdown + index.
- Denominator == 0 rejected with 400.
- Non-numeric garbage rejected with 400.
- Ref-row propagation: patching the canonical decl rewrites cross-file
  ``#task`` refs.
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


DATA = Path(tempfile.mkdtemp(prefix="vega-320-progress-patch-"))
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


def _get_task(c: TestClient, task_ref) -> dict:
    r = c.get(f"/api/tasks/{task_ref}", headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200, r.text
    return r.json()


def _patch(c: TestClient, task_ref, patch: dict) -> tuple[int, dict]:
    r = c.patch(
        f"/api/tasks/{task_ref}",
        json=patch,
        headers={"Authorization": AUTH_ADMIN},
    )
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else {})


def _read_disk(rel_path: str) -> str:
    return (settings.notes_dir / rel_path).read_text(encoding="utf-8")


def test_patch_adds_bare_counter(client):
    path = "p320-add/w1.md"
    _put_note(client, path, "# t\n!task #id T-PRG0001 Ship @admin\n")
    status, _ = _patch(client, "T-PRG0001", {"progress": "42"})
    assert status == 200
    assert "#progress 42" in _read_disk(path)
    assert _get_task(client, "T-PRG0001")["attrs"].get("progress") == "42"


def test_patch_adds_ratio(client):
    path = "p320-ratio/w1.md"
    _put_note(client, path, "# t\n!task #id T-PRG0002 Ship @admin\n")
    status, _ = _patch(client, "T-PRG0002", {"progress": "12/35"})
    assert status == 200
    assert "#progress 12/35" in _read_disk(path)
    assert _get_task(client, "T-PRG0002")["attrs"].get("progress") == "12/35"


def test_patch_adds_ratio_with_label(client):
    path = "p320-label/w1.md"
    _put_note(client, path, "# t\n!task #id T-PRG0003 Ship @admin\n")
    status, _ = _patch(client, "T-PRG0003", {"progress": "30/54 fixed"})
    assert status == 200
    assert "#progress 30/54 fixed" in _read_disk(path)
    assert _get_task(client, "T-PRG0003")["attrs"].get("progress") == "30/54 fixed"


def test_patch_replaces_ratio_without_label_leak(client):
    """Regression guard for the multi-word ``remove_attr`` leak bug:
    replacing ``30/54 fixed`` with ``50/54 fixed`` must not leave the
    old ``fixed`` word dangling."""
    path = "p320-replace/w1.md"
    _put_note(
        client, path,
        "# t\n!task #id T-PRG0004 Ship @admin #progress 30/54 fixed\n",
    )
    status, _ = _patch(client, "T-PRG0004", {"progress": "50/54 fixed"})
    assert status == 200
    line = _read_disk(path).splitlines()[1]
    assert "#progress 50/54 fixed" in line
    # Only one occurrence, no stray token.
    assert line.count("fixed") == 1


def test_patch_replace_narrowing_label_strips_old_word(client):
    """Replacing a labelled progress with a bare ratio must strip the
    old label word entirely."""
    path = "p320-narrow/w1.md"
    _put_note(
        client, path,
        "# t\n!task #id T-PRG0005 Ship @admin #progress 30/54 fixed\n",
    )
    status, _ = _patch(client, "T-PRG0005", {"progress": "50/54"})
    assert status == 200
    line = _read_disk(path).splitlines()[1]
    assert "#progress 50/54" in line
    assert "fixed" not in line


def test_patch_empty_string_clears_progress(client):
    path = "p320-clear/w1.md"
    _put_note(
        client, path,
        "# t\n!task #id T-PRG0006 Ship @admin #progress 30/54 fixed\n",
    )
    status, _ = _patch(client, "T-PRG0006", {"progress": ""})
    assert status == 200
    line = _read_disk(path).splitlines()[1]
    assert "#progress" not in line
    # The label word must not survive as orphan prose.
    assert "fixed" not in line
    j = _get_task(client, "T-PRG0006")
    assert j["attrs"].get("progress") in (None, "")


def test_patch_rejects_zero_denominator(client):
    path = "p320-badzero/w1.md"
    _put_note(client, path, "# t\n!task #id T-PRG0007 Ship @admin\n")
    status, body = _patch(client, "T-PRG0007", {"progress": "12/0"})
    assert status == 400, body


def test_patch_rejects_non_numeric(client):
    path = "p320-badgarbage/w1.md"
    _put_note(client, path, "# t\n!task #id T-PRG0008 Ship @admin\n")
    for bad in ["abc", "12/xx", "12/35/7", "-5/10", "12/35 two words"]:
        status, _ = _patch(client, "T-PRG0008", {"progress": bad})
        assert status == 400, f"expected 400 for {bad!r}"


def test_patch_propagates_progress_into_ref_rows(client):
    """A ``#task <ref>`` reference in another file should be rewritten
    with the new progress value on PATCH (mirrors #hsd behavior)."""
    canon = "p320-refprop/canon.md"
    ref = "p320-refprop/ref.md"
    _put_note(client, canon, "# c\n!task #id T-PRG0009 Ship @admin\n")
    _put_note(client, ref, "# r\n#task T-PRG0009 Ship @admin\n")

    status, _ = _patch(client, "T-PRG0009", {"progress": "12/35"})
    assert status == 200

    ref_md = _read_disk(ref)
    assert "#progress 12/35" in ref_md
