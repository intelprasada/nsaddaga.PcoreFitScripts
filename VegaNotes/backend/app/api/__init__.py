"""REST API routers."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import bindparam, text
from sqlmodel import Session, select

from ..auth import hash_password, require_admin, require_user
from ..config import settings
from ..db import get_session
from ..indexer import reindex_file, remove_path
from ..markdown_ops import (
    inject_missing_ids, replace_attr, replace_multi_attr, replace_notes,
    append_note,
    remove_attr, roll_to_next_week, update_task_status,
)
from ..models import (
    Feature, Link, Note, Project, ProjectMember, Task, TaskAttr, TaskFeature,
    TaskOwner, TaskProject, User,
)
from ..parser import parse
from ..safe_io import (
    StaleWriteError, _safe_write_unlocked, etag_for, safe_write, with_file_lock,
)

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
        out["children"] = [
            {
                "id": c.id,
                "task_uuid": c.task_uuid,
                "slug": c.slug,
                "title": c.title,
                "status": c.status,
                "kind": c.kind,
                "line": c.line,
                "eta": next(
                    (a.value_norm for a in s.exec(
                        select(TaskAttr).where(TaskAttr.task_id == c.id, TaskAttr.key == "eta")
                    ).all()),
                    None,
                ),
            }
            for c in kids
        ]
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


@router.get("/notes/abs-path")
def note_abs_path(
    path: str = Query(..., description="Repo-relative note path"),
    s: Session = Depends(get_session),
    user: str = Depends(require_user),
) -> dict[str, str]:
    """Return the absolute filesystem path for a note.

    Used by the frontend's *Edit in Vim* affordance so a user on the same
    host can run `vim "<abs path>"` directly in their terminal. The file
    watcher reindexes on save, so changes round-trip into the UI without
    further action.
    """
    if ".." in path or path.startswith("/"):
        raise HTTPException(400, "invalid path")
    project = _project_for_path(path)
    if _user_role_for_project(s, user, project) == "none":
        raise HTTPException(403, "no access")
    full = settings.notes_dir / path
    if not full.exists():
        raise HTTPException(404, "note not found")
    return {
        "path": path,
        "abs_path": str(full.resolve()),
        "vim_cmd": f'vim "{full.resolve()}"',
    }


@router.get("/notes/{note_id}")
def get_note(note_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    n = s.get(Note, note_id)
    if not n:
        raise HTTPException(404, "note not found")
    full = settings.notes_dir / n.path
    return {
        "id": n.id, "path": n.path, "title": n.title,
        "body_md": n.body_md, "updated_at": n.updated_at,
        "etag": etag_for(full),
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
    return {"id": note.id, "path": note.path, "etag": new_etag}


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


# ---------- parse preview ---------------------------------------------------

@router.post("/parse")
def parse_preview(body: dict = Body(...)) -> dict[str, Any]:
    return parse(body.get("body_md", ""))


# ---------- tasks (composable filters) -------------------------------------

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
) -> dict[str, Any]:
    sql = ["SELECT DISTINCT t.id FROM task t"]
    params: dict[str, Any] = {}

    def _join_multi(table: str, name_table: str, names: list[str], alias: str) -> None:
        if not names:
            return
        sql.append(
            f"JOIN {table} {alias}_j ON {alias}_j.task_id = t.id "
            f"JOIN {name_table} {alias} ON {alias}.id = {alias}_j.{name_table}_id "
        )
        sql.append(f"AND {alias}.name IN :{alias}_names")
        params[f"{alias}_names"] = tuple(names)

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
    if priority:
        prios = _split(priority)
        sql.append("JOIN taskattr pa ON pa.task_id = t.id AND pa.key='priority'")
        where.append("pa.value IN :prios")
        params["prios"] = tuple(prios)
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
    if top_level_only:
        where.append("(t.parent_task_id IS NULL AND t.kind = 'task')")

    sql.append("WHERE " + " AND ".join(where))
    sql_text = " ".join(sql)
    stmt = text(sql_text)
    expanding_keys = [k for k in ("u_names", "p_names", "f_names", "statuses", "prios", "kinds") if k in params]
    if expanding_keys:
        stmt = stmt.bindparams(*[bindparam(k, expanding=True) for k in expanding_keys])
    rows = s.exec(stmt.bindparams(**params)).all()
    ids = [r[0] for r in rows]
    if not ids:
        return {"tasks": [], "aggregations": {"owners": [], "projects": [], "features": [], "status_breakdown": {}, "priority_breakdown": {}}}

    tasks = [_task_to_dict(s, s.get(Task, i), include_children=include_children) for i in ids]

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
        "aggregations": {
            "owners": agg_owners,
            "projects": agg_projects,
            "features": agg_features,
            "status_breakdown": status_bd,
            "priority_breakdown": prio_bd,
        },
    }


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
    if body.status is not None:
        md = update_task_status(md, t.line, body.status)
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
    reindex_file(full, s)
    refreshed = s.get(Task, task_id)
    return _task_to_dict(s, refreshed) if refreshed else {"ok": True}


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
    return {"name": user, "is_admin": bool(u and u.is_admin)}


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
    rows = s.exec(text("""
        SELECT n.id, n.path, n.title
        FROM notes_fts f JOIN note n ON n.id = f.rowid
        WHERE notes_fts MATCH :q
        LIMIT 50
    """).bindparams(q=q)).all()
    return [{"id": r[0], "path": r[1], "title": r[2]} for r in rows]
