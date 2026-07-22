from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import MetaData, text
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

_engine = None
_archive_engine = None

# Tables that live ONLY in the main DB and are never mirrored into
# archive.db (#304): user-scoped state has no meaning in the archive
# read model.
_ARCHIVE_EXCLUDED_TABLES: frozenset[str] = frozenset({
    "user",
    "userbadge",
    "projectmember",
    "activityevent",
})


def get_engine():
    global _engine
    if _engine is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{settings.db_path}"
        _engine = create_engine(url, connect_args={"check_same_thread": False})
    return _engine


def get_archive_engine():
    """Sibling SQLite engine for the #304 archive DB.

    Populated by user-driven archive; drained by unarchive. Same schema
    as the main DB minus user-scoped tables (see ``_ARCHIVE_EXCLUDED_TABLES``).
    """
    global _archive_engine
    if _archive_engine is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{settings.archive_db_path}"
        _archive_engine = create_engine(
            url, connect_args={"check_same_thread": False},
        )
    return _archive_engine


def _archive_metadata() -> MetaData:
    """SQLModel metadata subset appropriate for the archive DB.

    Copies each retained table's schema onto a fresh ``MetaData`` so the
    two engines don't share a metadata registry (which would cause
    ``create_all`` on either engine to attempt to create ALL tables).

    Foreign keys pointing at excluded tables (e.g. ``taskowner.user_id
    -> user.id``) are dropped: the archive DB is a query surface, not a
    referential-integrity boundary. The read layer (PR 4) resolves
    excluded-table joins by opening a session on the main engine.
    """
    from sqlalchemy import Column, ForeignKey, Table

    # Import models so SQLModel.metadata is populated.
    from . import models  # noqa: F401

    md = MetaData()
    for name, table in SQLModel.metadata.tables.items():
        if name in _ARCHIVE_EXCLUDED_TABLES:
            continue
        # Build a fresh column list, cloning without FKs that target
        # excluded tables. Same-archive FKs (task -> note, taskattr ->
        # task, etc.) are preserved.
        new_cols = []
        for col in table.columns:
            kept_fks = []
            for fk in col.foreign_keys:
                target_table = fk.column.table.name
                if target_table in _ARCHIVE_EXCLUDED_TABLES:
                    continue
                kept_fks.append(ForeignKey(fk.target_fullname))
            new_col = Column(
                col.name,
                col.type,
                *kept_fks,
                primary_key=col.primary_key,
                nullable=col.nullable,
                index=col.index,
                unique=col.unique,
                default=col.default,
                server_default=col.server_default,
            )
            new_cols.append(new_col)
        Table(name, md, *new_cols)
    return md


def init_archive_db() -> None:
    """Create the archive DB file + schema if missing.  Idempotent."""
    engine = get_archive_engine()
    _archive_metadata().create_all(engine)
    # Mirror the same lightweight column-add migrations that ``init_db``
    # applies to the main DB, so an archive DB created before these
    # columns existed still round-trips archived rows correctly.
    with engine.begin() as conn:
        note_cols = {
            row[1] for row in conn.execute(
                text("PRAGMA table_info(note)")
            ).fetchall()
        }
        if note_cols and "archived" not in note_cols:
            conn.execute(text(
                "ALTER TABLE note ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            ))
        if note_cols and "archive_kind" not in note_cols:
            conn.execute(text(
                "ALTER TABLE note ADD COLUMN archive_kind TEXT NOT NULL DEFAULT ''"
            ))
        proj_cols = {
            row[1] for row in conn.execute(
                text("PRAGMA table_info(project)")
            ).fetchall()
        }
        if proj_cols and "archived" not in proj_cols:
            conn.execute(text(
                "ALTER TABLE project ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            ))


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
        if "task_uuid" not in existing:
            conn.execute(text("ALTER TABLE task ADD COLUMN task_uuid TEXT"))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_task_task_uuid "
                "ON task(task_uuid) WHERE task_uuid IS NOT NULL"
            ))
        # User: pass_hash + is_admin added when multi-user auth landed.
        user_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(user)")).fetchall()}
        if user_cols and "pass_hash" not in user_cols:
            conn.execute(text("ALTER TABLE user ADD COLUMN pass_hash TEXT NOT NULL DEFAULT ''"))
        if user_cols and "is_admin" not in user_cols:
            conn.execute(text("ALTER TABLE user ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"))
        if user_cols and "tz" not in user_cols:
            # Per-user IANA TZ for gamification (Phase 3). Empty ≡ UTC.
            conn.execute(text("ALTER TABLE user ADD COLUMN tz TEXT NOT NULL DEFAULT ''"))
        # Note.archived: rollover/archive feature. Marks a weekly note that
        # has been rolled forward — popover/PATCH writes are gated by the
        # API to project managers, and tree/listing endpoints hide it from
        # default views.  ``DEFAULT 0`` keeps every existing row visible.
        note_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(note)")).fetchall()}
        if note_cols and "archived" not in note_cols:
            conn.execute(text("ALTER TABLE note ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_note_archived ON note(archived)"))
        # Note.archive_kind (#304): distinguishes rollover archives (grandfathered,
        # untouched by the user-driven archive feature) from user-driven archives
        # (which participate in main-DB eviction + archive.db copy). Default ""
        # for legacy rows; consumers treat "" + ``/_archive/`` path as "rollover".
        if note_cols and "archive_kind" not in note_cols:
            conn.execute(text(
                "ALTER TABLE note ADD COLUMN archive_kind TEXT NOT NULL DEFAULT ''"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_note_archive_kind "
                "ON note(archive_kind)"
            ))
        # Project.archived (#304): user-driven project-level archive flag.
        proj_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(project)")).fetchall()}
        if proj_cols and "archived" not in proj_cols:
            conn.execute(text(
                "ALTER TABLE project ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_project_archived "
                "ON project(archived)"
            ))
        # Normalise legacy priority value_norm rows: old indexer stored 'p0'-'p3'
        # as the value_norm; new indexer stores the integer rank ('0'-'3').
        # Re-write any non-numeric priority value_norm to the integer rank.
        _PRIO_MAP = {"p0": "0", "p1": "1", "p2": "2", "p3": "3",
                     "high": "1", "med": "2", "medium": "2", "low": "3"}
        rows = conn.execute(
            text("SELECT id, value_norm FROM taskattr WHERE key='priority'")
        ).fetchall()
        for row_id, vn in rows:
            if vn and not vn.lstrip("-").isdigit():
                new_vn = _PRIO_MAP.get((vn or "").lower(), "999")
                conn.execute(
                    text("UPDATE taskattr SET value_norm=:vn WHERE id=:id"),
                    {"vn": new_vn, "id": row_id},
                )
        # FTS5 virtual table for note search.
        conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts "
            "USING fts5(title, body_md);"
        ))
        # Composite index for the gamification activity log: per-user time
        # range scans dominate the read pattern (`/api/me/activity`,
        # streak/stats math). Single-column indexes on user_id and ts
        # already exist via the model definition; this composite makes the
        # common WHERE user_id=? AND ts>=? query a single index seek.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_activityevent_user_ts "
            "ON activityevent(user_id, ts);"
        ))
        # Idempotency guard for badge awards: one (user, badge) row max.
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_userbadge_user_key "
            "ON userbadge(user_id, badge_key);"
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
    # Bootstrap the sibling archive DB (#304) so first archive can
    # write into it without a race.
    init_archive_db()


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


@contextmanager
def archive_session_scope() -> Session:
    """Session scope for the sibling archive DB (#304)."""
    s = Session(get_archive_engine())
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


def get_archive_session() -> Session:
    """FastAPI dependency for archive-DB reads (#304)."""
    with archive_session_scope() as s:
        yield s


def ensure_data_dirs() -> None:
    settings.notes_dir.mkdir(parents=True, exist_ok=True)
    Path(settings.data_dir / "exports").mkdir(parents=True, exist_ok=True)
