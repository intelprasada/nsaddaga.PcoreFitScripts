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

from ..auth import hash_password, validate_password, verify_password, require_admin, require_user
from ..config import settings
from ..db import get_session, get_engine
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
    merge_with_disk_tasks,
    remove_attr, replace_task_title, roll_to_next_week, update_task_status,
    find_ref_row_lines, patch_ref_rows, insert_ar_ref_row_after,
)
from ..models import (
    ActivityEvent, Feature, Link, Note, Project, ProjectMember, Task, TaskAttr,
    TaskFeature, TaskOwner, TaskProject, User,
)
from ..parser import parse
from ..safe_io import (
    StaleWriteError, _safe_write_unlocked, etag_components, etag_for,
    etag_for_bytes, safe_write, with_file_lock,
)
from .. import gamify
from ..phonebook import get_phonebook
from ..owner_normalize import canonical_idsid

router = APIRouter(dependencies=[Depends(require_user)])


def _canon_owner_filter(values: list[str]) -> list[str]:
    """Expand an owner-filter list to include canonical idsids (#174 follow-up).

    The indexer rewrites task owners to canonical idsids via the local
    phonebook. Filter callers (``/tasks?owner=admin``, ``MyTasksView``
    which passes ``me.name``) may still send the *login* username or
    any curated alias. Without expansion, ``?owner=admin`` matches zero
    rows because the DB stores ``nsaddaga``.

    For each input we include both the canonical idsid (when an alias
    matches a curated entry) **and** the original value. The original
    is kept as a defensive fallback so any non-canonical row that
    slipped through (e.g. pre-#174 data, ambiguous tokens, local seed
    accounts) still matches. Order is preserved, duplicates removed.
    """
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if not v:
            continue
        if v not in seen:
            seen.add(v)
            out.append(v)
        try:
            canon, status = canonical_idsid(v)
        except Exception:
            continue
        if status == "resolved" and canon and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def _owner_display_map(names: list[str]) -> dict[str, str]:
    """Map owner User.name → friendly display name from the phonebook
    (#226 follow-up). Names not in the phonebook map to themselves so
    callers can render uniformly without null-checking. Result is
    deterministic and side-effect-free; safe to call per-request."""
    out: dict[str, str] = {}
    if not names:
        return out
    pb = get_phonebook()
    for n in names:
        if not n:
            continue
        try:
            entry, _ = pb.resolve(n, local_only=True)
        except Exception:
            entry = None
        out[n] = (entry.display if entry and entry.display else n)
    return out

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


def _require_root_admin(s: Session, user: str, action: str) -> None:
    """Guard destructive ops on root-level notes (#231).

    ``_user_role_for_project(s, user, None)`` returns ``"manager"`` for
    everyone because root-level files are considered "open". That's
    fine for reads, but lumps destructive writes (delete / overwrite /
    rename) in with reads. Callers that mutate a root-level note must
    additionally require admin.
    """
    u = s.exec(select(User).where(User.name == user)).first()
    if u is None or not u.is_admin:
        raise HTTPException(
            403, f"admin role required to {action} root-level notes"
        )


def _require_project_access(
    s: Session, user: str, project: Optional[str], *, need_manager: bool = False
) -> str:
    role = _user_role_for_project(s, user, project)
    if role == "none":
        raise HTTPException(403, f"no access to project '{project}'")
    if need_manager and role != "manager":
        raise HTTPException(403, "manager role required")
    return role


# ---------- read-side RBAC (#230) -------------------------------------------
# Visibility rules for GET endpoints. Admins see everything. Non-admins
# see (a) any note whose top-level folder is a project they belong to,
# and (b) root-level notes (no top folder) — preserves the existing
# write-side semantic where _user_role_for_project(None) == "manager".


def _visible_projects(s: Session, user: str) -> tuple[bool, set[str]]:
    """Return ``(is_admin, set_of_project_names_user_belongs_to)``.

    Used by read endpoints to scope query results to projects the caller
    is allowed to see.  Root-level notes (path with no '/') are always
    visible — callers add that branch to their WHERE clause.
    """
    u = s.exec(select(User).where(User.name == user)).first()
    if u is not None and u.is_admin:
        return (True, set())
    rows = s.exec(
        select(ProjectMember.project_name).where(ProjectMember.user_name == user)
    ).all()
    return (False, {r for r in rows})


def _note_visibility_sql_clause(
    note_alias: str, is_admin: bool, projects: set[str],
    params: dict[str, Any], expanding: list[str], slot: str,
) -> str | None:
    """Return a SQL fragment (no leading AND) restricting ``<note_alias>.path``
    to notes the caller may see, or ``None`` if admin (no restriction).

    Mutates ``params``/``expanding`` to register the bound projects.
    """
    if is_admin:
        return None
    # Root-level files: path contains no '/'.
    if not projects:
        return f"INSTR({note_alias}.path, '/') = 0"
    params[slot] = tuple(projects)
    expanding.append(slot)
    # Substring before first '/' must be in the visible-projects set.
    return (
        f"(INSTR({note_alias}.path, '/') = 0 "
        f"OR SUBSTR({note_alias}.path, 1, INSTR({note_alias}.path, '/') - 1) "
        f"IN :{slot})"
    )


def _note_is_visible(note: Note, is_admin: bool, projects: set[str]) -> bool:
    """Python-side mirror of the SQL clause above."""
    if is_admin:
        return True
    project = _project_for_path(note.path)
    if project is None:
        return True
    return project in projects


def _require_note_access(s: Session, user: str, note: Note) -> None:
    """403 if ``user`` can't see ``note`` (mirrors write-side rules)."""
    is_admin, projects = _visible_projects(s, user)
    if not _note_is_visible(note, is_admin, projects):
        raise HTTPException(403, "no access to project")


def _require_task_access(s: Session, user: str, task: Task) -> None:
    note = s.get(Note, task.note_id)
    if note is None:
        raise HTTPException(404, "task note not found")
    _require_note_access(s, user, note)


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


def _collect_filter(values: list[str] | None) -> list[str]:
    """Flatten a query-string filter into a token list.

    Supports both repeated query keys (``?owner=a&owner=b``) and the
    legacy comma-separated form (``?owner=a,b``) interchangeably. Empty
    tokens are dropped. Order is preserved.

    Background: ``list[str] = Query(default_factory=list)`` binds repeated
    keys correctly, but the comma-CSV form arrives as ``['a,b']`` — a
    single element. This helper splits each element on commas so callers
    don't have to care which form the caller used. See #237.
    """
    out: list[str] = []
    for v in values or []:
        if not v:
            continue
        for tok in v.split(","):
            tok = tok.strip()
            if tok:
                out.append(tok)
    return out


import re as _re
_UUID_RE = _re.compile(r"^T-[0-9A-Z]{6,}$")
# Locate `#id T-XXXXXX` tokens on a single line; matches the parser's
# rule for stamped task uuids (see backend/app/parser/lexer.py).
_ID_TOKEN_RE = _re.compile(r"#id\s+(T-[0-9A-Z]{6,})\b")


def _find_task_line_in_md(md: str, t: Task) -> Optional[int]:
    """Locate the current 0-indexed line of ``t`` inside ``md``.

    Used by :func:`patch_task` to **re-anchor** a popover patch when the
    cached ``Task.line`` has gone stale relative to disk (see issue
    #239). The previous implementation trusted ``t.line`` and could
    silently rewrite the wrong line when the file shifted between
    fast-path popover patches.

    Strategy:

    * If ``t.task_uuid`` is set, scan ``md`` for ``#id <uuid>``. This is
      the only fully-reliable matcher because uuids are unique.
    * Otherwise (unstamped task) parse ``md`` and match by slug. If more
      than one task in the file shares this slug, return ``None`` —
      caller must fail closed rather than risk patching the wrong task.

    Returns ``None`` when the task cannot be uniquely located. Caller
    should treat that as a 409 / 404 (the task may have been removed or
    renamed out-of-band).
    """
    if t.task_uuid:
        target = t.task_uuid
        for i, line in enumerate(md.splitlines()):
            m = _ID_TOKEN_RE.search(line)
            if m and m.group(1) == target:
                return i
        return None
    # Unstamped: fall back to slug match via the parser, which does the
    # same de-duplication / continuation handling as the indexer.
    try:
        parsed = parse(md).get("tasks", [])
    except Exception:
        return None
    matches = [pt for pt in parsed if pt.get("slug") == t.slug]
    if len(matches) == 1:
        return matches[0]["line"]
    return None


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
    # Design 8d: two-axis etag. When ``if_match_prose`` is provided the
    # server only requires the *prose* component of the disk etag to
    # match; popover-driven task-line drift on disk is reconciled via
    # :func:`merge_with_disk_tasks` so the user's free-text edits land
    # without colliding on every concurrent ``PATCH /tasks/...``. See
    # checkpoint "Implementing two-axis etag design 8d".
    if_match_prose: Optional[str] = None


@router.get("/notes")
def list_notes(
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
    is_admin, projects = _visible_projects(s, user)
    notes = s.exec(select(Note).order_by(Note.updated_at.desc())).all()
    return [
        {"id": n.id, "path": n.path, "title": n.title, "updated_at": n.updated_at}
        for n in notes
        if _note_is_visible(n, is_admin, projects)
    ]


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
    if not path or ".." in path or path.startswith("/") or path.endswith("/"):
        raise HTTPException(400, "invalid path")
    project = _project_for_path(path)
    if _user_role_for_project(s, user, project) == "none":
        raise HTTPException(403, "no access")
    full = settings.notes_dir / path
    if not full.exists() or not full.is_file():
        raise HTTPException(404, "note not found")
    disk_md = full.read_text(encoding="utf-8")
    components = etag_components(disk_md)
    return {
        "path": path,
        "etag": etag_for_bytes(disk_md.encode()),
        "prose_etag": components["prose"],
        "tasks_etag": components["tasks"],
        "mtime": full.stat().st_mtime,
    }


@router.get("/notes/{note_id}")
def get_note(
    note_id: int,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    n = s.get(Note, note_id)
    if not n:
        raise HTTPException(404, "note not found")
    _require_note_access(s, user, n)
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
    components = etag_components(disk_md)
    return {
        "id": n.id, "path": n.path, "title": n.title,
        "body_md": disk_md, "updated_at": n.updated_at,
        "etag": disk_etag,
        "prose_etag": components["prose"],
        "tasks_etag": components["tasks"],
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
    if project is None:
        # #231: root-level writes are destructive (overwrite existing
        # files, no project owner), so require admin.
        _require_root_admin(s, user, "write")
    full = settings.notes_dir / body.path
    expected = body.if_match if body.if_match is not None else if_match
    pre_existing = full.exists()
    pre_body = full.read_text(encoding="utf-8") if pre_existing else ""

    # Design 8d: prose-aware concurrency. If the client sent
    # ``if_match_prose`` we ignore byte-level ``if_match`` and instead
    # accept the write as long as the *prose* component of the on-disk
    # file matches what the client started from. We then merge the
    # client's prose with whatever task lines are currently on disk so
    # popover ``PATCH /tasks/...`` writes that landed during the typing
    # session are preserved. See checkpoint
    # "Implementing two-axis etag design 8d".
    write_md = body.body_md
    if body.if_match_prose is not None and pre_existing:
        with with_file_lock(full):
            disk_now = full.read_text(encoding="utf-8")
            disk_components = etag_components(disk_now)
            if disk_components["prose"] != body.if_match_prose:
                # Genuine prose-vs-prose conflict — surface to the user.
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "stale_prose",
                        "message": "the prose changed under you; reload before saving",
                        "current_content": disk_now,
                        "current_etag": etag_for_bytes(disk_now.encode()),
                        "current_prose_etag": disk_components["prose"],
                        "current_tasks_etag": disk_components["tasks"],
                    },
                )
            # Prose matches — overlay disk's task lines onto the
            # incoming prose buffer and rewrite. Bypass the byte-etag
            # check below by clearing ``expected``: the prose-axis
            # check we just performed is the authoritative gate.
            write_md = merge_with_disk_tasks(body.body_md, disk_now)
            expected = None
    try:
        new_etag = safe_write(
            full, write_md,
            notes_dir=settings.notes_dir, expected_etag=expected,
        )
    except StaleWriteError as e:
        # 409 Conflict — body carries current content + etag for the
        # client to surface a recovery / merge UI. See issue #60.
        cur_components = etag_components(e.current_content or "")
        raise HTTPException(
            status_code=409,
            detail={
                "error": "stale_write",
                "message": "the file changed under you; reload before saving",
                "current_content": e.current_content,
                "current_etag": e.current_etag,
                "current_prose_etag": cur_components["prose"],
                "current_tasks_etag": cur_components["tasks"],
            },
        )
    note = reindex_file(full, s)
    awarded: list[str] = []
    if not pre_existing:
        awarded = gamify.record_event(s, user, gamify.NOTE_CREATED, ref=body.path)
    elif pre_body.strip() != write_md.strip():
        # Skip whitespace-only / no-op writes so streaks aren't gamed by
        # repeatedly saving an unchanged file.
        awarded = gamify.record_event(s, user, gamify.NOTE_EDITED, ref=body.path)
    final_md = full.read_text(encoding="utf-8") if full.exists() else write_md
    final_components = etag_components(final_md)
    out: dict[str, Any] = {
        "id": note.id,
        "path": note.path,
        "etag": new_etag,
        "prose_etag": final_components["prose"],
        "tasks_etag": final_components["tasks"],
    }
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
    """Roll a weekly note forward to the next Intel work week with the
    archive (single-active-file) model.

    Behaviour:

    * Open top-level ``!task`` / ``!AR`` declarations move canonically to
      the new ww file (their entire indent block, including done children
      of an open parent, follows them).
    * Done top-level declarations stay canonical in the archived copy of
      the source file (under sibling ``_archive/<basename>``).
    * The source file is removed from disk and its ``Note`` row is
      repathed to ``_archive/<basename>`` and flagged ``archived = True``.
      Subsequent popover/PATCH writes target the new file unless the
      caller is a project manager (RBAC enforced in ``patch_task`` /
      ``delete_task``).
    * Cross-file ``#task T-XXX`` references continue to resolve by uuid;
      child rows (TaskAttr / TaskOwner / Link) stay attached because the
      ``Task`` row is moved, not recreated.

    Returns ``{id, path, from_ww, to_ww, archived_path, moved_count}``.
    """
    src_rel = body.path
    if ".." in src_rel or src_rel.startswith("/"):
        raise HTTPException(400, "invalid path")
    project = _project_for_path(src_rel)
    role = _user_role_for_project(s, user, project)
    if role == "none" or (role == "member" and project is not None):
        raise HTTPException(403, "manager role required to create notes")
    if project is None:
        _require_root_admin(s, user, "roll")
    src_full = settings.notes_dir / src_rel
    if not src_full.exists():
        raise HTTPException(404, "source note not found")

    archive_dir = src_full.parent / "_archive"
    archived_path = archive_dir / src_full.name
    archived_rel = str(archived_path.relative_to(settings.notes_dir))

    # ── Read + plan under the source file's lock so a concurrent edit
    #    can't interleave between read and the rollover write-back.
    with with_file_lock(src_full):
        src_md = src_full.read_text(encoding="utf-8")
        try:
            new_md, new_base, cur, nxt, archived_md, moved_uuids = roll_to_next_week(
                src_md, src_full.name,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        dst_full = src_full.parent / new_base
        dst_rel = str(dst_full.relative_to(settings.notes_dir))
        if dst_full.exists() and not body.overwrite:
            raise HTTPException(409, f"target note already exists: {dst_rel}")
        if archived_path.exists() and not body.overwrite:
            raise HTTPException(409, f"archive already exists: {archived_rel}")

    # ── Phase 1 (DB): repath the old Note → archive, create the new Note,
    #    bulk-reassign moved tasks to the new note id.  All committed
    #    BEFORE any disk side-effects so the watcher's later reindex pass
    #    (or any concurrent reader) sees a consistent view.
    old_note = s.exec(select(Note).where(Note.path == src_rel)).first()
    if old_note is None:
        # Force-index it now so we have something to repath.
        old_note = reindex_file(src_full, s)

    # If overwrite=True and an archived/dst Note already exists, drop them
    # so the UNIQUE(path) repath below succeeds without a constraint clash.
    if body.overwrite:
        stale_archive = s.exec(select(Note).where(Note.path == archived_rel)).first()
        if stale_archive is not None and stale_archive.id != old_note.id:
            remove_path(archived_rel, s)
            s.flush()

    old_note.path = archived_rel
    old_note.archived = True
    old_note.body_md = archived_md
    old_note.updated_at = datetime.utcnow()
    s.add(old_note)
    s.flush()

    new_note = s.exec(select(Note).where(Note.path == dst_rel)).first()
    if new_note is None:
        new_note = Note(path=dst_rel, body_md=new_md, archived=False)
        s.add(new_note)
    else:
        new_note.body_md = new_md
        new_note.archived = False
        new_note.updated_at = datetime.utcnow()
    s.flush()

    if moved_uuids:
        # Bulk reassign Task rows by stable uuid.  This MUST happen before
        # we reindex the new file — otherwise ``_incremental_reindex``
        # would see the moved tasks as "new" (no match by uuid in the new
        # note's existing rows) and try to INSERT, failing the UNIQUE
        # constraint on ``task.task_uuid``.
        ph_list = ",".join(f":u{i}" for i in range(len(moved_uuids)))
        params: dict[str, Any] = {"nid": new_note.id}
        for i, u in enumerate(moved_uuids):
            params[f"u{i}"] = u
        s.exec(
            text(f"UPDATE task SET note_id = :nid WHERE task_uuid IN ({ph_list})")
            .bindparams(**params)
        )
    s.commit()

    # ── Phase 2 (disk): write the archive, write the next-week file,
    #    delete the source.  Watcher events fire AFTER DB is consistent;
    #    its reindex passes are idempotent against our state so any race
    #    is benign.
    archive_dir.mkdir(parents=True, exist_ok=True)
    safe_write(archived_path, archived_md, notes_dir=settings.notes_dir)
    safe_write(dst_full, new_md, notes_dir=settings.notes_dir)
    with with_file_lock(src_full):
        if src_full.exists():
            src_full.unlink()

    # ── Phase 3: explicit reindex to refresh ``Task.line`` for both files
    #    (canonical declarations move = line numbers shift).
    reindex_file(archived_path, s)
    reindex_file(dst_full, s)

    return {
        "id": new_note.id,
        "path": new_note.path,
        "from_ww": cur,
        "to_ww": nxt,
        "archived_path": archived_rel,
        "moved_count": len(moved_uuids),
    }


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
    if project is None:
        _require_root_admin(s, user, "modify")
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
    project = _project_for_path(n.path)
    if project is None:
        # #231: deleting a root-level note used to fall through the
        # _user_role_for_project(None)=='manager' shortcut. Require admin.
        _require_root_admin(s, user, "delete")
    else:
        _require_project_access(s, user, project, need_manager=True)
    full = settings.notes_dir / n.path
    rel = n.path
    if full.exists():
        full.unlink()
    remove_path(rel, s)
    return {"status": "deleted"}


# ---------- archive / unarchive (#304) --------------------------------------
# User-driven archive: evict a note's or a project's derived rows from the
# main DB into archive.db.  Rollover archives (`/_archive/` + archive_kind
# in {'', 'rollover'}) are explicitly rejected by the archive_ops helper —
# this feature does not touch them.

def _open_archive_session() -> Session:
    from ..db import get_archive_engine
    return Session(get_archive_engine())


@router.post("/notes/{note_id}/archive")
def archive_note_endpoint(
    note_id: int,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    from .. import archive_ops
    n = s.get(Note, note_id)
    if not n:
        raise HTTPException(404, "note not found")
    project = _project_for_path(n.path)
    if project is None:
        _require_root_admin(s, user, "archive")
    else:
        _require_project_access(s, user, project, need_manager=True)
    if n.archive_kind in archive_ops.ROLLOVER_ARCHIVE_KINDS and \
            archive_ops._is_rollover_path(n.path):
        raise HTTPException(
            409, "cannot user-archive a weekly rollover archive; "
                 "these are managed by the rollover flow (#304 carve-out).",
        )
    with _open_archive_session() as archive:
        result = archive_ops.archive_notes(s, archive, [note_id])
    result["archive_kind"] = "user"
    return result


@router.post("/notes/{note_id}/unarchive")
def unarchive_note_endpoint(
    note_id: int,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    from .. import archive_ops
    n = s.get(Note, note_id)
    if not n:
        raise HTTPException(404, "note not found")
    project = _project_for_path(n.path)
    if project is None:
        _require_root_admin(s, user, "unarchive")
    else:
        _require_project_access(s, user, project, need_manager=True)
    if n.archive_kind != "user":
        raise HTTPException(
            409,
            f"note is not a user-archive (archive_kind={n.archive_kind!r}); "
            f"rollover archives cannot be unarchived through this endpoint.",
        )
    full = settings.notes_dir / n.path
    if not full.exists():
        raise HTTPException(
            409,
            f"source file missing on disk: {n.path}. "
            f"Restore it before unarchiving.",
        )
    with _open_archive_session() as archive:
        result = archive_ops.unarchive_notes(s, archive, [note_id])
    return result


@router.post("/projects/{project}/archive")
def archive_project_endpoint(
    project: str,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    from .. import archive_ops
    _require_project_access(s, user, project, need_manager=True)
    proj = s.exec(select(Project).where(Project.name == project)).first()
    # Gather every note under this project folder — includes root-level
    # files exactly one level deep (top-level ``project/*.md`` files) as
    # well as anything deeper. Rollover archives are filtered out by
    # ``archive_ops.archive_notes`` itself.
    prefix = f"{project}/"
    candidates = s.exec(
        select(Note).where(
            (Note.path == project) | (Note.path.like(f"{prefix}%"))  # type: ignore[attr-defined]
        )
    ).all()
    note_ids = [
        n.id for n in candidates
        if not (n.archive_kind in archive_ops.ROLLOVER_ARCHIVE_KINDS
                and archive_ops._is_rollover_path(n.path))
    ]
    with _open_archive_session() as archive:
        result = archive_ops.archive_notes(s, archive, note_ids)
    # Mirror the note flag on the Project row so listing endpoints can
    # skip archived projects without re-checking each note.
    if proj is not None:
        proj.archived = True
        s.add(proj)
        s.commit()
    result["project"] = project
    return result


@router.post("/projects/{project}/unarchive")
def unarchive_project_endpoint(
    project: str,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    from .. import archive_ops
    _require_project_access(s, user, project, need_manager=True)
    proj = s.exec(select(Project).where(Project.name == project)).first()
    prefix = f"{project}/"
    candidates = s.exec(
        select(Note).where(
            ((Note.path == project) | (Note.path.like(f"{prefix}%")))  # type: ignore[attr-defined]
            & (Note.archive_kind == "user")  # type: ignore[arg-type]
        )
    ).all()
    note_ids = [n.id for n in candidates]
    with _open_archive_session() as archive:
        result = archive_ops.unarchive_notes(s, archive, note_ids)
    if proj is not None:
        proj.archived = False
        s.add(proj)
        s.commit()
    result["project"] = project
    return result


@router.post("/archive/reconcile")
def reconcile_archives_endpoint(
    s: Session = Depends(get_session),
    user: str = Depends(require_admin),
) -> dict[str, Any]:
    """Admin-only: repair after a crashed two-DB archive txn (#304)."""
    from .. import archive_ops
    with _open_archive_session() as archive:
        return archive_ops.reconcile_archives(s, archive)


# ---------- archive read endpoints (#304, PR 4) ---------------------------
#
# These endpoints expose the frozen state that lives in ``archive.db``
# (task rows, child rows, notes/projects/features by natural key) plus
# the ``Note.archive_kind == 'user'`` rows that stay in ``main.db`` for
# navigation. RBAC mirrors the main read surface — users see only
# archives whose owning project they have visibility for.


def _archive_task_row(row: Any, note_path: str, user_by_id: dict[int, str],
                      project_by_id: dict[int, str],
                      feature_by_id: dict[int, str],
                      owners_by_tid: dict[int, list[int]],
                      projects_by_tid: dict[int, list[int]],
                      features_by_tid: dict[int, list[int]],
                      attrs_by_tid: dict[int, dict[str, str]] | None = None,
                      ) -> dict[str, Any]:
    """Shape a single archive.db Task row for the JSON response."""
    attrs = (attrs_by_tid or {}).get(row.id, {}) if attrs_by_tid else {}
    return {
        "id": row.id,
        "task_uuid": row.task_uuid,
        "note_path": note_path,
        "title": row.title,
        "status": row.status,
        "priority": attrs.get("priority"),
        "eta": attrs.get("eta"),
        "kind": row.kind,
        "slug": row.slug,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "owners": [
            user_by_id.get(uid, f"user#{uid}")
            for uid in owners_by_tid.get(row.id, [])
        ],
        "projects": [
            project_by_id[pid]
            for pid in projects_by_tid.get(row.id, [])
            if pid in project_by_id
        ],
        "features": [
            feature_by_id[fid]
            for fid in features_by_tid.get(row.id, [])
            if fid in feature_by_id
        ],
    }


@router.get("/archive/notes")
def list_archived_notes(
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
    """User-archived notes visible to the caller.

    Rows live in ``main.db`` (``archive_kind == 'user'``); task counts
    are read from ``archive.db``.
    """
    is_admin, projects = _visible_projects(s, user)
    notes = s.exec(
        select(Note)
        .where(Note.archive_kind == "user")  # type: ignore[arg-type]
        .order_by(Note.updated_at.desc())  # type: ignore[attr-defined]
    ).all()
    visible = [n for n in notes if _note_is_visible(n, is_admin, projects)]
    if not visible:
        return []

    counts: dict[str, int] = {}
    with _open_archive_session() as arch:
        for n in visible:
            arch_note = arch.exec(select(Note).where(Note.path == n.path)).first()
            if arch_note is None:
                counts[n.path] = 0
                continue
            row = arch.exec(
                text("SELECT COUNT(*) FROM task WHERE note_id = :i")
                .bindparams(i=arch_note.id)
            ).first()
            counts[n.path] = int(row[0]) if row else 0

    return [
        {
            "id": n.id,
            "path": n.path,
            "title": n.title,
            "project": _project_for_path(n.path),
            "updated_at": n.updated_at,
            "task_count": counts.get(n.path, 0),
        }
        for n in visible
    ]


@router.get("/archive/notes/{note_id}")
def get_archived_note(
    note_id: int,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    n = s.get(Note, note_id)
    if n is None or n.archive_kind != "user":
        raise HTTPException(404, "archived note not found")
    project = _project_for_path(n.path)
    if _user_role_for_project(s, user, project) == "none":
        raise HTTPException(403, "no access")

    with _open_archive_session() as arch:
        arch_note = arch.exec(select(Note).where(Note.path == n.path)).first()
        task_rows: list[Any] = []
        task_ids: list[int] = []
        if arch_note is not None:
            task_rows = arch.exec(
                select(Task)
                .where(Task.note_id == arch_note.id)  # type: ignore[arg-type]
                .order_by(Task.line)  # type: ignore[attr-defined]
            ).all()
            task_ids = [t.id for t in task_rows]

        owners_by_tid: dict[int, list[int]] = {}
        projects_by_tid: dict[int, list[int]] = {}
        features_by_tid: dict[int, list[int]] = {}
        user_by_id: dict[int, str] = {}
        project_by_id: dict[int, str] = {}
        feature_by_id: dict[int, str] = {}

        if task_ids:
            for r in arch.exec(
                text("SELECT task_id, user_id FROM taskowner WHERE task_id IN :ids")
                .bindparams(bindparam("ids", expanding=True))
                .bindparams(ids=task_ids),
            ).all():
                owners_by_tid.setdefault(r[0], []).append(r[1])
            for r in arch.exec(
                text("SELECT task_id, project_id FROM taskproject WHERE task_id IN :ids")
                .bindparams(bindparam("ids", expanding=True))
                .bindparams(ids=task_ids),
            ).all():
                projects_by_tid.setdefault(r[0], []).append(r[1])
            for r in arch.exec(
                text("SELECT task_id, feature_id FROM taskfeature WHERE task_id IN :ids")
                .bindparams(bindparam("ids", expanding=True))
                .bindparams(ids=task_ids),
            ).all():
                features_by_tid.setdefault(r[0], []).append(r[1])
            for p in arch.exec(select(Project)).all():
                project_by_id[p.id] = p.name
            for f in arch.exec(select(Feature)).all():
                feature_by_id[f.id] = f.name

    attrs_by_tid: dict[int, dict[str, str]] = {}
    if task_ids:
        with _open_archive_session() as arch2:
            for r in arch2.exec(
                text("SELECT task_id, key, value FROM taskattr "
                     "WHERE task_id IN :ids AND key IN ('priority', 'eta')")
                .bindparams(bindparam("ids", expanding=True))
                .bindparams(ids=task_ids),
            ).all():
                attrs_by_tid.setdefault(int(r[0]), {})[str(r[1])] = str(r[2])

    # Resolve user names from MAIN db (the archive.db user table is
    # intentionally excluded — see #304 PR 1 schema notes).
    all_uids = {uid for uids in owners_by_tid.values() for uid in uids}
    for u in s.exec(select(User).where(User.id.in_(all_uids))).all():  # type: ignore[attr-defined]
        user_by_id[u.id] = u.name

    return {
        "id": n.id,
        "path": n.path,
        "title": n.title,
        "body_md": n.body_md,
        "project": project,
        "updated_at": n.updated_at,
        "tasks": [
            _archive_task_row(
                r, n.path,
                user_by_id, project_by_id, feature_by_id,
                owners_by_tid, projects_by_tid, features_by_tid,
                attrs_by_tid,
            )
            for r in task_rows
        ],
    }


@router.get("/archive/tasks")
def list_archived_tasks(
    project: Optional[str] = None,
    owner: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Flat list of archived tasks, filtered + RBAC-scoped.

    Reads from ``archive.db`` only; joins back to ``main.db`` for user
    names via ``User.id`` (the archive-side user table is intentionally
    excluded from the schema subset).
    """
    is_admin, visible_projects = _visible_projects(s, user)

    with _open_archive_session() as arch:
        sql = ["SELECT t.id FROM task t JOIN note n ON n.id = t.note_id"]
        params: dict[str, Any] = {}
        where = ["1=1"]

        if project:
            sql.append("JOIN taskproject tp ON tp.task_id = t.id "
                       "JOIN project p ON p.id = tp.project_id")
            where.append("p.name = :p_name")
            params["p_name"] = project

        if owner:
            main_user = s.exec(select(User).where(User.name == owner)).first()
            if main_user is None:
                return {"tasks": [], "total": 0}
            sql.append("JOIN taskowner to_j ON to_j.task_id = t.id")
            where.append("to_j.user_id = :o_uid")
            params["o_uid"] = main_user.id

        if status:
            where.append("t.status = :status")
            params["status"] = status

        if q:
            where.append("(t.title LIKE :qlike OR t.task_uuid = :qexact)")
            params["qlike"] = f"%{q}%"
            params["qexact"] = q

        sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY t.updated_at DESC")
        sql.append("LIMIT :limit OFFSET :offset")
        params["limit"] = limit
        params["offset"] = offset

        rows = arch.exec(text(" ".join(sql)).bindparams(**params)).all()
        task_ids = [int(r[0]) for r in rows]
        if not task_ids:
            return {"tasks": [], "total": 0}

        tasks = arch.exec(
            select(Task).where(Task.id.in_(task_ids))  # type: ignore[attr-defined]
        ).all()
        by_id = {t.id: t for t in tasks}
        note_by_id: dict[int, Note] = {
            n.id: n for n in arch.exec(
                select(Note).where(Note.id.in_({t.note_id for t in tasks}))  # type: ignore[attr-defined]
            ).all()
        }

        owners_by_tid: dict[int, list[int]] = {}
        projects_by_tid: dict[int, list[int]] = {}
        features_by_tid: dict[int, list[int]] = {}
        for r in arch.exec(
            text("SELECT task_id, user_id FROM taskowner WHERE task_id IN :ids")
            .bindparams(bindparam("ids", expanding=True))
                .bindparams(ids=task_ids),
        ).all():
            owners_by_tid.setdefault(r[0], []).append(r[1])
        for r in arch.exec(
            text("SELECT task_id, project_id FROM taskproject WHERE task_id IN :ids")
            .bindparams(bindparam("ids", expanding=True))
                .bindparams(ids=task_ids),
        ).all():
            projects_by_tid.setdefault(r[0], []).append(r[1])
        for r in arch.exec(
            text("SELECT task_id, feature_id FROM taskfeature WHERE task_id IN :ids")
            .bindparams(bindparam("ids", expanding=True))
                .bindparams(ids=task_ids),
        ).all():
            features_by_tid.setdefault(r[0], []).append(r[1])

        project_by_id = {p.id: p.name for p in arch.exec(select(Project)).all()}
        feature_by_id = {f.id: f.name for f in arch.exec(select(Feature)).all()}

        attrs_by_tid: dict[int, dict[str, str]] = {}
        for r in arch.exec(
            text("SELECT task_id, key, value FROM taskattr "
                 "WHERE task_id IN :ids AND key IN ('priority', 'eta')")
            .bindparams(bindparam("ids", expanding=True))
            .bindparams(ids=task_ids),
        ).all():
            attrs_by_tid.setdefault(int(r[0]), {})[str(r[1])] = str(r[2])

    all_uids = {uid for uids in owners_by_tid.values() for uid in uids}
    user_by_id: dict[int, str] = {}
    if all_uids:
        for u in s.exec(
            select(User).where(User.id.in_(all_uids))  # type: ignore[attr-defined]
        ).all():
            user_by_id[u.id] = u.name

    out: list[dict[str, Any]] = []
    for tid in task_ids:
        t = by_id.get(tid)
        if t is None:
            continue
        note = note_by_id.get(t.note_id)
        if note is None:
            continue
        note_project = _project_for_path(note.path)
        if not is_admin and note_project not in visible_projects:
            continue
        out.append(
            _archive_task_row(
                t, note.path,
                user_by_id, project_by_id, feature_by_id,
                owners_by_tid, projects_by_tid, features_by_tid,
                attrs_by_tid,
            )
        )
    return {"tasks": out, "total": len(out)}


@router.get("/archive/tasks/{task_uuid}")
def get_archived_task(
    task_uuid: str,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    with _open_archive_session() as arch:
        t = arch.exec(select(Task).where(Task.task_uuid == task_uuid)).first()
        if t is None:
            raise HTTPException(404, "archived task not found")
        note = arch.get(Note, t.note_id)
        if note is None:
            raise HTTPException(500, "orphan archive task")

        owners = [r[0] for r in arch.exec(
            text("SELECT user_id FROM taskowner WHERE task_id = :i")
            .bindparams(i=t.id)
        ).all()]
        project_ids = [r[0] for r in arch.exec(
            text("SELECT project_id FROM taskproject WHERE task_id = :i")
            .bindparams(i=t.id)
        ).all()]
        feature_ids = [r[0] for r in arch.exec(
            text("SELECT feature_id FROM taskfeature WHERE task_id = :i")
            .bindparams(i=t.id)
        ).all()]
        project_by_id = {p.id: p.name for p in arch.exec(select(Project)).all()}
        feature_by_id = {f.id: f.name for f in arch.exec(select(Feature)).all()}

        attrs: dict[str, str] = {}
        for r in arch.exec(
            text("SELECT key, value FROM taskattr WHERE task_id = :i "
                 "AND key IN ('priority', 'eta')")
            .bindparams(i=t.id)
        ).all():
            attrs[str(r[0])] = str(r[1])

    note_project = _project_for_path(note.path)
    if _user_role_for_project(s, user, note_project) == "none":
        raise HTTPException(403, "no access")

    user_by_id: dict[int, str] = {}
    if owners:
        for u in s.exec(
            select(User).where(User.id.in_(owners))  # type: ignore[attr-defined]
        ).all():
            user_by_id[u.id] = u.name

    return _archive_task_row(
        t, note.path,
        user_by_id, project_by_id, feature_by_id,
        {t.id: owners}, {t.id: project_ids}, {t.id: feature_ids},
        {t.id: attrs},
    )


@router.get("/archive/projects")
def list_archived_projects(
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
    """Projects flagged ``Project.archived == True`` in main.db that the
    caller has visibility for.  Includes archive.db note+task counts."""
    is_admin, visible_projects = _visible_projects(s, user)
    rows = s.exec(
        select(Project).where(Project.archived == True)  # noqa: E712
        .order_by(Project.name)  # type: ignore[attr-defined]
    ).all()

    with _open_archive_session() as arch:
        out: list[dict[str, Any]] = []
        for p in rows:
            if not is_admin and p.name not in visible_projects:
                continue
            arch_proj = arch.exec(
                select(Project).where(Project.name == p.name)
            ).first()
            task_count = 0
            note_count = 0
            if arch_proj is not None:
                trow = arch.exec(
                    text(
                        "SELECT COUNT(DISTINCT t.id) FROM task t "
                        "JOIN taskproject tp ON tp.task_id = t.id "
                        "WHERE tp.project_id = :pid"
                    ).bindparams(pid=arch_proj.id)
                ).first()
                task_count = int(trow[0]) if trow else 0
            prefix = f"{p.name}/"
            nrow = arch.exec(
                text(
                    "SELECT COUNT(*) FROM note "
                    "WHERE path = :name OR path LIKE :prefix"
                ).bindparams(name=p.name, prefix=f"{prefix}%")
            ).first()
            note_count = int(nrow[0]) if nrow else 0
            out.append({
                "name": p.name,
                "archived": True,
                "note_count": note_count,
                "task_count": task_count,
            })
    return out


@router.get("/archive/summary")
def archive_summary(
    project: Optional[str] = None,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Aggregate counts across the archive: totals + by status + by
    project + top owners.  Scoped to projects the caller can access.
    """
    is_admin, visible_projects = _visible_projects(s, user)

    with _open_archive_session() as arch:
        base_sql = (
            "SELECT t.id, t.status, p.name AS project_name "
            "FROM task t JOIN note n ON n.id = t.note_id "
            "LEFT JOIN taskproject tp ON tp.task_id = t.id "
            "LEFT JOIN project p ON p.id = tp.project_id"
        )
        where = []
        params: dict[str, Any] = {}
        if project:
            where.append("p.name = :p_name")
            params["p_name"] = project
        if where:
            base_sql += " WHERE " + " AND ".join(where)
        rows = arch.exec(text(base_sql).bindparams(**params)).all()

        # Owner lookup — collect all task_ids first, then batch.
        task_ids = [int(r[0]) for r in rows]
        owner_rows: list[tuple[int, int]] = []
        if task_ids:
            owner_rows = [
                (int(r[0]), int(r[1])) for r in arch.exec(
                    text("SELECT task_id, user_id FROM taskowner "
                         "WHERE task_id IN :ids")
                    .bindparams(bindparam("ids", expanding=True))
                .bindparams(ids=task_ids),
                ).all()
            ]

    total = 0
    by_status: dict[str, int] = {}
    by_project: dict[str, int] = {}
    task_projects: dict[int, str] = {}
    for tid, status, project_name in rows:
        tid = int(tid)
        proj_name = project_name or ""
        if not is_admin and proj_name and proj_name not in visible_projects:
            continue
        if not is_admin and not proj_name:
            continue
        total += 1
        by_status[status] = by_status.get(status, 0) + 1
        by_project[proj_name] = by_project.get(proj_name, 0) + 1
        task_projects[tid] = proj_name

    owner_counts: dict[int, int] = {}
    for tid, uid in owner_rows:
        if tid not in task_projects:
            continue
        owner_counts[uid] = owner_counts.get(uid, 0) + 1

    top_uids = sorted(owner_counts.keys(), key=lambda u: -owner_counts[u])[:20]
    name_by_uid: dict[int, str] = {}
    if top_uids:
        for u in s.exec(
            select(User).where(User.id.in_(top_uids))  # type: ignore[attr-defined]
        ).all():
            name_by_uid[u.id] = u.name

    top_owners = [
        {"name": name_by_uid.get(uid, f"user#{uid}"), "count": owner_counts[uid]}
        for uid in top_uids
    ]

    return {
        "total_tasks": total,
        "by_status": by_status,
        "by_project": by_project,
        "top_owners": top_owners,
    }


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

    Archived notes (``Note.archived = True`` or anything under a sibling
    ``_archive/`` folder) are skipped — under the single-active-file model
    new tasks always land on the current week's file.  An explicit
    ``note_path`` pointing at an archived note is rejected (422) so the
    caller can re-target the active week.
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
        # Reject archived destinations so new tasks don't accidentally
        # land in a rolled-forward week.
        note_row = s.exec(select(Note).where(Note.path == note_path)).first()
        if (note_row and note_row.archived) or "/_archive/" in f"/{note_path}/":
            raise HTTPException(
                422, f"note '{note_path}' is archived; create the task on the active week's file",
            )
        return full
    if project:
        proj_dir = nd / project
        if not proj_dir.is_dir():
            raise HTTPException(404, f"project not found: {project}")
        # Skip files under any ``_archive/`` segment so the resolver
        # always lands on a live week.
        candidates = [
            p for p in proj_dir.rglob("*.md")
            if p.is_file() and "_archive" not in p.relative_to(nd).parts
        ]
        # Cross-check against the DB to skip any file flagged archived
        # (covers files whose folder isn't named ``_archive`` but whose
        # Note row is archived — should not happen today but is defensive).
        live: list[Path] = []
        for p in candidates:
            rel = str(p.relative_to(nd))
            n = s.exec(select(Note).where(Note.path == rel)).first()
            if n is not None and n.archived:
                continue
            live.append(p)
        live.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not live:
            raise HTTPException(
                422, f"no notes in project '{project}'. Create a note first.",
            )
        return live[0]
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

    # Owner inheritance: when caller omits ``owners`` (popover default —
    # see ``TaskEditPopover``), the AR inherits the parent task's effective
    # owner set rather than being attributed to the requester. This keeps
    # ARs visually attached to whoever is responsible for the parent work
    # instead of accumulating @<requester> tags every time someone files
    # a follow-up under another person's task. An explicit ``owners=[]``
    # still means "no owner — inherit from section context"; an explicit
    # non-empty list overrides everything.
    if body.owners is not None:
        cleaned_owners = [o.strip().lstrip("@") for o in body.owners if o and o.strip()]
    else:
        cleaned_owners = [o for o in parent_owners if o]

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

        # Issue #253: archived notes (rolled-forward weeks) are immutable
        # historical records and must NEVER receive propagated AR ref rows.
        # The archive-style rollover (#251 / 2e95e67) writes a `#task T-XXX`
        # ref row into every archive for each migrated task, so without
        # this filter every AR-create on a long-lived task fans out into
        # every prior archive — corrupting history and bypassing the
        # manager-only popover RBAC.
        from sqlmodel import col as _col
        candidate_notes = s.exec(
            select(Note)
            .where(_col(Note.body_md).contains(parent_uuid))
            .where(Note.id != note.id)
            .where(Note.archived == False)  # noqa: E712
        ).all()
        # Belt-and-suspenders: skip anything physically under an
        # ``_archive/`` segment in case a row's ``archived`` flag wasn't
        # set (legacy data, partially-migrated installs).
        candidate_notes = [
            n for n in candidate_notes
            if "/_archive/" not in f"/{n.path}/"
        ]

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
    user: str = Depends(require_user),
    # #237: accept either repeated query keys (?owner=a&owner=b) OR
    # comma-separated values (?owner=a,b). list[str] binds the repeated
    # form; _collect_filter then splits any embedded commas so both
    # forms are equivalent.
    owner: list[str] = Query(default_factory=list),
    project: list[str] = Query(default_factory=list),
    feature: list[str] = Query(default_factory=list),
    priority: list[str] = Query(default_factory=list),
    status: list[str] = Query(default_factory=list),
    eta_before: Optional[date] = None,
    eta_after: Optional[date] = None,
    hide_done: bool = False,
    q: Optional[str] = None,
    kind: list[str] = Query(default_factory=list),
    top_level_only: bool = False,
    include_children: bool = False,
    # ── new (issue #38 follow-up) ─────────────────────────────────────
    not_owner: list[str] = Query(default_factory=list),
    not_project: list[str] = Query(default_factory=list),
    not_feature: list[str] = Query(default_factory=list),
    not_status: list[str] = Query(default_factory=list),
    not_priority: list[str] = Query(default_factory=list),
    attr: list[str] = Query(default_factory=list),
    sort: Optional[str] = None,
    limit: Optional[int] = Query(default=None, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    # #258: scope done tasks to non-archived notes by default. "active" hides
    # done tasks that live in archived (rolled-over) weekly md files; "all"
    # is the historical behaviour. Open tasks are unaffected — only the
    # `status = done` set is scoped.
    done_scope: str = Query(default="active"),
    # #320: filter by the recurring #progress metric.  All three are
    # independent — combine to narrow further.
    #   progress_min_pct=50  -> only tasks with `#progress N/D` where
    #                           N/D * 100 >= 50 (bare counters skipped
    #                           since they have no percent).
    #   progress_max_pct=99  -> only tasks whose percent is <= 99.
    #   progress_has=1       -> only tasks that have any `#progress`
    #                           token at all (matches counter form too).
    progress_min_pct: Optional[int] = Query(default=None, ge=0, le=1000),
    progress_max_pct: Optional[int] = Query(default=None, ge=0, le=1000),
    progress_has: bool = Query(default=False),
) -> dict[str, Any]:
    if done_scope not in ("active", "all"):
        raise HTTPException(
            422, f"done_scope must be 'active' or 'all', got {done_scope!r}",
        )
    sql = ["SELECT DISTINCT t.id FROM task t"]
    params: dict[str, Any] = {}
    expanding: list[str] = []

    # #230: read-side RBAC. Join Note for path-based visibility filter.
    _vis_admin, _vis_projects = _visible_projects(s, user)
    if not _vis_admin:
        sql.append("JOIN note rbac_n ON rbac_n.id = t.note_id")
    # #258: separate join when we need archived-flag visibility for done
    # scoping. We could in principle reuse `rbac_n` when it's present, but
    # keeping the alias dedicated keeps the SQL readable and avoids coupling
    # the two concerns.
    if done_scope == "active":
        sql.append("JOIN note done_n ON done_n.id = t.note_id")

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

    _join_multi("taskowner", "user", _canon_owner_filter(_collect_filter(owner)), "u")
    _join_multi("taskproject", "project", _collect_filter(project), "p")
    _join_multi("taskfeature", "feature", _collect_filter(feature), "f")

    where = ["1=1"]
    # #230: visibility WHERE clause (no-op for admins).
    _vis_clause = _note_visibility_sql_clause(
        "rbac_n", _vis_admin, _vis_projects, params, expanding, "rbac_projects",
    )
    if _vis_clause is not None:
        where.append(_vis_clause)
    statuses = _collect_filter(status)
    if hide_done or "!done" in statuses:
        where.append("t.status != 'done'")
    elif statuses:
        where.append("t.status IN :statuses")
        params["statuses"] = tuple(statuses)
        expanding.append("statuses")
    # #258: when done_scope="active", drop done tasks whose source note is
    # archived. Open tasks (todo/in-progress/blocked) are intentionally
    # unaffected — they're either still relevant on the active week or
    # candidates for sweeping (separate concern).
    if done_scope == "active":
        where.append("NOT (t.status = 'done' AND done_n.archived = 1)")
    prios = _collect_filter(priority)
    if prios:
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
    kinds = _collect_filter(kind)
    if kinds:
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

    _exclude("user",    "taskowner",   "user_id",    _canon_owner_filter(_collect_filter(not_owner)),   "not_u_names")
    _exclude("project", "taskproject", "project_id", _collect_filter(not_project), "not_p_names")
    _exclude("feature", "taskfeature", "feature_id", _collect_filter(not_feature), "not_f_names")

    not_statuses = _collect_filter(not_status)
    if not_statuses:
        where.append("t.status NOT IN :not_statuses")
        params["not_statuses"] = tuple(not_statuses)
        expanding.append("not_statuses")
    not_prios = _collect_filter(not_priority)
    if not_prios:
        where.append(
            "t.id NOT IN ("
            "SELECT task_id FROM taskattr WHERE key='priority' AND value IN :not_prios)"
        )
        params["not_prios"] = tuple(not_prios)
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

    # ── #320: recurring #progress metric filters ─────────────────────────
    # value_norm stores the numeric head (`N` or `N/D`) so we can compute
    # percent inline via a CASE.  Bare counters (denom missing) are
    # excluded from the min/max_pct filters — they have no percent to
    # compare — but are kept by `progress_has`.
    if progress_has:
        where.append(
            "EXISTS (SELECT 1 FROM taskattr pgh "
            "WHERE pgh.task_id = t.id AND pgh.key = 'progress')"
        )
    if progress_min_pct is not None or progress_max_pct is not None:
        pct_clauses = []
        if progress_min_pct is not None:
            pct_clauses.append(
                "(CAST(substr(pgn.value_norm, 1, instr(pgn.value_norm, '/') - 1) AS REAL) * 100.0 / "
                "CAST(substr(pgn.value_norm, instr(pgn.value_norm, '/') + 1) AS REAL)) >= :pg_min_pct"
            )
            params["pg_min_pct"] = float(progress_min_pct)
        if progress_max_pct is not None:
            pct_clauses.append(
                "(CAST(substr(pgn.value_norm, 1, instr(pgn.value_norm, '/') - 1) AS REAL) * 100.0 / "
                "CAST(substr(pgn.value_norm, instr(pgn.value_norm, '/') + 1) AS REAL)) <= :pg_max_pct"
            )
            params["pg_max_pct"] = float(progress_max_pct)
        where.append(
            "EXISTS (SELECT 1 FROM taskattr pgn "
            "WHERE pgn.task_id = t.id AND pgn.key = 'progress' "
            "AND instr(pgn.value_norm, '/') > 0 "  # only ratio form
            f"AND {' AND '.join(pct_clauses)})"
        )

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
            "owner_displays": _owner_display_map(agg_owners),
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
    user: str = Depends(require_user),
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
    expanding: list[str] = []
    # #230: read-side RBAC.
    _vis_admin, _vis_projects = _visible_projects(s, user)
    if not _vis_admin:
        sql += " JOIN note rbac_n ON rbac_n.id = t.note_id "
    if owner:
        owners_expanded = _canon_owner_filter([owner])
        sql += (
            " JOIN taskowner o ON o.task_id = t.id "
            " JOIN user u ON u.id = o.user_id AND u.name IN :owners "
        )
        params["owners"] = tuple(owners_expanded)
        expanding.append("owners")
    _vis_clause = _note_visibility_sql_clause(
        "rbac_n", _vis_admin, _vis_projects, params, expanding, "rbac_projects",
    )
    vis_sql = f" AND {_vis_clause}" if _vis_clause else ""
    sql += f"""
    WHERE t.status != 'done'
      AND ea.value_norm BETWEEN :start AND :end
      {vis_sql}
    ORDER BY ea.value_norm ASC, pri_rank ASC, t.id ASC
    """
    stmt = text(sql)
    if expanding:
        stmt = stmt.bindparams(*[bindparam(k, expanding=True) for k in expanding])
    rows = s.exec(stmt.bindparams(**params)).all()
    grouped: dict[str, list[dict]] = {}
    for tid, eta, _pri in rows:
        grouped.setdefault(eta, []).append(_task_to_dict(s, s.get(Task, tid)))
    return {"window": {"start": today.isoformat(), "end": end_d.isoformat(), "days": (end_d - today).days}, "by_day": grouped}


# ---------- features (cross-user pull) -------------------------------------

@router.get("/features")
def list_features(
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[str]:
    is_admin, projects = _visible_projects(s, user)
    if is_admin:
        return [f.name for f in s.exec(select(Feature).order_by(Feature.name)).all()]
    # Only surface features that have at least one task in a visible note.
    rows = s.exec(
        select(Feature.name, Note.path)
        .join(TaskFeature, TaskFeature.feature_id == Feature.id)
        .join(Task, Task.id == TaskFeature.task_id)
        .join(Note, Note.id == Task.note_id)
    ).all()
    visible = sorted({
        name for name, path in rows
        if _project_for_path(path) is None or _project_for_path(path) in projects
    })
    return visible


@router.get("/features/{name}/tasks")
def feature_tasks(
    name: str,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    feat = s.exec(select(Feature).where(Feature.name == name)).first()
    if not feat:
        raise HTTPException(404, "feature not found")
    is_admin, projects = _visible_projects(s, user)
    rows = s.exec(
        select(Task.id).join(TaskFeature, TaskFeature.task_id == Task.id)
        .where(TaskFeature.feature_id == feat.id)
    ).all()
    tasks: list[dict[str, Any]] = []
    for r in rows:
        tid = r[0] if isinstance(r, tuple) else r
        t = s.get(Task, tid)
        if t is None:
            continue
        n = s.get(Note, t.note_id)
        if n is None or not _note_is_visible(n, is_admin, projects):
            continue
        tasks.append(_task_to_dict(s, t))
    agg_o = sorted({o for t in tasks for o in t["owners"]})
    return {
        "feature": name,
        "tasks": tasks,
        "aggregations": {
            "owners": agg_o,
            "owner_displays": _owner_display_map(agg_o),
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
def card_links(
    task_ref: str,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    t = _resolve_task(task_ref, s)
    _require_task_access(s, user, t)
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
        cached_lookup, filter_by_first_name, filter_by_last_name,
        rank_by_distance_full, resolve_anchor_wwid,
    )
    from ..phonebook import Phonebook
    from ..config import settings as _s
    q = (body.q or "").strip()
    enabled = bool(getattr(_s, "phonebook_scraper_enabled", False))
    if not q:
        return {"query": "", "candidates": [], "enabled": enabled}
    if len(q) > 200:
        raise HTTPException(status_code=400, detail="query too long (max 200)")
    # #226: support GAL "Last, First" and two-token "First Last" forms.
    given, surname = Phonebook._split_compound_token(q)
    hits = cached_lookup(given)
    # First-name-only filter (#215) — drop Pavel-as-lastname noise etc.
    hits = filter_by_first_name(hits, given)
    if surname:
        hits = filter_by_last_name(hits, surname)
    anchor = _pick_phonebook_anchor(body.anchor, user)
    anchor_wwid = resolve_anchor_wwid(anchor) if anchor else None
    pen = int(getattr(_s, "phonebook_seniority_penalty", 3))
    bias_score = int(getattr(_s, "phonebook_subtree_bias", 0))
    bias_wwids: list[str] = []
    if anchor and bias_score:
        from ..phonebook import get_phonebook as _get_pb
        try:
            bias_wwids = _get_pb()._anchor_bias_wwids(anchor)
        except Exception:  # pragma: no cover — defensive
            bias_wwids = []
    ranked = rank_by_distance_full(
        hits, anchor_wwid, seniority_penalty=pen,
        subtree_bias_wwids=bias_wwids,
        subtree_bias_score=bias_score,
    ) if anchor_wwid else [(h, None, None, None, None) for h in hits]
    out = []
    bias_set = set(bias_wwids)
    for h, up, down, raw, score in ranked:
        d = h.to_dict()
        d["up_hops"] = up
        d["down_hops"] = down
        d["org_distance"] = raw
        d["score"] = score
        if bias_set and h.wwid:
            from ..phonebook_intel import chain_passes_through
            d["bias_applied"] = chain_passes_through(h.wwid, bias_set)
        out.append(d)
    return {
        "query": q,
        "enabled": enabled,
        "anchor": anchor,
        "anchor_wwid": anchor_wwid,
        "seniority_penalty": pen,
        "subtree_bias": bias_score,
        "subtree_bias_wwids": bias_wwids,
        "candidates": out,
    }


@router.get("/projects")
def list_projects(
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """List projects = top-level subfolders of notes/. Includes the caller's role.

    #310: user-archived projects (``Project.archived == True``) are hidden by
    default so the sidebar tree and every project-dropdown consumer stop
    surfacing them once the user opts them out of the active workspace.
    Pass ``?include_archived=1`` to include archived projects (used only by
    the Archive view and admin flows today).
    """
    out: list[dict[str, Any]] = []
    nd = settings.notes_dir
    nd.mkdir(parents=True, exist_ok=True)
    archived_names: set[str] = set()
    if not include_archived:
        archived_names = {
            p.name for p in s.exec(select(Project).where(Project.archived == True)).all()  # noqa: E712
        }
    for child in sorted(nd.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name == "_meta":
            continue
        if child.name in archived_names:
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
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    _require_project_access(s, user, project)
    pdir = settings.notes_dir / project
    if not pdir.is_dir():
        raise HTTPException(404, "project not found")
    out: list[dict[str, Any]] = []
    files = sorted(pdir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files:
        rel = str(p.relative_to(settings.notes_dir))
        # Skip files under a sibling ``_archive/`` folder unless the
        # caller explicitly opts in via ``?include_archived=1``.
        in_archive_dir = "_archive" in p.relative_to(settings.notes_dir).parts
        if in_archive_dir and not include_archived:
            continue
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
        # Belt-and-suspenders: also filter the DB ``archived`` flag.
        if note is not None and note.archived and not include_archived:
            continue
        out.append({
            "path": rel,
            "id": note.id if note else None,
            "title": note.title if note else p.stem,
            "archived": bool(note.archived) if note else in_archive_dir,
        })
    return out


@router.get("/tree")
def tree(
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """Project → notes tree, filtered by caller's RBAC.

    Archived notes (rolled-forward weeklies under sibling ``_archive/``
    folders) are hidden by default to keep the active workspace focused
    on the current week.  Pass ``?include_archived=1`` to include them.

    #310: user-archived projects (``Project.archived == True``) are also
    hidden by default and skipped entirely — their folder still exists on
    disk, but their derived task rows live in ``archive.db`` and must not
    be lazily resurrected by the inline ``reindex_file`` self-heal below.
    """
    out: list[dict[str, Any]] = []
    nd = settings.notes_dir
    nd.mkdir(parents=True, exist_ok=True)
    archived_names: set[str] = set()
    if not include_archived:
        archived_names = {
            p.name for p in s.exec(select(Project).where(Project.archived == True)).all()  # noqa: E712
        }
    # Top-level projects (folders)
    for child in sorted(nd.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name == "_meta":
            continue
        if child.name in archived_names:
            continue
        role = _user_role_for_project(s, user, child.name)
        if role == "none":
            continue
        notes = []
        for p in sorted(child.rglob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
            rel = str(p.relative_to(nd))
            in_archive_dir = "_archive" in p.relative_to(nd).parts
            if in_archive_dir and not include_archived:
                continue
            note = s.exec(select(Note).where(Note.path == rel)).first()
            if note is None:
                try:
                    note = reindex_file(p, s)
                    s.commit()
                except Exception:
                    s.rollback()
                    note = None
            if note is not None and note.archived and not include_archived:
                continue
            notes.append({
                "path": rel,
                "id": note.id if note else None,
                "title": note.title if note else p.stem,
                "archived": bool(note.archived) if note else in_archive_dir,
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
        if note is not None and note.archived and not include_archived:
            continue
        loose.append({
            "path": rel,
            "id": note.id if note else None,
            "title": note.title if note else p.stem,
            "archived": bool(note.archived) if note else False,
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
    # #314: external-URL capsule tokens. Each is a full replacement; ``[]``
    # clears all values for that key. Values must be whitespace-free (the
    # lexer reads a single word); URL-encode spaces if needed.
    url: Optional[list[str]] = None    # generic ``#url <val>``; supports ``LABEL:url`` prefix
    hsd: Optional[list[str]] = None    # ``#hsd <id>`` -> hsdes.intel.com
    jira: Optional[list[str]] = None   # ``#jira <KEY>`` -> jira.devtools.intel.com
    pr: Optional[list[str]] = None     # ``#pr <owner/repo#N>`` -> github.com
    # #320: recurring progress metric. Single value shaped like ``N``,
    # ``N/D``, or ``N/D label`` (label is [A-Za-z][\w-]*). Empty string
    # ("") clears the token.
    progress: Optional[str] = None
    # New title text (trimmed). None = no change. Empty string is rejected
    # because a blank declaration line cannot be re-parsed. The keyword
    # (!task / !AR) and every trailing #attr / @owner token are preserved.
    title: Optional[str] = None
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
    user: str = Depends(require_user),
    include_children: bool = False,
) -> dict[str, Any]:
    """Fetch a single task by integer PK or `T-XXXXXX` uuid ref."""
    t = _resolve_task(task_ref, s)
    _require_task_access(s, user, t)
    return _task_to_dict(s, t, include_children=include_children)


@router.get("/tasks/{task_ref}/activity")
def task_activity(
    task_ref: str,
    limit: int = Query(200, ge=1, le=1000),
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
    """Return the chronological audit timeline for a single task.

    Includes every ``ActivityEvent`` whose ``ref`` matches the task's
    uuid (or its legacy ``task#<id>`` fallback for unstamped tasks).
    Each row carries the actor name, kind, ts and meta blob — the
    full per-field history surfaced by issue #251 (status, owners,
    priority, eta, features, notes, deletion).

    Access is RBAC-scoped via :func:`_require_task_access`: anyone
    who can read the task can read its activity.
    """
    t = _resolve_task(task_ref, s)
    _require_task_access(s, user, t)
    refs: list[str] = []
    if t.task_uuid:
        refs.append(t.task_uuid)
    refs.append(f"task#{t.id}")
    q = (
        select(ActivityEvent, User.name)
        .join(User, User.id == ActivityEvent.user_id)
        .where(ActivityEvent.ref.in_(refs))
        .order_by(ActivityEvent.ts.desc())
        .limit(limit)
    )
    out: list[dict[str, Any]] = []
    for ev, actor in s.exec(q).all():
        try:
            meta = json.loads(ev.meta_json) if ev.meta_json else {}
        except json.JSONDecodeError:
            meta = {}
        out.append({
            "id": ev.id,
            "kind": ev.kind,
            "ref": ev.ref,
            "ts": ev.ts.isoformat(),
            "actor": actor,
            "meta": meta,
        })
    return out


@router.get("/tasks/{task_ref}/progress-history")
def task_progress_history(
    task_ref: str,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
    """Weekly history of a task's ``#progress`` metric (#320).

    Aggregates ``TaskAttr(key='progress')`` rows across main.db and
    archive.db, groups by ISO week derived from the containing note's
    filename (``ww29.md`` → ``YYYY-W29``), and returns one row per week
    sorted ascending.  When two notes fall in the same ISO week the
    one with the newer ``updated_at`` wins.

    Response shape::

        [ { "week": "2026-W29", "numerator": 30, "denominator": 54,
            "label": "fixed" }, ... ]

    Access is RBAC-scoped via :func:`_require_task_access`.
    """
    import re as _re
    t = _resolve_task(task_ref, s)
    _require_task_access(s, user, t)
    if not t.task_uuid:
        # An unstamped task has no cross-file identity to hang a history
        # off — return the single main-db reading (if any) and no
        # archive rollup.
        return _progress_history_for_uuid(s, None, t.id)

    return _progress_history_for_uuid(s, t.task_uuid, t.id)


_WW_RE = _re.compile(r"(?i)(?:^|[^0-9a-z])ww(\d{1,2})(?![0-9])")
_ISO_YEARWEEK_RE = _re.compile(r"(20\d{2})[-_]?W(\d{1,2})", _re.IGNORECASE)
_PROGRESS_HEAD_RE = _re.compile(r"^(\d+)(?:/(\d+))?(?:\s+([A-Za-z][\w-]*))?")


def _iso_week_for_note(path: str, updated_at: datetime) -> str:
    """Derive ``YYYY-Www`` for a note.

    Tries ``YYYY-Www`` / ``YYYY_Www`` first, then ``wwNN`` (year from
    ``updated_at``), then falls back to the ISO calendar week of
    ``updated_at``.
    """
    base = path.rsplit("/", 1)[-1]
    m = _ISO_YEARWEEK_RE.search(base)
    if m:
        return f"{m.group(1)}-W{int(m.group(2)):02d}"
    m = _WW_RE.search(base)
    if m:
        return f"{updated_at.year:04d}-W{int(m.group(1)):02d}"
    iso = updated_at.isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


def _parse_progress_value(value: str) -> Optional[dict[str, Any]]:
    if not value:
        return None
    m = _PROGRESS_HEAD_RE.match(value.strip())
    if not m:
        return None
    num = int(m.group(1))
    denom = int(m.group(2)) if m.group(2) else None
    label = m.group(3)
    return {"numerator": num, "denominator": denom, "label": label}


def _progress_history_for_uuid(
    s: Session, task_uuid: Optional[str], task_pk: Optional[int],
) -> list[dict[str, Any]]:
    # week -> (updated_at_ts, payload)
    by_week: dict[str, tuple[float, dict[str, Any]]] = {}

    def _record(week: str, updated_at: datetime, parsed: dict[str, Any]) -> None:
        ts = updated_at.timestamp()
        row = {"week": week, **parsed}
        prior = by_week.get(week)
        if prior is None or prior[0] < ts:
            by_week[week] = (ts, row)

    # -- main.db reading -------------------------------------------------
    q = (
        select(TaskAttr.value, Note.path, Note.updated_at)
        .join(Task, Task.id == TaskAttr.task_id)
        .join(Note, Note.id == Task.note_id)
        .where(TaskAttr.key == "progress")
    )
    if task_uuid:
        q = q.where(Task.task_uuid == task_uuid)
    elif task_pk is not None:
        q = q.where(Task.id == task_pk)
    else:
        return []
    for value, path, updated_at in s.exec(q).all():
        parsed = _parse_progress_value(value or "")
        if not parsed:
            continue
        _record(_iso_week_for_note(path, updated_at), updated_at, parsed)

    # -- archive.db rollup ----------------------------------------------
    if task_uuid:
        with _open_archive_session() as archive:
            aq = (
                select(TaskAttr.value, Note.path, Note.updated_at)
                .join(Task, Task.id == TaskAttr.task_id)
                .join(Note, Note.id == Task.note_id)
                .where(TaskAttr.key == "progress")
                .where(Task.task_uuid == task_uuid)
            )
            try:
                arows = archive.exec(aq).all()
            except Exception:
                arows = []
        for value, path, updated_at in arows:
            parsed = _parse_progress_value(value or "")
            if not parsed:
                continue
            _record(_iso_week_for_note(path, updated_at), updated_at, parsed)

    return [payload for _week, (_ts, payload) in sorted(by_week.items())]



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
    # Archived notes are read-mostly under the single-active-file model.
    # Only project managers / admins can mutate tasks in a note that has
    # been rolled forward — owners need to act on the active week's file.
    if note.archived and role != "manager":
        raise HTTPException(
            403,
            "archived notes are read-only; this week was rolled forward — "
            "edit the task on the current week's note instead",
        )

    full = settings.notes_dir / note.path
    if not full.exists():
        raise HTTPException(404, "note file missing on disk")

    # Snapshot pre-mutation values for the audit-trail events emitted
    # below (issue #251). Owners are already loaded above; priority /
    # eta / features come from the TaskAttr / TaskFeature mirrors.
    old_owners_norm = sorted({(o or "").strip().lower() for o in owners if o and o.strip()})
    _old_priority_row = s.exec(
        select(TaskAttr.value).where(TaskAttr.task_id == task_id, TaskAttr.key == "priority")
    ).first()
    old_priority = (_old_priority_row[0] if isinstance(_old_priority_row, tuple) else _old_priority_row) or ""
    _old_eta_row = s.exec(
        select(TaskAttr.value).where(TaskAttr.task_id == task_id, TaskAttr.key == "eta")
    ).first()
    old_eta = (_old_eta_row[0] if isinstance(_old_eta_row, tuple) else _old_eta_row) or ""
    _old_features = s.exec(
        select(Feature.name).join(TaskFeature, TaskFeature.feature_id == Feature.id)
        .where(TaskFeature.task_id == task_id)
    ).all()
    old_features_norm = sorted({(f or "").strip().lower() for f in _old_features if f and f.strip()})
    # ── RMW under the file lock ───────────────────────────────────────────
    # The fast-path popover (issue #140) updates ``note.body_md`` without
    # re-running the parser, so individual ``Task.line`` values in the
    # index can drift from disk over time. To keep popover patches from
    # silently rewriting the wrong line (issue #239: T-EWGPDY appeared
    # done in kanban but the ``#status done`` token never reached the md
    # file because ``t.line`` pointed at a sibling row that already had
    # ``#status done``), we now:
    #
    #   1. acquire the file lock first,
    #   2. read the authoritative disk content,
    #   3. re-anchor ``t.line`` by searching disk for ``#id <uuid>``
    #      (or, for unstamped tasks, an unambiguous slug match),
    #   4. apply mutations against disk content (not the cached body_md),
    #   5. write + reindex while still holding the lock.
    #
    # Replaces the previous broad ``disk_md != note.body_md`` 409 check;
    # mutations are surgical (token-level), so concurrent edits to the
    # same task that don't touch the patched fields are preserved.
    with with_file_lock(full):
        disk_md = full.read_text(encoding="utf-8")
        actual_line = _find_task_line_in_md(disk_md, t)
        if actual_line is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "stale_task",
                    "message": "task no longer present in current file content; refetch and retry",
                },
            )
        if actual_line != t.line:
            t.line = actual_line  # self-heal stale index

        md = disk_md
        changed = False
        old_status = t.status
        status_changed = False
        old_title = t.title
        title_changed = False
        if body.title is not None:
            new_title_stripped = body.title.strip()
            if not new_title_stripped:
                raise HTTPException(400, "title must not be blank")
            if new_title_stripped != old_title:
                try:
                    md = replace_task_title(md, t.line, new_title_stripped)
                except ValueError as e:
                    raise HTTPException(400, f"could not rewrite title: {e}")
                title_changed = True
                changed = True
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
        # #314 / #316: external-URL capsule tokens.
        # Non-#url values must remain whitespace-free (HSD ID, JIRA key,
        # PR spec).  #url additionally accepts a markdown-link form
        # ``[Label with spaces](https://…)`` whose interior spaces live
        # inside the brackets — that shape is now the preferred syntax.
        _md_link_re = _re.compile(r"^\[[^\]]+\]\([^\s()]+(?:\([^\s()]*\)[^\s()]*)*\)$")
        for _link_key in ("url", "hsd", "jira", "pr"):
            _link_val = getattr(body, _link_key)
            if _link_val is None:
                continue
            _cleaned = [v.strip() for v in _link_val if v and v.strip()]
            for _v in _cleaned:
                if not any(ch.isspace() for ch in _v):
                    continue
                if _link_key == "url" and _md_link_re.match(_v):
                    continue
                raise HTTPException(
                    400,
                    f"#{_link_key} value must not contain whitespace; "
                    "URL-encode spaces or use `[Label](https://…)` MD form",
                )
            md = replace_multi_attr(md, t.line, _link_key, _cleaned)
            changed = True
        # #320: single-valued recurring-progress metric.
        # Accepted shapes:  ``N``, ``N/D``, ``N/D label`` where label is
        # ``[A-Za-z][\w-]*``. Empty string clears the token.
        if body.progress is not None:
            _p = body.progress.strip()
            if _p == "":
                md = remove_attr(md, t.line, "progress")
            else:
                if not _re.match(
                    r"^\d+(?:/\d+)?(?:\s+[A-Za-z][\w-]*)?$", _p,
                ):
                    raise HTTPException(
                        400,
                        "#progress must be `N`, `N/D`, or `N/D label` "
                        "(label = [A-Za-z][\\w-]*); got: " + repr(_p),
                    )
                # Extra sanity — denominator must be > 0 when supplied.
                _m = _re.match(r"^(\d+)(?:/(\d+))?", _p)
                if _m and _m.group(2) is not None and int(_m.group(2)) == 0:
                    raise HTTPException(
                        400, "#progress denominator must be positive",
                    )
                md = replace_attr(md, t.line, "progress", _p)
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

        _safe_write_unlocked(full, md, notes_dir=settings.notes_dir)

        # ── Fast index update (issue #140): only this single task changed ────
        # The popover never mutates more than one task at a time, so a full
        # reindex_file (which re-parses every line and re-fingerprints every
        # task) is wasteful. Apply the same mutations directly to the index
        # rows for `task_id`, plus a single line-shift UPDATE for any tasks
        # below the insertion point if append_note added rows.
        new_disk = full.read_text(encoding="utf-8")
        new_mtime = full.stat().st_mtime
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
            title=body.title.strip() if body.title is not None else None,
            add_note=body.add_note,
            # #314: pass link-token replacements through to the index update.
            link_attrs={
                k: [v.strip() for v in getattr(body, k) if v and v.strip()]
                for k in ("url", "hsd", "jira", "pr")
                if getattr(body, k) is not None
            },
            # #320: single-valued progress metric flows through the same
            # single-attr update path used by priority/eta.
            progress=body.progress.strip() if body.progress is not None else None,
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
        # #314: propagate link-token replacements to every ref row so the
        # cross-file @link mirror stays consistent with the canonical decl.
        for _link_key in ("url", "hsd", "jira", "pr"):
            _link_val = getattr(body, _link_key)
            if _link_val is not None:
                ref_patch[_link_key] = [
                    v.strip() for v in _link_val if v and v.strip()
                ]
        # #320: propagate the current-week progress metric to every ref row
        # so a subsequent reindex of a referring weekly note cannot push a
        # stale value back over the canonical decl.
        if body.progress is not None:
            ref_patch["progress"] = body.progress.strip()
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
            #
            # Issue #253: exclude archived notes so PATCH on an active-week
            # task can't rewrite prior weeks' archives. Same reasoning as
            # the AR-create propagator above.
            from sqlmodel import col as _col
            candidate_notes = s.exec(
                select(Note)
                .where(_col(Note.body_md).contains(ref_id))
                .where(Note.id != note.id)   # canonical file already written
                .where(Note.archived == False)  # noqa: E712
            ).all()
            candidate_notes = [
                n for n in candidate_notes
                if "/_archive/" not in f"/{n.path}/"
            ]

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
    ev_ref = (refreshed.task_uuid if refreshed and refreshed.task_uuid
              else (t.task_uuid or f"task#{task_id}"))
    if status_changed and body.status is not None:
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
    # ── Per-field audit events (issue #251) ──────────────────────────────
    # One event per field that actually changed. Skip no-ops and reorder-
    # only multi-value updates. Emission is best-effort (record_event
    # swallows errors), so a logging hiccup never blocks the PATCH.
    if body.priority is not None:
        new_priority = (body.priority or "").strip()
        if (old_priority or "") != new_priority:
            awarded += gamify.record_event(
                s, user, gamify.TASK_PRIORITY_SET,
                ref=ev_ref,
                meta={"from": old_priority or None, "to": new_priority or None},
            )
    if body.eta is not None:
        new_eta = (body.eta or "").strip()
        if (old_eta or "") != new_eta:
            awarded += gamify.record_event(
                s, user, gamify.TASK_ETA_SET,
                ref=ev_ref,
                meta={"from": old_eta or None, "to": new_eta or None},
            )
    if body.owners is not None:
        new_owners_clean = [o.strip().lstrip("@") for o in body.owners if o and o.strip()]
        new_owners_norm = sorted({o.lower() for o in new_owners_clean})
        if new_owners_norm != old_owners_norm:
            awarded += gamify.record_event(
                s, user, gamify.TASK_OWNERS_SET,
                ref=ev_ref,
                meta={"from": [o for o in owners if o], "to": new_owners_clean},
            )
    if body.features is not None:
        new_features_clean = [f.strip() for f in body.features if f and f.strip()]
        new_features_norm = sorted({f.lower() for f in new_features_clean})
        if new_features_norm != old_features_norm:
            awarded += gamify.record_event(
                s, user, gamify.TASK_FEATURES_SET,
                ref=ev_ref,
                meta={"from": [f for f in _old_features if f], "to": new_features_clean},
            )
    if title_changed:
        awarded += gamify.record_event(
            s, user, gamify.TASK_TITLE_SET,
            ref=ev_ref,
            meta={"from": old_title, "to": body.title.strip() if body.title is not None else ""},
        )
    if body.add_note is not None and body.add_note.strip():
        awarded += gamify.record_event(
            s, user, gamify.TASK_NOTE_ADDED,
            ref=ev_ref,
            meta={"text": body.add_note.strip()},
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
    if note.archived and role != "manager":
        raise HTTPException(
            403,
            "archived notes are read-only; this week was rolled forward — "
            "delete the task on the current week's note instead",
        )

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
    # Audit event (issue #251). Emitted after the index delete commits
    # via session_scope so the row references a stable task_uuid string.
    ev_ref = task_uuid or f"task#{t.id}"
    gamify.record_event(
        s, user, gamify.TASK_DELETED,
        ref=ev_ref,
        meta={"title": task_title, "last_status": t.status, "note_path": note.path},
    )
    return {"status": "deleted", "task_uuid": task_uuid, "title": task_title}


# ---------- users / search -------------------------------------------------
@router.get("/users")
def list_users(
    s: Session = Depends(get_session),
    with_display: bool = False,
    project: str | None = Query(default=None, description="Restrict to users who own tasks in this project."),
) -> list[Any]:
    """List User.name values. When ``with_display=1`` returns a richer
    shape ``[{"name": "nsaddaga", "display": "Prasad Addagarla"}, ...]``
    so the FilterBar dropdown can render the friendly display name while
    still keying the option value on the canonical idsid.

    With the flag, the result is also restricted to users that own at
    least one task — keeps the FilterBar dropdown free of orphan rows
    left behind by pre-canonicalization (#174) reindexes.

    #312: pass ``project=<name>`` to further restrict to users who own
    at least one *active* task inside that project. Combined with the
    fact that archived tasks live in ``archive.db`` (not this engine),
    archiving a project or note causes its exclusive owners to drop
    from every project-scoped users query — which is what the FilterBar,
    per-task autocomplete, and owner chip suggestion lists want.

    Without the flag, returns a plain string list including all User
    rows (used by admin / member-management UIs that need every user)."""
    if not with_display:
        if project is None:
            return [u.name for u in s.exec(select(User).order_by(User.name)).all()]
        rows = s.exec(
            select(User.name)
            .join(TaskOwner, TaskOwner.user_id == User.id)
            .join(TaskProject, TaskProject.task_id == TaskOwner.task_id)
            .join(Project, Project.id == TaskProject.project_id)
            .where(Project.name == project)
            .group_by(User.name).order_by(User.name)
        ).all()
        return [r if isinstance(r, str) else r[0] for r in rows]
    if project is None:
        rows = s.exec(
            select(User.name).join(TaskOwner, TaskOwner.user_id == User.id)
            .group_by(User.name).order_by(User.name)
        ).all()
    else:
        rows = s.exec(
            select(User.name)
            .join(TaskOwner, TaskOwner.user_id == User.id)
            .join(TaskProject, TaskProject.task_id == TaskOwner.task_id)
            .join(Project, Project.id == TaskProject.project_id)
            .where(Project.name == project)
            .group_by(User.name).order_by(User.name)
        ).all()
    names = [r if isinstance(r, str) else r[0] for r in rows]
    disp = _owner_display_map(names)
    return [{"name": n, "display": disp.get(n, n)} for n in names]


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
    validate_password(body.new_password)
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
    validate_password(body.password)
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
        validate_password(body.password)
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
def search(
    q: str,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> list[dict[str, Any]]:
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
    # #230: post-filter by visibility (50-row cap keeps this cheap).
    is_admin, projects = _visible_projects(s, user)
    if not is_admin:
        def _vis(path: str) -> bool:
            p = _project_for_path(path)
            return p is None or p in projects
        rows = [r for r in rows if _vis(r[1])]
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
        "user_orphans_swept": WATCHER_STATE.get("user_orphans_swept_last", 0),
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


# ── Focus of the Week (#266) ─────────────────────────────────────────
# Free-form team / project goal of the week, stored as a single
# markdown file at ``<notes_dir>/_meta/focus.md``. The indexer skips
# ``_meta/`` entirely (see indexer/__init__.py); the file lives on
# disk for git tracking and is read/written exclusively through these
# endpoints. No parsing, no task extraction — the body is the source
# of truth.

FOCUS_REL_PATH = "_meta/focus.md"


class FocusWeekIn(BaseModel):
    markdown: str


def _focus_full_path() -> Path:
    return settings.notes_dir / FOCUS_REL_PATH


@router.get("/focus-week")
def get_focus_week(
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Return the current Focus of the Week markdown.

    Response: ``{"markdown": str, "updated_at": ISO8601 str, "path": str}``.
    Returns 404 if the file does not exist; the frontend hides the
    banner in that case.
    """
    full = _focus_full_path()
    if not full.exists():
        raise HTTPException(404, "focus file not set")
    try:
        markdown = full.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"failed to read focus file: {e}")
    mtime = datetime.utcfromtimestamp(full.stat().st_mtime).replace(microsecond=0)
    return {
        "markdown": markdown,
        "updated_at": mtime.isoformat() + "Z",
        "path": FOCUS_REL_PATH,
    }


@router.put("/focus-week")
def put_focus_week(
    body: FocusWeekIn,
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Overwrite ``_meta/focus.md``. Admin only.

    Empty/whitespace-only markdown deletes the file so the banner
    auto-hides — saves clients from having to call a separate DELETE.
    """
    _require_root_admin(s, user, "edit focus-of-the-week")
    full = _focus_full_path()
    full.parent.mkdir(parents=True, exist_ok=True)

    stripped = body.markdown.strip()
    if not stripped:
        if full.exists():
            with with_file_lock(full):
                if full.exists():
                    full.unlink()
        return {"markdown": "", "updated_at": None, "path": FOCUS_REL_PATH}

    safe_write(full, body.markdown, notes_dir=settings.notes_dir)
    mtime = datetime.utcfromtimestamp(full.stat().st_mtime).replace(microsecond=0)
    return {
        "markdown": body.markdown,
        "updated_at": mtime.isoformat() + "Z",
        "path": FOCUS_REL_PATH,
    }


# ── Dashboard (issue #290) ──────────────────────────────────────────────────

def _lookup_is_admin(username: str) -> bool:
    """Return True if the user has admin rights (no exception on miss)."""
    with Session(get_engine()) as s:
        u = s.exec(select(User).where(User.name == username)).first()
        return u is not None and bool(u.is_admin)


@router.get("/dashboard/data")
def dashboard_data(
    project: str = "ALL",
    range: str = "H1",
    year: Optional[int] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    force: bool = False,
    user: str = Depends(require_user),
    s: Session = Depends(get_session),
) -> Any:
    """Return full team git-metric report. Admin only."""
    u = s.exec(select(User).where(User.name == user)).first()
    if u is None or not u.is_admin:
        raise HTTPException(403, "admin role required")
    from ..dashboard import compute_dashboard_data
    return compute_dashboard_data(
        project=project, force=force,
        range_key=range, year=year, since=since, until=until,
    )


@router.get("/dashboard/turnins")
def dashboard_turnins(
    project: str = "ALL",
    engineer: Optional[str] = None,
    range: str = "H1",
    year: Optional[int] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    force: bool = False,
    user: str = Depends(require_user),
    s: Session = Depends(get_session),
) -> Any:
    """Return turnin data.

    - Admin: can pass any ``?engineer=name``. Omitting ``engineer`` returns
      the full-team summary.
    - IC: ``engineer`` param is ignored — always returns caller's own data.
    """
    from ..dashboard import fetch_turnins_for, resolve_engineer_name
    u = s.exec(select(User).where(User.name == user)).first()
    is_admin = u is not None and bool(u.is_admin)
    if not is_admin:
        engineer = resolve_engineer_name(user)
    # admin with engineer=None → fetch_turnins_for passes None → team summary
    return fetch_turnins_for(
        engineer=engineer, project=project, force=force,
        range_key=range, year=year, since=since, until=until,
    )


@router.get("/dashboard/roster")
def dashboard_roster(
    user: str = Depends(require_user),
) -> Any:
    """Return team roster. Admin gets full list; IC gets only their own entry."""
    from ..dashboard import get_roster, resolve_engineer_name
    if _lookup_is_admin(user):
        return get_roster()
    name = resolve_engineer_name(user)
    return [name] if name else [user]


@router.get("/dashboard/diff")
def dashboard_diff(
    project: str = Query(...),
    shas: str = Query(""),
    path: str = Query(...),
    turnin_id: Optional[str] = Query(None),
    user: str = Depends(require_user),
) -> Any:
    """Return plain-text git diff for a file at the given commit(s).

    Tries baseline model repo first; falls back to the gatekeeper bundle repo
    for in-flight turnins.
    """
    from ..dashboard import get_file_diff
    from fastapi.responses import PlainTextResponse
    sha_list = [s.strip() for s in shas.split(",") if s.strip()]
    text = get_file_diff(project, sha_list, path, turnin_id)
    return PlainTextResponse(content=text)
