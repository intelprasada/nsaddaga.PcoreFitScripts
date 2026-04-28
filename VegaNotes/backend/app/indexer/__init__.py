"""Sync markdown files on disk into the SQLite index.

The indexer is the single point that mutates the index: API write paths
write the .md file then call ``reindex_path``; the watcher picks up
out-of-band edits (CLI/IDE/git) and does the same.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy import text
from sqlmodel import Session, select

from ..config import settings
from ..db import get_engine, init_db
from ..models import (
    Feature, Link, Note, Project, ProjectMember, Task, TaskAttr, TaskFeature,
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


def _task_fingerprint_from_parsed(pt: dict) -> str:
    """Fingerprint for a parsed task — what the indexer would write."""
    # Flatten multi-value attrs to sorted (key, str(v)) pairs; skip "id" (stored elsewhere).
    attr_pairs: list[tuple[str, str]] = []
    for k, v in pt["attrs"].items():
        if k == "id":
            continue
        for val in (v if isinstance(v, list) else [v]):
            attr_pairs.append((k, str(val)))
    attr_pairs.sort()
    link_pairs = sorted((r["dst_slug"], r["kind"]) for r in pt.get("refs", []))
    raw = f"{pt['title']}|{pt['status']}|{pt['line']}|{pt['indent']}|{pt.get('kind','task')}|{attr_pairs}|{link_pairs}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _task_fingerprint_from_db(t: Task, attrs: list, links: list) -> str:
    """Fingerprint built from DB rows — must produce same value as _from_parsed when unchanged."""
    attr_pairs = sorted((a[0], a[1]) for a in attrs)  # (key, value) tuples
    link_pairs = sorted((lnk[0], lnk[1]) for lnk in links)  # (dst_slug, kind) tuples
    raw = f"{t.title}|{t.status}|{t.line}|{t.indent}|{t.kind}|{attr_pairs}|{link_pairs}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _upsert_task_attrs(session: Session, tid: int, pt: dict, folder_project: str | None) -> None:
    """Write TaskAttr / TaskOwner / TaskProject / TaskFeature / Link rows for *tid*."""
    for key, val in pt["attrs"].items():
        if key == "id":
            continue
        values = val if isinstance(val, list) else [val]
        norms = pt["attrs_norm"].get(key)
        norms_list = norms if isinstance(norms, list) else [norms] * len(values)
        for v, n in zip(values, norms_list):
            norm_str = None if n is None else str(n)
            session.add(TaskAttr(task_id=tid, key=key, value=str(v), value_norm=norm_str))

    for name in (pt["attrs"].get("owner", []) if isinstance(pt["attrs"].get("owner"), list) else []):
        user = _get_or_create(session, User, name=name)
        session.add(TaskOwner(task_id=tid, user_id=user.id))
    for name in (pt["attrs"].get("project", []) if isinstance(pt["attrs"].get("project"), list) else []):
        proj = _get_or_create(session, Project, name=name)
        session.add(TaskProject(task_id=tid, project_id=proj.id))
    if folder_project:
        explicit = pt["attrs"].get("project", []) if isinstance(pt["attrs"].get("project"), list) else []
        if folder_project not in explicit:
            proj = _get_or_create(session, Project, name=folder_project)
            session.add(TaskProject(task_id=tid, project_id=proj.id))
            session.add(TaskAttr(task_id=tid, key="project", value=folder_project, value_norm=folder_project.lower()))
    for name in (pt["attrs"].get("feature", []) if isinstance(pt["attrs"].get("feature"), list) else []):
        feat = _get_or_create(session, Feature, name=name)
        session.add(TaskFeature(task_id=tid, feature_id=feat.id))
    for ref in pt["refs"]:
        session.add(Link(src_task_id=tid, dst_slug=ref["dst_slug"], kind=ref["kind"]))


def reindex_file(path: Path, session: Session) -> Note:
    rel = str(path.relative_to(settings.notes_dir))
    body = path.read_text(encoding="utf-8")
    parsed = parse(body)
    title = next((ln.lstrip("# ").strip() for ln in body.splitlines() if ln.startswith("#")), rel)
    mtime = path.stat().st_mtime

    parts = Path(rel).parts
    folder_project: str | None = parts[0] if len(parts) >= 2 else None

    note = session.exec(select(Note).where(Note.path == rel)).first()
    now = datetime.utcnow()
    is_new_note = note is None
    if is_new_note:
        note = Note(path=rel, title=title, body_md=body, mtime=mtime, updated_at=now)
        session.add(note)
        session.flush()
    else:
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

    new_tasks = parsed["tasks"]

    if is_new_note:
        # Fresh note: full insert, no diff needed.
        _insert_all_tasks(session, note.id, new_tasks, folder_project)
    else:
        # Incremental reindex: diff new parsed tasks against existing DB rows.
        _incremental_reindex(session, note.id, new_tasks, folder_project)

    # Ref-row write-through (unchanged from before)
    _apply_ref_rows(session, parsed.get("ref_rows", []))

    return note


def _insert_all_tasks(
    session: Session, note_id: int, tasks: list[dict], folder_project: str | None
) -> None:
    """First-time insert for a brand-new note (no existing rows to diff against)."""
    slug_to_id: dict[str, int] = {}
    for pt in tasks:
        t = Task(
            note_id=note_id,
            slug=pt["slug"],
            title=pt["title"],
            status=pt["status"],
            line=pt["line"],
            indent=pt["indent"],
            kind=pt.get("kind", "task"),
            task_uuid=pt["attrs"].get("id") or None,
        )
        session.add(t)
        session.flush()
        slug_to_id[pt["slug"]] = t.id

    for pt in tasks:
        tid = slug_to_id[pt["slug"]]
        t = session.get(Task, tid)
        if pt["parent_slug"] and pt["parent_slug"] in slug_to_id:
            t.parent_task_id = slug_to_id[pt["parent_slug"]]
        _upsert_task_attrs(session, tid, pt, folder_project)


def _incremental_reindex(
    session: Session, note_id: int, new_tasks: list[dict], folder_project: str | None
) -> None:
    """Diff new parsed tasks against existing DB rows and apply minimal changes.

    Diff key (priority order):
      1. task_uuid — stable; present on stamped tasks
      2. (note_id, slug) — fallback for unstamped tasks

    For each task:
      - fingerprint unchanged → skip (zero writes)
      - fingerprint changed   → update scalar columns + replace child rows
      - missing from new set  → delete
      - new in new set        → insert
    """
    # Load all existing task rows for this note.
    existing = session.exec(select(Task).where(Task.note_id == note_id)).all()
    if not existing and not new_tasks:
        return

    task_ids = [t.id for t in existing]

    # Bulk-load all attrs + links in two queries (avoid N+1).
    attrs_by_tid: dict[int, list[tuple[str, str]]] = {t.id: [] for t in existing}
    if task_ids:
        ph = ",".join(str(i) for i in task_ids)
        for row in session.exec(
            text(f"SELECT task_id, key, value FROM taskattr WHERE task_id IN ({ph}) AND key != 'id'")
        ).all():
            attrs_by_tid[row[0]].append((row[1], row[2]))

    links_by_tid: dict[int, list[tuple[str, str]]] = {t.id: [] for t in existing}
    if task_ids:
        for row in session.exec(
            text(f"SELECT src_task_id, dst_slug, kind FROM link WHERE src_task_id IN ({ph})")
        ).all():
            links_by_tid[row[0]].append((row[1], row[2]))

    # Build existing fingerprints.
    existing_fp: dict[int, str] = {
        t.id: _task_fingerprint_from_db(t, attrs_by_tid[t.id], links_by_tid[t.id])
        for t in existing
    }

    # Index existing by uuid and slug.
    by_uuid: dict[str, Task] = {t.task_uuid: t for t in existing if t.task_uuid}
    by_slug: dict[str, Task] = {t.slug: t for t in existing}

    # Match new parsed tasks to existing rows.
    new_uuids = {pt["attrs"].get("id") for pt in new_tasks if pt["attrs"].get("id")}
    matched: dict[str, int] = {}  # new_slug → existing task.id
    for pt in new_tasks:
        uuid = pt["attrs"].get("id") or None
        row = (by_uuid.get(uuid) if uuid else None) or by_slug.get(pt["slug"])
        if row:
            matched[pt["slug"]] = row.id

    # Delete tasks that no longer exist.
    new_slugs = {pt["slug"] for pt in new_tasks}
    for t in existing:
        still_present = t.slug in new_slugs or (t.task_uuid and t.task_uuid in new_uuids)
        if not still_present:
            tid = t.id
            for table in ("taskattr", "taskowner", "taskproject", "taskfeature", "link"):
                col = "src_task_id" if table == "link" else "task_id"
                session.exec(text(f"DELETE FROM {table} WHERE {col} = {int(tid)}"))
            session.delete(t)

    session.flush()

    # Upsert: first pass to create/update rows.
    slug_to_id: dict[str, int] = {}
    for pt in new_tasks:
        new_fp = _task_fingerprint_from_parsed(pt)
        existing_id = matched.get(pt["slug"])

        if existing_id is not None:
            slug_to_id[pt["slug"]] = existing_id
            if existing_fp.get(existing_id) == new_fp:
                continue  # Unchanged — zero writes.
            # Changed: update task scalars + replace child rows.
            t = session.get(Task, existing_id)
            t.slug = pt["slug"]
            t.title = pt["title"]
            t.status = pt["status"]
            t.line = pt["line"]
            t.indent = pt["indent"]
            t.kind = pt.get("kind", "task")
            t.task_uuid = pt["attrs"].get("id") or t.task_uuid
            t.updated_at = datetime.utcnow()
            for table in ("taskattr", "taskowner", "taskproject", "taskfeature", "link"):
                col = "src_task_id" if table == "link" else "task_id"
                session.exec(text(f"DELETE FROM {table} WHERE {col} = {int(existing_id)}"))
            _upsert_task_attrs(session, existing_id, pt, folder_project)
        else:
            # New task: insert.
            t = Task(
                note_id=note_id, slug=pt["slug"], title=pt["title"],
                status=pt["status"], line=pt["line"], indent=pt["indent"],
                kind=pt.get("kind", "task"), task_uuid=pt["attrs"].get("id") or None,
            )
            session.add(t)
            session.flush()
            slug_to_id[pt["slug"]] = t.id
            _upsert_task_attrs(session, t.id, pt, folder_project)

    # Second pass: resolve parent_task_id.
    # Also clears stale parent when a task has been dedented to root level.
    for pt in new_tasks:
        tid = slug_to_id.get(pt["slug"])
        if not tid:
            continue
        t = session.get(Task, tid)
        if t is None:
            continue
        if not pt["parent_slug"]:
            # Task is now root-level — clear any stale parent from a previous indent.
            if t.parent_task_id is not None:
                t.parent_task_id = None
        else:
            parent_id = slug_to_id.get(pt["parent_slug"])
            if parent_id and t.parent_task_id != parent_id:
                t.parent_task_id = parent_id


def _apply_ref_rows(session: Session, ref_rows: list[dict]) -> None:
    """Apply agenda reference-row write-through overrides.

    Ref-rows can override status, owners, priority, eta, and features.
    Scalar overrides (status, priority, eta) replace the existing value.
    List overrides (owner, feature) are additive — they upsert into both
    the taskattr table AND the corresponding join table (taskowner,
    taskfeature) so that the owner= / feature= filters in list_tasks work
    correctly.  Previously only taskattr was updated, causing owner-filter
    mismatches for tasks whose ownership came from a ref-row.
    """
    for rr in ref_rows:
        ref_id = rr.get("ref_id")
        overrides = rr.get("attrs") or {}
        if not ref_id or not overrides:
            continue
        row = session.exec(
            text("SELECT id FROM task WHERE task_uuid = :rid LIMIT 1")
            .bindparams(rid=ref_id)
        ).first()
        if not row:
            row = session.exec(
                text(
                    "SELECT t.id FROM task t JOIN taskattr a ON a.task_id = t.id "
                    "WHERE a.key = 'id' AND a.value = :rid LIMIT 1"
                ).bindparams(rid=ref_id)
            ).first()
        if not row:
            continue
        try:
            tgt_id = int(row[0])
        except (TypeError, ValueError):
            continue
        tgt = session.get(Task, tgt_id)
        if tgt is None:
            continue
        for key, val in overrides.items():
            if key == "status":
                from ..parser.tokens import normalize_status
                tgt.status = normalize_status(str(val))
            if isinstance(val, list):
                for v in val:
                    session.add(TaskAttr(task_id=tgt_id, key=key, value=str(v), value_norm=str(v).lower()))
                    # Also sync join tables so filter queries work correctly.
                    if key == "owner":
                        u = _get_or_create(session, User, name=str(v))
                        existing_to = session.exec(
                            select(TaskOwner).where(
                                TaskOwner.task_id == tgt_id,
                                TaskOwner.user_id == u.id,
                            )
                        ).first()
                        if not existing_to:
                            session.add(TaskOwner(task_id=tgt_id, user_id=u.id))
                    elif key == "feature":
                        feat = _get_or_create(session, Feature, name=str(v))
                        existing_tf = session.exec(
                            select(TaskFeature).where(
                                TaskFeature.task_id == tgt_id,
                                TaskFeature.feature_id == feat.id,
                            )
                        ).first()
                        if not existing_tf:
                            session.add(TaskFeature(task_id=tgt_id, feature_id=feat.id))
            else:
                session.exec(
                    text("DELETE FROM taskattr WHERE task_id = :tid AND key = :k")
                    .bindparams(tid=tgt_id, k=key)
                )
                session.add(TaskAttr(task_id=tgt_id, key=key, value=str(val), value_norm=str(val).lower()))


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
    _bootstrap_orphan_projects(session)
    return n


def _bootstrap_orphan_projects(session: Session) -> None:
    """Auto-assign the first admin as manager for any project folder that has
    no ProjectMember rows — e.g. folders created outside the UI by git or the
    filesystem.  Without this, non-admin users cannot see or access those
    projects at all.
    """
    admin = session.exec(
        select(User).where(User.is_admin == True).order_by(User.name)  # noqa: E712
    ).first()
    if admin is None:
        return  # No admin yet (fresh install, not seeded) — skip.

    # Derive project names from note paths: "ProjectName/foo.md" → "ProjectName"
    notes = session.exec(select(Note)).all()
    project_names: set[str] = set()
    for note in notes:
        parts = note.path.replace("\\", "/").split("/")
        if len(parts) >= 2:
            project_names.add(parts[0])

    for project_name in sorted(project_names):
        has_member = session.exec(
            select(ProjectMember).where(ProjectMember.project_name == project_name)
        ).first()
        if has_member is None:
            log.warning(
                "Project %r has no members; auto-assigning admin %r as manager.",
                project_name,
                admin.name,
            )
            session.add(ProjectMember(project_name=project_name, user_name=admin.name, role="manager"))
    session.commit()


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
