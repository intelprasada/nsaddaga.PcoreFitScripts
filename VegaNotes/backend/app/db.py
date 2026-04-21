from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{settings.db_path}"
        _engine = create_engine(url, connect_args={"check_same_thread": False})
    return _engine


def init_db() -> None:
    # Import models so SQLModel metadata is populated.
    from . import models  # noqa: F401

    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        # Lightweight schema migrations for columns added after the initial
        # SQLModel.metadata.create_all() ran on a pre-existing dev DB.
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(task)")).fetchall()}
        if "kind" not in existing:
            conn.execute(text("ALTER TABLE task ADD COLUMN kind TEXT NOT NULL DEFAULT 'task'"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_task_kind ON task(kind)"))
        # User: pass_hash + is_admin added when multi-user auth landed.
        user_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(user)")).fetchall()}
        if user_cols and "pass_hash" not in user_cols:
            conn.execute(text("ALTER TABLE user ADD COLUMN pass_hash TEXT NOT NULL DEFAULT ''"))
        if user_cols and "is_admin" not in user_cols:
            conn.execute(text("ALTER TABLE user ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"))
        # FTS5 virtual table for note search.
        conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts "
            "USING fts5(title, body_md);"
        ))
        # Bidirectional view over `link` (treats every edge as undirected).
        conn.execute(text(
            "CREATE VIEW IF NOT EXISTS links_bidir AS "
            "SELECT src_task_id AS task_id, dst_slug AS other_slug, "
            "       kind, 'out' AS direction FROM link "
            "UNION ALL "
            "SELECT t.id AS task_id, t2.slug AS other_slug, l.kind, 'in' AS direction "
            "FROM link l "
            "JOIN task t ON t.slug = l.dst_slug "
            "JOIN task t2 ON t2.id = l.src_task_id;"
        ))


@contextmanager
def session_scope() -> Session:
    s = Session(get_engine())
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Session:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s


def ensure_data_dirs() -> None:
    settings.notes_dir.mkdir(parents=True, exist_ok=True)
    Path(settings.data_dir / "exports").mkdir(parents=True, exist_ok=True)
