"""Two-DB archive/unarchive row shuttle (#304, PR 2).

Moves rows for a set of notes between the main index DB (``app.db``) and
the sibling ``archive.db``:

* :func:`archive_notes` copies rows to archive.db, then deletes them
  from main. Sets ``Note.archived=True, archive_kind='user'`` (the row
  itself stays in main so listing endpoints can enumerate it).
* :func:`unarchive_notes` reindexes the ``.md`` from disk (parser
  re-derives identical rows via the ``#id T-XXX`` uuid), then deletes
  the archive-side rows.
* :func:`reconcile_archives` cleans up orphan archive rows if the main
  DB commit failed after the archive DB commit succeeded (SQLite has no
  cross-DB atomic commit).

Design notes:

* ``Task.id`` is **not** preserved across DBs — the archive engine
  assigns fresh autoincrement values. A per-batch main→archive
  id-mapping is built while copying tasks and used to translate child
  rows (``TaskAttr``, ``TaskOwner``, ``TaskProject``, ``TaskFeature``,
  ``Link.src_task_id``). ``Task.task_uuid`` IS preserved and remains the
  stable cross-DB identifier — it's also what the reindex-on-unarchive
  path uses to reproduce the same Task rows in main.
* ``Note`` / ``Project`` / ``Feature`` are matched across DBs by their
  UNIQUE natural keys (``path``, ``name``, ``name``) so the archive
  copy is repeatable and re-archives after unarchive work cleanly.
* ``TaskOwner.user_id`` in archive.db is an opaque integer — the
  ``user`` table is intentionally NOT mirrored (see #304 carve-out).
  Read endpoints (PR 4) resolve owner names by opening a session on the
  main engine.
* Weekly rollover archives (``Note.archive_kind`` in ``{"", "rollover"}``
  or path under ``/_archive/``) are **explicitly rejected** by
  :func:`archive_notes` — this feature does not touch them.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from sqlalchemy import text
from sqlmodel import Session, select

from .config import settings
from .indexer import reindex_file
from .models import (
    Feature, Link, Note, Project, Task, TaskAttr, TaskFeature, TaskOwner,
    TaskProject,
)

log = logging.getLogger(__name__)


ROLLOVER_ARCHIVE_KINDS = frozenset({"", "rollover"})


def _is_rollover_path(rel: str) -> bool:
    return "/_archive/" in f"/{rel}/"


def _archive_note_row(archive: Session, main_note: Note) -> Note:
    """Upsert the ``Note`` row into archive.db keyed by path.

    Sets ``archived=True, archive_kind='user'`` on the archive-side row
    so ``/api/archive/*`` reads know it came from user-driven archive.
    """
    existing = archive.exec(
        select(Note).where(Note.path == main_note.path)
    ).first()
    if existing is not None:
        existing.title = main_note.title
        existing.body_md = main_note.body_md
        existing.mtime = main_note.mtime
        existing.archived = True
        existing.archive_kind = "user"
        archive.add(existing)
        archive.flush()
        return existing
    row = Note(
        path=main_note.path,
        title=main_note.title,
        body_md=main_note.body_md,
        mtime=main_note.mtime,
        created_at=main_note.created_at,
        updated_at=main_note.updated_at,
        archived=True,
        archive_kind="user",
    )
    archive.add(row)
    archive.flush()
    return row


def _archive_project_id(archive: Session, name: str) -> int:
    """Upsert a Project row in archive.db by name; return archive-side id."""
    existing = archive.exec(
        select(Project).where(Project.name == name)
    ).first()
    if existing is not None:
        return existing.id
    row = Project(name=name, archived=True)
    archive.add(row)
    archive.flush()
    return row.id


def _archive_feature_id(archive: Session, name: str) -> int:
    existing = archive.exec(
        select(Feature).where(Feature.name == name)
    ).first()
    if existing is not None:
        return existing.id
    row = Feature(name=name)
    archive.add(row)
    archive.flush()
    return row.id


def _copy_note_to_archive(
    main: Session, archive: Session, note: Note,
) -> dict[str, int]:
    """Copy this note and all its derived rows to archive.db.

    Returns ``{"tasks": N, "attrs": N, "owners": N, "projects": N,
    "features": N, "links": N}`` counts. The caller drives the outer
    transaction; this function only ``flush()`` s.
    """
    archive_note = _archive_note_row(archive, note)

    tasks = main.exec(select(Task).where(Task.note_id == note.id)).all()
    id_map: dict[int, int] = {}  # main_task_id -> archive_task_id
    for t in tasks:
        row = Task(
            note_id=archive_note.id,
            parent_task_id=None,  # patched in a second pass below
            slug=t.slug,
            task_uuid=t.task_uuid,
            title=t.title,
            status=t.status,
            line=t.line,
            indent=t.indent,
            kind=t.kind,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        archive.add(row)
        archive.flush()
        id_map[t.id] = row.id
    # Second pass: translate parent_task_id via the local map. Tasks
    # whose parent lives outside this note (rare — cross-note parenting
    # isn't a normal shape) are stored as NULL.
    for t in tasks:
        if t.parent_task_id and t.parent_task_id in id_map:
            archive.exec(
                text("UPDATE task SET parent_task_id = :p WHERE id = :i")
                .bindparams(p=id_map[t.parent_task_id], i=id_map[t.id])
            )

    counts = {"tasks": len(id_map), "attrs": 0, "owners": 0,
              "projects": 0, "features": 0, "links": 0}
    if not id_map:
        return counts

    main_ids = list(id_map.keys())
    ids_ph = ",".join(str(int(i)) for i in main_ids)

    for attr in main.exec(
        text(f"SELECT task_id, key, value, value_norm FROM taskattr "
             f"WHERE task_id IN ({ids_ph})")
    ).all():
        archive.add(TaskAttr(
            task_id=id_map[attr[0]],
            key=attr[1], value=attr[2], value_norm=attr[3],
        ))
        counts["attrs"] += 1

    for own in main.exec(
        text(f"SELECT task_id, user_id FROM taskowner "
             f"WHERE task_id IN ({ids_ph})")
    ).all():
        archive.add(TaskOwner(task_id=id_map[own[0]], user_id=own[1]))
        counts["owners"] += 1

    proj_ids = {row[1] for row in main.exec(
        text(f"SELECT task_id, project_id FROM taskproject "
             f"WHERE task_id IN ({ids_ph})")
    ).all()}
    proj_id_map: dict[int, int] = {}
    for pid in proj_ids:
        pname = main.exec(select(Project.name).where(Project.id == pid)).first()
        if pname:
            proj_id_map[pid] = _archive_project_id(archive, pname)
            counts["projects"] += 1
    for row in main.exec(
        text(f"SELECT task_id, project_id FROM taskproject "
             f"WHERE task_id IN ({ids_ph})")
    ).all():
        tp_task_id, tp_proj_id = row
        if tp_proj_id in proj_id_map:
            archive.add(TaskProject(
                task_id=id_map[tp_task_id],
                project_id=proj_id_map[tp_proj_id],
            ))

    feat_ids = {row[1] for row in main.exec(
        text(f"SELECT task_id, feature_id FROM taskfeature "
             f"WHERE task_id IN ({ids_ph})")
    ).all()}
    feat_id_map: dict[int, int] = {}
    for fid in feat_ids:
        fname = main.exec(select(Feature.name).where(Feature.id == fid)).first()
        if fname:
            feat_id_map[fid] = _archive_feature_id(archive, fname)
            counts["features"] += 1
    for row in main.exec(
        text(f"SELECT task_id, feature_id FROM taskfeature "
             f"WHERE task_id IN ({ids_ph})")
    ).all():
        tf_task_id, tf_feat_id = row
        if tf_feat_id in feat_id_map:
            archive.add(TaskFeature(
                task_id=id_map[tf_task_id],
                feature_id=feat_id_map[tf_feat_id],
            ))

    for lnk in main.exec(
        text(f"SELECT src_task_id, dst_slug, kind FROM link "
             f"WHERE src_task_id IN ({ids_ph})")
    ).all():
        archive.add(Link(
            src_task_id=id_map[lnk[0]],
            dst_slug=lnk[1], kind=lnk[2],
        ))
        counts["links"] += 1

    archive.flush()
    return counts


def _delete_note_task_rows_from_main(main: Session, note_id: int) -> int:
    """Delete Task + all child rows for this note from the main DB.

    Returns the number of task rows deleted (for the caller's stats).
    """
    task_ids = [
        row[0] for row in main.exec(
            text("SELECT id FROM task WHERE note_id = :nid").bindparams(nid=note_id)
        ).all()
    ]
    if not task_ids:
        return 0
    ids_ph = ",".join(str(int(i)) for i in task_ids)
    for table in ("taskattr", "taskowner", "taskproject", "taskfeature"):
        main.exec(text(f"DELETE FROM {table} WHERE task_id IN ({ids_ph})"))
    main.exec(text(f"DELETE FROM link WHERE src_task_id IN ({ids_ph})"))
    main.exec(text(f"DELETE FROM task WHERE id IN ({ids_ph})"))
    return len(task_ids)


def _delete_note_from_archive(archive: Session, note_path: str) -> int:
    """Delete an archived note's rows from archive.db by path.

    Called by :func:`unarchive_notes` after ``reindex_file`` reproduces
    the same rows in main. Idempotent — safe to re-run for reconcile.
    Returns count of task rows dropped.
    """
    arow = archive.exec(select(Note).where(Note.path == note_path)).first()
    if arow is None:
        return 0
    task_ids = [
        row[0] for row in archive.exec(
            text("SELECT id FROM task WHERE note_id = :nid").bindparams(nid=arow.id)
        ).all()
    ]
    n = 0
    if task_ids:
        ids_ph = ",".join(str(int(i)) for i in task_ids)
        for table in ("taskattr", "taskowner", "taskproject", "taskfeature"):
            archive.exec(text(f"DELETE FROM {table} WHERE task_id IN ({ids_ph})"))
        archive.exec(text(f"DELETE FROM link WHERE src_task_id IN ({ids_ph})"))
        archive.exec(text(f"DELETE FROM task WHERE id IN ({ids_ph})"))
        n = len(task_ids)
    archive.exec(text("DELETE FROM note WHERE id = :i").bindparams(i=arow.id))
    return n


def archive_notes(
    main: Session, archive: Session, note_ids: Iterable[int],
) -> dict[str, Any]:
    """Archive ``note_ids`` — copy their rows to archive.db, delete from main.

    Skips notes that are already user-archived or are rollover archives
    (path under ``/_archive/`` or ``archive_kind`` in the rollover set).
    The caller is responsible for RBAC.
    """
    note_ids = list(note_ids)
    if not note_ids:
        return {"archived": 0, "notes": [], "evicted_tasks": 0}

    archived_paths: list[str] = []
    total_tasks = 0

    # ── Phase 1: copy every note's rows into archive.db, commit. ──────
    for nid in note_ids:
        note = main.get(Note, nid)
        if note is None:
            continue
        if note.archived and note.archive_kind == "user":
            continue  # already archived; idempotent
        if note.archive_kind in ROLLOVER_ARCHIVE_KINDS and _is_rollover_path(note.path):
            log.info(
                "archive_notes: skipping rollover archive %r (kind=%r)",
                note.path, note.archive_kind,
            )
            continue
        counts = _copy_note_to_archive(main, archive, note)
        total_tasks += counts["tasks"]
        archived_paths.append(note.path)
    archive.commit()

    # ── Phase 2: flip the flags + evict rows from main, commit. ───────
    # If this commit fails after archive.commit() above succeeded, the
    # archive.db copies become orphans reachable via /api/archive/reconcile.
    for nid in note_ids:
        note = main.get(Note, nid)
        if note is None:
            continue
        if note.archive_kind in ROLLOVER_ARCHIVE_KINDS and _is_rollover_path(note.path):
            continue
        _delete_note_task_rows_from_main(main, nid)
        note.archived = True
        note.archive_kind = "user"
        main.add(note)
    main.commit()

    return {
        "archived": len(archived_paths),
        "notes": archived_paths,
        "evicted_tasks": total_tasks,
    }


def unarchive_notes(
    main: Session, archive: Session, note_ids: Iterable[int],
) -> dict[str, Any]:
    """Unarchive ``note_ids`` — reindex from disk, then drop archive rows.

    Refuses to touch rollover archives (they were never in archive.db
    to begin with). Refuses if the on-disk file is missing (409 in the
    caller).
    """
    note_ids = list(note_ids)
    if not note_ids:
        return {"unarchived": 0, "notes": [], "reindexed_tasks": 0}

    unarchived_paths: list[str] = []
    total_tasks = 0
    missing: list[str] = []

    for nid in note_ids:
        note = main.get(Note, nid)
        if note is None:
            continue
        if note.archive_kind != "user":
            log.info(
                "unarchive_notes: skipping non-user-archive %r (kind=%r)",
                note.path, note.archive_kind,
            )
            continue
        full = settings.notes_dir / note.path
        if not full.exists():
            missing.append(note.path)
            continue
        note.archived = False
        note.archive_kind = ""
        main.add(note)
        main.flush()
        reindexed = reindex_file(full, main)
        if reindexed is not None:
            row = main.exec(
                text("SELECT COUNT(*) FROM task WHERE note_id = :nid")
                .bindparams(nid=reindexed.id)
            ).first()
            total_tasks += int(row[0]) if row else 0
        unarchived_paths.append(note.path)
    main.commit()

    # Drop archive-side rows only after main is durably reindexed.
    for path in unarchived_paths:
        _delete_note_from_archive(archive, path)
    archive.commit()

    result: dict[str, Any] = {
        "unarchived": len(unarchived_paths),
        "notes": unarchived_paths,
        "reindexed_tasks": total_tasks,
    }
    if missing:
        result["missing"] = missing
    return result


def reconcile_archives(main: Session, archive: Session) -> dict[str, Any]:
    """Clean up orphan archive rows after a two-DB txn crash.

    Two failure modes:

    * Archive committed, main crashed BEFORE flag flip: main-DB task
      rows still exist. Fix: drop the archive-side copies.
    * Archive committed, main crashed AFTER task delete but BEFORE flag
      flip: main has no task rows but ``Note.archived`` is False.
      Fix: flip the flag.

    Returns counts of what was reconciled.
    """
    orphans_dropped = 0
    flags_fixed = 0

    # Case 1: task_uuid appears in BOTH DBs — archive copy is orphaned.
    archive_uuids = {
        row[0] for row in archive.exec(
            text("SELECT task_uuid FROM task WHERE task_uuid IS NOT NULL")
        ).all()
    }
    if archive_uuids:
        placeholders = ",".join(f":u{i}" for i in range(len(archive_uuids)))
        params = {f"u{i}": u for i, u in enumerate(archive_uuids)}
        colliding = {
            row[0] for row in main.exec(
                text(f"SELECT task_uuid FROM task WHERE task_uuid IN ({placeholders})")
                .bindparams(**params)
            ).all()
        }
        # Drop archive-side task rows for colliding uuids + their children.
        # Do it note-at-a-time so children are cleaned by our existing helper.
        for uuid in colliding:
            arch_task = archive.exec(
                select(Task).where(Task.task_uuid == uuid)
            ).first()
            if arch_task is None:
                continue
            # Drop just this one task and its children; leave siblings alone.
            ids_ph = str(int(arch_task.id))
            for table in ("taskattr", "taskowner", "taskproject", "taskfeature"):
                archive.exec(text(
                    f"DELETE FROM {table} WHERE task_id IN ({ids_ph})"
                ))
            archive.exec(text(f"DELETE FROM link WHERE src_task_id = {ids_ph}"))
            archive.exec(text(f"DELETE FROM task WHERE id = {ids_ph}"))
            orphans_dropped += 1

    # Case 2: Note row in archive.db but main-side flag is still False.
    for arow in archive.exec(select(Note)).all():
        mnote = main.exec(select(Note).where(Note.path == arow.path)).first()
        if mnote is not None and (not mnote.archived or mnote.archive_kind != "user"):
            mnote.archived = True
            mnote.archive_kind = "user"
            main.add(mnote)
            flags_fixed += 1
    main.commit()
    archive.commit()

    return {
        "orphans_dropped": orphans_dropped,
        "flags_fixed": flags_fixed,
    }
