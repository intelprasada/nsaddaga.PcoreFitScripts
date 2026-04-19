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


class ProjectMember(SQLModel, table=True):
    """RBAC: which users can access a project (folder under notes/) and at what role.

    role: "manager" = full CRUD on project's notes/tasks; "member" = can only edit
    tasks they own (where #owner contains their username).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    project_name: str = Field(index=True)
    user_name: str = Field(index=True)
    role: str = "member"  # manager | member
