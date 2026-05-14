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
from typing import Iterable, Optional

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


def apply_single_task_patch_to_index(
    session: Session,
    *,
    note_id: int,
    task_id: int,
    new_body_md: str,
    new_mtime: float,
    line_shift: int = 0,
    line_shift_pivot: int = -1,
    status: str | None = None,
    priority: str | None = None,
    eta: str | None = None,
    owners: list[str] | None = None,
    features: list[str] | None = None,
    add_note: str | None = None,
) -> None:
    """Cheap variant of :func:`reindex_file` for a single-task popover patch.

    Skips the parser, per-task fingerprint diff, and ``_apply_ref_rows``.
    Only the affected ``Note`` (body / mtime / FTS5), the affected ``task``
    row, and that task's child rows (``TaskAttr`` / ``TaskOwner`` /
    ``TaskFeature``) are touched. Other tasks in the file see only a
    cheap ``UPDATE task SET line = line + :n WHERE line > :pivot``.

    Argument semantics:

    * ``new_body_md`` / ``new_mtime``: post-mutation file contents and
      mtime — the caller has already written them to disk under the lock.
    * ``line_shift`` / ``line_shift_pivot``: shift ``task.line`` by
      ``line_shift`` for every task with ``line > line_shift_pivot``.
      Use ``line_shift=0`` (default) when the mutation didn't change line
      counts (e.g. an in-place attribute replacement).
    * ``status`` / ``priority`` / ``eta`` / ``owners`` / ``features`` /
      ``add_note``: pass only the fields the patch actually changed; pass
      ``None`` to leave that field alone in the index. ``[]`` for owners /
      features means "clear all".

    Caller MUST hold the per-file lock and run this in the same DB
    transaction as the on-disk write so a crash cannot leave indexes
    desynced.
    """
    from ..parser.tokens import REGISTRY  # lazy: avoid potential cycle

    note = session.get(Note, note_id)
    if note is None:
        raise ValueError(f"note_id {note_id} not found")
    note.body_md = new_body_md
    note.mtime = new_mtime
    note.updated_at = datetime.utcnow()

    session.exec(text("DELETE FROM notes_fts WHERE rowid = :id").bindparams(id=note.id))
    session.exec(
        text("INSERT INTO notes_fts(rowid, title, body_md) VALUES (:id, :t, :b)")
        .bindparams(id=note.id, t=note.title, b=new_body_md)
    )

    if line_shift:
        session.exec(
            text(
                "UPDATE task SET line = line + :n "
                "WHERE note_id = :nid AND line > :pivot"
            ).bindparams(n=line_shift, pivot=line_shift_pivot, nid=note_id)
        )

    task = session.get(Task, task_id)
    if task is None:
        raise ValueError(f"task_id {task_id} not found")

    def _set_attr(key: str, raw_value: str | None) -> None:
        """Replace every ``TaskAttr`` row for ``key`` on this task.

        ``raw_value`` is the post-strip user input. Empty / None clears the
        attr (no row inserted). The normalized form mirrors what the parser
        would compute via REGISTRY[key].normalize.
        """
        session.exec(
            text("DELETE FROM taskattr WHERE task_id = :tid AND key = :k")
            .bindparams(tid=task_id, k=key)
        )
        v = (raw_value or "").strip()
        if not v:
            return
        spec = REGISTRY.get(key)
        norm: object | None = None
        if spec and spec.normalize is not None:
            try:
                norm = spec.normalize(v)
            except Exception:
                norm = None
        norm_str = None if norm is None else str(norm)
        session.add(TaskAttr(task_id=task_id, key=key, value=v, value_norm=norm_str))

    if status is not None:
        task.status = status
        _set_attr("status", status)

    if priority is not None:
        _set_attr("priority", priority)

    if eta is not None:
        _set_attr("eta", eta)

    if owners is not None:
        cleaned = [o.strip().lstrip("@") for o in owners if o and o.strip()]
        # Wipe both the join-table rows and the legacy mirror TaskAttr rows.
        session.exec(
            text("DELETE FROM taskowner WHERE task_id = :tid").bindparams(tid=task_id)
        )
        session.exec(
            text("DELETE FROM taskattr WHERE task_id = :tid AND key = 'owner'")
            .bindparams(tid=task_id)
        )
        for name in cleaned:
            user = _get_or_create(session, User, name=name)
            session.add(TaskOwner(task_id=task_id, user_id=user.id))
            session.add(TaskAttr(
                task_id=task_id, key="owner", value=name, value_norm=name.lower(),
            ))

    if features is not None:
        cleaned = [f.strip() for f in features if f and f.strip()]
        session.exec(
            text("DELETE FROM taskfeature WHERE task_id = :tid").bindparams(tid=task_id)
        )
        session.exec(
            text("DELETE FROM taskattr WHERE task_id = :tid AND key = 'feature'")
            .bindparams(tid=task_id)
        )
        for name in cleaned:
            feat = _get_or_create(session, Feature, name=name)
            session.add(TaskFeature(task_id=task_id, feature_id=feat.id))
            session.add(TaskAttr(
                task_id=task_id, key="feature", value=name, value_norm=name.lower(),
            ))

    if add_note is not None and add_note.strip():
        for raw_line in add_note.split("\n"):
            txt = raw_line.strip()
            if not txt:
                continue
            session.add(TaskAttr(
                task_id=task_id, key="note", value=txt, value_norm=None,
            ))


def update_note_body_only(
    session: Session,
    *,
    note_id: int,
    new_body_md: str,
    new_mtime: float,
) -> None:
    """Refresh ``Note.body_md`` / ``mtime`` and rebuild that note's FTS5 row.

    Used by the ref-row propagation loop after a popover patch: the
    canonical task's index rows are already updated by the canonical
    write, and ref files only need their body / search index synced. The
    parser, ref-row override re-application, and per-task fingerprinting
    are skipped — a full reindex would be wasted work for a write that
    just rewrote one ref-row line in-place.
    """
    note = session.get(Note, note_id)
    if note is None:
        raise ValueError(f"note_id {note_id} not found")
    note.body_md = new_body_md
    note.mtime = new_mtime
    note.updated_at = datetime.utcnow()
    session.exec(text("DELETE FROM notes_fts WHERE rowid = :id").bindparams(id=note.id))
    session.exec(
        text("INSERT INTO notes_fts(rowid, title, body_md) VALUES (:id, :t, :b)")
        .bindparams(id=note.id, t=note.title, b=new_body_md)
    )


def insert_single_task_into_index(
    session: Session,
    *,
    note_id: int,
    new_body_md: str,
    new_mtime: float,
    new_task_uuid: str,
    lines_inserted: int = 1,
    folder_project: str | None = None,
) -> int:
    """Index a single newly-inserted task (create-task / AR-add fast path).

    The caller has already written ``new_body_md`` to disk under the
    file lock and knows the new task's stamped ``#id`` (``new_task_uuid``).
    We parse the new body to recover the new task's slug / title /
    line / indent / attrs (parsing is cheap; the expensive parts of
    :func:`reindex_file` are the bulk attr loads + per-task fingerprint
    diff, which we skip).

    Existing tasks in the same file at or below the new task's parsed
    line are shifted downward by ``lines_inserted`` so their ``line``
    columns stay aligned with the post-insert file. Pass the actual
    number of newly added lines (defaults to ``1`` for a single bullet).
    """
    update_note_body_only(
        session,
        note_id=note_id,
        new_body_md=new_body_md,
        new_mtime=new_mtime,
    )

    parsed = parse(new_body_md)
    new_pt = next(
        (p for p in parsed["tasks"] if p["attrs"].get("id") == new_task_uuid),
        None,
    )
    if new_pt is None:
        raise ValueError(
            f"new task uuid {new_task_uuid!r} not found in parsed body"
        )

    new_line = int(new_pt["line"])
    if lines_inserted:
        # Shift everything at or below the insertion point. The new task
        # isn't in the DB yet, so it can't be incorrectly bumped.
        session.exec(
            text(
                "UPDATE task SET line = line + :n "
                "WHERE note_id = :nid AND line >= :pivot"
            ).bindparams(n=lines_inserted, pivot=new_line, nid=note_id)
        )

    parent_task_id: int | None = None
    if new_pt.get("parent_slug"):
        parent = session.exec(
            select(Task).where(
                Task.note_id == note_id,
                Task.slug == new_pt["parent_slug"],
            )
        ).first()
        if parent:
            parent_task_id = parent.id

    t = Task(
        note_id=note_id,
        slug=new_pt["slug"],
        title=new_pt["title"],
        status=new_pt["status"],
        line=new_line,
        indent=new_pt["indent"],
        kind=new_pt.get("kind", "task"),
        task_uuid=new_task_uuid,
        parent_task_id=parent_task_id,
    )
    session.add(t)
    session.flush()
    _upsert_task_attrs(session, t.id, new_pt, folder_project)
    return t.id


def delete_single_task_from_index(
    session: Session,
    *,
    note_id: int,
    task_id: int,
    new_body_md: str,
    new_mtime: float,
    line_shift_pivot: int,
    line_shift: int,
) -> None:
    """Remove one task (and its descendant subtree) from the index.

    The caller has already excised the task block from the .md file and
    written it to disk under the file lock. ``line_shift`` should be
    negative — the number of removed lines — and ``line_shift_pivot`` is
    the line number of the deleted task (so tasks strictly below shift
    upward).

    Children are recursively cascaded via ``parent_task_id``. This does
    NOT call ``_apply_ref_rows`` — ref rows pointing at a deleted task
    will simply fail to resolve until the next full reindex of those
    files; that's acceptable for the rare delete case.
    """
    update_note_body_only(
        session,
        note_id=note_id,
        new_body_md=new_body_md,
        new_mtime=new_mtime,
    )

    # Collect the task and all transitive descendants in this note.
    to_delete: list[int] = []
    frontier: list[int] = [task_id]
    while frontier:
        cur = frontier.pop()
        to_delete.append(cur)
        kids = session.exec(
            select(Task.id).where(
                Task.note_id == note_id,
                Task.parent_task_id == cur,
            )
        ).all()
        frontier.extend(int(k) for k in kids)

    placeholders = ",".join(str(int(i)) for i in to_delete)
    for table in ("taskattr", "taskowner", "taskproject", "taskfeature"):
        session.exec(text(f"DELETE FROM {table} WHERE task_id IN ({placeholders})"))
    session.exec(text(f"DELETE FROM link WHERE src_task_id IN ({placeholders})"))
    session.exec(text(f"DELETE FROM task WHERE id IN ({placeholders})"))

    if line_shift:
        session.exec(
            text(
                "UPDATE task SET line = line + :n "
                "WHERE note_id = :nid AND line > :pivot"
            ).bindparams(n=line_shift, pivot=line_shift_pivot, nid=note_id)
        )


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
    """Walk the notes directory, (re)index every ``.md`` file, and reconcile
    the DB against disk by sweeping orphan ``Note`` rows whose backing file
    no longer exists.

    Returns the number of files indexed.  Stores the orphan-sweep count on
    ``WATCHER_STATE['orphans_swept_last']`` and ``['orphans_swept_total']``
    for diagnostics (#207).
    """
    n = 0
    present: set[str] = set()
    for path in sorted(settings.notes_dir.rglob("*.md")):
        # Skip the per-write backup tree — paths under any ``.trash`` segment
        # are not real notes.  ``rglob('*.md')`` already excludes ``.bak``
        # files but we also want to ignore any genuine ``.md`` someone may
        # have dropped into ``.trash/`` for hand recovery.
        try:
            rel = str(path.relative_to(settings.notes_dir))
        except ValueError:
            continue
        if rel.startswith(".trash/") or "/.trash/" in rel or "/." in "/" + rel:
            # Skip dotfile dirs (.trash, .git, .vscode, etc.)
            continue
        reindex_file(path, session)
        present.add(rel)
        n += 1

    # ── Reconcile: drop Note rows whose file is gone from disk ───────────
    # Without this, a delete event missed by the watcher (common on NFS
    # mounts where inotify is unreliable) leaves orphan tasks haunting the
    # UI and search results forever.  remove_path() cascades through
    # _delete_task_children + notes_fts.  See issue #207.
    orphans = 0
    all_notes = session.exec(select(Note)).all()
    for note in all_notes:
        if note.path not in present:
            remove_path(note.path, session)
            orphans += 1
    if orphans:
        log.info("reindex_all: swept %d orphan note row(s)", orphans)
    WATCHER_STATE["orphans_swept_last"] = orphans
    WATCHER_STATE["orphans_swept_total"] = (
        WATCHER_STATE.get("orphans_swept_total", 0) + orphans
    )

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


# --- watcher (#150) -------------------------------------------------------
#
# Module-level state populated by ``watch_loop``.  Exposed via
# ``GET /api/admin/watcher_status`` so operators can confirm the watcher is
# alive and which mode it picked (event vs polling).  See #150 for the NFS
# silence bug that motivated this.
WATCHER_STATE: dict = {
    "started_at": None,
    "last_event_at": None,
    "events_total": 0,
    "errors_total": 0,
    "mode": None,         # "event" | "polling"
    "fs_type": None,      # detected via /proc/mounts (best-effort)
    "notes_dir": None,
    "force_polling": None,
    "poll_delay_ms": None,
    "orphans_swept_last": 0,
    "orphans_swept_total": 0,
}

# Filesystems that do not deliver inotify events for off-host writes and
# therefore require polling for ``watch_loop`` to be useful.
_POLLING_FS_TYPES = frozenset({
    "nfs", "nfs3", "nfs4", "nfsv3", "nfsv4",
    "cifs", "smb", "smb2", "smb3", "smbfs",
    "fuse.sshfs", "fuse.davfs", "fuse.s3fs", "fuse.gcsfuse",
})


def _detect_fs_type(path: Path, mounts_file: str = "/proc/mounts") -> Optional[str]:
    """Return the kernel filesystem type for *path* by scanning /proc/mounts.

    Picks the longest mountpoint prefix that contains *path*.  Returns
    ``None`` on any error so callers fall back to safe defaults.
    """
    try:
        target = str(Path(path).resolve())
        with open(mounts_file, "r", encoding="utf-8", errors="replace") as fh:
            best = ("", None)  # (mountpoint, fstype)
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mp = parts[1]
                fstype = parts[2]
                if (target == mp or target.startswith(mp.rstrip("/") + "/")) \
                        and len(mp) > len(best[0]):
                    best = (mp, fstype)
            return best[1]
    except Exception:
        return None


def _compute_force_polling(notes_dir: Path) -> tuple[bool, Optional[str]]:
    """Return ``(force_polling, fs_type)``.

    Honours the explicit ``settings.watcher_force_polling`` toggle when set;
    otherwise auto-enables polling on filesystems known to drop inotify
    events (NFS, CIFS, network FUSE).
    """
    fs_type = _detect_fs_type(notes_dir)
    if settings.watcher_force_polling is not None:
        return bool(settings.watcher_force_polling), fs_type
    return (fs_type or "").lower() in _POLLING_FS_TYPES, fs_type


async def watch_loop() -> None:
    """Background task: watch notes_dir and reindex on change."""
    from watchfiles import Change, awatch

    init_db()
    force_polling, fs_type = _compute_force_polling(settings.notes_dir)
    poll_delay_ms = int(settings.watcher_poll_delay_ms)
    mode = "polling" if force_polling else "event"

    WATCHER_STATE.update({
        "started_at": _utcnow_iso(),
        "mode": mode,
        "fs_type": fs_type,
        "notes_dir": str(settings.notes_dir),
        "force_polling": force_polling,
        "poll_delay_ms": poll_delay_ms,
    })
    log.info(
        "Indexer watching %s (mode=%s fs=%s poll_delay_ms=%d)",
        settings.notes_dir, mode, fs_type, poll_delay_ms,
    )

    async for changes in awatch(
        settings.notes_dir,
        force_polling=force_polling,
        poll_delay_ms=poll_delay_ms,
    ):
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
                        WATCHER_STATE["errors_total"] += 1
                        log.exception("reindex failed for %s", path)
                WATCHER_STATE["events_total"] += 1
                WATCHER_STATE["last_event_at"] = _utcnow_iso()
        await asyncio.sleep(0)  # cooperative


def _utcnow_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
