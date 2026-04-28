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
