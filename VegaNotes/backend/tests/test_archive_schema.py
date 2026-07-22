"""Schema-level tests for the #304 archive-DB feature (PR 1 of 5).

Covers only the schema + engine wiring shipped in PR 1:
- ``Note.archive_kind`` column exists on both DBs and defaults to "".
- ``Project.archived`` column exists on both DBs and defaults to False.
- Sibling ``archive.db`` file is created by ``init_db()`` and carries the
  same schema as the main DB, minus the user-scoped tables.
- Idempotent re-run of ``init_db()`` on an existing DB doesn't crash and
  doesn't duplicate columns/indexes.
- ALTER-TABLE bootstrap successfully adds the new columns to a
  pre-existing DB that lacks them (simulates upgrade path on live data).

Higher-level archive/unarchive behaviour is covered in later PRs.
"""
from __future__ import annotations

import importlib
import os
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import text


def _fresh_env():
    """Return a clean data dir + reload the ``app.db`` module against it.

    Each test needs its own ``data_dir`` to avoid interference with the
    module-scoped ``client`` fixture in ``tests/api/test_api.py``.
    """
    data = Path(tempfile.mkdtemp(prefix="vega-archive-schema-"))
    os.environ["VEGANOTES_DATA_DIR"] = str(data)
    # Force ``settings`` and ``db`` to pick up the new env var.
    import app.config as config_mod
    import app.db as db_mod
    importlib.reload(config_mod)
    importlib.reload(db_mod)
    # Reset cached engines so ``get_engine()`` opens the new file.
    db_mod._engine = None
    db_mod._archive_engine = None
    return data, db_mod


def _table_columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {
            row[1] for row in conn.execute(
                text(f"PRAGMA table_info({table})")
            ).fetchall()
        }


def _table_names(engine) -> set[str]:
    with engine.connect() as conn:
        return {
            row[0] for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }


def test_main_db_has_new_archive_columns():
    data, db_mod = _fresh_env()
    try:
        db_mod.init_db()
        main = db_mod.get_engine()
        note_cols = _table_columns(main, "note")
        proj_cols = _table_columns(main, "project")
        assert "archived" in note_cols
        assert "archive_kind" in note_cols
        assert "archived" in proj_cols
    finally:
        shutil.rmtree(data, ignore_errors=True)


def test_archive_db_is_created_alongside_main():
    data, db_mod = _fresh_env()
    try:
        db_mod.init_db()
        from app.config import settings
        assert settings.archive_db_path.exists(), (
            f"expected archive.sqlite at {settings.archive_db_path}, "
            f"got: {list(data.iterdir())}"
        )
        # And it's a sibling — same directory as main DB.
        assert settings.archive_db_path.parent == settings.db_path.parent
    finally:
        shutil.rmtree(data, ignore_errors=True)


def test_archive_db_schema_matches_shared_subset():
    data, db_mod = _fresh_env()
    try:
        db_mod.init_db()
        archive = db_mod.get_archive_engine()
        names = _table_names(archive)
        # Present: everything that could plausibly reference a task.
        for expected in (
            "note", "task", "taskattr", "taskowner",
            "project", "taskproject", "feature", "taskfeature", "link",
        ):
            assert expected in names, (
                f"archive.db missing {expected}; has: {sorted(names)}"
            )
        # Excluded: user-scoped state per #304 carve-out.
        for forbidden in db_mod._ARCHIVE_EXCLUDED_TABLES:
            assert forbidden not in names, (
                f"archive.db unexpectedly contains {forbidden}; "
                f"should stay in main DB per #304"
            )
        # New columns from PR 1 must be present in the archive DB too so
        # rows copied from main round-trip without column-count skew.
        assert "archive_kind" in _table_columns(archive, "note")
        assert "archived" in _table_columns(archive, "project")
    finally:
        shutil.rmtree(data, ignore_errors=True)


def test_init_db_is_idempotent():
    data, db_mod = _fresh_env()
    try:
        db_mod.init_db()
        # Second call must not raise, must not duplicate columns.
        db_mod.init_db()
        main = db_mod.get_engine()
        note_cols_before = _table_columns(main, "note")
        db_mod.init_db()
        note_cols_after = _table_columns(main, "note")
        assert note_cols_before == note_cols_after
    finally:
        shutil.rmtree(data, ignore_errors=True)


def test_bootstrap_adds_columns_to_legacy_db():
    """Simulate an upgrade path: DB exists but lacks the new columns.

    We create the note/project tables the old way (no ``archive_kind``,
    no ``archived`` on project) and then call ``init_db()``. The
    ALTER TABLE bootstraps must add the missing columns without data
    loss.
    """
    data, db_mod = _fresh_env()
    try:
        # Build a minimal legacy DB by hand — pre-#304 shape.
        eng = db_mod.get_engine()
        with eng.begin() as conn:
            conn.execute(text(
                "CREATE TABLE note ("
                "  id INTEGER PRIMARY KEY, path TEXT UNIQUE, "
                "  title TEXT DEFAULT '', body_md TEXT DEFAULT '', "
                "  mtime REAL DEFAULT 0, created_at TEXT, updated_at TEXT, "
                "  archived INTEGER NOT NULL DEFAULT 0"
                ")"
            ))
            conn.execute(text(
                "CREATE TABLE project ("
                "  id INTEGER PRIMARY KEY, name TEXT UNIQUE"
                ")"
            ))
            conn.execute(text(
                "INSERT INTO note(path, archived) VALUES ('legacy.md', 1)"
            ))
            conn.execute(text(
                "INSERT INTO project(name) VALUES ('legacy-project')"
            ))

        db_mod.init_db()

        # Columns added; row data preserved.
        with eng.connect() as conn:
            note_cols = _table_columns(eng, "note")
            proj_cols = _table_columns(eng, "project")
            assert "archive_kind" in note_cols
            assert "archived" in proj_cols
            legacy_note = conn.execute(
                text("SELECT path, archived, archive_kind FROM note WHERE path='legacy.md'")
            ).fetchone()
            assert legacy_note is not None
            assert legacy_note[1] == 1  # archived preserved
            assert legacy_note[2] == ""  # archive_kind defaults to '' (rollover-heuristic)
            legacy_project = conn.execute(
                text("SELECT name, archived FROM project WHERE name='legacy-project'")
            ).fetchone()
            assert legacy_project is not None
            assert legacy_project[1] == 0  # archived defaults to False
    finally:
        shutil.rmtree(data, ignore_errors=True)
