"""SQLModel data model — the *index* over markdown files on disk."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class Note(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    path: str = Field(index=True, unique=True)
    title: str = ""
    body_md: str = ""
    mtime: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    note_id: int = Field(foreign_key="note.id", index=True)
    parent_task_id: Optional[int] = Field(default=None, foreign_key="task.id", index=True)
    slug: str = Field(index=True)
    # Stable identity minted once by stamp_task_ids and embedded in the .md
    # file as "#id T-XXXXXX". Survives title renames and file moves.
    # Nullable: tasks that haven't been stamped yet won't have one.
    task_uuid: Optional[str] = Field(default=None, index=True, unique=True)
    title: str = ""
    status: str = Field(default="todo", index=True)
    line: int = 0
    indent: int = 0
    kind: str = Field(default="task", index=True)  # "task" | "ar"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TaskAttr(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    key: str = Field(index=True)
    value: str = ""
    value_norm: Optional[str] = Field(default=None, index=True)


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    saved_views_json: str = "[]"
    pass_hash: str = ""
    is_admin: bool = False
    # IANA tz name (e.g. "America/Los_Angeles"). Used by gamification stats
    # so streaks roll over at the user's local midnight, not UTC's. Empty
    # string ≡ UTC (the historical default).
    tz: str = ""


class TaskOwner(SQLModel, table=True):
    task_id: int = Field(foreign_key="task.id", primary_key=True)
    user_id: int = Field(foreign_key="user.id", primary_key=True)


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)


class TaskProject(SQLModel, table=True):
    task_id: int = Field(foreign_key="task.id", primary_key=True)
    project_id: int = Field(foreign_key="project.id", primary_key=True)


class Feature(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)


class TaskFeature(SQLModel, table=True):
    task_id: int = Field(foreign_key="task.id", primary_key=True)
    feature_id: int = Field(foreign_key="feature.id", primary_key=True)


class Link(SQLModel, table=True):
    """Directed reference between two tasks (or task->arbitrary slug).

    Bidirectional queries are served via the ``links_bidir`` SQL view created
    in :func:`init_db`.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    src_task_id: int = Field(foreign_key="task.id", index=True)
    dst_slug: str = Field(index=True)
    kind: str = "task"  # task | link | blocks | blocked_by


class ActivityEvent(SQLModel, table=True):
    """Append-only log of user actions for gamification stats / badges.

    One row per atomic event (task close, note edit, …). The actor is the
    authenticated user that issued the API call — not the task owner. All
    reads are scoped to the calling user via ``/api/me/activity``; this
    table is never exposed cross-user.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    kind: str = Field(index=True)
    # Free-form reference for the event subject (e.g. "T-ABC123" or note
    # path). Indexed so we can ask "all events touching this task".
    ref: str = Field(default="", index=True)
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)
    # JSON blob with event-specific fields (e.g. {"from":"todo","to":"done"}).
    # Source-of-truth for badge logic; intentionally untyped.
    meta_json: str = ""


class UserBadge(SQLModel, table=True):
    """One row per (user, badge) award. Awarding is idempotent: the
    composite uniqueness is enforced by an index in db.init_db so a
    re-run of recompute_badges never double-awards."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    badge_key: str = Field(index=True)
    awarded_at: datetime = Field(default_factory=datetime.utcnow)


class ProjectMember(SQLModel, table=True):
    """RBAC: which users can access a project (folder under notes/) and at what role.

    role: "manager" = full CRUD on project's notes/tasks; "member" = can only edit
    tasks they own (where #owner contains their username).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    project_name: str = Field(index=True)
    user_name: str = Field(index=True)
    role: str = "member"  # manager | member
