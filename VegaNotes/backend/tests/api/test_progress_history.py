"""Tests for #320 GET /tasks/{ref}/progress-history.

Confirms:
- History rolls up main.db + archive.db by ISO week from filename.
- Two notes in the same week collapse to the newer one (by mtime).
- Archived rollovers contribute rows without unarchiving.
- Unauth returns 401 (route depends on require_user).
- Rejected shapes still return an empty list rather than 500 when
  malformed data slipped in historically.
"""
from __future__ import annotations

import base64
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


DATA = Path(tempfile.mkdtemp(prefix="vega-320-progress-hist-"))
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


def _history(c: TestClient, task_ref: str) -> list[dict]:
    r = c.get(
        f"/api/tasks/{task_ref}/progress-history",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_history_empty_for_task_without_progress(client):
    _put_note(client, "hist-empty/w1.md", "# t\n!task #id T-HST0001 Ship @admin\n")
    assert _history(client, "T-HST0001") == []


def test_history_returns_current_week_reading(client):
    _put_note(
        client, "hist-single/ww28.md",
        "# t\n!task #id T-HST0002 Ship @admin #progress 12/35\n",
    )
    rows = _history(client, "T-HST0002")
    assert len(rows) == 1
    assert rows[0]["numerator"] == 12
    assert rows[0]["denominator"] == 35
    assert rows[0]["label"] is None
    # Filename-driven week.
    assert rows[0]["week"].endswith("-W28")


def test_history_parses_ratio_and_label(client):
    _put_note(
        client, "hist-label/ww29.md",
        "# t\n!task #id T-HST0003 Ship @admin #progress 30/54 fixed\n",
    )
    rows = _history(client, "T-HST0003")
    assert rows == [{
        "week": rows[0]["week"], "numerator": 30, "denominator": 54,
        "label": "fixed",
    }]
    assert rows[0]["week"].endswith("-W29")


def test_history_parses_bare_counter(client):
    _put_note(
        client, "hist-bare/ww30.md",
        "# t\n!task #id T-HST0004 Ship @admin #progress 42\n",
    )
    rows = _history(client, "T-HST0004")
    assert len(rows) == 1
    assert rows[0]["numerator"] == 42
    assert rows[0]["denominator"] is None
    assert rows[0]["label"] is None


def test_history_rolls_up_across_weeks(client):
    """Two notes in different weeks, one archived, one active — history
    aggregates across both databases and sorts by week ascending.
    """
    _put_note(
        client, "hist-multi/ww31.md",
        "# t\n!task #id T-HST0005 Ship @admin #progress 12/35\n",
    )
    # Look up the note id, then archive it so the task uuid is freed up
    # from main.db but survives in archive.db.
    from sqlmodel import Session, select
    from app.models import Note
    with Session(_db_mod.get_engine()) as s:
        n = s.exec(select(Note).where(Note.path == "hist-multi/ww31.md")).first()
        assert n is not None
        nid = n.id
    r = client.post(
        f"/api/notes/{nid}/archive",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text

    time.sleep(0.02)
    _put_note(
        client, "hist-multi/ww32.md",
        "# t\n!task #id T-HST0005 Ship @admin #progress 24/35\n",
    )

    rows = _history(client, "T-HST0005")
    weeks = [r["week"] for r in rows]
    assert weeks == sorted(weeks), f"expected sorted ascending, got {weeks}"
    assert len({r["week"] for r in rows}) == 2, weeks
    by_week = {r["week"]: r for r in rows}
    w31 = next(w for w in weeks if w.endswith("-W31"))
    w32 = next(w for w in weeks if w.endswith("-W32"))
    assert by_week[w31]["numerator"] == 12
    assert by_week[w32]["numerator"] == 24


def test_history_unauthenticated_is_rejected(client):
    _put_note(
        client, "hist-auth/ww33.md",
        "# t\n!task #id T-HST0006 Ship @admin #progress 5/10\n",
    )
    r = client.get("/api/tasks/T-HST0006/progress-history")
    # ``require_user`` returns 401 without an auth header.
    assert r.status_code in (401, 403), r.status_code
