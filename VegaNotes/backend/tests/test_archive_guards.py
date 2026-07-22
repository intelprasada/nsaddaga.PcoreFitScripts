"""Regression tests for #304 PR 3: indexer archive carve-outs.

Bulk-walker (``reindex_all``) and per-event watcher path must skip:
- files under a user-archived project (``Project.archived == True``)
- individual user-archived notes (``Note.archive_kind == 'user'``)

Skipped notes' rows survive the walk (no orphan sweep) so the archive
stays browsable via ``?include_archived=1``.  Direct API callers
(``PUT /api/notes``, unarchive) still route through ``reindex_file``
directly and are NOT affected.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from sqlmodel import Session, select

DATA = Path(tempfile.mkdtemp(prefix="vega-archive-guards-"))
os.environ["VEGANOTES_DATA_DIR"] = str(DATA)
os.environ["VEGANOTES_SERVE_STATIC"] = "false"

from app.config import settings  # noqa: E402
import app.db as _db_mod  # noqa: E402
from app.indexer import reindex_all, _is_archived_target  # noqa: E402
from app.models import Note, Project, Task  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _bootstrap():
    saved_data_dir = settings.data_dir
    saved_engine = _db_mod._engine
    saved_archive_engine = _db_mod._archive_engine

    settings.data_dir = DATA
    settings.notes_dir.mkdir(parents=True, exist_ok=True)
    _db_mod._engine = None
    _db_mod._archive_engine = None
    _db_mod.init_db()

    yield

    settings.data_dir = saved_data_dir
    _db_mod._engine = saved_engine
    _db_mod._archive_engine = saved_archive_engine
    shutil.rmtree(DATA, ignore_errors=True)


def _write(rel: str, body: str) -> Path:
    p = settings.notes_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _get_note(rel: str) -> Note | None:
    with Session(_db_mod.get_engine()) as s:
        return s.exec(select(Note).where(Note.path == rel)).first()


def _count_tasks(rel: str) -> int:
    n = _get_note(rel)
    if n is None:
        return 0
    with Session(_db_mod.get_engine()) as s:
        return len(s.exec(select(Task).where(Task.note_id == n.id)).all())


def test_archived_project_files_skipped_by_reindex_all():
    _write("ArchProjA/one.md", "# One\n!task first !p !high\n")
    _write("ArchProjA/two.md", "# Two\n!task second\n")

    with Session(_db_mod.get_engine()) as s:
        reindex_all(s)
        s.commit()

    assert _count_tasks("ArchProjA/one.md") == 1
    assert _count_tasks("ArchProjA/two.md") == 1

    with Session(_db_mod.get_engine()) as s:
        proj = s.exec(select(Project).where(Project.name == "ArchProjA")).first()
        assert proj is not None
        proj.archived = True
        s.add(proj)
        s.commit()

    _write("ArchProjA/one.md", "# One\n!task first !p !low\n!task extra\n")

    with Session(_db_mod.get_engine()) as s:
        reindex_all(s)
        s.commit()

    assert _count_tasks("ArchProjA/one.md") == 1
    assert _get_note("ArchProjA/one.md") is not None
    assert _get_note("ArchProjA/two.md") is not None


def test_user_archived_note_skipped_even_when_project_active():
    _write("ActiveProj/keep.md", "# Keep\n!task alpha\n")
    _write("ActiveProj/archived.md", "# Archived\n!task beta\n")

    with Session(_db_mod.get_engine()) as s:
        reindex_all(s)
        s.commit()

    with Session(_db_mod.get_engine()) as s:
        n = s.exec(select(Note).where(Note.path == "ActiveProj/archived.md")).first()
        assert n is not None
        n.archive_kind = "user"
        n.archived = True
        s.add(n)
        s.commit()

    _write("ActiveProj/archived.md", "# Archived\n!task beta\n!task gamma\n")

    with Session(_db_mod.get_engine()) as s:
        reindex_all(s)
        s.commit()

    assert _count_tasks("ActiveProj/archived.md") == 1
    assert _count_tasks("ActiveProj/keep.md") == 1
    assert _get_note("ActiveProj/archived.md") is not None


def test_is_archived_target_helper():
    _write("HelperProj/n.md", "# N\n!task solo\n")
    with Session(_db_mod.get_engine()) as s:
        reindex_all(s)
        s.commit()

    with Session(_db_mod.get_engine()) as s:
        assert _is_archived_target(s, "HelperProj/n.md") is False

        proj = s.exec(select(Project).where(Project.name == "HelperProj")).first()
        assert proj is not None
        proj.archived = True
        s.add(proj)
        s.commit()

        assert _is_archived_target(s, "HelperProj/n.md") is True

        proj.archived = False
        s.add(proj)
        s.commit()
        assert _is_archived_target(s, "HelperProj/n.md") is False

        n = s.exec(select(Note).where(Note.path == "HelperProj/n.md")).first()
        assert n is not None
        n.archive_kind = "user"
        n.archived = True
        s.add(n)
        s.commit()
        assert _is_archived_target(s, "HelperProj/n.md") is True

        n.archive_kind = "rollover"
        s.add(n)
        s.commit()
        assert _is_archived_target(s, "HelperProj/n.md") is False


def test_orphan_sweep_preserves_archived_note_rows():
    _write("SweepProj/orphan-me.md", "# Orphan\n!task x\n")

    with Session(_db_mod.get_engine()) as s:
        reindex_all(s)
        s.commit()

    with Session(_db_mod.get_engine()) as s:
        n = s.exec(select(Note).where(Note.path == "SweepProj/orphan-me.md")).first()
        assert n is not None
        n.archive_kind = "user"
        n.archived = True
        s.add(n)
        s.commit()

    with Session(_db_mod.get_engine()) as s:
        reindex_all(s)
        s.commit()

    assert _get_note("SweepProj/orphan-me.md") is not None
