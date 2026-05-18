"""Owner-token canonicalization tests (#174).

Verifies that varied owner spellings (canonical idsid, friendly
first name, friendly last name, mixed case) collapse to a single
canonical identity in the index, so My Tasks / owner filters
group correctly.
"""
from __future__ import annotations

import json
from pathlib import Path

# Set env BEFORE app imports.
import tests.api.test_api as _test_api  # noqa: F401  side-effect: env + client
from tests.api.test_api import AUTH, client  # noqa: E402

import pytest  # noqa: E402

from app.owner_normalize import canonical_idsid  # noqa: E402
from app.phonebook import reset_phonebook_for_test  # noqa: E402


@pytest.fixture
def curated_pb(tmp_path: Path):
    """Replace the autouse-empty phonebook with a richer one for these
    tests. Restored automatically by the package-level autouse fixture."""
    p = tmp_path / "phonebook.json"
    p.write_text(json.dumps({
        "nsaddaga": {
            "idsid": "nsaddaga",
            "display": "Prasad Addagarla",
            "email": "prasad.addagarla@intel.com",
            "aliases": ["prasad", "addagarla", "prasad addagarla"],
        },
        "pkumar2": {
            "idsid": "pkumar2",
            "display": "Prasad Kumar",
            "email": "prasad.kumar@intel.com",
            "aliases": ["prasad"],  # collides on the friendly name
        },
        "ahuman": {
            "idsid": "ahuman",
            "display": "Alice Human",
            "email": "alice.human@intel.com",
            "aliases": ["alice"],
        },
    }), encoding="utf-8")
    reset_phonebook_for_test(p)
    yield p


def test_canonical_idsid_resolved(curated_pb):
    """All friendly variants map to the same canonical idsid."""
    for tok in ("@nsaddaga", "nsaddaga", "@addagarla", "addagarla",
                "@Addagarla", "Prasad Addagarla", "prasad addagarla",
                "PRASAD ADDAGARLA"):
        name, status = canonical_idsid(tok)
        assert (name, status) == ("nsaddaga", "resolved"), tok


def test_canonical_idsid_ambiguous_preserves_input(curated_pb):
    """Friendly name shared by two people stays raw, status=ambiguous."""
    name, status = canonical_idsid("@Prasad")
    assert status == "ambiguous"
    # Input is preserved (sans leading @ / whitespace).
    assert name == "Prasad"


def test_canonical_idsid_unresolved_preserves_input(curated_pb):
    name, status = canonical_idsid("@Bob")
    assert (name, status) == ("Bob", "unresolved")


def test_canonical_idsid_empty():
    assert canonical_idsid("") == ("", "unresolved")
    assert canonical_idsid("@") == ("", "unresolved")
    assert canonical_idsid("   @   ") == ("", "unresolved")


def test_canonical_idsid_email_resolves(curated_pb):
    name, status = canonical_idsid("prasad.addagarla@intel.com")
    assert (name, status) == ("nsaddaga", "resolved")


def test_indexer_collapses_owner_aliases(curated_pb, client):
    """End-to-end: write a note with three spellings of the same person.
    The index must store exactly one User row and three TaskOwner edges
    pointing at it. The ?owner=nsaddaga filter returns all three tasks."""
    r = client.put("/api/notes", json={
        "path": "alias174/wk01.md",
        "body_md":
            "# Aliases\n"
            "- !task Owner174A @nsaddaga\n"
            "- !task Owner174B @Addagarla\n"
            "- !task Owner174C #owner \"prasad addagarla\"\n",
    }, headers={"Authorization": AUTH})
    assert r.status_code in (200, 201), r.text
    # Filter by canonical idsid — must hit all three tasks.
    r = client.get("/api/tasks?owner=nsaddaga", headers={"Authorization": AUTH})
    assert r.status_code == 200
    tasks = r.json()["tasks"]
    titles = sorted(t["title"] for t in tasks if t.get("title", "").startswith("Owner174"))
    assert titles == ["Owner174A", "Owner174B", "Owner174C"], titles
    # Filtering by a curated friendly spelling now ALSO returns the
    # canonical tasks (#174 follow-up): the API expands aliases via the
    # phonebook before querying so 'My Tasks' for a user whose login
    # username differs from their canonical idsid still works.
    r = client.get("/api/tasks?owner=Addagarla", headers={"Authorization": AUTH})
    assert r.status_code == 200
    alias_titles = sorted(
        t["title"] for t in r.json()["tasks"]
        if t.get("title", "").startswith("Owner174")
    )
    assert alias_titles == ["Owner174A", "Owner174B", "Owner174C"], alias_titles
    # Database invariant: exactly ONE User row exists for nsaddaga, and
    # NO rows exist under any of the friendly spellings.
    from app.db import get_engine
    from app.models import User
    from sqlmodel import Session, select
    with Session(get_engine()) as s:
        users = s.exec(
            select(User).where(User.name.in_(
                ["nsaddaga", "Addagarla", "addagarla", "Prasad Addagarla",
                 "prasad addagarla", "PRASAD ADDAGARLA"]
            ))
        ).all()
        names = sorted(u.name for u in users)
        assert names == ["nsaddaga"], (
            f"expected exactly one canonical User row, got {names!r}"
        )


def test_indexer_preserves_unresolved_owner(curated_pb, client):
    """Unknown owner stays as raw text — never silently dropped."""
    r = client.put("/api/notes", json={
        "path": "alias174/wk02.md",
        "body_md": "- !task Hello @ZorbluxTheUnknown\n",
    }, headers={"Authorization": AUTH})
    assert r.status_code in (200, 201)
    r = client.get("/api/tasks?owner=ZorbluxTheUnknown",
                   headers={"Authorization": AUTH})
    assert r.status_code == 200
    titles = [t["title"] for t in r.json()["tasks"]]
    assert "Hello" in titles


def test_indexer_keeps_ambiguous_owner_unmerged(curated_pb, client):
    """Ambiguous friendly name stays as raw — author can disambiguate
    later, and the two real people remain distinct."""
    r = client.put("/api/notes", json={
        "path": "alias174/wk03.md",
        "body_md": "- !task Ambig @Prasad\n",
    }, headers={"Authorization": AUTH})
    assert r.status_code in (200, 201)
    # Should appear under the raw spelling, not under either canonical idsid.
    r = client.get("/api/tasks?owner=Prasad",
                   headers={"Authorization": AUTH})
    assert "Ambig" in [t["title"] for t in r.json()["tasks"]]
    r = client.get("/api/tasks?owner=nsaddaga",
                   headers={"Authorization": AUTH})
    assert "Ambig" not in [t["title"] for t in r.json()["tasks"]]


def test_users_with_display_returns_phonebook_display(curated_pb, client):
    """GET /users?with_display=1 returns name+display objects so the
    UI dropdown can render 'Prasad Addagarla' while the option value
    stays the canonical idsid (#226 follow-up)."""
    # Seed at least one task so the User row exists.
    r = client.put("/api/notes", json={
        "path": "owner-display/n.md",
        "body_md": "# x\n- !task X @nsaddaga\n",
    }, headers={"Authorization": AUTH})
    assert r.status_code in (200, 201), r.text
    r = client.get("/api/users?with_display=1", headers={"Authorization": AUTH})
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list) and rows and isinstance(rows[0], dict)
    by_name = {row["name"]: row["display"] for row in rows}
    assert by_name.get("nsaddaga") == "Prasad Addagarla"


def test_users_default_shape_unchanged(curated_pb, client):
    """Without the flag, /users still returns a flat list[str]."""
    r = client.get("/api/users", headers={"Authorization": AUTH})
    assert r.status_code == 200
    out = r.json()
    assert isinstance(out, list)
    assert all(isinstance(x, str) for x in out)


def test_tasks_aggregations_include_owner_displays(curated_pb, client):
    """`aggregations.owner_displays` is a name->display map covering
    every owner appearing in the result set."""
    r = client.put("/api/notes", json={
        "path": "owner-display/agg.md",
        "body_md": "# x\n- !task DispAgg @nsaddaga\n",
    }, headers={"Authorization": AUTH})
    assert r.status_code in (200, 201), r.text
    r = client.get("/api/tasks?owner=nsaddaga", headers={"Authorization": AUTH})
    assert r.status_code == 200
    aggs = r.json()["aggregations"]
    assert "owner_displays" in aggs
    assert aggs["owner_displays"].get("nsaddaga") == "Prasad Addagarla"


def test_tasks_owner_filter_accepts_alias(curated_pb, client):
    """The /tasks?owner=... filter must accept any curated alias and
    expand it to the canonical idsid before querying. Without this,
    'My Tasks' for a user whose login != idsid (e.g. admin) is empty."""
    r = client.put("/api/notes", json={
        "path": "alias-filter/wk01.md",
        "body_md": "- !task FilterByAlias @nsaddaga\n",
    }, headers={"Authorization": AUTH})
    assert r.status_code in (200, 201), r.text
    for tok in ("nsaddaga", "Addagarla", "prasad addagarla", "ADDAGARLA"):
        r = client.get(f"/api/tasks?owner={tok}", headers={"Authorization": AUTH})
        assert r.status_code == 200
        titles = [t["title"] for t in r.json()["tasks"]]
        assert "FilterByAlias" in titles, f"alias {tok!r} did not match: {titles!r}"


def test_tasks_owner_filter_alias_works_alongside_raw(curated_pb, client):
    """Defensive: the original spelling is kept in the IN clause so
    any non-canonical row still matches alongside aliased lookups."""
    r = client.put("/api/notes", json={
        "path": "alias-filter/wk02.md",
        "body_md": "- !task RawOwner @ZorbluxTheUnknown\n",
    }, headers={"Authorization": AUTH})
    assert r.status_code in (200, 201), r.text
    r = client.get("/api/tasks?owner=ZorbluxTheUnknown", headers={"Authorization": AUTH})
    assert r.status_code == 200
    assert "RawOwner" in [t["title"] for t in r.json()["tasks"]]


def test_agenda_owner_filter_accepts_alias(curated_pb, client):
    """The /agenda?owner=... filter must also expand aliases."""
    r = client.put("/api/notes", json={
        "path": "alias-filter/agenda.md",
        "body_md": "- !task AgendaAlias @nsaddaga #eta 2099-01-15\n",
    }, headers={"Authorization": AUTH})
    assert r.status_code in (200, 201), r.text
    r = client.get(
        "/api/agenda?owner=Addagarla&start=2099-01-01&end=2099-12-31",
        headers={"Authorization": AUTH},
    )
    assert r.status_code == 200, r.text
    titles = [t["title"] for day_tasks in r.json()["by_day"].values() for t in day_tasks]
    assert "AgendaAlias" in titles, titles


def test_reindex_sweeps_orphan_users(curated_pb, client):
    """Reindex should drop User rows that own no tasks AND have no
    password set (admin-tab cleanup after canonicalization rekey).
    Login accounts (has_password=True) and admin are preserved."""
    from app.db import get_engine
    from app.models import User
    from sqlmodel import Session, select

    # Seed: one orphan (no password, no tasks), one with-password
    # account, one task-owner. Reindex should only delete the orphan.
    with Session(get_engine()) as s:
        s.add(User(name="OrphanGhost", pass_hash=""))
        s.add(User(name="LegacyLoginUser",
                   pass_hash="$2b$04$abcdefghijklmnopqrstuv"))
        s.commit()

    # Create a task so we have a confirmed real owner row.
    r = client.put("/api/notes", json={
        "path": "user-sweep/wk01.md",
        "body_md": "- !task RealOwnerTask @nsaddaga\n",
    }, headers={"Authorization": AUTH})
    assert r.status_code in (200, 201), r.text

    r = client.post("/api/admin/reindex", headers={"Authorization": AUTH})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("user_orphans_swept", 0) >= 1, body

    with Session(get_engine()) as s:
        names = {u.name for u in s.exec(select(User)).all()}
    assert "OrphanGhost" not in names, "orphan should have been swept"
    assert "LegacyLoginUser" in names, \
        "password-bearing legacy account must NOT be auto-deleted"
    assert "nsaddaga" in names, "real owner must survive"
    assert "admin" in names, "admin must survive"


def test_ar_endpoint_dedupes_owner_aliases(curated_pb, client):
    """Regression: when a task ends up with multiple aliases of the same
    person (e.g. @Gautham + @gajith both → gajith) the indexer must
    collapse them to a single TaskOwner row. Without this, POST /tasks/
    {ref}/ars (and any reindex path that re-writes TaskOwner edges)
    blew up with `UNIQUE constraint failed: taskowner.task_id,
    taskowner.user_id`. See the gajith 500 reported 2026-05-18."""
    # Create a parent task owned by nsaddaga, then add an AR with two
    # aliases of the SAME person (canonical idsid: nsaddaga) in owners.
    r = client.put("/api/notes", json={
        "path": "ardup/wk01.md",
        "body_md": "- !task ParentTask @nsaddaga #id T-ARDUP1\n",
    }, headers={"Authorization": AUTH})
    assert r.status_code in (200, 201), r.text

    r = client.post(
        "/api/tasks/T-ARDUP1/ars",
        json={
            "title": "ChildAR",
            # Two spellings of the same person — must NOT cause UNIQUE
            # constraint failure when inserted.
            "owners": ["nsaddaga", "Addagarla"],
        },
        headers={"Authorization": AUTH},
    )
    assert r.status_code in (200, 201), r.text

    # Verify exactly one TaskOwner edge for the new AR.
    from app.db import get_engine
    from app.models import Task, TaskOwner, User
    from sqlmodel import Session, select
    with Session(get_engine()) as s:
        ar = s.exec(select(Task).where(Task.title == "ChildAR")).first()
        assert ar is not None
        owners = s.exec(
            select(User.name)
            .join(TaskOwner, TaskOwner.user_id == User.id)
            .where(TaskOwner.task_id == ar.id)
        ).all()
    assert sorted(owners) == ["nsaddaga"], (
        f"expected exactly one canonical owner edge, got {owners!r}"
    )
