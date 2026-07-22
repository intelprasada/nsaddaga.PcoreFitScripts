"""Integration tests for #304 PR 2: archive/unarchive endpoints.

Covers:
- POST /notes/{id}/archive evicts task rows from main into archive.db.
- POST /notes/{id}/unarchive reindexes from disk; identical task_uuid,
  archive.db rows removed.
- Rollover archives (`/_archive/` + archive_kind '') are refused (409).
- Project-level archive cascades to member notes and skips rollovers.
- RBAC: members without manager role get 403.
- Reconcile: crash simulation (archive.db has task, main.db still has
  same task_uuid) → orphan dropped.
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


# When another test module has already initialized ``settings`` +
# engines against its own tempdir, we need to point them at OURS for
# the duration of this module's tests only — then restore original
# state at teardown so subsequent modules still work.
DATA = Path(tempfile.mkdtemp(prefix="vega-archive-endpoints-"))
os.environ["VEGANOTES_DATA_DIR"] = str(DATA)
os.environ["VEGANOTES_SERVE_STATIC"] = "false"

from app.main import app  # noqa: E402
from app.config import settings  # noqa: E402
import app.db as _db_mod  # noqa: E402


AUTH_ADMIN = "Basic " + base64.b64encode(b"admin:admin").decode()


@pytest.fixture(scope="module")
def client():
    # Snapshot whatever settings/engine state the previous test module
    # left behind so we can restore it on teardown.
    saved_data_dir = settings.data_dir
    saved_engine = _db_mod._engine
    saved_archive_engine = _db_mod._archive_engine

    settings.data_dir = DATA
    _db_mod._engine = None
    _db_mod._archive_engine = None
    _db_mod.init_db()

    with TestClient(app) as c:
        yield c

    # Restore for other test modules.
    settings.data_dir = saved_data_dir
    _db_mod._engine = saved_engine
    _db_mod._archive_engine = saved_archive_engine
    shutil.rmtree(DATA, ignore_errors=True)


def _get_engine():
    return _db_mod.get_engine()


def _get_archive_engine():
    return _db_mod.get_archive_engine()


from app.models import Note, Task  # noqa: E402


def _put_note(c: TestClient, path: str, body: str, auth: str = AUTH_ADMIN):
    r = c.put(
        "/api/notes",
        json={"path": path, "body_md": body},
        headers={"Authorization": auth},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _get_note(c: TestClient, note_id: int, auth: str = AUTH_ADMIN):
    r = c.get(f"/api/notes/{note_id}", headers={"Authorization": auth})
    assert r.status_code == 200, r.text
    return r.json()


def _find_note_by_path(path: str) -> Note | None:
    with Session(_get_engine()) as s:
        return s.exec(select(Note).where(Note.path == path)).first()


def _count_main_tasks_for_path(path: str) -> int:
    with Session(_get_engine()) as s:
        n = s.exec(select(Note).where(Note.path == path)).first()
        if n is None:
            return 0
        row = s.exec(
            text("SELECT COUNT(*) FROM task WHERE note_id = :i").bindparams(i=n.id)
        ).first()
        return int(row[0]) if row else 0


def _count_archive_tasks_for_path(path: str) -> int:
    with Session(_get_archive_engine()) as s:
        n = s.exec(select(Note).where(Note.path == path)).first()
        if n is None:
            return 0
        row = s.exec(
            text("SELECT COUNT(*) FROM task WHERE note_id = :i").bindparams(i=n.id)
        ).first()
        return int(row[0]) if row else 0


def _task_uuids_main(path: str) -> set[str]:
    with Session(_get_engine()) as s:
        n = s.exec(select(Note).where(Note.path == path)).first()
        if n is None:
            return set()
        return {
            r[0] for r in s.exec(
                text("SELECT task_uuid FROM task WHERE note_id = :i "
                     "AND task_uuid IS NOT NULL").bindparams(i=n.id)
            ).all()
        }


# ---------------------------------------------------------------------------
# Round-trip: archive → unarchive
# ---------------------------------------------------------------------------

def test_archive_note_evicts_tasks_from_main_into_archive(client):
    project = "arch-p1"
    path = f"{project}/w1.md"
    body = (
        "# W1\n"
        "!task #id T-AAA0001 Ship v1 @alice #priority p1\n"
        "\t!AR #id T-AAA0002 Follow up @bob\n"
        "!task #id T-AAA0003 Ship v2 @alice\n"
    )
    _put_note(client, path, body)

    # Baseline: 3 task rows in main; 0 in archive.
    assert _count_main_tasks_for_path(path) == 3
    assert _count_archive_tasks_for_path(path) == 0

    note = _find_note_by_path(path)
    r = client.post(f"/api/notes/{note.id}/archive", headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["archived"] == 1
    assert j["evicted_tasks"] == 3
    assert j["archive_kind"] == "user"

    # Main DB: task rows are gone; Note row remains with archived=True.
    assert _count_main_tasks_for_path(path) == 0
    with Session(_get_engine()) as s:
        n = s.exec(select(Note).where(Note.path == path)).first()
        assert n is not None
        assert n.archived is True
        assert n.archive_kind == "user"

    # Archive DB: 3 task rows now present, tied to the same uuids.
    assert _count_archive_tasks_for_path(path) == 3
    with Session(_get_archive_engine()) as s:
        arch_note = s.exec(select(Note).where(Note.path == path)).first()
        uuids = {
            r[0] for r in s.exec(
                text("SELECT task_uuid FROM task WHERE note_id = :i").bindparams(i=arch_note.id)
            ).all()
        }
        assert uuids == {"T-AAA0001", "T-AAA0002", "T-AAA0003"}


def test_unarchive_note_restores_tasks_and_clears_archive(client):
    project = "arch-p2"
    path = f"{project}/w1.md"
    body = (
        "# W1\n"
        "!task #id T-BBB0001 x @alice\n"
        "!task #id T-BBB0002 y @bob\n"
    )
    _put_note(client, path, body)
    note = _find_note_by_path(path)

    # Archive first.
    r = client.post(f"/api/notes/{note.id}/archive", headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200
    assert _count_main_tasks_for_path(path) == 0
    assert _count_archive_tasks_for_path(path) == 2

    # Unarchive.
    r = client.post(f"/api/notes/{note.id}/unarchive", headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["unarchived"] == 1
    assert j["reindexed_tasks"] == 2

    # Main DB: task rows restored with the SAME uuids (via #id T-XXX embed).
    assert _count_main_tasks_for_path(path) == 2
    assert _task_uuids_main(path) == {"T-BBB0001", "T-BBB0002"}
    with Session(_get_engine()) as s:
        n = s.exec(select(Note).where(Note.path == path)).first()
        assert n.archived is False
        assert n.archive_kind == ""

    # Archive DB: rows drained.
    assert _count_archive_tasks_for_path(path) == 0


# ---------------------------------------------------------------------------
# Rollover carve-out
# ---------------------------------------------------------------------------

def test_rollover_archive_cannot_be_user_archived(client):
    project = "arch-p3"
    src_path = f"{project}/FIT weekly ww10.md"
    _put_note(client, src_path,
              "# WW10\n!task #id T-CCC0001 alpha @alice\n")

    # Trigger the rollover flow, which moves the src note to `_archive/`.
    r = client.post(
        "/api/notes/next-week",
        json={"path": src_path, "overwrite": True},
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text
    archived_path = r.json()["archived_path"]
    assert "/_archive/" in f"/{archived_path}/"

    # The archived Note row exists but archive_kind is '' (grandfathered
    # rollover heuristic). Attempting to user-archive it must 409.
    arch_note = _find_note_by_path(archived_path)
    assert arch_note is not None
    r = client.post(
        f"/api/notes/{arch_note.id}/archive",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 409, r.text
    assert "rollover" in r.text.lower()

    # And it must not be reachable via unarchive either.
    r = client.post(
        f"/api/notes/{arch_note.id}/unarchive",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# Project-level archive
# ---------------------------------------------------------------------------

def test_archive_project_cascades_to_notes(client):
    project = "arch-p4"
    _put_note(client, f"{project}/a.md",
              "# A\n!task #id T-DDD0001 a1 @alice\n!task #id T-DDD0002 a2 @bob\n")
    _put_note(client, f"{project}/b.md",
              "# B\n!task #id T-DDD0003 b1 @alice\n")

    r = client.post(
        f"/api/projects/{project}/archive",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["archived"] == 2
    assert j["evicted_tasks"] == 3
    assert j["project"] == project

    assert _count_main_tasks_for_path(f"{project}/a.md") == 0
    assert _count_main_tasks_for_path(f"{project}/b.md") == 0
    assert _count_archive_tasks_for_path(f"{project}/a.md") == 2
    assert _count_archive_tasks_for_path(f"{project}/b.md") == 1

    # Unarchive the whole project restores both.
    r = client.post(
        f"/api/projects/{project}/unarchive",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["unarchived"] == 2
    assert j["reindexed_tasks"] == 3

    assert _count_main_tasks_for_path(f"{project}/a.md") == 2
    assert _count_main_tasks_for_path(f"{project}/b.md") == 1
    assert _count_archive_tasks_for_path(f"{project}/a.md") == 0
    assert _count_archive_tasks_for_path(f"{project}/b.md") == 0


def test_project_archive_skips_rollover_notes_in_folder(client):
    project = "arch-p5"
    src = f"{project}/FIT weekly ww11.md"
    _put_note(client, src, "# WW11\n!task #id T-EEE0001 alpha @alice\n")
    # Roll it — creates `{project}/_archive/FIT weekly ww11.md`.
    r = client.post(
        "/api/notes/next-week",
        json={"path": src, "overwrite": True},
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200
    rollover_path = r.json()["archived_path"]
    rollover_main_task_count_before = _count_main_tasks_for_path(rollover_path)

    # Now also add a live note under the same project.
    _put_note(client, f"{project}/live.md",
              "# Live\n!task #id T-EEE0100 live @alice\n")

    r = client.post(
        f"/api/projects/{project}/archive",
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text
    j = r.json()

    # Rollover note untouched — task rows still in main, still absent
    # from archive.db. DoD invariant from #304.
    assert _count_main_tasks_for_path(rollover_path) == rollover_main_task_count_before
    assert _count_archive_tasks_for_path(rollover_path) == 0

    # Live note evicted as usual.
    assert _count_main_tasks_for_path(f"{project}/live.md") == 0


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

def test_member_cannot_archive_project(client):
    project = "arch-p6"
    _put_note(client, f"{project}/a.md",
              "# A\n!task #id T-FFF0001 x @alice\n")

    # Create member "member1" who is NOT a manager on this project.
    r = client.post(
        "/api/admin/users",
        json={"name": "member1", "password": "member1pw", "is_admin": False},
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code in (200, 201), r.text
    r = client.put(
        f"/api/projects/{project}/members",
        json={"user_name": "member1", "role": "member"},
        headers={"Authorization": AUTH_ADMIN},
    )
    assert r.status_code == 200, r.text
    member_auth = "Basic " + base64.b64encode(b"member1:member1pw").decode()

    note = _find_note_by_path(f"{project}/a.md")

    # Member can't archive an individual note in the project.
    r = client.post(
        f"/api/notes/{note.id}/archive",
        headers={"Authorization": member_auth},
    )
    assert r.status_code == 403, r.text

    # Member can't archive the whole project.
    r = client.post(
        f"/api/projects/{project}/archive",
        headers={"Authorization": member_auth},
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

def test_reconcile_drops_orphan_archive_rows(client):
    """Simulate a crashed two-DB txn: archive.db has a task copy, but main
    still has the row (i.e. main-DB delete never happened). Reconcile
    should drop the orphan from archive.db and leave main intact.
    """
    project = "arch-p7"
    path = f"{project}/w.md"
    body = "# W\n!task #id T-GGG0001 orphan-me @alice\n"
    _put_note(client, path, body)

    # Manually stuff a duplicate task row into archive.db with the SAME
    # task_uuid as an existing main-DB task, simulating "archive
    # committed but main crashed before delete".
    with Session(_get_archive_engine()) as arch:
        arch_note = arch.exec(select(Note).where(Note.path == path)).first()
        if arch_note is None:
            arch_note = Note(path=path, title="W", body_md=body,
                             mtime=0.0, archived=True, archive_kind="user")
            arch.add(arch_note)
            arch.commit()
            arch.refresh(arch_note)
        arch.add(Task(
            note_id=arch_note.id, slug="orphan-me",
            task_uuid="T-GGG0001", title="orphan-me",
            status="todo", line=1, indent=0, kind="task",
        ))
        arch.commit()

    # Main still has T-GGG0001 (was never archived).
    assert "T-GGG0001" in _task_uuids_main(path)

    # Reconcile.
    r = client.post("/api/archive/reconcile", headers={"Authorization": AUTH_ADMIN})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["orphans_dropped"] >= 1

    # Archive.db no longer carries T-GGG0001.
    with Session(_get_archive_engine()) as arch:
        row = arch.exec(
            text("SELECT COUNT(*) FROM task WHERE task_uuid = 'T-GGG0001'")
        ).first()
        assert int(row[0]) == 0

    # Main untouched.
    assert "T-GGG0001" in _task_uuids_main(path)
