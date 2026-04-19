"""Sync markdown files on disk into the SQLite index.

The indexer is the single point that mutates the index: API write paths
write the .md file then call ``reindex_path``; the watcher picks up
out-of-band edits (CLI/IDE/git) and does the same.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy import text
from sqlmodel import Session, select

from ..config import settings
from ..db import get_engine, init_db
from ..models import (
    Feature, Link, Note, Project, Task, TaskAttr, TaskFeature,
    TaskOwner, TaskProject, User,
)
from ..parser import parse

log = logging.getLogger(__name__)


def _get_or_create(session: Session, model, **kwargs):
    inst = session.exec(select(model).filter_by(**kwargs)).first()
    if inst:
        return inst
    inst = model(**kwargs)
    session.add(inst)
    session.flush()
    return inst


def _delete_task_children(session: Session, note_id: int) -> None:
    tasks = session.exec(select(Task).where(Task.note_id == note_id)).all()
    task_ids = [t.id for t in tasks]
    if not task_ids:
        return
    placeholders = ",".join(str(int(i)) for i in task_ids)
    for table in ("taskattr", "taskowner", "taskproject", "taskfeature", "link"):
        col = "src_task_id" if table == "link" else "task_id"
        session.exec(text(f"DELETE FROM {table} WHERE {col} IN ({placeholders})"))
    session.exec(text(f"DELETE FROM task WHERE note_id = {int(note_id)}"))


def reindex_file(path: Path, session: Session) -> Note:
    rel = str(path.relative_to(settings.notes_dir))
    body = path.read_text(encoding="utf-8")
    parsed = parse(body)
    title = next((ln.lstrip("# ").strip() for ln in body.splitlines() if ln.startswith("#")), rel)
    mtime = path.stat().st_mtime

    # Folder-derived project: the top-level subdirectory under notes/ is the
    # implicit project for every task in this file. It is added to the task's
    # project set unless the user explicitly listed a different / additional
    # project via #project tokens.
    parts = Path(rel).parts
    folder_project: str | None = parts[0] if len(parts) >= 2 else None

    note = session.exec(select(Note).where(Note.path == rel)).first()
    now = datetime.utcnow()
    if note is None:
        note = Note(path=rel, title=title, body_md=body, mtime=mtime, updated_at=now)
        session.add(note)
        session.flush()
    else:
        _delete_task_children(session, note.id)
        note.title = title
        note.body_md = body
        note.mtime = mtime
        note.updated_at = now

    # FTS5 sync
    session.exec(text("DELETE FROM notes_fts WHERE rowid = :id").bindparams(id=note.id))
    session.exec(
        text("INSERT INTO notes_fts(rowid, title, body_md) VALUES (:id, :t, :b)")
        .bindparams(id=note.id, t=title, b=body)
    )

    # Two-pass: create tasks, then resolve parents by slug.
    slug_to_id: dict[str, int] = {}
    for pt in parsed["tasks"]:
        t = Task(
            note_id=note.id,
            slug=pt["slug"],
            title=pt["title"],
            status=pt["status"],
            line=pt["line"],
            indent=pt["indent"],
        )
        session.add(t)
        session.flush()
        slug_to_id[pt["slug"]] = t.id

    for pt in parsed["tasks"]:
        tid = slug_to_id[pt["slug"]]
        t = session.get(Task, tid)
        if pt["parent_slug"] and pt["parent_slug"] in slug_to_id:
            t.parent_task_id = slug_to_id[pt["parent_slug"]]

        # Attributes
        for key, val in pt["attrs"].items():
            values = val if isinstance(val, list) else [val]
            norms = pt["attrs_norm"].get(key)
            norms_list = norms if isinstance(norms, list) else [norms] * len(values)
            for v, n in zip(values, norms_list):
                norm_str = None if n is None else str(n)
                session.add(TaskAttr(task_id=tid, key=key, value=str(v), value_norm=norm_str))

        # Owners / Projects / Features
        for name in pt["attrs"].get("owner", []) if isinstance(pt["attrs"].get("owner"), list) else []:
            user = _get_or_create(session, User, name=name)
            session.add(TaskOwner(task_id=tid, user_id=user.id))
        for name in pt["attrs"].get("project", []) if isinstance(pt["attrs"].get("project"), list) else []:
            proj = _get_or_create(session, Project, name=name)
            session.add(TaskProject(task_id=tid, project_id=proj.id))
        # Folder-derived project: union with any explicit #project values.
        if folder_project:
            explicit = pt["attrs"].get("project", []) if isinstance(pt["attrs"].get("project"), list) else []
            if folder_project not in explicit:
                proj = _get_or_create(session, Project, name=folder_project)
                session.add(TaskProject(task_id=tid, project_id=proj.id))
                session.add(TaskAttr(task_id=tid, key="project", value=folder_project, value_norm=folder_project.lower()))
        for name in pt["attrs"].get("feature", []) if isinstance(pt["attrs"].get("feature"), list) else []:
            feat = _get_or_create(session, Feature, name=name)
            session.add(TaskFeature(task_id=tid, feature_id=feat.id))

        # Outgoing refs
        for ref in pt["refs"]:
            session.add(Link(src_task_id=tid, dst_slug=ref["dst_slug"], kind=ref["kind"]))

    return note


def remove_path(rel: str, session: Session) -> None:
    note = session.exec(select(Note).where(Note.path == rel)).first()
    if note is None:
        return
    _delete_task_children(session, note.id)
    session.exec(text("DELETE FROM notes_fts WHERE rowid = :id").bindparams(id=note.id))
    session.delete(note)


def reindex_all(session: Session) -> int:
    n = 0
    for path in sorted(settings.notes_dir.rglob("*.md")):
        reindex_file(path, session)
        n += 1
    return n


def list_md_files() -> Iterable[Path]:
    return sorted(settings.notes_dir.rglob("*.md"))


async def watch_loop() -> None:
    """Background task: watch notes_dir and reindex on change."""
    from watchfiles import Change, awatch

    init_db()
    log.info("Indexer watching %s", settings.notes_dir)
    async for changes in awatch(settings.notes_dir):
        from ..db import session_scope
        with session_scope() as s:
            for change, p in changes:
                path = Path(p)
                if path.suffix != ".md":
                    continue
                try:
                    rel = str(path.relative_to(settings.notes_dir))
                except ValueError:
                    continue
                if change == Change.deleted or not path.exists():
                    remove_path(rel, s)
                else:
                    try:
                        reindex_file(path, s)
                    except Exception:
                        log.exception("reindex failed for %s", path)
        await asyncio.sleep(0)  # cooperative
