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

def _create_user(client, name: str, password: str = "pw") -> None:
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
    _create_user(client, "pw-user", "oldpass")
    user_auth = "Basic " + base64.b64encode(b"pw-user:oldpass").decode()

    # Wrong current password → 403.
    r = client.patch(
        "/api/me/password",
        json={"current_password": "wrongpass", "new_password": "newpass"},
        headers={"Authorization": user_auth},
    )
    assert r.status_code == 403

    # Empty new password → 400.
    r = client.patch(
        "/api/me/password",
        json={"current_password": "oldpass", "new_password": ""},
        headers={"Authorization": user_auth},
    )
    assert r.status_code == 400

    # Correct change → 200.
    r = client.patch(
        "/api/me/password",
        json={"current_password": "oldpass", "new_password": "newpass"},
        headers={"Authorization": user_auth},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    # Old credentials now rejected.
    r = client.get("/api/me", headers={"Authorization": user_auth})
    assert r.status_code == 401

    # New credentials work.
    new_auth = "Basic " + base64.b64encode(b"pw-user:newpass").decode()
    r = client.get("/api/me", headers={"Authorization": new_auth})
    assert r.status_code == 200
    assert r.json()["name"] == "pw-user"


def test_admin_can_reset_any_password(client):
    """Admin can reset another user's password via PATCH /admin/users/{name}."""
    _create_user(client, "reset-target", "original")

    # Admin resets to "forced".
    r = client.patch(
        "/api/admin/users/reset-target",
        json={"password": "forced"},
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200

    # New password works.
    new_auth = "Basic " + base64.b64encode(b"reset-target:forced").decode()
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
