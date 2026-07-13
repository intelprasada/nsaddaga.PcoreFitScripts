import base64
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

DATA = Path(tempfile.mkdtemp(prefix="vega-test-"))
os.environ["VEGANOTES_DATA_DIR"] = str(DATA)
os.environ["VEGANOTES_SERVE_STATIC"] = "false"

from app.main import app  # noqa: E402

AUTH = "Basic " + base64.b64encode(b"admin:admin").decode()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    shutil.rmtree(DATA, ignore_errors=True)


def test_health(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_auth_required(client):
    r = client.get("/api/notes")
    assert r.status_code == 401


def test_create_and_query(client):
    body = (Path(__file__).parent.parent / "fixtures" / "sprint14.md").read_text()
    r = client.put(
        "/api/notes",
        json={"path": "sprint14.md", "body_md": body},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    # Tasks for alice (hide done)
    r = client.get("/api/tasks?owner=alice&hide_done=1", headers={"Authorization": AUTH})
    assert r.status_code == 200
    tasks = r.json()["tasks"]
    titles = sorted(t["title"] for t in tasks)
    assert titles == ["Add OAuth callback", "Add login screen", "Wire up SSO"]

    # Agenda window covering 2027-04-24 (use big window).
    # "Wire up SSO" (alice, eta 04-22) rolls up to 04-24 (max child ETA).
    # "Add login screen" (bob, inherited alice) doesn't surface in agenda
    # since the agenda JOIN requires explicit owner row — pre-existing known gap.
    r = client.get("/api/agenda?owner=alice&days=3650", headers={"Authorization": AUTH})
    days = r.json()["by_day"]
    assert "2027-04-24" in days

    # Feature aggregation
    r = client.get("/api/features/search-rewrite/tasks", headers={"Authorization": AUTH})
    j = r.json()
    assert j["aggregations"]["owners"] == ["alice"]
    assert "Migrate index" in [t["title"] for t in j["tasks"]]

    # Bidirectional links: migrate-index -> wire-up-sso
    r = client.get("/api/tasks?q=Migrate", headers={"Authorization": AUTH})
    migrate_id = r.json()["tasks"][0]["id"]
    r = client.get(f"/api/cards/{migrate_id}/links", headers={"Authorization": AUTH})
    links = r.json()["links"]
    assert any(l["other_slug"] == "wire-up-sso" and l["direction"] == "out" for l in links)
    # Reverse direction on the target card
    r = client.get("/api/tasks?q=Wire", headers={"Authorization": AUTH})
    wire_id = r.json()["tasks"][0]["id"]
    r = client.get(f"/api/cards/{wire_id}/links", headers={"Authorization": AUTH})
    links = r.json()["links"]
    assert any(l["other_slug"] == "migrate-index" and l["direction"] == "in" for l in links)


# ---------------------------------------------------------------------------
# RBAC: last-manager protection (#81)
# ---------------------------------------------------------------------------

def _create_user(client, name: str, password: str = "password1") -> None:
    r = client.post(
        "/api/admin/users",
        json={"name": name, "password": password, "is_admin": False},
        headers={"Authorization": AUTH},
    )
    assert r.status_code in (200, 201, 409), r.text  # 201 created, 409 = already exists


def _admin_auth(name: str, password: str = "admin") -> str:
    return "Basic " + base64.b64encode(f"{name}:{password}".encode()).decode()


def test_last_manager_protection(client):
    """Removing or demoting the sole manager of a project must be rejected."""
    _create_user(client, "mgr1")
    _create_user(client, "mem1")

    # Create project as admin (admin auto-becomes manager via create_project).
    r = client.post(
        "/api/projects",
        json={"name": "test-rbac-proj"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code in (200, 409), r.text

    # Add mgr1 as manager, mem1 as member.
    client.put(
        "/api/projects/test-rbac-proj/members",
        json={"user_name": "mgr1", "role": "manager"},
        headers={"Authorization": AUTH},
    )
    client.put(
        "/api/projects/test-rbac-proj/members",
        json={"user_name": "mem1", "role": "member"},
        headers={"Authorization": AUTH},
    )

    # Remove admin from managers — now mgr1 is the only manager.
    r = client.delete(
        "/api/projects/test-rbac-proj/members/admin",
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200

    # Trying to demote mgr1 to member (sole manager) must return 400.
    r = client.put(
        "/api/projects/test-rbac-proj/members",
        json={"user_name": "mgr1", "role": "member"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 400
    assert "last manager" in r.json()["detail"].lower()

    # Trying to delete mgr1 (sole manager) must return 400.
    r = client.delete(
        "/api/projects/test-rbac-proj/members/mgr1",
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 400
    assert "last manager" in r.json()["detail"].lower()

    # Promoting mem1 to manager first — now removal/demotion of mgr1 is allowed.
    r = client.put(
        "/api/projects/test-rbac-proj/members",
        json={"user_name": "mem1", "role": "manager"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200

    r = client.delete(
        "/api/projects/test-rbac-proj/members/mgr1",
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Filesystem project bootstrap (#82)
# ---------------------------------------------------------------------------

def test_fs_project_bootstrap(client):
    """Projects created by dropping folders on disk get auto-assigned admin manager."""
    import shutil as _shutil
    proj_dir = DATA / "notes" / "fs-only-project"
    proj_dir.mkdir(parents=True, exist_ok=True)
    note = proj_dir / "readme.md"
    note.write_text("# FS-only project\n\n!task #title Hello #owner alice\n")

    # Trigger a reindex via the admin reindex endpoint.
    r = client.post("/api/admin/reindex", headers={"Authorization": AUTH})
    assert r.status_code == 200

    # The project should now be visible via /api/projects.
    r = client.get("/api/projects", headers={"Authorization": AUTH})
    names = [p["name"] for p in r.json()]
    assert "fs-only-project" in names

    # Admin must be listed as manager.
    r = client.get("/api/projects/fs-only-project/members", headers={"Authorization": AUTH})
    assert r.status_code == 200
    members = r.json()
    admin_row = next((m for m in members if m["user_name"] == "admin"), None)
    assert admin_row is not None
    assert admin_row["role"] == "manager"

    # Cleanup
    _shutil.rmtree(proj_dir, ignore_errors=True)


def test_reindex_sweeps_orphan_notes_when_file_deleted(client):
    """A Note row whose .md file no longer exists must be cascade-deleted on
    the next reindex_all() — guards against the NFS+inotify drift documented
    in #207, where the user lost a file on disk but the DB kept serving its
    tasks via /api/tasks (and `vn show task`).

    We bypass the watcher entirely by injecting a phantom Note row that
    points at a non-existent path: this is exactly the post-condition the
    user hit (DB had note 22, disk did not).  reindex_all must clean it up.
    """
    from app.db import session_scope
    from app.models import Note, Task
    from app.indexer import reindex_all

    fake_path = "phantom-orphan-proj/never_existed.md"
    fake_disk_path = DATA / "notes" / fake_path
    assert not fake_disk_path.exists(), "test precondition: file must NOT be on disk"

    # Inject the phantom note + a task that depends on it, mimicking the
    # state left behind when the watcher misses a delete.
    with session_scope() as s:
        n = Note(path=fake_path, title="Phantom", body_md="!task #title GhostTask\n")
        s.add(n)
        s.flush()
        t = Task(note_id=n.id, line=1, indent=0, kind="task",
                 title="GhostTask", status="todo",
                 slug="ghosttask", task_uuid="T-PHANT0M")
        s.add(t)
        s.commit()
        phantom_id = n.id

    # Sanity: the phantom is visible until we sweep.
    r = client.get(f"/api/notes/{phantom_id}", headers={"Authorization": AUTH})
    assert r.status_code == 200, "phantom note should be reachable before reindex"

    # Trigger reindex_all via the admin endpoint — it must reconcile the
    # orphan even though nothing changed on disk and the watcher never
    # fired.
    r = client.post("/api/admin/reindex", headers={"Authorization": AUTH})
    assert r.status_code == 200
    body = r.json()
    assert body.get("orphans_swept", 0) >= 1, body

    # The phantom note row is gone.
    r = client.get(f"/api/notes/{phantom_id}", headers={"Authorization": AUTH})
    assert r.status_code == 404, r.text

    # And so is its task — cascade through remove_path → _delete_task_children.
    r = client.get("/api/tasks", headers={"Authorization": AUTH})
    titles = [t.get("title") for t in r.json().get("tasks", r.json())] \
             if isinstance(r.json(), dict) else [t.get("title") for t in r.json()]
    assert "GhostTask" not in titles, "orphan task survived reindex sweep"


# ---------------------------------------------------------------------------
# Self-service password change (#66)
# ---------------------------------------------------------------------------


def test_change_own_password(client):
    """Any user can change their own password; wrong current password is rejected."""
    _create_user(client, "pw-user", "oldpassword")
    user_auth = "Basic " + base64.b64encode(b"pw-user:oldpassword").decode()

    # Wrong current password → 403.
    r = client.patch(
        "/api/me/password",
        json={"current_password": "wrongpass1", "new_password": "newpassword"},
        headers={"Authorization": user_auth},
    )
    assert r.status_code == 403

    # Empty new password → 400.
    r = client.patch(
        "/api/me/password",
        json={"current_password": "oldpassword", "new_password": ""},
        headers={"Authorization": user_auth},
    )
    assert r.status_code == 400

    # Correct change → 200.
    r = client.patch(
        "/api/me/password",
        json={"current_password": "oldpassword", "new_password": "newpassword"},
        headers={"Authorization": user_auth},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    # Old credentials now rejected.
    r = client.get("/api/me", headers={"Authorization": user_auth})
    assert r.status_code == 401

    # New credentials work.
    new_auth = "Basic " + base64.b64encode(b"pw-user:newpassword").decode()
    r = client.get("/api/me", headers={"Authorization": new_auth})
    assert r.status_code == 200
    assert r.json()["name"] == "pw-user"


def test_admin_can_reset_any_password(client):
    """Admin can reset another user's password via PATCH /admin/users/{name}."""
    _create_user(client, "reset-target", "originalpw")

    # Admin resets to "forcedpass1".
    r = client.patch(
        "/api/admin/users/reset-target",
        json={"password": "forcedpass1"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200

    # New password works.
    new_auth = "Basic " + base64.b64encode(b"reset-target:forcedpass1").decode()
    r = client.get("/api/me", headers={"Authorization": new_auth})
    assert r.status_code == 200
    assert r.json()["name"] == "reset-target"


# ---------------------------------------------------------------------------
# Ref-row propagation (#92)
# ---------------------------------------------------------------------------

def test_patch_propagates_to_ref_rows(client):
    """PATCH /tasks/{id} must update #task T-XXXX ref rows in other files too."""
    # 1. Create the canonical note with a stamped task.
    canonical_md = (
        "# Sprint\n"
        "- !task #id T-PROP01 Fix cache #status todo @alice\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "canonical-prop.md", "body_md": canonical_md},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text
    canonical_note_id = r.json()["id"]

    # 2. Create a weekly note that references the task with override attrs.
    ref_md = (
        "# Weekly\n"
        "- #task T-PROP01 Fix cache #status todo @alice\n"
        "- #task T-PROP01 Fix cache copy two #status todo\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "weekly-prop.md", "body_md": ref_md},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text
    ref_note_id = r.json()["id"]

    # 3. Resolve the task ID from the index.
    r = client.get("/api/tasks?q=Fix+cache", headers={"Authorization": AUTH})
    tasks = r.json()["tasks"]
    task = next(t for t in tasks if t.get("task_uuid") == "T-PROP01")
    task_id = task["id"]

    # 4. PATCH status + eta.
    r = client.patch(
        f"/api/tasks/{task_id}",
        json={"status": "done", "eta": "2026-W20"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"

    # 5. The canonical note must reflect the patch.
    r = client.get(f"/api/notes/{canonical_note_id}", headers={"Authorization": AUTH})
    canonical_body = r.json()["body_md"]
    assert "#status done" in canonical_body
    assert "#eta 2026-W20" in canonical_body

    # 6. The weekly (ref-row) note must also be updated on disk.
    r = client.get(f"/api/notes/{ref_note_id}", headers={"Authorization": AUTH})
    ref_body = r.json()["body_md"]
    # Both ref rows updated.
    assert ref_body.count("#status done") == 2
    assert ref_body.count("#eta 2026-W20") == 2
    # Old status gone.
    assert "#status todo" not in ref_body


def test_search_handles_fts5_special_chars(client):
    """Regression: queries containing FTS5 operators (-, :, ", *, etc.)
    used to bubble sqlite3.OperationalError -> 500.  They must be
    sanitised into a phrase-AND query and return 200 (possibly empty)."""
    for q in ["fit-val", "foo:bar", 'has"quote', "wild*", "a.b", "(paren)"]:
        r = client.get(f"/api/search?q={q}", headers={"Authorization": AUTH})
        assert r.status_code == 200, f"q={q!r} -> {r.status_code}: {r.text}"
        assert isinstance(r.json(), list)


def test_search_empty_query_returns_empty_list(client):
    r = client.get("/api/search?q=%20%20", headers={"Authorization": AUTH})
    assert r.status_code == 200
    assert r.json() == []


def test_dedent_clears_parent_task_id(client):
    """Regression: dedenting a subtask to root level must clear parent_task_id.

    Previously _incremental_reindex's second pass skipped tasks with no
    parent_slug, so a stale parent_task_id persisted in the DB after the task
    was dedented.  The task was then invisible in top_level_only queries
    (Kanban, My Tasks) because parent_task_id IS NULL was false.
    """
    notes_dir = DATA / "notes"
    notes_dir.mkdir(exist_ok=True)

    # Step 1: create a note where T-NEST01 is a subtask of T-ROOT01.
    md_nested = (
        "# Nesting test\n"
        "!task #id T-ROOT01 parent task @alice\n"
        "    !task #id T-NEST01 child task @alice\n"
    )
    note_path = notes_dir / "nest_test.md"
    note_path.write_text(md_nested)
    r = client.put("/api/notes", json={"path": "nest_test.md", "body_md": md_nested},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200

    # Confirm T-NEST01 is a child (parent_task_id set).
    r = client.get("/api/tasks/T-NEST01", headers={"Authorization": AUTH})
    assert r.status_code == 200
    assert r.json()["parent_task_id"] is not None, "T-NEST01 should have a parent"

    # Step 2: edit the note so T-NEST01 is now at root indent.
    md_flat = (
        "# Nesting test\n"
        "!task #id T-ROOT01 parent task @alice\n"
        "!task #id T-NEST01 child task @alice\n"
    )
    note_path.write_text(md_flat)
    r = client.put("/api/notes", json={"path": "nest_test.md", "body_md": md_flat},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200

    # T-NEST01 must now have parent_task_id cleared.
    r = client.get("/api/tasks/T-NEST01", headers={"Authorization": AUTH})
    assert r.status_code == 200
    assert r.json()["parent_task_id"] is None, "parent_task_id must be cleared after dedent"

    # And it must appear in a top_level_only query for its owner.
    r = client.get("/api/tasks?owner=alice&top_level_only=true",
                   headers={"Authorization": AUTH})
    assert r.status_code == 200
    uuids = [t["task_uuid"] for t in r.json()["tasks"]]
    assert "T-NEST01" in uuids, "Dedented task must appear in top_level_only owner query"


def test_ref_row_owner_syncs_taskowner(client):
    """Regression: owner override in a ref-row must write to taskowner (not
    just taskattr) so that the owner= filter in list_tasks matches the task.

    Previously _apply_ref_rows only added to taskattr, causing tasks whose
    ownership came exclusively from a ref-row to be invisible in any
    owner= query.
    """
    notes_dir = DATA / "notes"
    notes_dir.mkdir(exist_ok=True)

    # Canonical task owned by alice only.
    canonical = (
        "# Canonical\n"
        "!task #id T-REFOWN1 ref row owner test @alice\n"
    )
    canonical_path = notes_dir / "canonical_refown.md"
    canonical_path.write_text(canonical)
    r = client.put("/api/notes", json={"path": "canonical_refown.md", "body_md": canonical},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200

    # bob is NOT an owner yet.
    r = client.get("/api/tasks?owner=bob", headers={"Authorization": AUTH})
    assert "T-REFOWN1" not in [t["task_uuid"] for t in r.json()["tasks"]]

    # Weekly note with a ref-row that adds bob as owner override.
    weekly = (
        "# Weekly\n"
        "#task T-REFOWN1 @bob\n"
    )
    weekly_path = notes_dir / "weekly_refown.md"
    weekly_path.write_text(weekly)
    r = client.put("/api/notes", json={"path": "weekly_refown.md", "body_md": weekly},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200

    # bob must now appear in the owner= filter result.
    r = client.get("/api/tasks?owner=bob", headers={"Authorization": AUTH})
    assert r.status_code == 200
    uuids = [t["task_uuid"] for t in r.json()["tasks"]]
    assert "T-REFOWN1" in uuids, "Ref-row owner override must be visible via owner= filter"

    # The task's owners list must contain bob.
    r = client.get("/api/tasks/T-REFOWN1", headers={"Authorization": AUTH})
    assert r.status_code == 200
    assert "bob" in r.json()["owners"], "bob must appear in task owners after ref-row override"


def test_ref_row_attr_value_norm_matches_canonical(client):
    """Regression (#235): ref-row attr overrides must normalize value_norm
    via REGISTRY[key].normalize, exactly like canonical declarations.

    Without this fix, ETA ref-row overrides land with value_norm = the raw
    lowercased token (``'2099-09-09'`` happens to round-trip but words like
    ``tomorrow`` don't), and priority ref-row overrides land with
    value_norm = ``'p0'`` instead of the rank ``'0'`` — breaking ETA
    windows and priority sort.
    """
    notes_dir = DATA / "notes"
    notes_dir.mkdir(exist_ok=True)

    # Canonical task declares eta + priority via #attr tokens.
    canonical = (
        "# Canonical\n"
        "- !task due thing #id T-NORM01 #eta 2099-09-09 #priority p0\n"
    )
    (notes_dir / "canon_norm.md").write_text(canonical)
    r = client.put("/api/notes",
                   json={"path": "canon_norm.md", "body_md": canonical},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200

    # Capture canonical value_norm via the public single-task endpoint.
    r = client.get("/api/tasks/T-NORM01", headers={"Authorization": AUTH})
    assert r.status_code == 200
    canon_eta = r.json()["eta"]
    canon_pri_rank = r.json()["priority_rank"]

    # Same task, second task declared canonically with different eta/pri.
    # Then override via a ref-row in another file. The ref-row's
    # value_norm must round-trip to the same shape as the canonical path
    # (ETA -> ISO date, priority -> rank integer-as-string).
    canonical2 = (
        "# Canonical2\n"
        "- !task due thing2 #id T-NORM02 #eta 2099-01-01 #priority p3\n"
    )
    (notes_dir / "canon_norm2.md").write_text(canonical2)
    r = client.put("/api/notes",
                   json={"path": "canon_norm2.md", "body_md": canonical2},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200

    weekly = (
        "# Weekly\n"
        "#task T-NORM02 #eta 2099-09-09 #priority p0\n"
    )
    (notes_dir / "weekly_norm.md").write_text(weekly)
    r = client.put("/api/notes",
                   json={"path": "weekly_norm.md", "body_md": weekly},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200

    # Read raw value_norm out of the DB and compare.
    from sqlmodel import Session, select
    from app.db import get_engine
    from app.models import Task, TaskAttr
    with Session(get_engine()) as s:
        t1 = s.exec(select(Task).where(Task.task_uuid == "T-NORM01")).first()
        t2 = s.exec(select(Task).where(Task.task_uuid == "T-NORM02")).first()
        assert t1 and t2
        attrs_canon = {a.key: a.value_norm for a in s.exec(
            select(TaskAttr).where(TaskAttr.task_id == t1.id)).all()}
        attrs_ref = {a.key: a.value_norm for a in s.exec(
            select(TaskAttr).where(TaskAttr.task_id == t2.id)).all()}

    # The same logical override must produce the same value_norm.
    assert attrs_canon["eta"] == attrs_ref["eta"], (
        f"eta value_norm mismatch: canon={attrs_canon['eta']!r} "
        f"ref={attrs_ref['eta']!r}"
    )
    assert attrs_canon["priority"] == attrs_ref["priority"], (
        f"priority value_norm mismatch: canon={attrs_canon['priority']!r} "
        f"ref={attrs_ref['priority']!r}"
    )
    # And the rendered values match too.
    r = client.get("/api/tasks/T-NORM02", headers={"Authorization": AUTH})
    assert r.json()["eta"] == canon_eta
    assert r.json()["priority_rank"] == canon_pri_rank


def test_ref_row_reindex_is_idempotent_for_taskattr(client):
    """Regression (#234): reindexing the same unchanged ref-row file
    repeatedly must not duplicate taskattr rows. taskowner / taskfeature
    were already deduped via select-first; the parallel taskattr write
    was not, so it grew by N per reindex.
    """
    notes_dir = DATA / "notes"
    notes_dir.mkdir(exist_ok=True)

    # Canonical task and a ref-row file that adds two owners + a feature.
    (notes_dir / "canon_dup.md").write_text(
        "# Canon\n- !task root task #id T-DUP001 #status todo\n"
    )
    r = client.put("/api/notes",
                   json={"path": "canon_dup.md",
                         "body_md": (notes_dir / "canon_dup.md").read_text()},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200

    weekly_md = (
        "# Weekly\n"
        "#task T-DUP001 @alice @bob #feature search-rewrite\n"
    )
    (notes_dir / "weekly_dup.md").write_text(weekly_md)
    for _ in range(3):
        r = client.put("/api/notes",
                       json={"path": "weekly_dup.md", "body_md": weekly_md},
                       headers={"Authorization": AUTH})
        assert r.status_code == 200

    from sqlmodel import Session, select
    from app.db import get_engine
    from app.models import Task, TaskAttr
    with Session(get_engine()) as s:
        t = s.exec(select(Task).where(Task.task_uuid == "T-DUP001")).first()
        assert t is not None
        attrs = s.exec(
            select(TaskAttr).where(TaskAttr.task_id == t.id)
        ).all()
        from collections import Counter
        counts = Counter((a.key, a.value) for a in attrs)
        # After 3 reindexes of the same ref-row, each (key,value) must
        # still appear exactly once.
        assert counts[("owner", "alice")] == 1, dict(counts)
        assert counts[("owner", "bob")] == 1, dict(counts)
        assert counts[("feature", "search-rewrite")] == 1, dict(counts)


def test_create_task_appends_to_project_note(client):
    """Issue #63 — POST /api/tasks creates a task in the most recently
    modified note of the given project, with the requester as default owner.
    """
    notes_dir = DATA / "notes" / "issue63proj"
    notes_dir.mkdir(parents=True, exist_ok=True)
    seed = notes_dir / "wk01.md"
    seed.write_text("# Weekly\n", encoding="utf-8")
    r = client.put("/api/notes", json={"path": "issue63proj/wk01.md", "body_md": "# Weekly\n"},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/tasks",
        json={"title": "Triage build break", "status": "in-progress",
              "project": "issue63proj", "priority": "P1"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["title"] == "Triage build break"
    assert created["status"] == "in-progress"
    assert "admin" in created["owners"]
    assert created["task_uuid"] and created["task_uuid"].startswith("T-")
    assert created["note_path"] == "issue63proj/wk01.md"

    # The bullet should now exist in the markdown file.
    md = (DATA / "notes" / "issue63proj" / "wk01.md").read_text(encoding="utf-8")
    assert "Triage build break" in md
    assert created["task_uuid"] in md


def test_create_task_no_destination_returns_422(client):
    r = client.post(
        "/api/tasks",
        json={"title": "no destination"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 422


def test_create_task_empty_project_returns_422(client):
    notes_dir = DATA / "notes" / "issue63empty"
    notes_dir.mkdir(parents=True, exist_ok=True)
    r = client.post(
        "/api/tasks",
        json={"title": "x", "project": "issue63empty"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 422
    assert "no notes" in r.json()["detail"].lower()


def test_create_task_does_not_inherit_eof_section_owner(client):
    """Issue #121 — a new task appended to a file whose EOF sits under an
    `@otheruser` section must NOT inherit that user as a co-owner.  The
    blank-line separator before the appended task line breaks the parser's
    section-context inheritance.  Also: the appended line must not start
    with a `- ` bullet prefix.
    """
    notes_dir = DATA / "notes" / "issue121proj"
    notes_dir.mkdir(parents=True, exist_ok=True)
    seed_md = (
        "# Weekly\n"
        "@yongxi\n"
        "\t#task T-EXISTING1 some prior task\n"
    )
    rel = "issue121proj/wk01.md"
    r = client.put("/api/notes", json={"path": rel, "body_md": seed_md},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/tasks",
        json={"title": "Aboli's new task", "project": "issue121proj"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    # Owners must be exactly the requester, not yongxi from the EOF section.
    assert created["owners"] == ["admin"], (
        f"expected only admin as owner, got {created['owners']!r} "
        "(EOF @yongxi section bled into context — see issue #121)"
    )

    md = (DATA / "notes" / "issue121proj" / "wk01.md").read_text(encoding="utf-8")
    # The appended line must use the bare `!task ...` shape, not `- !task ...`.
    new_lines = [ln for ln in md.splitlines() if created["task_uuid"] in ln]
    assert len(new_lines) == 1, f"expected one line carrying the new id, got {new_lines!r}"
    assert not new_lines[0].lstrip().startswith("- "), (
        f"appended task line must not have `- ` bullet prefix; got: {new_lines[0]!r}"
    )
    assert new_lines[0].lstrip().startswith("!task"), (
        f"appended task line should start with `!task`; got: {new_lines[0]!r}"
    )


def test_delete_task_removes_line_and_children(client):
    """DELETE /api/tasks/{ref} — removes the task declaration line and any
    deeper-indented children (sub-tasks, ARs, #note continuations).  Owner
    or manager/admin may delete.
    """
    notes_dir = DATA / "notes" / "issuedelproj"
    notes_dir.mkdir(parents=True, exist_ok=True)
    seed = (
        "# Weekly\n"
        "@admin\n"
        "\t!task #id T-DELME01 Parent task\n"
        "\t\t!AR #id T-DELME02 child ar\n"
        "\t\t#note some continuation\n"
        "\t!task #id T-KEEPME1 Sibling task\n"
    )
    rel = "issuedelproj/wk.md"
    r = client.put("/api/notes", json={"path": rel, "body_md": seed},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    # admin is the requester and is also a user — RBAC passes (admin role).
    r = client.delete("/api/tasks/T-DELME01", headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "deleted"

    md = (DATA / "notes" / "issuedelproj" / "wk.md").read_text(encoding="utf-8")
    assert "T-DELME01" not in md
    assert "T-DELME02" not in md, "child AR should also be removed"
    assert "some continuation" not in md, "#note continuation should be removed"
    assert "T-KEEPME1" in md, "sibling at same indent must be preserved"

    # The deleted task should no longer be queryable.
    r = client.get("/api/tasks/T-DELME01", headers={"Authorization": AUTH})
    assert r.status_code == 404


def test_create_ar_under_task_inserts_inside_block(client):
    """POST /api/tasks/{ref}/ars — appends an AR child line inside the parent's
    block (after any existing children, before the next blank-line / sibling).
    The AR must be reachable as a child in the parent's `include_children`
    payload."""
    notes_dir = DATA / "notes" / "araddproj"
    notes_dir.mkdir(parents=True, exist_ok=True)
    seed = (
        "# Weekly\n"
        "@admin\n"
        "\t!task #id T-PARENT01 Parent task\n"
        "\t\t!AR #id T-EXIST001 existing ar\n"
        "\n"
        "\t!task #id T-SIB00001 Sibling task\n"
    )
    rel = "araddproj/wk.md"
    r = client.put("/api/notes", json={"path": rel, "body_md": seed},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/tasks/T-PARENT01/ars",
        json={"title": "newly added AR"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 201, r.text
    new_ar = r.json()
    assert new_ar["kind"] == "ar"
    assert new_ar["title"] == "newly added AR"
    assert new_ar["parent_task_uuid"] == "T-PARENT01"

    md = (DATA / "notes" / "araddproj" / "wk.md").read_text(encoding="utf-8")
    # New AR sits between the existing AR and the blank line — same indent as
    # the existing one.  Sibling task downstream is untouched.
    lines = md.splitlines()
    parent_idx = next(i for i, l in enumerate(lines) if "T-PARENT01" in l)
    existing_idx = next(i for i, l in enumerate(lines) if "T-EXIST001" in l)
    new_idx = next(i for i, l in enumerate(lines) if "newly added AR" in l)
    sibling_idx = next(i for i, l in enumerate(lines) if "T-SIB00001" in l)
    assert parent_idx < existing_idx < new_idx < sibling_idx

    # The new AR must show up in the parent's children when fetched with
    # include_children — that's how the Kanban + My Tasks dropdown picks it up.
    r = client.get("/api/tasks/T-PARENT01?include_children=true",
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    parent_payload = r.json()
    child_titles = [c["title"] for c in parent_payload.get("children", [])]
    assert "newly added AR" in child_titles


def test_create_ar_inherits_parent_owner_not_requester(client):
    """Popover AR-create (only ``title`` in the payload) must inherit the
    parent task's owner — NOT silently tag the AR with the requester's
    name. Surgical write-up: project manager ``admin`` files an AR under
    a task explicitly owned by ``khbyers`` (with no overriding section
    ``@owner`` heading); the resulting !AR line must carry ``@khbyers``
    and the API ``owners`` field must reflect that — admin is the
    requester, not an owner.
    """
    _create_user(client, "khbyers")
    notes_dir = DATA / "notes" / "arinheritproj"
    notes_dir.mkdir(parents=True, exist_ok=True)
    # No `@admin` section heading — parent's only owner is the explicit
    # `@khbyers` token. This isolates the inheritance behaviour from
    # section-context owner injection.
    seed = (
        "# Weekly\n"
        "!task #id T-INHERIT01 Parent owned by khbyers @khbyers\n"
    )
    r = client.put("/api/notes",
                   json={"path": "arinheritproj/wk.md", "body_md": seed},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    # admin (manager / requester) files an AR with the popover payload
    # shape — only ``title``. No owners list at all.
    r = client.post("/api/tasks/T-INHERIT01/ars",
                    json={"title": "follow up debug"},
                    headers={"Authorization": AUTH})
    assert r.status_code == 201, r.text
    new_ar = r.json()
    assert new_ar["owners"] == ["khbyers"], (
        f"AR should inherit parent owner [khbyers], got {new_ar['owners']!r}"
    )
    assert "admin" not in new_ar["owners"], (
        "AR must not be silently tagged with the requester (admin)"
    )

    md = (DATA / "notes" / "arinheritproj" / "wk.md").read_text(encoding="utf-8")
    ar_line = next(l for l in md.splitlines() if "follow up debug" in l)
    assert "@khbyers" in ar_line, f"!AR line missing @khbyers token:\n{ar_line}"
    assert "@admin" not in ar_line, (
        f"!AR line should not carry @admin (the requester):\n{ar_line}"
    )


def test_create_ar_explicit_owners_override_inheritance(client):
    """When the caller passes a non-empty ``owners`` list, that list wins
    — inheritance from the parent only kicks in when ``owners`` is None.
    """
    _create_user(client, "alice")
    notes_dir = DATA / "notes" / "aroverrideproj"
    notes_dir.mkdir(parents=True, exist_ok=True)
    seed = (
        "# Weekly\n"
        "!task #id T-OVRD0001 Parent owned by bob @bob\n"
    )
    _create_user(client, "bob")
    client.put("/api/notes",
               json={"path": "aroverrideproj/wk.md", "body_md": seed},
               headers={"Authorization": AUTH})

    r = client.post("/api/tasks/T-OVRD0001/ars",
                    json={"title": "delegate work", "owners": ["alice"]},
                    headers={"Authorization": AUTH})
    assert r.status_code == 201, r.text
    # Note: parser child-inheritance of parent owner tokens means the API
    # response also includes @bob; what we're pinning here is that the
    # written !AR line carries @alice (the explicit override) and NOT @bob
    # (which would have been the inheritance default).
    md = (DATA / "notes" / "aroverrideproj" / "wk.md").read_text(encoding="utf-8")
    ar_line = next(l for l in md.splitlines() if "delegate work" in l)
    assert "@alice" in ar_line, f"explicit @alice missing from !AR line:\n{ar_line}"
    assert "@bob" not in ar_line, (
        f"!AR line should not carry @bob — explicit owners override "
        f"parent-inheritance:\n{ar_line}"
    )
    assert "alice" in r.json()["owners"]


def test_add_ar_propagates_to_ref_row_files(client):
    """POST /api/tasks/{ref}/ars must add a `#AR <new_id> <title>` row to
    every md file that already contains a `#task <parent_uuid>` ref row.

    Without this propagation, the new AR is invisible from weekly notes
    and any later PATCH on the AR's status finds no ref-row to update
    (the user-facing symptom described in #148).
    """
    notes_dir = DATA / "notes" / "arpropproj"
    notes_dir.mkdir(parents=True, exist_ok=True)
    canonical = (
        "# Sprint ww16\n"
        "@admin\n"
        "\t!task #id T-ARPROP01 Parent task\n"
    )
    weekly = (
        "# Weekly ww17\n"
        "- #task T-ARPROP01 Parent task #status todo\n"
        "Some prose\n"
    )
    r = client.put("/api/notes", json={"path": "arpropproj/ww16.md", "body_md": canonical},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    r = client.put("/api/notes", json={"path": "arpropproj/ww17.md", "body_md": weekly},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/tasks/T-ARPROP01/ars",
        json={"title": "investigate dns"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 201, r.text
    new_id = r.json()["task_uuid"]
    assert new_id and new_id.startswith("T-")

    # Canonical: !AR declaration written into ww16.
    ww16 = (DATA / "notes" / "arpropproj" / "ww16.md").read_text(encoding="utf-8")
    assert f"!AR #id {new_id} investigate dns" in ww16

    # Propagated: ref-row row appears in ww17 directly under the parent's
    # #task ref row, at the same indent (no leading tab).
    ww17 = (DATA / "notes" / "arpropproj" / "ww17.md").read_text(encoding="utf-8")
    lines = ww17.splitlines()
    parent_idx = next(i for i, l in enumerate(lines) if "#task T-ARPROP01" in l)
    ar_idx = next((i for i, l in enumerate(lines) if f"#AR {new_id}" in l), None)
    assert ar_idx is not None, f"propagated AR ref row missing in ww17:\n{ww17}"
    assert ar_idx == parent_idx + 1, "AR ref row must sit immediately under the parent ref row"
    assert lines[ar_idx] == f"- #AR {new_id} investigate dns @admin"


def test_add_ar_then_patch_status_propagates(client):
    """Second-order regression mirroring the user's reported symptom: add
    AR, then change its status. Both the canonical AR line AND the
    propagated ref row must carry the new status (the existing #92
    PATCH-propagation block requires the ref row to exist in the first
    place, which is what #148 fixes).
    """
    notes_dir = DATA / "notes" / "arstatusproj"
    notes_dir.mkdir(parents=True, exist_ok=True)
    canonical = "# ww16\n@admin\n\t!task #id T-ARSTAT01 Parent\n"
    weekly = "# ww17\n- #task T-ARSTAT01 Parent #status todo\n"
    client.put("/api/notes", json={"path": "arstatusproj/ww16.md", "body_md": canonical},
               headers={"Authorization": AUTH})
    client.put("/api/notes", json={"path": "arstatusproj/ww17.md", "body_md": weekly},
               headers={"Authorization": AUTH})

    r = client.post("/api/tasks/T-ARSTAT01/ars",
                    json={"title": "fix tests"},
                    headers={"Authorization": AUTH})
    assert r.status_code == 201, r.text
    new_id = r.json()["task_uuid"]

    r = client.patch(f"/api/tasks/{new_id}",
                     json={"status": "done"},
                     headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    ww16 = (DATA / "notes" / "arstatusproj" / "ww16.md").read_text(encoding="utf-8")
    ww17 = (DATA / "notes" / "arstatusproj" / "ww17.md").read_text(encoding="utf-8")
    # Canonical AR line carries the new status.
    assert f"!AR #id {new_id}" in ww16
    canonical_ar = next(l for l in ww16.splitlines() if f"!AR #id {new_id}" in l)
    assert "#status done" in canonical_ar
    # Propagated AR ref row also carries the new status.
    assert f"#AR {new_id}" in ww17
    propagated_ar = next(l for l in ww17.splitlines() if f"#AR {new_id}" in l)
    assert "#status done" in propagated_ar


def test_add_ar_skips_files_without_parent_ref(client):
    """A note that mentions the parent UUID only inside prose (no `#task`
    keyword) must not be modified. The propagation must trigger off the
    `#task <parent>` ref-row pattern, not bare substring matches."""
    notes_dir = DATA / "notes" / "arproseproj"
    notes_dir.mkdir(parents=True, exist_ok=True)
    canonical = "# ww16\n@admin\n\t!task #id T-ARPROS01 Parent\n"
    prose = (
        "# Notes\n"
        "Talking about T-ARPROS01 in passing — no ref row here.\n"
    )
    client.put("/api/notes", json={"path": "arproseproj/ww16.md", "body_md": canonical},
               headers={"Authorization": AUTH})
    client.put("/api/notes", json={"path": "arproseproj/loose.md", "body_md": prose},
               headers={"Authorization": AUTH})

    r = client.post("/api/tasks/T-ARPROS01/ars",
                    json={"title": "do thing"},
                    headers={"Authorization": AUTH})
    assert r.status_code == 201, r.text
    new_id = r.json()["task_uuid"]

    loose_after = (DATA / "notes" / "arproseproj" / "loose.md").read_text(encoding="utf-8")
    assert prose == loose_after, "prose-only note must be untouched"
    assert new_id not in loose_after


def test_archived_notes_are_not_mutated_by_ar_or_patch(client):
    """Issue #253: AR-create / task PATCH must NEVER rewrite archived
    weekly notes, even when the archive contains a `#task <parent>` ref
    row pointing at the active task. The archive-style rollover (#251)
    leaves such ref rows in every prior week's archive, so without the
    ``archived == False`` filter on the candidate-notes query, every
    edit on a long-lived task fans out into all of them — corrupting
    history and bypassing the manager-only popover RBAC.
    """
    from app.db import session_scope
    from app.models import Note

    proj = DATA / "notes" / "archnoprop"
    (proj / "_archive").mkdir(parents=True, exist_ok=True)

    # Active week: canonical declaration of the parent task.
    active = (
        "# ww24\n"
        "@admin\n"
        "\t!task #id T-ARCHFIX01 Long-lived parent\n"
    )
    # Archived week: contains a `#task` ref row pointing at the parent
    # (this is exactly what archive-style rollover writes for migrated
    # tasks). It MUST remain byte-identical after any active-week edit.
    archived = (
        "# ww23 (archived)\n"
        "- #task T-ARCHFIX01 Long-lived parent #status in-progress\n"
        "Closing notes for the week.\n"
    )
    r = client.put("/api/notes",
                   json={"path": "archnoprop/ww24.md", "body_md": active},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    r = client.put("/api/notes",
                   json={"path": "archnoprop/_archive/ww23.md", "body_md": archived},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    # Flag the archive row as Note.archived = True (mirrors what
    # roll_to_next_week does in 2e95e67). The path is also under
    # ``_archive/`` so the belt-and-suspenders skip would catch it too,
    # but we want both gates exercised.
    with session_scope() as s:
        from sqlmodel import select as _select
        n = s.exec(_select(Note).where(Note.path == "archnoprop/_archive/ww23.md")).one()
        n.archived = True
        s.add(n)

    archive_path = DATA / "notes" / "archnoprop" / "_archive" / "ww23.md"
    archive_bytes_before = archive_path.read_bytes()

    # 1) AR-create on the active week's task.
    r = client.post("/api/tasks/T-ARCHFIX01/ars",
                    json={"title": "investigate hang"},
                    headers={"Authorization": AUTH})
    assert r.status_code == 201, r.text
    new_id = r.json()["task_uuid"]
    assert new_id and new_id.startswith("T-")

    archive_bytes_after_ar = archive_path.read_bytes()
    assert archive_bytes_after_ar == archive_bytes_before, (
        "archived note was rewritten by AR-create propagation (issue #253)"
    )
    assert new_id.encode() not in archive_bytes_after_ar, (
        f"new AR id {new_id} leaked into archive"
    )

    # 2) PATCH the parent task — status / eta / add_note.
    r = client.patch("/api/tasks/T-ARCHFIX01",
                     json={"status": "done", "eta": "WW25.1",
                           "add_note": "wrapping up"},
                     headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    archive_bytes_after_patch = archive_path.read_bytes()
    assert archive_bytes_after_patch == archive_bytes_before, (
        "archived note was rewritten by patch_task propagation (issue #253)"
    )
    # The active week's canonical line should reflect the patch.
    active_after = (DATA / "notes" / "archnoprop" / "ww24.md").read_text(encoding="utf-8")
    assert "#status done" in active_after
    assert "#eta WW25.1" in active_after


def test_add_ar_idempotent_on_retry(client):
    """If create_ar_under_task is called twice for the same logical AR
    (transient 5xx + client retry produces a new task_uuid each time, but
    a retry that re-sends the same request body should not duplicate the
    ref row in ref files).

    Because each POST stamps a fresh `T-NEW...` id, a second POST does
    represent a new AR — so we expect a SECOND ref row, not zero.
    Idempotency at the helper level (same id passed twice) is covered by
    the unit tests; this test pins the API behaviour: each call → exactly
    one new ref row per file, no duplication of prior ARs.
    """
    notes_dir = DATA / "notes" / "aridempproj"
    notes_dir.mkdir(parents=True, exist_ok=True)
    canonical = "# ww16\n@admin\n\t!task #id T-ARIDEM01 Parent\n"
    weekly = "# ww17\n- #task T-ARIDEM01 Parent #status todo\n"
    client.put("/api/notes", json={"path": "aridempproj/ww16.md", "body_md": canonical},
               headers={"Authorization": AUTH})
    client.put("/api/notes", json={"path": "aridempproj/ww17.md", "body_md": weekly},
               headers={"Authorization": AUTH})

    r1 = client.post("/api/tasks/T-ARIDEM01/ars",
                     json={"title": "first"},
                     headers={"Authorization": AUTH})
    assert r1.status_code == 201, r1.text
    id1 = r1.json()["task_uuid"]
    r2 = client.post("/api/tasks/T-ARIDEM01/ars",
                     json={"title": "second"},
                     headers={"Authorization": AUTH})
    assert r2.status_code == 201, r2.text
    id2 = r2.json()["task_uuid"]
    assert id1 != id2

    ww17 = (DATA / "notes" / "aridempproj" / "ww17.md").read_text(encoding="utf-8")
    assert ww17.count(f"#AR {id1}") == 1, f"first AR row duplicated:\n{ww17}"
    assert ww17.count(f"#AR {id2}") == 1, f"second AR row duplicated:\n{ww17}"


def test_add_note_rejects_ar_or_task_token(client):
    """PATCH /api/tasks/{id} with `add_note` text starting with `!AR` or
    `!task` is refused — those payloads were silently being filed as #note
    continuations and the parser couldn't recover them as task lines.
    See issue #125."""
    notes_dir = DATA / "notes" / "guardproj"
    notes_dir.mkdir(parents=True, exist_ok=True)
    seed = (
        "# Weekly\n"
        "@admin\n"
        "\t!task #id T-GUARD001 Parent task\n"
    )
    rel = "guardproj/wk.md"
    r = client.put("/api/notes", json={"path": rel, "body_md": seed},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    for bad in ("!AR follow up", "!ar fix this", "  !task new task", "- !AR x"):
        r = client.patch(
            "/api/tasks/T-GUARD001",
            json={"add_note": bad},
            headers={"Authorization": AUTH},
        )
        assert r.status_code == 400, f"expected 400 for {bad!r}, got {r.status_code}: {r.text}"
        assert "Add an AR" in r.json()["detail"] or "AR" in r.json()["detail"]

    # Plain note text still works.
    r = client.patch(
        "/api/tasks/T-GUARD001",
        json={"add_note": "ordinary note that mentions !AR in the middle"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text


def test_add_ar_strips_redundant_ar_prefix(client):
    """If a user types `!AR foo` into the AR title field (because they
    remember the markdown keyword), the endpoint should strip the leading
    `!AR ` so the resulting line is `!AR #id T-XXX foo` (not double-bang).
    See issue #125."""
    notes_dir = DATA / "notes" / "stripproj"
    notes_dir.mkdir(parents=True, exist_ok=True)
    seed = (
        "# Weekly\n"
        "@admin\n"
        "\t!task #id T-STRIP001 Parent task\n"
    )
    rel = "stripproj/wk.md"
    r = client.put("/api/notes", json={"path": rel, "body_md": seed},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/tasks/T-STRIP001/ars",
        json={"title": "!AR my action item"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "my action item"

    md = (DATA / "notes" / "stripproj" / "wk.md").read_text(encoding="utf-8")
    # No double-bang in the file.
    assert "!AR #id" in md
    assert "!AR my action item" not in md, "redundant prefix should have been stripped"
    assert "my action item" in md


def test_patch_add_note_propagates_to_ref_row_files(client):
    """Notes added via PATCH /api/tasks/{id} must also be appended under any
    `#task T-XXX` ref-row in other md files that reference the same task —
    so the audit trail is consistent across the canonical declaration and
    every weekly/rolled file that points at it.  Follow-up to issue #92."""
    notes_dir = DATA / "notes" / "noteprop"
    notes_dir.mkdir(parents=True, exist_ok=True)
    canonical = (
        "# Sprint canonical\n"
        "@admin\n"
        "\t!task #id T-NOTEPRP1 propagation parent\n"
    )
    weekly = (
        "# WW17\n"
        "\t#task T-NOTEPRP1 propagation parent #status todo\n"
    )
    r = client.put("/api/notes",
                   json={"path": "noteprop/canonical.md", "body_md": canonical},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    r = client.put("/api/notes",
                   json={"path": "noteprop/ww17.md", "body_md": weekly},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    # Add a note via the standard PATCH path.
    r = client.patch("/api/tasks/T-NOTEPRP1",
                     json={"add_note": "first follow-up note"},
                     headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    canon_after = (DATA / "notes" / "noteprop" / "canonical.md").read_text(encoding="utf-8")
    weekly_after = (DATA / "notes" / "noteprop" / "ww17.md").read_text(encoding="utf-8")
    assert "first follow-up note" in canon_after, "note must land in canonical file"
    assert "first follow-up note" in weekly_after, (
        "note must also propagate to ref-row file. Got:\n" + weekly_after
    )
    # And it should be a real `#note` continuation, not free text.
    assert "#note" in weekly_after.split("first follow-up note")[0].splitlines()[-1]

    # A second note should append (not overwrite) in both files.
    r = client.patch("/api/tasks/T-NOTEPRP1",
                     json={"add_note": "second follow-up note"},
                     headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    canon2 = (DATA / "notes" / "noteprop" / "canonical.md").read_text(encoding="utf-8")
    weekly2 = (DATA / "notes" / "noteprop" / "ww17.md").read_text(encoding="utf-8")
    for body in (canon2, weekly2):
        assert "first follow-up note" in body and "second follow-up note" in body


# ---------------------------------------------------------------------------
# Note history persistence (regression coverage)
# ---------------------------------------------------------------------------
# These guard the contract that powers the "Notes — history" panel in
# TaskEditPopover: every PATCH `add_note` is *appended*; existing notes are
# never lost; and the GET `/api/tasks/{id}` response surfaces the full list
# in `note_history` (and joined as `notes`) so the popover can render it.

def test_note_history_appears_in_task_get(client):
    """Two sequential add_note PATCHes -> note_history has both entries
    in oldest-first order, and `notes` joins them with newlines.
    Regression: if note_history is missing/empty, the popover panel will
    say "No prior notes." even when the .md file has prior #note lines.
    """
    body = (
        "# History\n"
        "- !task #id T-HIST01 Track me #status todo @alice\n"
    )
    r = client.put("/api/notes",
                   json={"path": "history-task.md", "body_md": body},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    # First note.
    r = client.patch("/api/tasks/T-HIST01",
                     json={"add_note": "first observation"},
                     headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    # Second note.
    r = client.patch("/api/tasks/T-HIST01",
                     json={"add_note": "second observation"},
                     headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    # GET the task — both entries must surface in note_history (oldest first)
    # and the joined `notes` string used by legacy clients.
    r = client.get("/api/tasks?q=Track+me", headers={"Authorization": AUTH})
    task = next(t for t in r.json()["tasks"] if t.get("task_uuid") == "T-HIST01")
    assert task["note_history"] == ["first observation", "second observation"], (
        f"note_history lost entries; got {task['note_history']!r}"
    )
    assert task["notes"] == "first observation\nsecond observation"


def test_unrelated_patch_preserves_existing_notes(client):
    """A PATCH that mutates status/owner/eta MUST NOT clobber the existing
    #note continuation block. Regression for the legacy `body.notes` path
    (replace_notes) accidentally being reachable from the popover save flow.
    """
    body = (
        "# Mixed\n"
        "- !task #id T-HIST02 Mixed mutation #status todo @alice\n"
    )
    r = client.put("/api/notes",
                   json={"path": "history-mixed.md", "body_md": body},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    # Seed two notes.
    for txt in ("seed note alpha", "seed note beta"):
        r = client.patch("/api/tasks/T-HIST02",
                         json={"add_note": txt},
                         headers={"Authorization": AUTH})
        assert r.status_code == 200, r.text

    # Now do a status-only patch. Notes must survive on disk + in API.
    r = client.patch("/api/tasks/T-HIST02",
                     json={"status": "in-progress"},
                     headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    on_disk = (DATA / "notes" / "history-mixed.md").read_text(encoding="utf-8")
    assert "seed note alpha" in on_disk and "seed note beta" in on_disk, (
        "status-only patch wiped notes from disk:\n" + on_disk
    )

    r = client.get("/api/tasks?q=Mixed+mutation", headers={"Authorization": AUTH})
    task = next(t for t in r.json()["tasks"] if t.get("task_uuid") == "T-HIST02")
    assert task["note_history"] == ["seed note alpha", "seed note beta"]
    assert task["status"] == "in-progress"


def test_note_history_propagates_to_ref_row_file_and_persists(client):
    """Notes added via PATCH must (a) land in the canonical file as
    appended #note lines, (b) propagate to every ref-row file that
    references the task, and (c) on a GET, `note_history` reflects the
    canonical (de-duplicated) sequence — not the doubled sum across files.
    """
    canon = "# Canon\n- !task #id T-HIST03 Cross-file note @alice\n"
    ref = "# Weekly\n- #task T-HIST03 Cross-file note @alice\n"
    r = client.put("/api/notes",
                   json={"path": "history-canon.md", "body_md": canon},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    r = client.put("/api/notes",
                   json={"path": "history-ref.md", "body_md": ref},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    for txt in ("xfile note one", "xfile note two", "xfile note three"):
        r = client.patch("/api/tasks/T-HIST03",
                         json={"add_note": txt},
                         headers={"Authorization": AUTH})
        assert r.status_code == 200, r.text

    canon_md = (DATA / "notes" / "history-canon.md").read_text(encoding="utf-8")
    ref_md = (DATA / "notes" / "history-ref.md").read_text(encoding="utf-8")
    for txt in ("xfile note one", "xfile note two", "xfile note three"):
        assert txt in canon_md, f"missing {txt!r} in canonical:\n{canon_md}"
        assert txt in ref_md, f"missing {txt!r} in ref-row file:\n{ref_md}"

    # Each note appears EXACTLY once per file (not duplicated by propagation).
    for txt in ("xfile note one", "xfile note two", "xfile note three"):
        assert canon_md.count(txt) == 1, f"{txt!r} duplicated in canonical"
        assert ref_md.count(txt) == 1, f"{txt!r} duplicated in ref-row file"

    # And GET surfaces the canonical history (3 entries, in order).
    r = client.get("/api/tasks?q=Cross-file+note", headers={"Authorization": AUTH})
    task = next(t for t in r.json()["tasks"] if t.get("task_uuid") == "T-HIST03")
    assert task["note_history"] == [
        "xfile note one", "xfile note two", "xfile note three",
    ]


# --- watcher status (#150) ------------------------------------------------

def test_watcher_status_endpoint_requires_admin(client):
    r = client.get("/api/admin/watcher_status")
    assert r.status_code == 401


def test_watcher_status_endpoint_reports_state(client):
    r = client.get("/api/admin/watcher_status", headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    data = r.json()
    # Lifespan started watch_loop -> these keys must be populated.
    assert data["mode"] in {"event", "polling"}
    assert data["notes_dir"]
    assert isinstance(data["events_total"], int)
    assert isinstance(data["errors_total"], int)
    assert "fs_type" in data
    assert "force_polling" in data
    assert "poll_delay_ms" in data


# --- /notes/etag freshness probe (#153) ----------------------------------

def test_notes_etag_endpoint(client):
    body = "# probe-153\n\nhello\n"
    r = client.put("/api/notes",
        json={"path": "etag-probe.md", "body_md": body},
        headers={"Authorization": AUTH})
    assert r.status_code in (200, 201), r.text
    full_etag = r.json()["etag"]

    # Light endpoint: returns etag + mtime, no body.
    r2 = client.get("/api/notes/etag?path=etag-probe.md",
        headers={"Authorization": AUTH})
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["etag"] == full_etag
    assert "body_md" not in data
    assert isinstance(data["mtime"], (int, float))

    # Mutate and confirm etag advances.
    r = client.put("/api/notes",
        json={"path": "etag-probe.md", "body_md": body + "more\n"},
        headers={"Authorization": AUTH, "If-Match": full_etag})
    assert r.status_code == 200, r.text
    new_etag = r.json()["etag"]
    assert new_etag != full_etag

    r3 = client.get("/api/notes/etag?path=etag-probe.md",
        headers={"Authorization": AUTH})
    assert r3.json()["etag"] == new_etag


def test_notes_etag_endpoint_404(client):
    r = client.get("/api/notes/etag?path=does-not-exist.md",
        headers={"Authorization": AUTH})
    assert r.status_code == 404


def test_notes_etag_endpoint_400_on_empty_or_directory_path(client):
    """#232: empty / directory paths must be rejected with 400, not a
    500 from ``read_text`` on the notes_dir itself."""
    # Empty path (was 500 before the fix).
    r = client.get("/api/notes/etag?path=",
        headers={"Authorization": AUTH})
    assert r.status_code == 400, r.text

    # Trailing slash → looks like a directory.
    r = client.get("/api/notes/etag?path=foo/",
        headers={"Authorization": AUTH})
    assert r.status_code == 400, r.text


def test_notes_etag_endpoint_requires_auth(client):
    r = client.get("/api/notes/etag?path=etag-probe.md")
    assert r.status_code == 401


def test_parent_done_with_all_ars_done_persists_after_reindex(client):
    """Regression for #199: marking parent !task done and all child !ARs
    done must survive a full reindex (page refresh / file watcher fire).

    The bug was that the parser's _rollup_to_parents() mutated the parent
    task's status in-place, and the indexer then persisted the mutated
    value into the Task row, so a subsequent reparse "downgraded" a
    legitimately-done parent and propagated stale status back to the UI.
    """
    from pathlib import Path as _P
    from sqlmodel import Session as _S
    from app.indexer import reindex_file
    from app.db import get_engine

    notes_dir = DATA / "notes" / "p199proj"
    notes_dir.mkdir(parents=True, exist_ok=True)
    canonical = (
        "# ww16\n"
        "@admin\n"
        "\t!task #id T-P199AA Parent\n"
    )
    pr = client.put("/api/notes", json={"path": "p199proj/ww16.md", "body_md": canonical},
               headers={"Authorization": AUTH})
    assert pr.status_code in (200, 201), pr.text
    note_id = pr.json()["id"]

    # Read whatever the parser stamped (it may auto-generate UUIDs).
    server_md = (DATA / "notes" / "p199proj" / "ww16.md").read_text(encoding="utf-8")
    parent_id = next(
        (tok for line in server_md.splitlines() if "!task" in line
         for tok in line.split() if tok.startswith("T-")),
        None,
    )
    assert parent_id, server_md
    # Sanity: confirm the indexer registered this task before we proceed.
    rcheck = client.get(f"/api/tasks/{parent_id}", headers={"Authorization": AUTH})
    assert rcheck.status_code == 200, (
        f"parent {parent_id} not in index after PUT; "
        f"server_md={server_md!r}; rcheck={rcheck.text}"
    )

    # Add two ARs via the API so they get server-stamped UUIDs.
    ars = []
    for title in ("child one", "child two"):
        r = client.post(f"/api/tasks/{parent_id}/ars",
                        json={"title": title},
                        headers={"Authorization": AUTH})
        assert r.status_code == 201, r.text
        ars.append(r.json()["task_uuid"])

    # Mark both ARs done, then mark parent done.
    for ref in ars + [parent_id]:
        r = client.patch(f"/api/tasks/{ref}",
                         json={"status": "done"},
                         headers={"Authorization": AUTH})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "done"

    # Confirm markdown on disk is correct.
    md_after = (DATA / "notes" / "p199proj" / "ww16.md").read_text(encoding="utf-8")
    parent_line = next(l for l in md_after.splitlines() if parent_id in l)
    assert "#status done" in parent_line, parent_line
    for ref in ars:
        ar_line = next(l for l in md_after.splitlines() if ref in l)
        assert "#status done" in ar_line, ar_line

    # Force a full reindex (simulates page refresh after watcher fires).
    with _S(get_engine()) as s:
        reindex_file(_P(DATA / "notes" / "p199proj" / "ww16.md"), s)
        s.commit()

    # All three should still report `done` from the API.
    for ref in [parent_id] + ars:
        r = client.get(f"/api/tasks/{ref}", headers={"Authorization": AUTH})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "done", f"{ref} reverted: {r.json()}"


# ---------------------------------------------------------------------------
# Password policy (#238) — short passwords must be rejected.
# ---------------------------------------------------------------------------

def test_password_policy_rejects_short_on_admin_create(client):
    """POST /admin/users with a < 8-char password → 400."""
    r = client.post(
        "/api/admin/users",
        json={"name": "weakcreate", "password": "abc", "is_admin": False},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 400, r.text
    assert "8" in r.json()["detail"] or "characters" in r.json()["detail"].lower()


def test_password_policy_rejects_short_on_admin_patch(client):
    """PATCH /admin/users/{name} with a 1-char password → 400."""
    _create_user(client, "weakpatch")
    r = client.patch(
        "/api/admin/users/weakpatch",
        json={"password": "a"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 400, r.text
    # The original password is unchanged — the default _create_user pw
    # still works.
    orig_auth = "Basic " + base64.b64encode(b"weakpatch:password1").decode()
    r = client.get("/api/me", headers={"Authorization": orig_auth})
    assert r.status_code == 200


def test_password_policy_rejects_short_on_self_change(client):
    """PATCH /me/password with a short new_password → 400."""
    _create_user(client, "weakself", "originalpw")
    user_auth = "Basic " + base64.b64encode(b"weakself:originalpw").decode()
    r = client.patch(
        "/api/me/password",
        json={"current_password": "originalpw", "new_password": "abc"},
        headers={"Authorization": user_auth},
    )
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# #237 — GET /api/tasks must honour repeated query keys for list filters.
# Regression: previously params like `?not_owner=A&not_owner=B` were typed
# as `Optional[str]` so FastAPI silently kept only the last value, defeating
# the multi-value exclusion.
# ---------------------------------------------------------------------------

def test_list_tasks_accepts_repeated_query_keys_for_filters(client):
    """Repeated `owner=` keys should be intersected (OR-of-tokens),
    not silently coalesced to the last value."""
    md = (
        "# proj\n"
        "## Repeated query filter\n"
        "- !task Alpha @alice #id=T-RQK001\n"
        "- !task Beta  @bob   #id=T-RQK002\n"
        "- !task Gamma @carol #id=T-RQK003\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "rqk.md", "body_md": md},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    # Repeated keys: should match alice OR bob (both), not just bob.
    r = client.get(
        "/api/tasks?owner=alice&owner=bob",
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200
    titles = sorted(t["title"] for t in r.json()["tasks"]
                    if t["title"] in {"Alpha", "Beta", "Gamma"})
    assert titles == ["Alpha", "Beta"], titles

    # Equivalent comma-form still works (back-compat).
    r = client.get(
        "/api/tasks?owner=alice,bob",
        headers={"Authorization": AUTH},
    )
    titles = sorted(t["title"] for t in r.json()["tasks"]
                    if t["title"] in {"Alpha", "Beta", "Gamma"})
    assert titles == ["Alpha", "Beta"], titles

    # Negations: ?not_owner=alice&not_owner=bob must drop both.
    r = client.get(
        "/api/tasks?not_owner=alice&not_owner=bob",
        headers={"Authorization": AUTH},
    )
    titles = sorted(t["title"] for t in r.json()["tasks"]
                    if t["title"] in {"Alpha", "Beta", "Gamma"})
    assert titles == ["Gamma"], titles


# ---------------------------------------------------------------------------
# #239 — popover PATCH must self-heal when Task.line has gone stale.
# ---------------------------------------------------------------------------

def test_patch_task_self_heals_stale_line(client, monkeypatch):
    """If Task.line is stale (drifted vs disk), PATCH must re-anchor by
    task_uuid before mutating the file."""
    md = (
        "# stale\n"
        "## Self-heal probe\n"
        "!task Anchor task @alice #id T-STALE1\n"
        "!task Drifted task @alice #id T-STALE2\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "stale_line.md", "body_md": md},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    from app.config import settings as _s
    from app.db import get_engine
    from sqlmodel import Session as _Session, select as _select
    from app.models import Task as _Task
    from sqlalchemy import text as _sa_text
    eng = get_engine()
    with _Session(eng) as session:
        t1 = session.exec(_select(_Task).where(_Task.task_uuid == "T-STALE1")).first()
        t2 = session.exec(_select(_Task).where(_Task.task_uuid == "T-STALE2")).first()
        assert t1.line != t2.line
        good_line = t2.line
        bad_line = t1.line
    # Disable the awatch-driven reindex so it doesn't quietly heal the
    # corruption we're about to plant. We want patch_task itself to do
    # the self-heal, not the watcher.
    import app.indexer as _idx
    monkeypatch.setattr(_idx, "reindex_file", lambda *a, **k: None)
    from app import api as _api_pkg
    monkeypatch.setattr(_api_pkg, "reindex_file", lambda *a, **k: None, raising=False)
    with _Session(eng) as session:
        session.exec(_sa_text("UPDATE task SET line = :ln WHERE task_uuid = 'T-STALE2'").bindparams(ln=bad_line))
        session.commit()
    r = client.patch(
        "/api/tasks/T-STALE2",
        json={"status": "done"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    full = _s.notes_dir / "stale_line.md"
    text = full.read_text(encoding="utf-8")
    lines = text.splitlines()
    t1_lines = [ln for ln in lines if "T-STALE1" in ln]
    t2_lines = [ln for ln in lines if "T-STALE2" in ln]
    assert len(t1_lines) == 1 and len(t2_lines) == 1
    assert "#status done" in t2_lines[0], (
        f"T-STALE2 line missing #status done: {t2_lines[0]!r}"
    )
    assert "#status done" not in t1_lines[0], (
        f"T-STALE1 line should be untouched but got: {t1_lines[0]!r}"
    )
    with _Session(eng) as session:
        t2 = session.exec(_select(_Task).where(_Task.task_uuid == "T-STALE2")).first()
        assert t2.status == "done"


def test_patch_task_409_when_uuid_missing_from_disk(client):
    """If the task's #id token has been removed from the file out-of-band,
    PATCH must fail closed rather than guess."""
    md = (
        "# missing\n"
        "## Vanish probe\n"
        "!task Vanishing task @alice #id T-VAN001\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "vanish.md", "body_md": md},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200

    from app.config import settings as _s
    full = _s.notes_dir / "vanish.md"
    full.write_text(
        full.read_text(encoding="utf-8").replace("#id T-VAN001", "").rstrip() + "\n",
        encoding="utf-8",
    )

    r = client.patch(
        "/api/tasks/T-VAN001",
        json={"status": "done"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 409, r.text
    detail = r.json().get("detail", {})
    assert isinstance(detail, dict) and detail.get("error") == "stale_task"


# ---------------------------------------------------------------------------
# #251 — full per-field audit trail for task mutations.
# ---------------------------------------------------------------------------

def _audit_kinds_for(client, ref):
    r = client.get(f"/api/tasks/{ref}/activity", headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    return [(ev["kind"], ev.get("meta", {})) for ev in r.json()]


def test_audit_per_field_events_emitted(client):
    md = (
        "# audit\n"
        "## Audit probe\n"
        "!task Audit task @alice #id T-AUDIT1\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "audit.md", "body_md": md},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    # Owners change
    r = client.patch(
        "/api/tasks/T-AUDIT1",
        json={"owners": ["alice", "bob"]},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text
    # Priority change
    assert client.patch(
        "/api/tasks/T-AUDIT1", json={"priority": "P1"},
        headers={"Authorization": AUTH},
    ).status_code == 200
    # ETA change
    assert client.patch(
        "/api/tasks/T-AUDIT1", json={"eta": "ww30"},
        headers={"Authorization": AUTH},
    ).status_code == 200
    # Features change
    assert client.patch(
        "/api/tasks/T-AUDIT1", json={"features": ["alpha"]},
        headers={"Authorization": AUTH},
    ).status_code == 200
    # Note add
    assert client.patch(
        "/api/tasks/T-AUDIT1", json={"add_note": "first journal entry"},
        headers={"Authorization": AUTH},
    ).status_code == 200
    # Status change (already covered by older tests, but include here)
    assert client.patch(
        "/api/tasks/T-AUDIT1", json={"status": "done"},
        headers={"Authorization": AUTH},
    ).status_code == 200

    events = _audit_kinds_for(client, "T-AUDIT1")
    kinds = {k for k, _ in events}
    assert "task.owners.set" in kinds
    assert "task.priority.set" in kinds
    assert "task.eta.set" in kinds
    assert "task.features.set" in kinds
    assert "task.note.added" in kinds
    assert "task.status.set" in kinds

    # Verify meta payloads.
    by_kind = {k: m for k, m in events}
    assert by_kind["task.priority.set"]["to"] == "P1"
    assert by_kind["task.eta.set"]["to"] == "ww30"
    assert by_kind["task.note.added"]["text"] == "first journal entry"
    assert sorted(by_kind["task.owners.set"]["to"]) == ["alice", "bob"]
    assert by_kind["task.features.set"]["to"] == ["alpha"]


def test_audit_no_event_when_value_unchanged(client):
    md = (
        "# audit2\n"
        "!task Same value task @alice #priority P2 #id T-AUDIT2\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "audit2.md", "body_md": md},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    # PATCH priority to the SAME value as on disk → no event.
    r = client.patch(
        "/api/tasks/T-AUDIT2", json={"priority": "P2"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    # PATCH owners to same set (just reordered) → no event.
    r = client.patch(
        "/api/tasks/T-AUDIT2", json={"owners": ["alice"]},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    events = _audit_kinds_for(client, "T-AUDIT2")
    kinds = [k for k, _ in events]
    assert "task.priority.set" not in kinds, events
    assert "task.owners.set" not in kinds, events


def test_audit_delete_event_emitted(client):
    md = (
        "# audit3\n"
        "!task Doomed task @alice #id T-AUDIT3\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "audit3.md", "body_md": md},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    # Capture the events scoped to the uuid before deletion (the row
    # itself goes away, but ActivityEvent rows persist by ref string).
    r = client.delete(
        "/api/tasks/T-AUDIT3", headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    # Read activity by ref directly via /api/me/activity since the task
    # is gone (so /api/tasks/T-AUDIT3/activity would 404).
    r = client.get(
        "/api/me/activity?kind=task.deleted",
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text
    rows = [ev for ev in r.json() if ev["ref"] == "T-AUDIT3"]
    assert len(rows) == 1, rows
    meta = rows[0]["meta"]
    assert meta["title"] == "Doomed task"
    assert meta["note_path"] == "audit3.md"


# ── Rollover / archive (single-active-file model, #251 follow-up) ─────────


def test_rollover_moves_open_canonical_archives_source(client):
    """POST /notes/next-week moves open top-level !task declarations
    canonically into the next ww file, archives the source under
    sibling _archive/<basename>, and flips Note.archived on the old row.
    """
    md = (
        "# Weekly ww40\n"
        "!task Carry over @alice #id T-ROLL01 #status todo\n"
        "!task Already done @alice #id T-ROLL02 #status done\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "ww40.md", "body_md": md},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/notes/next-week",
        json={"path": "ww40.md"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["from_ww"] == 40
    assert out["to_ww"] == 41
    assert out["path"] == "ww41.md"
    assert out["archived_path"] == "_archive/ww40.md"
    assert out["moved_count"] == 1  # only the open task moved

    # Source file is gone from disk; archive + new file exist.
    assert not (DATA / "notes" / "ww40.md").exists()
    new_md = (DATA / "notes" / "ww41.md").read_text(encoding="utf-8")
    archived_md = (DATA / "notes" / "_archive" / "ww40.md").read_text(encoding="utf-8")
    # New file: open task is canonical.
    assert "!task" in new_md
    assert "Carry over" in new_md
    assert "T-ROLL01" in new_md
    assert "Already done" not in new_md  # done top-level dropped
    assert "#task T-" not in new_md  # no ref rows in active week
    # Archive: open task became a ref row, done task stays canonical.
    assert "#task T-ROLL01" in archived_md
    assert "!task Carry over" not in archived_md
    assert "T-ROLL02" in archived_md
    assert "Already done" in archived_md


def test_rollover_relinks_task_note_id(client):
    """After rollover, the moved Task row's note_id points at the new
    note (not the archived one) so popover writes target the active
    week's file."""
    md = (
        "# WW42\n"
        "!task Hello @alice #id T-ROLL10 #status todo\n"
    )
    client.put(
        "/api/notes",
        json={"path": "ww42.md", "body_md": md},
        headers={"Authorization": AUTH},
    )
    r = client.post(
        "/api/notes/next-week",
        json={"path": "ww42.md"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text
    new_note_id = r.json()["id"]

    # GET the task — its note_id should be the new file's note id.
    r = client.get("/api/tasks/T-ROLL10", headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    assert r.json()["note_id"] == new_note_id


def test_rollover_old_note_flagged_archived_and_hidden_from_tree(client):
    """The old Note row is flipped to archived=True and hidden from the
    default /api/tree view; ?include_archived=1 surfaces it."""
    md = "# WW43\n!task Some task #id T-ROLL20 #status todo\n"
    client.put(
        "/api/notes",
        json={"path": "ww43.md", "body_md": md},
        headers={"Authorization": AUTH},
    )
    r = client.post(
        "/api/notes/next-week",
        json={"path": "ww43.md"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    # Default /api/tree does NOT include the archived note.
    r = client.get("/api/tree", headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    paths_default: set[str] = set()
    for grp in r.json():
        for n in grp.get("notes", []):
            paths_default.add(n["path"])
    assert "_archive/ww43.md" not in paths_default
    assert "ww44.md" in paths_default

    # include_archived=1 shows it with archived flag set.
    r = client.get(
        "/api/tree?include_archived=1",
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text
    archived_entries: list[dict] = []
    for grp in r.json():
        for n in grp.get("notes", []):
            if n["path"] == "_archive/ww43.md":
                archived_entries.append(n)
    assert len(archived_entries) == 1
    assert archived_entries[0]["archived"] is True


def test_archived_note_patch_denied_for_non_manager(client):
    """A member who owns a task in an archived note still cannot patch
    it — archived notes are manager-only."""
    # Set up a project + a member who owns a task.
    client.post(
        "/api/projects",
        json={"name": "rollproj"},
        headers={"Authorization": AUTH},
    )
    _create_user(client, "rollmember", "pw12345pw")
    client.put(
        f"/api/projects/rollproj/members/rollmember",
        json={"user_name": "rollmember", "role": "member"},
        headers={"Authorization": AUTH},
    )
    # Note in the project, with a task @rollmember owns.
    md = (
        "# rollproj ww50\n"
        "!task Member task @rollmember #id T-ROLL50 #status todo\n"
    )
    client.put(
        "/api/notes",
        json={"path": "rollproj/ww50.md", "body_md": md},
        headers={"Authorization": AUTH},
    )
    # Roll forward.
    r = client.post(
        "/api/notes/next-week",
        json={"path": "rollproj/ww50.md"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    # The moved canonical task is now in rollproj/ww51.md → patches there
    # by the owner-member should still succeed.
    member_auth = "Basic " + base64.b64encode(b"rollmember:pw12345pw").decode()
    r = client.patch(
        "/api/tasks/T-ROLL50",
        json={"status": "in-progress"},
        headers={"Authorization": member_auth},
    )
    assert r.status_code == 200, r.text  # active week → owner can edit

    # Now flip the *active* note to archived directly in DB to simulate a
    # second rollover and confirm patches against an archived note are
    # 403 for members but 200 for managers/admin.
    from app.db import session_scope
    from app.models import Note as NoteModel
    from sqlmodel import select
    with session_scope() as s:
        n = s.exec(select(NoteModel).where(NoteModel.path == "rollproj/ww51.md")).first()
        assert n is not None
        n.archived = True
        s.add(n)

    # Owner-member can no longer patch.
    r = client.patch(
        "/api/tasks/T-ROLL50",
        json={"status": "done"},
        headers={"Authorization": member_auth},
    )
    assert r.status_code == 403, r.text
    assert "archived" in r.json()["detail"].lower()

    # Admin (manager) still can.
    r = client.patch(
        "/api/tasks/T-ROLL50",
        json={"status": "done"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text


def test_resolve_destination_skips_archived(client):
    """POST /api/tasks with project= must land on the live week, not on
    a more-recently-modified archived note."""
    client.post(
        "/api/projects",
        json={"name": "destproj"},
        headers={"Authorization": AUTH},
    )
    client.put(
        "/api/notes",
        json={"path": "destproj/ww60.md",
              "body_md": "# destproj ww60\n!task Seed #id T-DEST01 #status todo\n"},
        headers={"Authorization": AUTH},
    )
    r = client.post(
        "/api/notes/next-week",
        json={"path": "destproj/ww60.md"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    # Touch the archive's mtime so it's "most recently modified" — to
    # force the old (broken) resolver to pick it.  The new resolver
    # should still pick ww61.md (the live note).
    arch = DATA / "notes" / "destproj" / "_archive" / "ww60.md"
    import time
    later = time.time() + 100
    os.utime(arch, (later, later))

    r = client.post(
        "/api/tasks",
        json={
            "title": "Brand new task",
            "project": "destproj",
            "status": "todo",
            "owners": ["admin"],
        },
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 201, r.text
    new_path = r.json()["note_path"]
    assert new_path == "destproj/ww61.md"


def test_rollover_rejects_target_collision(client):
    """A second roll with overwrite=False must 409 when the target ww
    file (or archive) already exists."""
    md = "# WW80\n!task Solo80 #id T-ROLL80 #status todo\n"
    client.put(
        "/api/notes",
        json={"path": "ww80.md", "body_md": md},
        headers={"Authorization": AUTH},
    )
    r = client.post(
        "/api/notes/next-week",
        json={"path": "ww80.md"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    # Recreate the source with DIFFERENT uuids so reindex doesn't trip
    # the global Task.task_uuid UNIQUE constraint.  Then try to roll —
    # ww81.md already exists from the first roll → 409.
    md2 = "# WW80\n!task SoloAgain #id T-ROLL80B #status todo\n"
    client.put(
        "/api/notes",
        json={"path": "ww80.md", "body_md": md2},
        headers={"Authorization": AUTH},
    )
    r = client.post(
        "/api/notes/next-week",
        json={"path": "ww80.md"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# Design 8d: two-axis etag (prose + tasks)
# ---------------------------------------------------------------------------

def _put_8d(client, path: str, body_md: str, *, if_match: str | None = None,
             if_match_prose: str | None = None) -> object:
    payload = {"path": path, "body_md": body_md}
    if if_match_prose is not None:
        payload["if_match_prose"] = if_match_prose
    headers = {"Authorization": AUTH}
    if if_match is not None:
        headers["If-Match"] = if_match
    return client.put("/api/notes", json=payload, headers=headers)


def test_8d_get_endpoints_return_split_etags(client):
    """``GET /notes/etag`` and ``GET /notes/{id}`` both expose
    ``prose_etag`` + ``tasks_etag`` alongside the legacy byte-level etag.

    Without these axes the editor's 5 s freshness poll cannot tell
    "popover patched a task line" apart from "someone else edited the
    prose underneath you", and every popover write triggers a false
    conflict banner.
    """
    body = (
        "# 8d header\n\n"
        "@admin\n\n"
        "- !task Foo #id T-8DAA01 #owner @admin #status todo\n"
        "- !task Bar #id T-8DAA02 #owner @admin #status todo\n\n"
        "free prose tail\n"
    )
    r = _put_8d(client, "8d_split.md", body)
    assert r.status_code == 200, r.text
    note_id = r.json()["id"]
    assert "prose_etag" in r.json() and "tasks_etag" in r.json()

    r = client.get(f"/api/notes/{note_id}", headers={"Authorization": AUTH})
    assert r.status_code == 200
    assert "prose_etag" in r.json() and "tasks_etag" in r.json()
    assert r.json()["prose_etag"] != r.json()["tasks_etag"]

    r = client.get("/api/notes/etag?path=8d_split.md", headers={"Authorization": AUTH})
    assert r.status_code == 200
    assert "prose_etag" in r.json() and "tasks_etag" in r.json()


def test_8d_typing_prose_while_popover_patches_task_no_conflict(client):
    """Reproduces the user-visible "constant disk-changed" UX bug.

    Sequence:
      1. User opens a note (captures prose_etag P0, byte etag E0).
      2. While they're typing, a popover ``PATCH /tasks/...`` rewrites
         a task line on disk. Byte etag becomes E1, prose etag stays P0.
      3. User saves their prose edits, sending ``if_match_prose: P0``.

    Pre-8d: step 3 would 409 stale_write (E0 != E1) and the user would
    be forced to reload, losing in-progress edits or accept a confirm
    dialog.

    Post-8d: step 3 succeeds (200) because the prose axis still
    matches, and the popover task change is preserved via
    ``merge_with_disk_tasks``.
    """
    body = (
        "# 8d typing\n\n"
        "@admin\n\n"
        "- !task Touch me #id T-8DBB01 #owner @admin #status todo\n\n"
        "original prose\n"
    )
    r = _put_8d(client, "8d_typing.md", body)
    assert r.status_code == 200, r.text
    p0 = r.json()["prose_etag"]

    # Find the task in the DB-backed task list and patch it via the
    # structured endpoint — same path the popover uses.
    r = client.get("/api/tasks?q=Touch", headers={"Authorization": AUTH})
    tasks = r.json()["tasks"]
    assert tasks, r.text
    tid = tasks[0]["id"]
    r = client.patch(
        f"/api/tasks/{tid}",
        json={"status": "in-progress"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text

    # User's editor now writes their prose edit. Their copy of the task
    # line is still the pre-popover version (status: todo) — that's the
    # exact stale-task scenario 8d targets.
    edited = (
        "# 8d typing — EDITED HEADING\n\n"
        "@admin\n\n"
        "- !task Touch me #id T-8DBB01 #owner @admin #status todo\n\n"
        "original prose, plus a typed-in line\n"
    )
    r = _put_8d(client, "8d_typing.md", edited, if_match_prose=p0)
    assert r.status_code == 200, r.text
    saved = r.json()
    # Re-read to confirm the merged file: prose is the editor's, task
    # status is the popover's.
    r = client.get("/api/notes/etag?path=8d_typing.md", headers={"Authorization": AUTH})
    full = client.get(
        f"/api/notes/{saved['id']}", headers={"Authorization": AUTH}
    ).json()["body_md"]
    assert "EDITED HEADING" in full, full
    assert "typed-in line" in full, full
    assert "in-progress" in full, full


def test_8d_genuine_prose_conflict_returns_stale_prose(client):
    """If the prose axis itself drifted (e.g. another user edited the
    free text on disk), 8d still surfaces a 409 — this time with
    ``error: 'stale_prose'`` and the new ``current_prose_etag`` /
    ``current_tasks_etag`` so the frontend can populate the recovery
    dialog from the same response.
    """
    body = (
        "# 8d conflict\n\n"
        "@admin\n\n"
        "- !task X #id T-8DCC01 #owner @admin #status todo\n\n"
        "version A\n"
    )
    r = _put_8d(client, "8d_conflict.md", body)
    assert r.status_code == 200, r.text
    p0 = r.json()["prose_etag"]

    # Simulate another writer touching the prose axis.
    body_b = body.replace("version A", "version B")
    r = _put_8d(client, "8d_conflict.md", body_b)
    assert r.status_code == 200, r.text

    # Original client still thinks prose is at P0 — server must reject.
    body_c = body.replace("version A", "version C")
    r = _put_8d(client, "8d_conflict.md", body_c, if_match_prose=p0)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "stale_prose"
    assert "current_prose_etag" in detail
    assert "current_tasks_etag" in detail
    assert detail["current_content"] is not None


# ── #258: done_scope (active vs all) ──────────────────────────────────────
def _make_archived_done_fixture(client):
    """Build a project with one active note (ww70 — has one open + one done
    task) and one archived note (ww69 — flagged Note.archived=True, holds
    one done task that should be hidden by default).

    Returns (active_uuid_open, active_uuid_done, archived_uuid_done).
    """
    from app.db import session_scope
    from app.models import Note
    from sqlmodel import select as _select

    client.post("/api/projects", json={"name": "donescope"},
                headers={"Authorization": AUTH})
    client.put("/api/notes",
               json={"path": "donescope/ww70.md",
                     "body_md": (
                         "# donescope ww70\n"
                         "@admin\n"
                         "\t!task #id T-DSACTOPEN Active open #status todo\n"
                         "\t!task #id T-DSACTDONE Active done #status done\n"
                     )},
               headers={"Authorization": AUTH})
    client.put("/api/notes",
               json={"path": "donescope/_archive/ww69.md",
                     "body_md": (
                         "# donescope ww69 (archived)\n"
                         "@admin\n"
                         "\t!task #id T-DSARCDONE Archived done #status done\n"
                     )},
               headers={"Authorization": AUTH})
    with session_scope() as s:
        n = s.exec(_select(Note).where(
            Note.path == "donescope/_archive/ww69.md")).one()
        n.archived = True
        s.add(n)
    return ("T-DSACTOPEN", "T-DSACTDONE", "T-DSARCDONE")


def test_done_scope_active_hides_archived_done(client):
    """#258: GET /tasks?done_scope=active (default) excludes done tasks
    whose source Note is archived. Open tasks in archived notes are
    unaffected — only the done set is scoped."""
    open_uuid, act_done_uuid, arc_done_uuid = _make_archived_done_fixture(client)

    r = client.get("/api/tasks?done_scope=active",
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    uuids = {t["task_uuid"] for t in r.json()["tasks"]}
    assert open_uuid in uuids, "active-week open task must remain visible"
    assert act_done_uuid in uuids, "active-week done task must remain visible"
    assert arc_done_uuid not in uuids, (
        "archived-week done task must be hidden by done_scope=active "
        "(this is the whole point of #258)"
    )


def test_done_scope_default_is_active(client):
    """No explicit done_scope → behave as 'active' (the new default)."""
    _, _, arc_done_uuid = _make_archived_done_fixture(client)
    r = client.get("/api/tasks", headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    uuids = {t["task_uuid"] for t in r.json()["tasks"]}
    assert arc_done_uuid not in uuids


def test_done_scope_all_includes_archived_done(client):
    """done_scope=all preserves the historical behaviour — archived-week
    done tasks are returned alongside active-week ones."""
    open_uuid, act_done_uuid, arc_done_uuid = _make_archived_done_fixture(client)
    r = client.get("/api/tasks?done_scope=all", headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    uuids = {t["task_uuid"] for t in r.json()["tasks"]}
    assert open_uuid in uuids
    assert act_done_uuid in uuids
    assert arc_done_uuid in uuids


def test_done_scope_does_not_filter_open_tasks_in_archived_notes(client):
    """Sanity: only `status = done` is scoped. An open task accidentally
    left in an archived note must still be visible regardless of scope —
    those tasks need attention, not hiding."""
    from app.db import session_scope
    from app.models import Note
    from sqlmodel import select as _select

    client.post("/api/projects", json={"name": "dsopenarc"},
                headers={"Authorization": AUTH})
    client.put("/api/notes",
               json={"path": "dsopenarc/ww50.md",
                     "body_md": "# dsopenarc ww50\n@admin\n\t!task #id T-DSACTIVE seed #status todo\n"},
               headers={"Authorization": AUTH})
    client.put("/api/notes",
               json={"path": "dsopenarc/_archive/ww49.md",
                     "body_md": (
                         "# dsopenarc ww49 (archived)\n"
                         "@admin\n"
                         "\t!task #id T-DSOPENARC stuck open #status in-progress\n"
                     )},
               headers={"Authorization": AUTH})
    with session_scope() as s:
        n = s.exec(_select(Note).where(
            Note.path == "dsopenarc/_archive/ww49.md")).one()
        n.archived = True
        s.add(n)

    r = client.get("/api/tasks?done_scope=active",
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    uuids = {t["task_uuid"] for t in r.json()["tasks"]}
    assert "T-DSOPENARC" in uuids, (
        "open task in archived note must NOT be hidden by done_scope=active"
    )


def test_done_scope_invalid_value_returns_422(client):
    r = client.get("/api/tasks?done_scope=bogus",
                   headers={"Authorization": AUTH})
    assert r.status_code == 422
    assert "active" in r.json()["detail"]


def test_done_scope_active_composes_with_owner_filter(client):
    """done_scope=active still respects other filters (owner here). The
    archived-done filter is applied as an extra WHERE clause, not as a
    replacement for the existing query plan."""
    open_uuid, act_done_uuid, arc_done_uuid = _make_archived_done_fixture(client)
    r = client.get("/api/tasks?owner=admin&done_scope=active",
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    uuids = {t["task_uuid"] for t in r.json()["tasks"]}
    assert open_uuid in uuids
    assert act_done_uuid in uuids
    assert arc_done_uuid not in uuids

# ── #260: rollover (POST /api/notes/next-week) ───────────────────────────
def test_260_rollover_archive_has_done_blocks_active_drops_done_top_levels(client):
    """Issue #260: the previous indent-unit bug in
    ``_replace_top_level_open_with_refs`` caused every top-level row past
    the first open task to be dropped from the archive (data loss). Post
    fix, top-level done declarations are preserved in the archive while
    being absent from the active week."""
    client.post("/api/projects", json={"name": "rollbug260"},
                headers={"Authorization": AUTH})
    body = (
        "# weekly ww30\n"
        "@admin\n"
        "\t!task #id T-RB260OP Open parent\n"
        "\t\t!AR #id T-RB260OAR open ar #status todo\n"
        "\t\t!AR #id T-RB260DAR1 done ar under open parent #status done\n"
        "\t!task #id T-RB260DTOP Done top-level standalone #status done\n"
        "\t!task #id T-RB260DPAR Done parent with done kids #status done\n"
        "\t\t!AR #id T-RB260DAR2 done ar #status done\n"
    )
    r = client.put("/api/notes",
                   json={"path": "rollbug260/weekly ww30.md", "body_md": body},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    r = client.post("/api/notes/next-week",
                    json={"path": "rollbug260/weekly ww30.md"},
                    headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    new_path = r.json()["path"]
    archive_path = r.json()["archived_path"]
    assert new_path == "rollbug260/weekly ww31.md"

    new_disk = (DATA / "notes" / new_path).read_text(encoding="utf-8")
    arch_disk = (DATA / "notes" / archive_path).read_text(encoding="utf-8")

    # Active week: open parent + all its ARs (open and done) survive;
    # top-level done blocks are gone.
    assert "T-RB260OP" in new_disk
    assert "T-RB260OAR" in new_disk
    assert "T-RB260DAR1" in new_disk, (
        "done AR under open parent should ride forward (#260 follow-up)"
    )
    assert "T-RB260DTOP" not in new_disk
    assert "T-RB260DPAR" not in new_disk

    # Archive: top-level done declarations preserved (Bug #2 fix); open
    # top-level becomes a ref row only.
    assert "#task T-RB260OP" in arch_disk
    assert "T-RB260DAR1" not in arch_disk, (
        "done AR child of open parent must NOT duplicate into archive"
    )
    assert "T-RB260DTOP" in arch_disk, (
        "top-level done declaration was previously swallowed by the "
        "indent-unit bug; archive must preserve it"
    )
    assert "T-RB260DPAR" in arch_disk
    assert "T-RB260DAR2" in arch_disk, (
        "child of canonical-done parent must travel with the parent into archive"
    )


# ---------------------------------------------------------------------------
# PATCH /tasks/{ref} with title (issue #283)
# ---------------------------------------------------------------------------

def test_patch_title_rewrites_declaration_line_on_disk(client):
    """PATCH {title: ...} must rewrite the task-declaration line while
    preserving indent, keyword, #id, and every trailing attr."""
    body = (
        "# Sprint\n"
        "!task #id T-TITLE01 Original title #priority P0 @alice\n"
    )
    r = client.put("/api/notes",
                   json={"path": "title-basic.md", "body_md": body},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    r = client.patch("/api/tasks/T-TITLE01",
                     json={"title": "Renamed sharply"},
                     headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Renamed sharply"

    on_disk = (DATA / "notes" / "title-basic.md").read_text(encoding="utf-8")
    assert "!task #id T-TITLE01 Renamed sharply #priority P0 @alice" in on_disk
    # Everything else on the line is preserved
    assert "Original title" not in on_disk


def test_patch_title_indented_ar(client):
    body = (
        "# Sprint\n"
        "!task #id T-TITLE02 Parent task #status todo\n"
        "\t!AR #id T-TITLE02AR draft plan #status todo @alice\n"
    )
    r = client.put("/api/notes",
                   json={"path": "title-ar.md", "body_md": body},
                   headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    r = client.patch("/api/tasks/T-TITLE02AR",
                     json={"title": "draft rollout plan"},
                     headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text

    on_disk = (DATA / "notes" / "title-ar.md").read_text(encoding="utf-8")
    # Indent (normalized to tab by the write path) + keyword + id preserved;
    # title replaced; trailing attrs preserved.
    assert "\t!AR #id T-TITLE02AR draft rollout plan" in on_disk
    assert "#status todo" in on_disk
    # Owner mention may render as `@alice` or `#owner alice` depending on the
    # write path's normalization — either shape counts.
    assert "@alice" in on_disk or "#owner alice" in on_disk


def test_patch_title_blank_rejected(client):
    body = "# S\n!task #id T-TITLE03 Foo #status todo\n"
    client.put("/api/notes",
               json={"path": "title-blank.md", "body_md": body},
               headers={"Authorization": AUTH})
    r = client.patch("/api/tasks/T-TITLE03",
                     json={"title": "   "},
                     headers={"Authorization": AUTH})
    assert r.status_code == 400, r.text


def test_patch_title_index_reflects_new_title(client):
    body = "# S\n!task #id T-TITLE04 Old #status todo\n"
    client.put("/api/notes",
               json={"path": "title-idx.md", "body_md": body},
               headers={"Authorization": AUTH})
    r = client.patch("/api/tasks/T-TITLE04",
                     json={"title": "Brand new title"},
                     headers={"Authorization": AUTH})
    assert r.status_code == 200

    # Re-query the task; the index must show the new title.
    r = client.get("/api/tasks?q=Brand+new", headers={"Authorization": AUTH})
    hits = [t for t in r.json()["tasks"] if t.get("task_uuid") == "T-TITLE04"]
    assert len(hits) == 1
    assert hits[0]["title"] == "Brand new title"


def test_patch_title_combined_with_status(client):
    """Title patch composes cleanly with a status patch in the same call.
    Uses an owner via #owner (not `@` mention) to sidestep an unrelated
    `update_task_status` quirk with trailing `@user` mentions."""
    body = "# S\n!task #id T-TITLE05 Do stuff #status todo #owner alice\n"
    client.put("/api/notes",
               json={"path": "title-combo.md", "body_md": body},
               headers={"Authorization": AUTH})
    r = client.patch("/api/tasks/T-TITLE05",
                     json={"title": "Do better stuff", "status": "in-progress"},
                     headers={"Authorization": AUTH})
    assert r.status_code == 200

    on_disk = (DATA / "notes" / "title-combo.md").read_text(encoding="utf-8")
    assert "!task #id T-TITLE05 Do better stuff" in on_disk
    assert "#status in-progress" in on_disk
    assert "#owner alice" in on_disk


def test_patch_title_null_leaves_title_alone(client):
    """PATCH with no title field must not touch the title."""
    body = "# S\n!task #id T-TITLE06 Stable title #status todo\n"
    client.put("/api/notes",
               json={"path": "title-null.md", "body_md": body},
               headers={"Authorization": AUTH})
    r = client.patch("/api/tasks/T-TITLE06",
                     json={"status": "done"},
                     headers={"Authorization": AUTH})
    assert r.status_code == 200
    assert r.json()["title"] == "Stable title"


def test_patch_title_noop_when_unchanged(client):
    """Sending the same title back should be a cheap no-op (no error, no
    unnecessary rewrite of the file)."""
    body = "# S\n!task #id T-TITLE07 Same title #status todo\n"
    client.put("/api/notes",
               json={"path": "title-noop.md", "body_md": body},
               headers={"Authorization": AUTH})
    before = (DATA / "notes" / "title-noop.md").read_text(encoding="utf-8")
    r = client.patch("/api/tasks/T-TITLE07",
                     json={"title": "Same title"},
                     headers={"Authorization": AUTH})
    assert r.status_code == 200
    after = (DATA / "notes" / "title-noop.md").read_text(encoding="utf-8")
    assert before == after
