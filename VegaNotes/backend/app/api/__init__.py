"""REST API routers."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import bindparam, text
from sqlmodel import Session, select

from ..auth import hash_password, verify_password, require_admin, require_user
from ..config import settings
from ..db import get_session
from ..indexer import (
    apply_single_task_patch_to_index,
    delete_single_task_from_index,
    insert_single_task_into_index,
    reindex_all, reindex_file, remove_path,
    update_note_body_only,
    WATCHER_STATE,
)
from ..markdown_ops import (
    inject_missing_ids, replace_attr, replace_multi_attr, replace_notes,
    append_note, generate_task_id, existing_ids, delete_task_block,
    insert_ar_under_task,
    remove_attr, roll_to_next_week, update_task_status,
    find_ref_row_lines, patch_ref_rows, insert_ar_ref_row_after,
)
from ..models import (
    ActivityEvent, Feature, Link, Note, Project, ProjectMember, Task, TaskAttr,
    TaskFeature, TaskOwner, TaskProject, User,
)
from ..parser import parse
from ..safe_io import (
    StaleWriteError, _safe_write_unlocked, etag_for, etag_for_bytes, safe_write, with_file_lock,
)
from .. import gamify
from ..phonebook import get_phonebook

router = APIRouter(dependencies=[Depends(require_user)])

_PRIORITY_LABELS = {"p0": 0, "p1": 1, "p2": 2, "p3": 3, "high": 1, "med": 2, "medium": 2, "low": 3}


def _priority_rank(value_norm: str) -> int:
    """Convert a stored priority value_norm to a sortable integer rank.

    Handles both the new numeric form ('0','1','2','3') written by
    parse_priority_rank and legacy label form ('p1','high',…) written
    by older indexer versions before normalisation was wired up.
    """
    try:
        return int(value_norm)
    except (ValueError, TypeError):
        return _PRIORITY_LABELS.get((value_norm or "").lower(), 999)


# ---------- RBAC helpers ----------------------------------------------------

def _project_for_path(rel_path: str) -> Optional[str]:
    """Top-level folder of a note path is its project. None for root-level files."""
    parts = Path(rel_path).parts
    return parts[0] if len(parts) >= 2 else None


def _user_role_for_project(s: Session, user: str, project: Optional[str]) -> str:
    """Returns 'manager' | 'member' | 'none'. Admins are always managers."""
    u = s.exec(select(User).where(User.name == user)).first()
    if u is not None and u.is_admin:
        return "manager"
    if project is None:
        return "manager"  # root-level notes are unowned/open
    pm = s.exec(
        select(ProjectMember).where(
            ProjectMember.project_name == project,
            ProjectMember.user_name == user,
        )
    ).first()
    return pm.role if pm else "none"


def _require_project_access(
    s: Session, user: str, project: Optional[str], *, need_manager: bool = False
) -> str:
    role = _user_role_for_project(s, user, project)
    if role == "none":
        raise HTTPException(403, f"no access to project '{project}'")
    if need_manager and role != "manager":
        raise HTTPException(403, "manager role required")
    return role


# ---------- helpers ---------------------------------------------------------

def _task_to_dict(s: Session, t: Task, *, include_children: bool = False) -> dict[str, Any]:
    attrs = s.exec(select(TaskAttr).where(TaskAttr.task_id == t.id)).all()
    owners = s.exec(
        select(User.name).join(TaskOwner, TaskOwner.user_id == User.id)
        .where(TaskOwner.task_id == t.id)
    ).all()
    projects = s.exec(
        select(Project.name).join(TaskProject, TaskProject.project_id == Project.id)
        .where(TaskProject.task_id == t.id)
    ).all()
    features = s.exec(
        select(Feature.name).join(TaskFeature, TaskFeature.feature_id == Feature.id)
        .where(TaskFeature.task_id == t.id)
    ).all()
    attr_map: dict[str, Any] = {}
    for a in attrs:
        if a.key in attr_map:
            cur = attr_map[a.key]
            attr_map[a.key] = (cur if isinstance(cur, list) else [cur]) + [a.value]
        else:
            attr_map[a.key] = a.value
    out: dict[str, Any] = {
        "id": t.id,
        "task_uuid": t.task_uuid,
        "slug": t.slug,
        "title": t.title,
        "status": t.status,
        "kind": t.kind,
        "note_id": t.note_id,
        "parent_task_id": t.parent_task_id,
        "owners": owners,
        "projects": projects,
        "features": features,
        "attrs": attr_map,
        "eta": next((a.value_norm for a in attrs if a.key == "eta"), None),
        "priority_rank": next(
            (_priority_rank(a.value_norm) for a in attrs if a.key == "priority" and a.value_norm),
            999,
        ),
        "notes": "\n".join(a.value for a in attrs if a.key == "note"),
        "note_history": [a.value for a in attrs if a.key == "note"],
    }
    if include_children:
        kids = s.exec(
            select(Task).where(Task.parent_task_id == t.id).order_by(Task.line)
        ).all()
        kid_ids = [c.id for c in kids]
        # Batch lookups so a parent with N children still costs O(1)
        # joined queries, not O(N) per related collection.
        eta_by_kid: dict[int, TaskAttr] = {}
        owners_by_kid: dict[int, list[str]] = {kid: [] for kid in kid_ids}
        projects_by_kid: dict[int, list[str]] = {kid: [] for kid in kid_ids}
        features_by_kid: dict[int, list[str]] = {kid: [] for kid in kid_ids}
        if kid_ids:
            for a in s.exec(
                select(TaskAttr).where(
                    TaskAttr.task_id.in_(kid_ids), TaskAttr.key == "eta"
                )
            ).all():
                eta_by_kid.setdefault(a.task_id, a)
            for tid, name in s.exec(
                select(TaskOwner.task_id, User.name)
                .join(User, User.id == TaskOwner.user_id)
                .where(TaskOwner.task_id.in_(kid_ids))
            ).all():
                owners_by_kid[tid].append(name)
            for tid, name in s.exec(
                select(TaskProject.task_id, Project.name)
                .join(Project, Project.id == TaskProject.project_id)
                .where(TaskProject.task_id.in_(kid_ids))
            ).all():
                projects_by_kid[tid].append(name)
            for tid, name in s.exec(
                select(TaskFeature.task_id, Feature.name)
                .join(Feature, Feature.id == TaskFeature.feature_id)
                .where(TaskFeature.task_id.in_(kid_ids))
            ).all():
                features_by_kid[tid].append(name)
        out["children"] = []
        for c in kids:
            eta_attr = eta_by_kid.get(c.id)
            out["children"].append({
                "id": c.id,
                "task_uuid": c.task_uuid,
                "slug": c.slug,
                "title": c.title,
                "status": c.status,
                "kind": c.kind,
                "parent_task_id": c.parent_task_id,
                "line": c.line,
                # `eta` keeps its historical value_norm shape for back-compat;
                # `eta_raw` carries the user-typed string so clients (e.g. vn
                # --tree) can render parents and children consistently.
                "eta": eta_attr.value_norm if eta_attr else None,
                "eta_raw": eta_attr.value if eta_attr else None,
                "owners": owners_by_kid.get(c.id, []),
                "projects": projects_by_kid.get(c.id, []),
                "features": features_by_kid.get(c.id, []),
            })
    return out


def _split(csv: Optional[str]) -> list[str]:
    return [x.strip() for x in csv.split(",")] if csv else []


import re as _re
_UUID_RE = _re.compile(r"^T-[0-9A-Z]{6,}$")


def _resolve_task(ref: str, s: Session) -> Task:
    """Resolve a task by int PK or by T-XXXXXX uuid string.

    Raises HTTPException(404) if not found.
    """
    if _UUID_RE.match(ref):
        t = s.exec(select(Task).where(Task.task_uuid == ref)).first()
    else:
        try:
            t = s.get(Task, int(ref))
        except (ValueError, TypeError):
            t = None
    if t is None:
        raise HTTPException(404, f"task '{ref}' not found")
    return t


# ---------- notes -----------------------------------------------------------

class NoteIn(BaseModel):
    path: str
    body_md: str
    # Optional; when provided, the server requires the file's current sha256
    # etag to match exactly. Mismatch -> 409 with the current content + etag
    # so the client can reconcile. See issue #60.
    if_match: Optional[str] = None


@router.get("/notes")
def list_notes(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    notes = s.exec(select(Note).order_by(Note.updated_at.desc())).all()
    return [{"id": n.id, "path": n.path, "title": n.title, "updated_at": n.updated_at} for n in notes]


@router.get("/notes/etag")
def note_etag(
    path: str = Query(..., description="Repo-relative note path"),
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Cheap freshness check (#153).

    Returns just ``{etag, mtime}`` for a note's on-disk file so the editor
    can poll for out-of-band changes without transferring the full body on
    every tick.  The etag matches what ``GET /notes/{id}`` would return.
    """
    if ".." in path or path.startswith("/"):
        raise HTTPException(400, "invalid path")
    project = _project_for_path(path)
    if _user_role_for_project(s, user, project) == "none":
        raise HTTPException(403, "no access")
    full = settings.notes_dir / path
    if not full.exists():
        raise HTTPException(404, "note not found")
    disk_md = full.read_text(encoding="utf-8")
    return {
        "path": path,
        "etag": etag_for_bytes(disk_md.encode()),
        "mtime": full.stat().st_mtime,
    }


@router.get("/notes/{note_id}")
def get_note(note_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    n = s.get(Note, note_id)
    if not n:
        raise HTTPException(404, "note not found")
    full = settings.notes_dir / n.path
    # Always read body_md from disk so the editor never shows stale DB-cached
    # content.  The DB copy is only authoritative for structured queries
    # (tasks, attrs, FTS); the canonical document is always the .md file.
    if full.exists():
        disk_md = full.read_text(encoding="utf-8")
        disk_etag = etag_for_bytes(disk_md.encode())
    else:
        disk_md = n.body_md  # fallback if file somehow missing
        disk_etag = etag_for(full)
    return {
        "id": n.id, "path": n.path, "title": n.title,
        "body_md": disk_md, "updated_at": n.updated_at,
        "etag": disk_etag,
    }


@router.put("/notes")
def upsert_note(
    body: NoteIn,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
    if_match: Optional[str] = Header(None, alias="If-Match"),
) -> dict[str, Any]:
    if ".." in body.path or body.path.startswith("/"):
        raise HTTPException(400, "invalid path")
    project = _project_for_path(body.path)
    role = _user_role_for_project(s, user, project)
    if role == "none" or (role == "member" and project is not None):
        raise HTTPException(403, "manager role required to write notes")
    full = settings.notes_dir / body.path
    expected = body.if_match if body.if_match is not None else if_match
    pre_existing = full.exists()
    pre_body = full.read_text(encoding="utf-8") if pre_existing else ""
    try:
        new_etag = safe_write(
            full, body.body_md,
            notes_dir=settings.notes_dir, expected_etag=expected,
        )
    except StaleWriteError as e:
        # 409 Conflict — body carries current content + etag for the
        # client to surface a recovery / merge UI. See issue #60.
        raise HTTPException(
            status_code=409,
            detail={
                "error": "stale_write",
                "message": "the file changed under you; reload before saving",
                "current_content": e.current_content,
                "current_etag": e.current_etag,
            },
        )
    note = reindex_file(full, s)
    awarded: list[str] = []
    if not pre_existing:
        awarded = gamify.record_event(s, user, gamify.NOTE_CREATED, ref=body.path)
    elif pre_body.strip() != body.body_md.strip():
        # Skip whitespace-only / no-op writes so streaks aren't gamed by
        # repeatedly saving an unchanged file.
        awarded = gamify.record_event(s, user, gamify.NOTE_EDITED, ref=body.path)
    out: dict[str, Any] = {"id": note.id, "path": note.path, "etag": new_etag}
    if awarded:
        out["awarded_badges"] = awarded
    return out


class RollNextWeekIn(BaseModel):
    path: str
    overwrite: bool = False


@router.post("/notes/next-week")
def roll_note_next_week(
    body: RollNextWeekIn,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Create a follow-up note for the next Intel work week.

    Reads the source note, drops every `!task`/`!ar` line whose normalized
    status is `done` (and any nested sub-items), bumps every `wwN[.x]` token
    matching the source filename's WW number by +1, and writes the result to
    a sibling file with the bumped basename. Returns the new path.
    """
    src_rel = body.path
    if ".." in src_rel or src_rel.startswith("/"):
        raise HTTPException(400, "invalid path")
    project = _project_for_path(src_rel)
    role = _user_role_for_project(s, user, project)
    if role == "none" or (role == "member" and project is not None):
        raise HTTPException(403, "manager role required to create notes")
    src_full = settings.notes_dir / src_rel
    if not src_full.exists():
        raise HTTPException(404, "source note not found")
    # Read+write under the source file's lock so a concurrent edit can't
    # interleave between the read and the write-back of injected IDs.
    with with_file_lock(src_full):
        src_md = src_full.read_text(encoding="utf-8")
        try:
            new_md, new_base, cur, nxt, patched_src = roll_to_next_week(src_md, src_full.name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        dst_full = src_full.parent / new_base
        dst_rel = str(dst_full.relative_to(settings.notes_dir))
        if dst_full.exists() and not body.overwrite:
            raise HTTPException(409, f"target note already exists: {dst_rel}")
        if patched_src != src_md:
            _safe_write_unlocked(src_full, patched_src, notes_dir=settings.notes_dir)
            reindex_file(src_full, s)
    # Destination is a different file -> different lock domain. safe_write
    # creates it atomically and the existence check above + safe_write's
    # internal lock together prevent a parallel create from clobbering us
    # in the rare overwrite=True case.
    safe_write(dst_full, new_md, notes_dir=settings.notes_dir)
    note = reindex_file(dst_full, s)
    return {"id": note.id, "path": note.path, "from_ww": cur, "to_ww": nxt}


class StampIdsIn(BaseModel):
    path: str


@router.post("/notes/stamp-ids")
def stamp_task_ids(
    body: StampIdsIn,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Inject stable `#id <ID>` tokens into every `!task`/`!ar` line in the
    note that doesn't already have one. Idempotent. Used to opt a note into
    cross-week deduplication without waiting for "Next Week Agenda".
    """
    rel = body.path
    if ".." in rel or rel.startswith("/"):
        raise HTTPException(400, "invalid path")
    project = _project_for_path(rel)
    role = _user_role_for_project(s, user, project)
    if role == "none" or (role == "member" and project is not None):
        raise HTTPException(403, "manager role required to modify notes")
    full = settings.notes_dir / rel
    if not full.exists():
        raise HTTPException(404, "note not found")
    # RMW under the file lock so a concurrent edit can't interleave between
    # read and the ID-injection write-back.
    with with_file_lock(full):
        src_md = full.read_text(encoding="utf-8")
        patched, mapping = inject_missing_ids(src_md)
        injected = len(mapping)
        if patched != src_md:
            _safe_write_unlocked(full, patched, notes_dir=settings.notes_dir)
            reindex_file(full, s)
    return {"path": rel, "injected": injected, "body_md": patched}


@router.delete("/notes/{note_id}")
def delete_note(
    note_id: int,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, str]:
    n = s.get(Note, note_id)
    if not n:
        raise HTTPException(404, "note not found")
    _require_project_access(s, user, _project_for_path(n.path), need_manager=True)
    full = settings.notes_dir / n.path
    rel = n.path
    if full.exists():
        full.unlink()
    remove_path(rel, s)
    return {"status": "deleted"}


# ---------- create task (issue #63) ----------------------------------------

class TaskCreate(BaseModel):
    title: str
    status: str = "todo"
    project: Optional[str] = None       # project (folder) name
    note_path: Optional[str] = None     # explicit destination (relative to notes_dir)
    owners: Optional[list[str]] = None  # defaults to [requester]
    priority: Optional[str] = None
    eta: Optional[str] = None
    features: Optional[list[str]] = None
    kind: str = "task"                  # 'task' or 'ar'


_VALID_STATUSES = {"todo", "in-progress", "blocked", "done"}
_VALID_KINDS = {"task", "ar"}


def _resolve_destination_note(
    s: Session, project: Optional[str], note_path: Optional[str],
) -> Path:
    """Pick the markdown file a new task should be appended to.

    Resolution:
      1. explicit note_path (validated to live under notes_dir, project match if given)
      2. project given → most recently modified .md under that project folder
      3. neither → 422
    """
    nd = settings.notes_dir
    if note_path:
        if ".." in note_path or note_path.startswith("/"):
            raise HTTPException(400, "invalid note_path")
        full = nd / note_path
        if not full.exists() or not full.is_file():
            raise HTTPException(404, f"note not found: {note_path}")
        if project is not None and _project_for_path(note_path) != project:
            raise HTTPException(422, f"note '{note_path}' is not in project '{project}'")
        return full
    if project:
        proj_dir = nd / project
        if not proj_dir.is_dir():
            raise HTTPException(404, f"project not found: {project}")
        candidates = sorted(
            (p for p in proj_dir.rglob("*.md") if p.is_file()),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if not candidates:
            raise HTTPException(
                422, f"no notes in project '{project}'. Create a note first.",
            )
        return candidates[0]
    raise HTTPException(
        422,
        "no destination: provide either `project` (uses most recently modified "
        "note in that project) or an explicit `note_path`",
    )


def _build_task_line(
    *, kind: str, task_id: str, title: str, owners: list[str],
    priority: Optional[str], eta: Optional[str], features: list[str],
) -> str:
    """Compose a single bare markdown line for a new task — no leading bullet
    so the appended block matches the convention used in existing notes
    (`!task #id T-XXX <title> @owner ...`).  See issues #63 and #121.
    """
    keyword = "!AR" if kind == "ar" else "!task"
    parts = [f"{keyword} #id {task_id} {title.strip()}"]
    for o in owners:
        n = o.strip().lstrip("@")
        if n:
            parts.append(f"@{n}")
    if priority and priority.strip():
        parts.append(f"#priority {priority.strip()}")
    if eta and eta.strip():
        parts.append(f"#eta {eta.strip()}")
    for f in features:
        n = f.strip()
        if n:
            parts.append(f"#feature {n}")
    return " ".join(parts) + "\n"


@router.post("/tasks", status_code=201)
def create_task(
    body: TaskCreate,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Create a task by appending a new bullet to a markdown note.

    See issue #63.  Resolves the destination note via project or explicit
    note_path, applies RBAC against that project, writes the file under the
    safe_io per-file lock, reindexes, and returns the freshly-indexed task.
    """
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    status = body.status or "todo"
    if status not in _VALID_STATUSES:
        raise HTTPException(400, f"invalid status: {status!r}")
    kind = body.kind or "task"
    if kind not in _VALID_KINDS:
        raise HTTPException(400, f"invalid kind: {kind!r}")

    full = _resolve_destination_note(s, body.project, body.note_path)
    rel  = str(full.relative_to(settings.notes_dir))
    project = _project_for_path(rel)

    role = _user_role_for_project(s, user, project)
    if role == "none":
        raise HTTPException(403, f"no access to project '{project}'")

    # Default owner = requester
    owners = body.owners if body.owners is not None else [user]
    cleaned_owners = [o.strip().lstrip("@") for o in owners if o and o.strip()]

    # RMW under file lock so the ID we mint can't collide with a parallel writer.
    with with_file_lock(full):
        cur_md = full.read_text(encoding="utf-8") if full.exists() else ""
        ids = existing_ids(cur_md)
        new_id = generate_task_id(ids)
        line = _build_task_line(
            kind=kind, task_id=new_id, title=title,
            owners=cleaned_owners,
            priority=body.priority, eta=body.eta,
            features=body.features or [],
        )
        # Append at end-of-file with a separating blank line.  The blank line
        # is critical: the parser uses blank lines as section-context
        # boundaries, so without it the new task would inherit the owner /
        # project of whatever section happened to live at EOF (issue #121).
        if not cur_md:
            new_md = line
        else:
            sep = "" if cur_md.endswith("\n") else "\n"
            new_md = f"{cur_md}{sep}\n{line}"
        _safe_write_unlocked(full, new_md, notes_dir=settings.notes_dir)
        disk_after = full.read_text(encoding="utf-8")
        new_mtime = full.stat().st_mtime
    note_row = s.exec(select(Note).where(Note.path == rel)).first()
    if note_row is None:
        # First-ever write to this file: fall back to reindex_file so the
        # Note row is created.
        note_row = reindex_file(full, s)
    else:
        lines_inserted = len(disk_after.splitlines()) - len(cur_md.splitlines())
        insert_single_task_into_index(
            s,
            note_id=note_row.id,
            new_body_md=disk_after,
            new_mtime=new_mtime,
            new_task_uuid=new_id,
            lines_inserted=max(lines_inserted, 1),
            folder_project=project,
        )

    # If a non-todo status was requested, apply it as a follow-up edit so the
    # bullet carries the right `#status` token.  Doing this after the initial
    # write keeps the appended line shape simple and reuses the same status
    # update plumbing the editor uses.
    created = s.exec(select(Task).where(Task.task_uuid == new_id)).first()
    if created is None:
        raise HTTPException(500, "task created but not found in index — please refresh")
    if status != "todo":
        with with_file_lock(full):
            disk_md = full.read_text(encoding="utf-8")
            patched = update_task_status(disk_md, created.line, status)
            if patched != disk_md:
                _safe_write_unlocked(full, patched, notes_dir=settings.notes_dir)
                patched_disk = full.read_text(encoding="utf-8")
                patched_mtime = full.stat().st_mtime
                apply_single_task_patch_to_index(
                    s,
                    note_id=note_row.id,
                    task_id=created.id,
                    new_body_md=patched_disk,
                    new_mtime=patched_mtime,
                    status=status,
                )
        created = s.exec(select(Task).where(Task.task_uuid == new_id)).first()

    awarded = gamify.record_event(
        s, user, gamify.TASK_CREATED,
        ref=created.task_uuid or f"task#{created.id}",
        meta={"kind": created.kind, "status": created.status},
    )
    out = _task_to_dict(s, created, include_children=True) | {"note_path": rel}
    if awarded:
        out["awarded_badges"] = awarded
    return out


class ArCreate(BaseModel):
    title: str
    owners: Optional[list[str]] = None
    priority: Optional[str] = None
    eta: Optional[str] = None
    features: Optional[list[str]] = None


@router.post("/tasks/{task_ref}/ars", status_code=201)
def create_ar_under_task(
    task_ref: str,
    body: ArCreate,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Append a new `!AR` child line under an existing task.

    Inserts the AR inside the parent's block (after any existing children
    but before the next blank line / sibling), so the new AR inherits the
    same parser section context — same project, same `@owner` frame — as
    its parent.

    RBAC: same as PATCH/DELETE — requester must own the parent task or be
    a project manager / admin.  Pure project members who don't own the
    parent task get 403.
    """
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    # Strip a redundant leading bang token if the user typed the keyword
    # explicitly (e.g. "!AR foo bar").  The line builder always prepends
    # `!AR`, so without this we'd emit `!AR #id T-XXX !AR foo bar`.
    # See issue #125.
    low = title.lower()
    if low.startswith("!ar "):
        title = title[4:].strip()
    elif low.startswith("!task "):
        title = title[6:].strip()
    if not title:
        raise HTTPException(400, "title is required")

    parent = _resolve_task(task_ref, s)
    note = s.get(Note, parent.note_id)
    if not note:
        raise HTTPException(404, "parent note not found")
    project = _project_for_path(note.path)
    role = _user_role_for_project(s, user, project)
    parent_owners = s.exec(
        select(User.name).join(TaskOwner, TaskOwner.user_id == User.id)
        .where(TaskOwner.task_id == parent.id)
    ).all()
    is_owner = user in parent_owners
    if role == "none" and not is_owner:
        raise HTTPException(403, "no access to project")
    if role != "manager" and not is_owner:
        raise HTTPException(
            403, "only the parent task's owner or a project manager can add an AR",
        )

    full = settings.notes_dir / note.path
    parent_uuid = parent.task_uuid
    parent_line = parent.line

    # Default owner = requester (mirrors create_task), so the AR is attributed
    # to whoever filed it rather than silently inheriting the parent's owner
    # via section context.
    owners = body.owners if body.owners is not None else [user]
    cleaned_owners = [o.strip().lstrip("@") for o in owners if o and o.strip()]

    with with_file_lock(full):
        if not full.exists():
            raise HTTPException(404, "parent note file not found on disk")
        disk_md = full.read_text(encoding="utf-8")

        # Re-anchor by parent #id when present — line numbers can shift if
        # another writer touched the file between our cached parse and now.
        anchor_line = parent_line
        if parent_uuid:
            for i, raw in enumerate(disk_md.splitlines()):
                if f"#id {parent_uuid}" in raw:
                    anchor_line = i
                    break

        ids = existing_ids(disk_md)
        new_id = generate_task_id(ids)
        body_line = _build_task_line(
            kind="ar", task_id=new_id, title=title,
            owners=cleaned_owners,
            priority=body.priority, eta=body.eta,
            features=body.features or [],
        ).rstrip("\n")
        new_md = insert_ar_under_task(disk_md, anchor_line, body_line)
        if new_md == disk_md:
            raise HTTPException(500, "AR insert produced no change")
        _safe_write_unlocked(full, new_md, notes_dir=settings.notes_dir)
        # Read post-write content + mtime to keep the index aligned with
        # what's actually on disk (safe-write may normalize whitespace).
        disk_after = full.read_text(encoding="utf-8")
        new_mtime = full.stat().st_mtime
    rel = note.path
    parts = Path(rel).parts
    folder_project: str | None = parts[0] if len(parts) >= 2 else None
    lines_inserted = len(disk_after.splitlines()) - len(disk_md.splitlines())
    insert_single_task_into_index(
        s,
        note_id=note.id,
        new_body_md=disk_after,
        new_mtime=new_mtime,
        new_task_uuid=new_id,
        lines_inserted=max(lines_inserted, 1),
        folder_project=folder_project,
    )

    # ── Propagate the new AR to ref-row files ─────────────────────────────
    # Any other md file that contains a `#task <parent_uuid>` ref row should
    # also gain a `#AR <new_id> <title>` row at the same indent — mirrors
    # the shape that `roll_to_next_week` would emit for an open AR. Without
    # this, the AR is invisible from weekly notes that reference the parent,
    # which also defeats the #92 PATCH propagation for any later status /
    # priority / eta edits on the new AR (no ref row → no propagation
    # target). See issue #148.
    if parent_uuid:
        # Build the bare body (title + optional tokens). The helper prepends
        # the ref-row lead/bullet and the `#AR <id>` keyword.
        body_parts: list[str] = [title.strip()]
        for o in cleaned_owners:
            if o:
                body_parts.append(f"@{o}")
        if body.priority and body.priority.strip():
            body_parts.append(f"#priority {body.priority.strip()}")
        if body.eta and body.eta.strip():
            body_parts.append(f"#eta {body.eta.strip()}")
        for f in (body.features or []):
            n = f.strip()
            if n:
                body_parts.append(f"#feature {n}")
        ar_body = " ".join(body_parts)

        from sqlmodel import col as _col
        candidate_notes = s.exec(
            select(Note)
            .where(_col(Note.body_md).contains(parent_uuid))
            .where(Note.id != note.id)
        ).all()

        for ref_note in candidate_notes:
            ref_full = settings.notes_dir / ref_note.path
            ref_changed = False
            ref_disk_after: str = ""
            ref_mtime_after: float = 0.0
            with with_file_lock(ref_full):
                if ref_full.exists():
                    ref_disk_md = ref_full.read_text(encoding="utf-8")
                    new_ref_md, ref_changed = insert_ar_ref_row_after(
                        ref_disk_md, parent_uuid, new_id, ar_body,
                    )
                    if ref_changed:
                        _safe_write_unlocked(
                            ref_full, new_ref_md, notes_dir=settings.notes_dir,
                        )
                        ref_disk_after = ref_full.read_text(encoding="utf-8")
                        ref_mtime_after = ref_full.stat().st_mtime
            if ref_changed:
                # Body-only refresh: the new AR's canonical Task row lives
                # in the parent's note (already indexed above). Ref-row
                # attribute overrides for this AR will be derived on the
                # next full reindex of this file; for now we only need the
                # cached body to reflect what's on disk so subsequent #92
                # PATCH propagation can find the row.
                update_note_body_only(
                    s,
                    note_id=ref_note.id,
                    new_body_md=ref_disk_after,
                    new_mtime=ref_mtime_after,
                )

    created = s.exec(select(Task).where(Task.task_uuid == new_id)).first()
    if created is None:
        raise HTTPException(500, "AR created but not found in index — please refresh")
    awarded = gamify.record_event(
        s, user, gamify.TASK_CREATED,
        ref=created.task_uuid or f"task#{created.id}",
        meta={"kind": "ar", "parent_task_uuid": parent_uuid},
    )
    out = _task_to_dict(s, created) | {"parent_task_uuid": parent_uuid}
    if awarded:
        out["awarded_badges"] = awarded
    return out


@router.post("/parse")
def parse_preview(body: dict = Body(...)) -> dict[str, Any]:
    return parse(body.get("body_md", ""))


# ---------- tasks (composable filters) -------------------------------------

# Operators allowed for the repeatable `attr` query param.
# Form on the wire: `attr=key:op:value` (value may contain `:` itself).
# `exists` / `nexists` accept an empty value (`attr=key:exists:`).
_ATTR_OPS = {"eq", "ne", "in", "nin", "gte", "lte", "gt", "lt", "like", "exists", "nexists"}
_ATTR_RANGE_OPS = {"gte", "lte", "gt", "lt"}
_ATTR_OP_SQL = {
    "eq": "=", "ne": "!=", "gte": ">=", "lte": "<=", "gt": ">", "lt": "<",
}

# Whitelisted sort fields.  Map to (kind, sql-expression).  "task" =
# Task column, "attr" = a normalized TaskAttr lookup (joined LEFT so tasks
# missing the attr sort to the end).
_SORT_FIELDS = {
    "id":         ("task", "t.id"),
    "title":      ("task", "t.title"),
    "status":     ("task", "t.status"),
    "kind":       ("task", "t.kind"),
    "created_at": ("task", "t.created_at"),
    "updated_at": ("task", "t.updated_at"),
    "eta":        ("attr", "eta"),
    "priority":   ("attr", "priority"),
}


def _parse_attr_clause(raw: str) -> tuple[str, str, str]:
    """Parse `key:op:value` into (key, op, value).  Raises HTTPException(400)."""
    parts = raw.split(":", 2)
    if len(parts) < 2:
        raise HTTPException(400, f"bad attr clause: {raw!r} (expected key:op[:value])")
    key, op = parts[0].strip(), parts[1].strip().lower()
    value = parts[2] if len(parts) == 3 else ""
    if not key:
        raise HTTPException(400, f"bad attr clause: {raw!r} (empty key)")
    if op not in _ATTR_OPS:
        raise HTTPException(
            400,
            f"bad attr clause: {raw!r} (unknown op {op!r}; valid: {sorted(_ATTR_OPS)})",
        )
    if op in {"exists", "nexists"}:
        return key, op, ""
    if op in _ATTR_RANGE_OPS and key not in {"eta", "priority"}:
        # Range queries require a normalized value (value_norm).  Today only
        # eta/priority normalize, so reject other keys early with a clear
        # message instead of silently returning empty results.
        raise HTTPException(
            400,
            f"range op {op!r} on attr {key!r} requires value_norm; only "
            f"'eta' and 'priority' normalize today. Use eq/ne/in/nin instead.",
        )
    return key, op, value


@router.get("/tasks")
def list_tasks(
    s: Session = Depends(get_session),
    owner: Optional[str] = None,
    project: Optional[str] = None,
    feature: Optional[str] = None,
    priority: Optional[str] = None,
    status: Optional[str] = None,
    eta_before: Optional[date] = None,
    eta_after: Optional[date] = None,
    hide_done: bool = False,
    q: Optional[str] = None,
    kind: Optional[str] = None,
    top_level_only: bool = False,
    include_children: bool = False,
    # ── new (issue #38 follow-up) ─────────────────────────────────────
    not_owner: Optional[str] = None,
    not_project: Optional[str] = None,
    not_feature: Optional[str] = None,
    not_status: Optional[str] = None,
    not_priority: Optional[str] = None,
    attr: list[str] = Query(default_factory=list),
    sort: Optional[str] = None,
    limit: Optional[int] = Query(default=None, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    sql = ["SELECT DISTINCT t.id FROM task t"]
    params: dict[str, Any] = {}
    expanding: list[str] = []

    def _join_multi(table: str, name_table: str, names: list[str], alias: str) -> None:
        if not names:
            return
        sql.append(
            f"JOIN {table} {alias}_j ON {alias}_j.task_id = t.id "
            f"JOIN {name_table} {alias} ON {alias}.id = {alias}_j.{name_table}_id "
        )
        sql.append(f"AND {alias}.name IN :{alias}_names")
        params[f"{alias}_names"] = tuple(names)
        expanding.append(f"{alias}_names")

    _join_multi("taskowner", "user", _split(owner), "u")
    _join_multi("taskproject", "project", _split(project), "p")
    _join_multi("taskfeature", "feature", _split(feature), "f")

    where = ["1=1"]
    if hide_done or status == "!done":
        where.append("t.status != 'done'")
    elif status:
        statuses = _split(status)
        where.append("t.status IN :statuses")
        params["statuses"] = tuple(statuses)
        expanding.append("statuses")
    if priority:
        prios = _split(priority)
        sql.append("JOIN taskattr pa ON pa.task_id = t.id AND pa.key='priority'")
        where.append("pa.value IN :prios")
        params["prios"] = tuple(prios)
        expanding.append("prios")
    if eta_before or eta_after:
        sql.append("JOIN taskattr ea ON ea.task_id = t.id AND ea.key='eta'")
        if eta_before:
            where.append("ea.value_norm <= :eta_before")
            params["eta_before"] = eta_before.isoformat()
        if eta_after:
            where.append("ea.value_norm >= :eta_after")
            params["eta_after"] = eta_after.isoformat()
    if q:
        where.append("t.title LIKE :q")
        params["q"] = f"%{q}%"
    if kind:
        kinds = _split(kind)
        where.append("t.kind IN :kinds")
        params["kinds"] = tuple(kinds)
        expanding.append("kinds")
    if top_level_only:
        where.append("(t.parent_task_id IS NULL AND t.kind = 'task')")

    # ── negations ────────────────────────────────────────────────────────
    def _exclude(name_table: str, link_table: str, link_col: str,
                 names: list[str], slot: str) -> None:
        if not names:
            return
        where.append(
            f"t.id NOT IN ("
            f"SELECT lk.task_id FROM {link_table} lk "
            f"JOIN {name_table} nm ON nm.id = lk.{link_col} "
            f"WHERE nm.name IN :{slot})"
        )
        params[slot] = tuple(names)
        expanding.append(slot)

    _exclude("user",    "taskowner",   "user_id",    _split(not_owner),   "not_u_names")
    _exclude("project", "taskproject", "project_id", _split(not_project), "not_p_names")
    _exclude("feature", "taskfeature", "feature_id", _split(not_feature), "not_f_names")

    if not_status:
        where.append("t.status NOT IN :not_statuses")
        params["not_statuses"] = tuple(_split(not_status))
        expanding.append("not_statuses")
    if not_priority:
        where.append(
            "t.id NOT IN ("
            "SELECT task_id FROM taskattr WHERE key='priority' AND value IN :not_prios)"
        )
        params["not_prios"] = tuple(_split(not_priority))
        expanding.append("not_prios")

    # ── arbitrary @attr filters ──────────────────────────────────────────
    for i, raw in enumerate(attr):
        key, op, value = _parse_attr_clause(raw)
        kp, vp = f"axk_{i}", f"axv_{i}"
        params[kp] = key
        if op == "exists":
            where.append(
                f"EXISTS (SELECT 1 FROM taskattr ax{i} "
                f"WHERE ax{i}.task_id = t.id AND ax{i}.key = :{kp})"
            )
            continue
        if op == "nexists":
            where.append(
                f"NOT EXISTS (SELECT 1 FROM taskattr ax{i} "
                f"WHERE ax{i}.task_id = t.id AND ax{i}.key = :{kp})"
            )
            continue
        col = "value_norm" if op in _ATTR_RANGE_OPS else "value"
        if op == "in":
            where.append(
                f"EXISTS (SELECT 1 FROM taskattr ax{i} "
                f"WHERE ax{i}.task_id = t.id AND ax{i}.key = :{kp} "
                f"AND ax{i}.{col} IN :{vp})"
            )
            params[vp] = tuple(v for v in (tok.strip() for tok in value.split(",")) if v)
            expanding.append(vp)
        elif op == "nin":
            where.append(
                f"NOT EXISTS (SELECT 1 FROM taskattr ax{i} "
                f"WHERE ax{i}.task_id = t.id AND ax{i}.key = :{kp} "
                f"AND ax{i}.{col} IN :{vp})"
            )
            params[vp] = tuple(v for v in (tok.strip() for tok in value.split(",")) if v)
            expanding.append(vp)
        elif op == "ne":
            # "ne" means: there is no row with this key=value.  (Tasks that
            # don't have the key at all also satisfy `ne`.)
            where.append(
                f"NOT EXISTS (SELECT 1 FROM taskattr ax{i} "
                f"WHERE ax{i}.task_id = t.id AND ax{i}.key = :{kp} "
                f"AND ax{i}.{col} = :{vp})"
            )
            params[vp] = value
        elif op == "like":
            where.append(
                f"EXISTS (SELECT 1 FROM taskattr ax{i} "
                f"WHERE ax{i}.task_id = t.id AND ax{i}.key = :{kp} "
                f"AND ax{i}.{col} LIKE :{vp})"
            )
            params[vp] = value
        else:
            sql_op = _ATTR_OP_SQL[op]
            where.append(
                f"EXISTS (SELECT 1 FROM taskattr ax{i} "
                f"WHERE ax{i}.task_id = t.id AND ax{i}.key = :{kp} "
                f"AND ax{i}.{col} {sql_op} :{vp})"
            )
            params[vp] = value

    # ── ORDER BY ─────────────────────────────────────────────────────────
    order_clauses: list[str] = []
    sort_joins: list[str] = []
    if sort:
        for i, raw in enumerate([tok.strip() for tok in sort.split(",") if tok.strip()]):
            field, _, direction = raw.partition(":")
            field = field.strip()
            direction = (direction or "asc").strip().lower()
            if direction not in {"asc", "desc"}:
                raise HTTPException(400, f"bad sort direction: {raw!r}")
            spec = _SORT_FIELDS.get(field)
            if not spec:
                raise HTTPException(
                    400,
                    f"unknown sort field {field!r}; valid: {sorted(_SORT_FIELDS)}",
                )
            kind_, expr = spec
            if kind_ == "task":
                order_clauses.append(f"{expr} {direction.upper()}")
            else:
                alias = f"so{i}"
                sort_joins.append(
                    f"LEFT JOIN taskattr {alias} ON {alias}.task_id = t.id "
                    f"AND {alias}.key = '{expr}'"
                )
                # NULLs always last so unstamped tasks don't pollute the top.
                order_clauses.append(
                    f"({alias}.value_norm IS NULL) ASC, "
                    f"{alias}.value_norm {direction.upper()}"
                )
    # Stable tiebreaker so identical sort keys order deterministically.
    order_clauses.append("t.id ASC")

    sql.extend(sort_joins)
    sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY " + ", ".join(order_clauses))
    sql_text = " ".join(sql)
    stmt = text(sql_text)
    if expanding:
        stmt = stmt.bindparams(*[bindparam(k, expanding=True) for k in expanding])
    rows = s.exec(stmt.bindparams(**params)).all()
    all_ids = [r[0] for r in rows]
    total = len(all_ids)

    # Paginate after we know the total.  When `limit` is omitted we keep
    # the historical "return everything" behavior so existing callers
    # don't silently get truncated.
    if limit is not None:
        page_ids = all_ids[offset : offset + limit]
    else:
        page_ids = all_ids[offset:] if offset else all_ids

    if not page_ids:
        return {
            "tasks": [],
            "total": total,
            "offset": offset,
            "limit": limit,
            "aggregations": {
                "owners": [], "projects": [], "features": [],
                "status_breakdown": {}, "priority_breakdown": {},
            },
        }

    tasks = [_task_to_dict(s, s.get(Task, i), include_children=include_children) for i in page_ids]

    agg_owners = sorted({o for t in tasks for o in t["owners"]})
    agg_projects = sorted({p for t in tasks for p in t["projects"]})
    agg_features = sorted({f for t in tasks for f in t["features"]})
    status_bd: dict[str, int] = {}
    prio_bd: dict[str, int] = {}
    for t in tasks:
        status_bd[t["status"]] = status_bd.get(t["status"], 0) + 1
        p = t["attrs"].get("priority", "—")
        prio_bd[p if isinstance(p, str) else p[0]] = prio_bd.get(p if isinstance(p, str) else p[0], 0) + 1

    return {
        "tasks": tasks,
        "total": total,
        "offset": offset,
        "limit": limit,
        "aggregations": {
            "owners": agg_owners,
            "projects": agg_projects,
            "features": agg_features,
            "status_breakdown": status_bd,
            "priority_breakdown": prio_bd,
        },
    }


# ---------- attr key catalog (autocomplete) --------------------------------

@router.get("/attrs")
def list_attr_keys(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Distinct attr keys with cardinality and a few sample values.

    Used by the FilterBar (and `vn`) for tab-completing `@key` and `=value`
    in the query DSL.  Cheap aggregate; we cap the sample list at 25.
    """
    rows = s.exec(
        text("SELECT key, COUNT(*) AS cnt FROM taskattr GROUP BY key ORDER BY cnt DESC, key ASC")
    ).all()
    out: list[dict[str, Any]] = []
    for key, cnt in rows:
        samples = s.exec(
            text(
                "SELECT DISTINCT value FROM taskattr "
                "WHERE key = :k AND value != '' "
                "ORDER BY value LIMIT 25"
            ).bindparams(k=key)
        ).all()
        out.append({
            "key": key,
            "count": int(cnt),
            "sample_values": [r[0] for r in samples],
        })
    return out





# ---------- agenda ----------------------------------------------------------

@router.get("/agenda")
def agenda(
    s: Session = Depends(get_session),
    owner: Optional[str] = None,
    days: int = Query(default=None),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    days = days or settings.agenda_window_days
    if start:
        try:
            today = date.fromisoformat(start)
        except ValueError:
            raise HTTPException(400, "invalid start date")
    else:
        today = date.today()
    if end:
        try:
            end_d = date.fromisoformat(end)
        except ValueError:
            raise HTTPException(400, "invalid end date")
    else:
        end_d = today + timedelta(days=days)

    sql = """
    SELECT t.id, ea.value_norm AS eta,
           COALESCE(CAST(pa.value_norm AS INTEGER), 999) AS pri_rank
    FROM task t
    JOIN taskattr ea ON ea.task_id = t.id AND ea.key='eta'
    LEFT JOIN taskattr pa ON pa.task_id = t.id AND pa.key='priority'
    """
    params: dict[str, Any] = {"start": today.isoformat(), "end": end_d.isoformat()}
    if owner:
        sql += (
            " JOIN taskowner o ON o.task_id = t.id "
            " JOIN user u ON u.id = o.user_id AND u.name = :owner "
        )
        params["owner"] = owner
    sql += """
    WHERE t.status != 'done'
      AND ea.value_norm BETWEEN :start AND :end
    ORDER BY ea.value_norm ASC, pri_rank ASC, t.id ASC
    """
    rows = s.exec(text(sql).bindparams(**params)).all()
    grouped: dict[str, list[dict]] = {}
    for tid, eta, _pri in rows:
        grouped.setdefault(eta, []).append(_task_to_dict(s, s.get(Task, tid)))
    return {"window": {"start": today.isoformat(), "end": end_d.isoformat(), "days": (end_d - today).days}, "by_day": grouped}


# ---------- features (cross-user pull) -------------------------------------

@router.get("/features")
def list_features(s: Session = Depends(get_session)) -> list[str]:
    return [f.name for f in s.exec(select(Feature).order_by(Feature.name)).all()]


@router.get("/features/{name}/tasks")
def feature_tasks(name: str, s: Session = Depends(get_session)) -> dict[str, Any]:
    feat = s.exec(select(Feature).where(Feature.name == name)).first()
    if not feat:
        raise HTTPException(404, "feature not found")
    rows = s.exec(
        select(Task.id).join(TaskFeature, TaskFeature.task_id == Task.id)
        .where(TaskFeature.feature_id == feat.id)
    ).all()
    tasks = [_task_to_dict(s, s.get(Task, r[0] if isinstance(r, tuple) else r)) for r in rows]
    return {
        "feature": name,
        "tasks": tasks,
        "aggregations": {
            "owners": sorted({o for t in tasks for o in t["owners"]}),
            "projects": sorted({p for t in tasks for p in t["projects"]}),
            "status_breakdown": {st: sum(1 for t in tasks if t["status"] == st) for st in {t["status"] for t in tasks}},
            "eta_range": [
                min((t["eta"] for t in tasks if t["eta"]), default=None),
                max((t["eta"] for t in tasks if t["eta"]), default=None),
            ],
        },
    }


# ---------- cards / bidirectional links ------------------------------------

@router.get("/cards/{task_ref}/links")
def card_links(task_ref: str, s: Session = Depends(get_session)) -> dict[str, Any]:
    t = _resolve_task(task_ref, s)
    rows = s.exec(text("""
        SELECT other_slug, kind, direction FROM links_bidir WHERE task_id = :tid
    """).bindparams(tid=t.id)).all()
    return {
        "task_id": t.id,
        "task_uuid": t.task_uuid,
        "slug": t.slug,
        "links": [{"other_slug": r[0], "kind": r[1], "direction": r[2]} for r in rows],
    }


# ---------- projects (folders) / tree / RBAC -------------------------------

class PhonebookResolveRequest(BaseModel):
    tokens: list[str]
    anchor: str | None = None


def _pick_phonebook_anchor(req_anchor: str | None, user: str | None) -> str | None:
    """Pick the best org-distance anchor (#215).

    Prefers, in order: explicit request anchor → authenticated user →
    ``settings.phonebook_default_anchor``. Returns the first candidate
    that resolves to an Intel WWID via the scraper. If none resolve
    (scraper off or all unknown), returns the first non-empty candidate
    so logs/responses still echo a meaningful value — but downstream
    ranking will then no-op.
    """
    from ..phonebook_intel import resolve_anchor_wwid
    from ..config import settings as _s
    candidates = [
        req_anchor,
        user,
        getattr(_s, "phonebook_default_anchor", None),
    ]
    cleaned = [(c or "").strip() for c in candidates]
    cleaned = [c for c in cleaned if c]
    for c in cleaned:
        try:
            if resolve_anchor_wwid(c):
                return c
        except Exception:  # pragma: no cover — defensive
            continue
    return cleaned[0] if cleaned else None


@router.post("/phonebook/resolve")
def phonebook_resolve(
    body: PhonebookResolveRequest,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Bulk-resolve owner tokens (#174 / #210). Returns ``resolved``,
    ``ambiguous``, and ``unresolved`` buckets keyed by the original token.
    ``anchor`` (idsid / email / wwid) is used to rank scraper hits by
    org-tree distance — defaults to the authenticated user (#213)."""
    if not body.tokens:
        return {"resolved": {}, "ambiguous": {}, "unresolved": []}
    if len(body.tokens) > 500:
        raise HTTPException(status_code=400, detail="too many tokens (max 500)")
    anchor = _pick_phonebook_anchor(body.anchor, user)
    return get_phonebook().resolve_many(body.tokens, anchor=anchor)


class PhonebookLookupRequest(BaseModel):
    q: str
    anchor: str | None = None


@router.post("/phonebook/lookup")
def phonebook_lookup(
    body: PhonebookLookupRequest,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Live Intel Phonebook scraper lookup (#213). Returns raw candidates
    so the UI can render a disambiguation picker. No-op (empty list) if
    the scraper is disabled in settings or the query is empty.

    When ``anchor`` is set (defaults to the authenticated user), each
    candidate carries an ``org_distance`` integer (or ``null`` if no
    common ancestor was found within the chain depth limit), and
    candidates are returned sorted by that distance ascending."""
    from ..phonebook_intel import (
        cached_lookup, filter_by_first_name, rank_by_distance,
        resolve_anchor_wwid,
    )
    from ..config import settings as _s
    q = (body.q or "").strip()
    enabled = bool(getattr(_s, "phonebook_scraper_enabled", False))
    if not q:
        return {"query": "", "candidates": [], "enabled": enabled}
    if len(q) > 200:
        raise HTTPException(status_code=400, detail="query too long (max 200)")
    hits = cached_lookup(q)
    # First-name-only filter (#215) — drop Pavel-as-lastname noise etc.
    hits = filter_by_first_name(hits, q)
    anchor = _pick_phonebook_anchor(body.anchor, user)
    anchor_wwid = resolve_anchor_wwid(anchor) if anchor else None
    ranked = rank_by_distance(hits, anchor_wwid) if anchor_wwid else \
        [(h, None) for h in hits]
    out = []
    for h, dist in ranked:
        d = h.to_dict()
        d["org_distance"] = dist
        out.append(d)
    return {
        "query": q,
        "enabled": enabled,
        "anchor": anchor,
        "anchor_wwid": anchor_wwid,
        "candidates": out,
    }


@router.get("/projects")
def list_projects(
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
    """List projects = top-level subfolders of notes/. Includes the caller's role."""
    out: list[dict[str, Any]] = []
    nd = settings.notes_dir
    nd.mkdir(parents=True, exist_ok=True)
    for child in sorted(nd.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        role = _user_role_for_project(s, user, child.name)
        if role == "none":
            continue
        out.append({"name": child.name, "role": role})
    return out


class ProjectCreate(BaseModel):
    name: str


@router.post("/projects")
def create_project(
    body: ProjectCreate,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    name = body.name.strip()
    if not name or "/" in name or name.startswith(".") or ".." in name:
        raise HTTPException(400, "invalid project name")
    pdir = settings.notes_dir / name
    if pdir.exists():
        raise HTTPException(409, "project already exists")
    pdir.mkdir(parents=True)
    # Creator becomes manager.
    s.add(ProjectMember(project_name=name, user_name=user, role="manager"))
    s.commit()
    return {"name": name, "role": "manager"}


@router.delete("/projects/{project}")
def delete_project(
    project: str,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Delete a project: removes the folder, all notes/index rows under it,
    and all member rows. Requires manager role."""
    _require_project_access(s, user, project, need_manager=True)
    pdir = settings.notes_dir / project
    if not pdir.is_dir():
        raise HTTPException(404, "project not found")
    # Drop index rows for every .md under the folder.
    removed = 0
    for p in pdir.rglob("*.md"):
        rel = str(p.relative_to(settings.notes_dir))
        remove_path(rel, s)
        removed += 1
    # Drop members.
    for pm in s.exec(select(ProjectMember).where(ProjectMember.project_name == project)).all():
        s.delete(pm)
    s.commit()
    # Finally remove the directory tree from disk.
    shutil.rmtree(pdir)
    return {"status": "deleted", "project": project, "notes_removed": removed}


@router.get("/projects/{project}/notes")
def list_project_notes(
    project: str,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
    _require_project_access(s, user, project)
    pdir = settings.notes_dir / project
    if not pdir.is_dir():
        raise HTTPException(404, "project not found")
    out: list[dict[str, Any]] = []
    files = sorted(pdir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files:
        rel = str(p.relative_to(settings.notes_dir))
        note = s.exec(select(Note).where(Note.path == rel)).first()
        if note is None:
            # Self-heal: a markdown file exists on disk but the indexer never
            # persisted (or lost) its row. Without an id the editor cannot
            # open the note. Lazily reindex so the UI keeps working.
            try:
                note = reindex_file(p, s)
                s.commit()
            except Exception:
                s.rollback()
                note = None
        out.append({
            "path": rel,
            "id": note.id if note else None,
            "title": note.title if note else p.stem,
        })
    return out


@router.get("/tree")
def tree(
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
    """Project → notes tree, filtered by caller's RBAC."""
    out: list[dict[str, Any]] = []
    nd = settings.notes_dir
    nd.mkdir(parents=True, exist_ok=True)
    # Top-level projects (folders)
    for child in sorted(nd.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        role = _user_role_for_project(s, user, child.name)
        if role == "none":
            continue
        notes = []
        for p in sorted(child.rglob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
            rel = str(p.relative_to(nd))
            note = s.exec(select(Note).where(Note.path == rel)).first()
            if note is None:
                try:
                    note = reindex_file(p, s)
                    s.commit()
                except Exception:
                    s.rollback()
                    note = None
            notes.append({
                "path": rel,
                "id": note.id if note else None,
                "title": note.title if note else p.stem,
            })
        out.append({"project": child.name, "role": role, "notes": notes})
    # Root-level loose .md files (no project)
    loose = []
    for p in sorted(nd.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        rel = str(p.relative_to(nd))
        note = s.exec(select(Note).where(Note.path == rel)).first()
        if note is None:
            try:
                note = reindex_file(p, s)
                s.commit()
            except Exception:
                s.rollback()
                note = None
        loose.append({
            "path": rel,
            "id": note.id if note else None,
            "title": note.title if note else p.stem,
        })
    if loose:
        out.append({"project": None, "role": "manager", "notes": loose})
    return out


# ---------- project members (RBAC) -----------------------------------------

class MemberIn(BaseModel):
    user_name: str
    role: str  # manager | member


@router.get("/projects/{project}/members")
def list_members(
    project: str,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[dict[str, str]]:
    _require_project_access(s, user, project)
    rows = s.exec(
        select(ProjectMember).where(ProjectMember.project_name == project)
    ).all()
    return [{"user_name": r.user_name, "role": r.role} for r in rows]


def _manager_count(s: Session, project: str) -> int:
    """Return number of explicit manager rows for *project*."""
    return len(
        s.exec(
            select(ProjectMember).where(
                ProjectMember.project_name == project,
                ProjectMember.role == "manager",
            )
        ).all()
    )


@router.put("/projects/{project}/members")
def upsert_member(
    project: str,
    body: MemberIn,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, str]:
    _require_project_access(s, user, project, need_manager=True)
    if body.role not in {"manager", "member"}:
        raise HTTPException(400, "role must be 'manager' or 'member'")
    existing = s.exec(
        select(ProjectMember).where(
            ProjectMember.project_name == project,
            ProjectMember.user_name == body.user_name,
        )
    ).first()
    # Last-manager guard: block demotion if it would leave zero managers.
    if existing and existing.role == "manager" and body.role == "member":
        if _manager_count(s, project) <= 1:
            raise HTTPException(
                400,
                "Cannot demote the last manager. Promote another member to manager first.",
            )
    if existing:
        existing.role = body.role
    else:
        s.add(ProjectMember(project_name=project, user_name=body.user_name, role=body.role))
    s.commit()
    return {"user_name": body.user_name, "role": body.role}


@router.delete("/projects/{project}/members/{user_name}")
def remove_member(
    project: str,
    user_name: str,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, str]:
    _require_project_access(s, user, project, need_manager=True)
    existing = s.exec(
        select(ProjectMember).where(
            ProjectMember.project_name == project,
            ProjectMember.user_name == user_name,
        )
    ).first()
    # Last-manager guard: block removal if it would leave zero managers.
    if existing and existing.role == "manager":
        if _manager_count(s, project) <= 1:
            raise HTTPException(
                400,
                "Cannot remove the last manager. Promote another member to manager first.",
            )
    if existing:
        s.delete(existing)
        s.commit()
    return {"status": "removed"}


# ---------- tasks: PATCH (status round-trip to .md) ------------------------

class TaskPatch(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None  # e.g. "P1", "P2", or "" to clear
    eta: Optional[str] = None       # e.g. "2026-W18", "2026-04-30", or "" to clear
    owners: Optional[list[str]] = None    # full replacement; [] clears
    features: Optional[list[str]] = None  # full replacement; [] clears
    # Append a new `#note` continuation line under the task (preferred).
    # Multi-line input becomes one `#note` per non-empty line, all sharing
    # the same auto-prepended timestamp + author. Existing notes are kept.
    add_note: Optional[str] = None
    # Legacy "overwrite the whole notes block" — only honored if `add_note`
    # is not provided. Empty string clears the block. Kept for backwards
    # compat but discouraged because it destroys history (see issue #53).
    notes: Optional[str] = None


@router.get("/tasks/{task_ref}")
def get_task(
    task_ref: str,
    s: Session = Depends(get_session),
    include_children: bool = False,
) -> dict[str, Any]:
    """Fetch a single task by integer PK or `T-XXXXXX` uuid ref."""
    t = _resolve_task(task_ref, s)
    return _task_to_dict(s, t, include_children=include_children)


@router.patch("/tasks/{task_ref}")
def patch_task(
    task_ref: str,
    body: TaskPatch,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    t = _resolve_task(task_ref, s)
    task_id = t.id
    note = s.get(Note, t.note_id)
    if not note:
        raise HTTPException(404, "note not found")
    project = _project_for_path(note.path)
    role = _user_role_for_project(s, user, project)
    owners = s.exec(
        select(User.name).join(TaskOwner, TaskOwner.user_id == User.id)
        .where(TaskOwner.task_id == task_id)
    ).all()
    is_owner = user in owners
    # Ownership-grants-edit: an `@user` mention in the markdown is a
    # first-class permission grant on that single task, regardless of
    # ProjectMember rows. This keeps the @-mention contract honest — if you
    # appear in `owners` everywhere in the UI, you can also save edits to
    # the tasks you own. Project-membership remains required for whole-file
    # writes (PUT /api/notes) and for editing tasks you do NOT own.
    if role == "none" and not is_owner:
        raise HTTPException(403, "no access to project")
    if role != "manager" and not is_owner:
        raise HTTPException(403, "members can only edit their own tasks")

    md = note.body_md
    changed = False
    old_status = t.status
    status_changed = False
    if body.status is not None:
        md = update_task_status(md, t.line, body.status)
        status_changed = (body.status != old_status)
        changed = True
    if body.priority is not None:
        md = (replace_attr(md, t.line, "priority", body.priority.strip())
              if body.priority.strip() else remove_attr(md, t.line, "priority"))
        changed = True
    if body.eta is not None:
        md = (replace_attr(md, t.line, "eta", body.eta.strip())
              if body.eta.strip() else remove_attr(md, t.line, "eta"))
        changed = True
    if body.owners is not None:
        cleaned = [o.strip().lstrip("@") for o in body.owners if o and o.strip()]
        md = replace_multi_attr(md, t.line, "owner", cleaned)
        changed = True
    if body.features is not None:
        cleaned = [f.strip() for f in body.features if f and f.strip()]
        md = replace_multi_attr(md, t.line, "feature", cleaned)
        changed = True
    if body.add_note is not None and body.add_note.strip():
        # Guardrail: refuse note text that looks like a task / AR declaration.
        # Persisting it as a `#note` continuation would silently lose the
        # intended structure (the parser doesn't recognize `#note !AR ...`
        # as a task line).  See issue #125.
        for raw_line in body.add_note.splitlines():
            stripped = raw_line.lstrip().lstrip("-*+ ").lstrip()
            low = stripped.lower()
            if low.startswith(("!ar ", "!ar\t", "!task ", "!task\t")) or low in {"!ar", "!task"}:
                raise HTTPException(
                    400,
                    "note text starts with `!AR` or `!task` — that won't be "
                    "recognized as a task. Use the dedicated 'Add an AR' "
                    "field (POST /api/tasks/{ref}/ars) for action requests.",
                )
        md = append_note(md, t.line, body.add_note)
        changed = True
    elif body.notes is not None:
        # Legacy overwrite-the-block path. Discouraged; see issue #53.
        md = replace_notes(md, t.line, t.indent, body.notes)
        changed = True

    if not changed:
        return _task_to_dict(s, t)

    full = settings.notes_dir / note.path
    # RMW under the file lock: re-read current bytes from disk so a
    # concurrent writer (vim, another tab, another PATCH) doesn't get
    # silently overwritten by our DB-cached body_md. See issue #60.
    with with_file_lock(full):
        if full.exists():
            disk_md = full.read_text(encoding="utf-8")
            if disk_md != note.body_md:
                # Replay our mutations against the freshest content so the
                # patch is correctly anchored. The line numbers we have are
                # from the cached parse; if the file has shifted we have to
                # bail out rather than corrupt it. The client should refetch
                # the task and retry.
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "stale_task",
                        "message": "task base content changed under you; refetch and retry",
                    },
                )
        _safe_write_unlocked(full, md, notes_dir=settings.notes_dir)

    # ── Fast index update (issue #140): only this single task changed ────
    # The popover never mutates more than one task at a time, so a full
    # reindex_file (which re-parses every line and re-fingerprints every
    # task) is wasteful. Apply the same mutations directly to the index
    # rows for `task_id`, plus a single line-shift UPDATE for any tasks
    # below the insertion point if append_note added rows.
    new_disk = (settings.notes_dir / note.path).read_text(encoding="utf-8")
    new_mtime = (settings.notes_dir / note.path).stat().st_mtime
    line_shift = 0
    line_shift_pivot = -1
    if body.add_note is not None and body.add_note.strip():
        line_shift = sum(1 for ln in body.add_note.split("\n") if ln.strip())
        line_shift_pivot = t.line
    apply_single_task_patch_to_index(
        s,
        note_id=note.id,
        task_id=task_id,
        new_body_md=new_disk,
        new_mtime=new_mtime,
        line_shift=line_shift,
        line_shift_pivot=line_shift_pivot,
        status=body.status,
        priority=body.priority,
        eta=body.eta,
        owners=body.owners,
        features=body.features,
        add_note=body.add_note,
    )

    # ── Propagate to all ref-row files ────────────────────────────────────
    # Any .md file that references this task via `#task T-XXXX` / `#AR T-XXXX`
    # needs the same attribute mutations applied to those ref rows.  Without
    # this, the next reindex of a referencing weekly note would push its
    # stale `#status` override back into the SQLite index, silently clobbering
    # the PATCH we just made (see issue #92).
    ref_id = t.task_uuid
    if ref_id:
        ref_patch: dict = {}
        if body.status is not None:
            ref_patch["status"] = body.status
        if body.priority is not None:
            ref_patch["priority"] = body.priority
        if body.eta is not None:
            ref_patch["eta"] = body.eta
        if body.owners is not None:
            ref_patch["owners"] = [o.strip().lstrip("@") for o in body.owners if o and o.strip()]
        if body.features is not None:
            ref_patch["features"] = [f.strip() for f in body.features if f and f.strip()]
        # Notes are journal entries — propagate them too so cross-file
        # ref rows (e.g. a weekly note's `#task T-XXX` reference) carry
        # the same audit trail as the canonical declaration. See user
        # follow-up to issue #92: notes added in the popover should be
        # visible in every md file that references the task.
        if body.add_note is not None and body.add_note.strip():
            ref_patch["add_note"] = body.add_note

        if ref_patch:
            # Pre-filter: only notes whose cached body contains the ref_id text.
            # This is a cheap LIKE scan; false positives are eliminated by
            # find_ref_row_lines() inside patch_ref_rows().
            from sqlmodel import col as _col
            candidate_notes = s.exec(
                select(Note)
                .where(_col(Note.body_md).contains(ref_id))
                .where(Note.id != note.id)   # canonical file already written
            ).all()

            for ref_note in candidate_notes:
                ref_full = settings.notes_dir / ref_note.path
                ref_changed = False
                with with_file_lock(ref_full):
                    if ref_full.exists():
                        ref_disk_md = ref_full.read_text(encoding="utf-8")
                        new_ref_md, ref_changed = patch_ref_rows(ref_disk_md, ref_id, ref_patch)
                        if ref_changed:
                            _safe_write_unlocked(ref_full, new_ref_md, notes_dir=settings.notes_dir)
                            ref_disk_after = ref_full.read_text(encoding="utf-8")
                            ref_mtime_after = ref_full.stat().st_mtime
                if ref_changed:
                    # Body-only refresh: the canonical task's index rows
                    # (including the ref-row override TaskAttr / TaskOwner
                    # entries) were already updated by the canonical fast
                    # path above. The ref file itself owns no Task rows
                    # for this ref_id, so a full reindex_file would just
                    # re-derive identical state at O(file × tasks) cost.
                    update_note_body_only(
                        s,
                        note_id=ref_note.id,
                        new_body_md=ref_disk_after,
                        new_mtime=ref_mtime_after,
                    )

    refreshed = s.get(Task, task_id)
    awarded: list[str] = []
    if status_changed and body.status is not None:
        ev_ref = (refreshed.task_uuid if refreshed and refreshed.task_uuid
                  else (t.task_uuid or f"task#{task_id}"))
        awarded += gamify.record_event(
            s, user, gamify.TASK_STATUS_SET,
            ref=ev_ref,
            meta={"from": old_status, "to": body.status},
        )
        if (body.status or "").lower() == "done":
            awarded += gamify.record_event(
                s, user, gamify.TASK_CLOSED,
                ref=ev_ref,
                meta={"from": old_status, "to": body.status},
            )
    out = _task_to_dict(s, refreshed) if refreshed else {"ok": True}
    if awarded:
        # De-dupe: status_set + close on the same task can both award
        # (rare). Preserve order.
        seen: set[str] = set()
        deduped = [k for k in awarded if not (k in seen or seen.add(k))]
        out["awarded_badges"] = deduped
    return out


@router.delete("/tasks/{task_ref}")
def delete_task(
    task_ref: str,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Delete a task by removing its declaration line and any deeper-indented
    children (sub-tasks, AR items, `#note` continuations) from the source
    markdown file, then reindexing.

    RBAC: requester must be the task's owner OR a project manager OR admin.
    """
    t = _resolve_task(task_ref, s)
    note = s.get(Note, t.note_id)
    if not note:
        raise HTTPException(404, "note not found")
    project = _project_for_path(note.path)
    role = _user_role_for_project(s, user, project)
    owners = s.exec(
        select(User.name).join(TaskOwner, TaskOwner.user_id == User.id)
        .where(TaskOwner.task_id == t.id)
    ).all()
    is_owner = user in owners
    # Owner / manager / admin may delete.  Pure project members who don't
    # own this task cannot.
    if role == "none" and not is_owner:
        raise HTTPException(403, "no access to project")
    if role != "manager" and not is_owner:
        raise HTTPException(403, "only the task owner or a project manager can delete")

    full = settings.notes_dir / note.path
    task_uuid = t.task_uuid
    task_title = t.title
    task_line = t.line

    with with_file_lock(full):
        if not full.exists():
            raise HTTPException(404, "note file not found on disk")
        disk_md = full.read_text(encoding="utf-8")
        # Re-anchor by id when possible — line numbers can shift if another
        # writer touched the file between our cached parse and now.
        line_to_remove = task_line
        if task_uuid:
            for i, raw in enumerate(disk_md.splitlines()):
                if f"#id {task_uuid}" in raw:
                    line_to_remove = i
                    break
        new_md = delete_task_block(disk_md, line_to_remove)
        if new_md == disk_md:
            raise HTTPException(409, "task line not found in current file content")
        _safe_write_unlocked(full, new_md, notes_dir=settings.notes_dir)
        disk_after = full.read_text(encoding="utf-8")
        new_mtime = full.stat().st_mtime
    lines_removed = len(disk_md.splitlines()) - len(disk_after.splitlines())
    delete_single_task_from_index(
        s,
        note_id=note.id,
        task_id=t.id,
        new_body_md=disk_after,
        new_mtime=new_mtime,
        line_shift_pivot=line_to_remove,
        line_shift=-max(lines_removed, 1),
    )
    return {"status": "deleted", "task_uuid": task_uuid, "title": task_title}


# ---------- users / search -------------------------------------------------
@router.get("/users")
def list_users(s: Session = Depends(get_session)) -> list[str]:
    return [u.name for u in s.exec(select(User).order_by(User.name)).all()]


@router.get("/me")
def whoami(
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    u = s.exec(select(User).where(User.name == user)).first()
    return {
        "name": user,
        "is_admin": bool(u and u.is_admin),
        "tz": (u.tz if u else "") or "UTC",
    }


class SetTzIn(BaseModel):
    tz: str


@router.patch("/me/tz")
def set_my_tz(
    body: SetTzIn,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, str]:
    """Set this user's IANA timezone (e.g. 'America/Los_Angeles').

    Used by gamification stats so streaks roll over at local midnight.
    Empty string ≡ UTC. Unknown zones are rejected.
    """
    name = (body.tz or "").strip()
    if name:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(name)
        except Exception:
            raise HTTPException(400, f"unknown timezone: {name!r}")
    u = s.exec(select(User).where(User.name == user)).first()
    if u is None:
        raise HTTPException(404, "user not found")
    u.tz = name
    s.add(u)
    s.commit()
    return {"status": "ok", "tz": name or "UTC"}


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.patch("/me/password", status_code=200)
def change_my_password(
    body: ChangePasswordIn,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, str]:
    """Any authenticated user can change their own password.
    Requires the current password for verification.
    """
    if not body.new_password:
        raise HTTPException(400, "new_password cannot be empty")
    u = s.exec(select(User).where(User.name == user)).first()
    if u is None:
        raise HTTPException(404, "user not found")
    if u.pass_hash and not verify_password(body.current_password, u.pass_hash):
        raise HTTPException(403, "current password is incorrect")
    u.pass_hash = hash_password(body.new_password)
    s.add(u)
    s.commit()
    return {"status": "ok"}


# ---------- saved views (per-user query bookmarks) -------------------------

class SavedView(BaseModel):
    """Named reusable query — exactly the shape the FilterBar / `vn`
    serialize to and from.  `query` is an opaque dict of API params."""
    name: str
    query: dict[str, Any] = {}


def _load_views(u: User) -> list[dict[str, Any]]:
    raw = u.saved_views_json or "[]"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


@router.get("/me/views")
def list_my_views(
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
    u = s.exec(select(User).where(User.name == user)).first()
    if u is None:
        raise HTTPException(404, "user not found")
    return _load_views(u)


@router.put("/me/views")
def replace_my_views(
    body: list[SavedView] = Body(...),
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Replace this user's full saved-view list.  Idempotent."""
    u = s.exec(select(User).where(User.name == user)).first()
    if u is None:
        raise HTTPException(404, "user not found")
    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for v in body:
        name = v.name.strip()
        if not name:
            raise HTTPException(400, "view name cannot be empty")
        if name in seen:
            raise HTTPException(400, f"duplicate view name: {name!r}")
        seen.add(name)
        cleaned.append({"name": name, "query": v.query or {}})
    u.saved_views_json = json.dumps(cleaned)
    s.add(u)
    s.commit()
    return {"status": "ok", "count": len(cleaned)}


# ---------- gamification: per-user activity log ---------------------------

@router.get("/me/activity")
def list_my_activity(
    since: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD); inclusive"),
    until: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD); exclusive"),
    kind: Optional[str] = Query(None, description="Filter by event kind"),
    limit: int = Query(200, ge=1, le=1000),
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
    """Return the calling user's recent activity events.

    Privacy: this endpoint is hard-scoped to the authenticated user. There
    is intentionally no ``user`` query param; admins cannot view other
    users' streams here.
    """
    u = s.exec(select(User).where(User.name == user)).first()
    if u is None or u.id is None:
        raise HTTPException(404, "user not found")
    q = select(ActivityEvent).where(ActivityEvent.user_id == u.id)
    if since:
        try:
            q = q.where(ActivityEvent.ts >= datetime.fromisoformat(since))
        except ValueError:
            raise HTTPException(400, "since must be ISO date/datetime")
    if until:
        try:
            q = q.where(ActivityEvent.ts < datetime.fromisoformat(until))
        except ValueError:
            raise HTTPException(400, "until must be ISO date/datetime")
    if kind:
        q = q.where(ActivityEvent.kind == kind)
    q = q.order_by(ActivityEvent.ts.desc()).limit(limit)
    rows = s.exec(q).all()
    out: list[dict[str, Any]] = []
    for ev in rows:
        try:
            meta = json.loads(ev.meta_json) if ev.meta_json else {}
        except json.JSONDecodeError:
            meta = {}
        out.append({
            "id": ev.id,
            "kind": ev.kind,
            "ref": ev.ref,
            "ts": ev.ts.isoformat(),
            "meta": meta,
        })
    return out


@router.post("/admin/gamify/backfill")
def admin_gamify_backfill(
    s: Session = Depends(get_session),
    _: str = Depends(require_admin),
) -> dict[str, Any]:
    """One-shot reconstruction of historical events from existing tasks.

    Idempotent — re-running deletes prior backfill rows first. See
    ``app.gamify.backfill`` for the strategy.
    """
    counts = gamify.backfill(s)
    s.commit()
    return {"status": "ok", **counts}


# ---------- gamification: per-user stats (read-only) ---------------------

from .. import gamify_stats as _gstats  # noqa: E402  (after admin endpoint above)


@router.get("/me/stats")
def my_stats(
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Return the calling user's lifetime + windowed activity stats.

    Privacy: hard-scoped to the caller; no ``user`` parameter. UTC dates
    everywhere (per-user TZ is a future enhancement).
    """
    u = s.exec(select(User).where(User.name == user)).first()
    if u is None or u.id is None:
        raise HTTPException(404, "user not found")
    return _gstats.compute_stats(s, u.id)


@router.get("/me/streak")
def my_streak(
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Compact streak summary — the same numbers ``/me/stats`` returns,
    pulled out for callers (e.g. the CLI) that only want the headline.
    """
    u = s.exec(select(User).where(User.name == user)).first()
    if u is None or u.id is None:
        raise HTTPException(404, "user not found")
    full = _gstats.compute_stats(s, u.id)
    return {
        "current_streak_days": full["current_streak_days"],
        "longest_streak_days": full["longest_streak_days"],
        "rest_tokens_remaining": full["rest_tokens_remaining"],
        "as_of": full["as_of"],
    }


@router.get("/me/history")
def my_history(
    days: int = Query(30, ge=1, le=365),
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
    """Per-day close + note-edit counts for the trailing ``days`` window.

    Powers the ANSI sparkline in ``vn me history``.
    """
    u = s.exec(select(User).where(User.name == user)).first()
    if u is None or u.id is None:
        raise HTTPException(404, "user not found")
    return _gstats.compute_history(s, u.id, days=days)


@router.get("/me/badges")
def my_badges(
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Earned + locked badges for the calling user.

    Hidden badges are surfaced only after they're earned; until then
    they're rolled into ``hidden_locked_count`` so users can see *that*
    there's more without spoiling the catalog.
    """
    from .. import badges as _badges
    u = s.exec(select(User).where(User.name == user)).first()
    if u is None or u.id is None:
        raise HTTPException(404, "user not found")
    return _badges.list_badges(s, u.id)


# ---------- admin: user management ----------------------------------------

class UserCreateIn(BaseModel):
    name: str
    password: str
    is_admin: bool = False


class UserPatchIn(BaseModel):
    password: Optional[str] = None
    is_admin: Optional[bool] = None


def _user_to_dict(u: User) -> dict[str, Any]:
    return {"name": u.name, "is_admin": bool(u.is_admin), "has_password": bool(u.pass_hash)}


@router.get("/admin/users")
def admin_list_users(
    s: Session = Depends(get_session),
    _admin: str = Depends(require_admin),
) -> list[dict[str, Any]]:
    return [_user_to_dict(u) for u in s.exec(select(User).order_by(User.name)).all()]


@router.post("/admin/users", status_code=201)
def admin_create_user(
    body: UserCreateIn,
    s: Session = Depends(get_session),
    _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name required")
    if not body.password:
        raise HTTPException(400, "password required")
    existing = s.exec(select(User).where(User.name == name)).first()
    if existing is not None:
        raise HTTPException(409, f"user '{name}' already exists")
    u = User(name=name, pass_hash=hash_password(body.password), is_admin=body.is_admin)
    s.add(u)
    s.commit()
    s.refresh(u)
    return _user_to_dict(u)


@router.patch("/admin/users/{name}")
def admin_patch_user(
    name: str,
    body: UserPatchIn,
    s: Session = Depends(get_session),
    admin: str = Depends(require_admin),
) -> dict[str, Any]:
    u = s.exec(select(User).where(User.name == name)).first()
    if u is None:
        raise HTTPException(404, "user not found")
    if body.password is not None:
        if not body.password:
            raise HTTPException(400, "password cannot be empty")
        u.pass_hash = hash_password(body.password)
    if body.is_admin is not None:
        # Prevent removing admin from yourself or from the last remaining admin.
        if not body.is_admin and u.is_admin:
            if u.name == admin:
                raise HTTPException(400, "cannot remove admin from yourself")
            others = s.exec(select(User).where(User.is_admin == True, User.name != u.name)).all()  # noqa: E712
            if not others:
                raise HTTPException(400, "cannot remove admin from the last admin")
        u.is_admin = body.is_admin
    s.add(u)
    s.commit()
    s.refresh(u)
    return _user_to_dict(u)


@router.delete("/admin/users/{name}")
def admin_delete_user(
    name: str,
    s: Session = Depends(get_session),
    admin: str = Depends(require_admin),
) -> dict[str, str]:
    if name == admin:
        raise HTTPException(400, "cannot delete yourself")
    u = s.exec(select(User).where(User.name == name)).first()
    if u is None:
        raise HTTPException(404, "user not found")
    if u.is_admin:
        others = s.exec(select(User).where(User.is_admin == True, User.name != u.name)).all()  # noqa: E712
        if not others:
            raise HTTPException(400, "cannot delete the last admin")
    # Detach any project memberships referencing this user.
    for pm in s.exec(select(ProjectMember).where(ProjectMember.user_name == name)).all():
        s.delete(pm)
    s.delete(u)
    s.commit()
    return {"status": "deleted", "name": name}


@router.get("/search")
def search(q: str, s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    # FTS5 treats characters like '-', ':', '"', '*', '^', '(' / ')' and
    # '.' as operators; passing a raw user string (e.g. 'fit-val') would
    # raise sqlite3.OperationalError -> 500. Convert the input into a
    # safe AND-of-tokens query: split on whitespace, drop FTS5-special
    # punctuation from each token, and quote each surviving token as a
    # phrase. Empty result -> return [] without hitting the DB.
    raw_tokens = q.split() if q else []
    safe_tokens: list[str] = []
    for tok in raw_tokens:
        cleaned = _re.sub(r'[\"\*\^\(\)\:\.\-]', " ", tok).strip()
        for piece in cleaned.split():
            # Wrap in double quotes so SQLite treats it as a phrase token
            # (escape any embedded quote, though we just stripped them).
            safe_tokens.append('"' + piece.replace('"', '') + '"')
    if not safe_tokens:
        return []
    fts_query = " AND ".join(safe_tokens)
    rows = s.exec(text("""
        SELECT n.id, n.path, n.title
        FROM notes_fts f JOIN note n ON n.id = f.rowid
        WHERE notes_fts MATCH :q
        LIMIT 50
    """).bindparams(q=fts_query)).all()
    return [{"id": r[0], "path": r[1], "title": r[2]} for r in rows]


@router.post("/admin/reindex", status_code=200)
def admin_reindex(
    s: Session = Depends(get_session),
    _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    """Re-scan all notes on disk, update the index, sweep orphans (#207),
    and auto-bootstrap orphan projects."""
    n = reindex_all(s)
    return {
        "status": "ok",
        "files_indexed": n,
        "orphans_swept": WATCHER_STATE.get("orphans_swept_last", 0),
    }


@router.get("/admin/watcher_status")
def admin_watcher_status(
    _admin: str = Depends(require_admin),
) -> dict[str, Any]:
    """Diagnostic view of the file watcher (#150).

    Reports whether the watcher is running, which mode it picked
    (event/polling), the detected filesystem type, and how many events have
    been processed.  Use this to confirm out-of-band edits are being
    observed before relying on propagation fast paths.
    """
    from ..indexer import WATCHER_STATE
    return dict(WATCHER_STATE)
