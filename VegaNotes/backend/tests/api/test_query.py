"""Tests for the Phase-1 query enhancements on /api/tasks plus the new
/api/attrs and /api/me/views endpoints.

Issue #38 follow-up: generic attribute search.
"""
import base64
import os
import tempfile
from pathlib import Path

import pytest

DATA = Path(tempfile.mkdtemp(prefix="vega-test-query-"))
os.environ.setdefault("VEGANOTES_DATA_DIR", str(DATA))
os.environ.setdefault("VEGANOTES_SERVE_STATIC", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = "Basic " + base64.b64encode(b"admin:admin").decode()


@pytest.fixture(scope="module")
def client():
    settings.notes_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    import app.db as _db
    _db._engine = None
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def seeded(client):
    """Seed a project with five tasks covering the matrix we need:

      T1: alice, P0, eta ww17, #area fit-val, #risk high
      T2: alice, P1, eta ww18, #area fit-val, #risk low
      T3: bob,   P0, eta ww17, #area infra,   #risk high
      T4: bob,   P2, eta ww19, #area infra
      T5: alice, P3 (unstamped attr), no eta, status done
    """
    client.post("/api/projects", json={"name": "qry"}, headers={"Authorization": ADMIN})
    md = (
        "# qry seed\n"
        "- !task T1 task @alice #priority P0 #eta ww17 #area fit-val #risk high #id T-AAAA01\n"
        "- !task T2 task @alice #priority P1 #eta ww18 #area fit-val #risk low #id T-AAAA02\n"
        "- !task T3 task @bob   #priority P0 #eta ww17 #area infra   #risk high #id T-AAAA03\n"
        "- !task T4 task @bob   #priority P2 #eta ww19 #area infra              #id T-AAAA04\n"
        "- !task T5 task @alice #priority P3 #status done                       #id T-AAAA05\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "qry/seed.md", "body_md": md},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text
    # Sanity — should be 5 tasks indexed.
    r = client.get("/api/tasks?project=qry", headers={"Authorization": ADMIN})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 5
    return r.json()


# ── envelope additions: total/offset/limit always present ─────────────────

def test_response_envelope_has_total_offset_limit(client, seeded):
    r = client.get("/api/tasks?project=qry", headers={"Authorization": ADMIN})
    body = r.json()
    assert body["total"] == 5
    assert body["offset"] == 0
    assert body["limit"] is None
    assert len(body["tasks"]) == 5


# ── pagination ────────────────────────────────────────────────────────────

def test_pagination_limit_and_offset(client, seeded):
    page = client.get(
        "/api/tasks?project=qry&sort=id&limit=2&offset=1",
        headers={"Authorization": ADMIN},
    ).json()
    assert page["total"] == 5
    assert page["offset"] == 1
    assert page["limit"] == 2
    assert len(page["tasks"]) == 2
    # When sorted by id we know the order: skip the 1st, take 2nd & 3rd.
    titles = [t["title"] for t in page["tasks"]]
    assert titles == ["T2 task", "T3 task"]


def test_pagination_offset_past_end_returns_empty(client, seeded):
    body = client.get(
        "/api/tasks?project=qry&offset=100",
        headers={"Authorization": ADMIN},
    ).json()
    assert body["total"] == 5
    assert body["tasks"] == []


def test_limit_validation(client):
    r = client.get("/api/tasks?limit=0", headers={"Authorization": ADMIN})
    assert r.status_code == 422
    r = client.get("/api/tasks?limit=99999", headers={"Authorization": ADMIN})
    assert r.status_code == 422


# ── sort ──────────────────────────────────────────────────────────────────

def test_sort_by_task_column_asc_desc(client, seeded):
    asc = client.get(
        "/api/tasks?project=qry&sort=title:asc", headers={"Authorization": ADMIN}
    ).json()["tasks"]
    desc = client.get(
        "/api/tasks?project=qry&sort=title:desc", headers={"Authorization": ADMIN}
    ).json()["tasks"]
    assert [t["title"] for t in asc] == ["T1 task", "T2 task", "T3 task", "T4 task", "T5 task"]
    assert [t["title"] for t in desc] == ["T5 task", "T4 task", "T3 task", "T2 task", "T1 task"]


def test_sort_by_eta_puts_unstamped_last(client, seeded):
    body = client.get(
        "/api/tasks?project=qry&sort=eta:asc", headers={"Authorization": ADMIN}
    ).json()
    titles = [t["title"] for t in body["tasks"]]
    # Unstamped (T5 has no eta) must appear at the end regardless of direction.
    assert titles[-1] == "T5 task"
    # First three should be the ww17 / ww18 / ww19 tasks in order
    assert titles[0] in ("T1 task", "T3 task")  # both ww17 — id tiebreaker decides


def test_sort_unknown_field_400(client):
    r = client.get("/api/tasks?sort=bogus", headers={"Authorization": ADMIN})
    assert r.status_code == 400
    assert "unknown sort field" in r.json()["detail"]


def test_sort_bad_direction_400(client):
    r = client.get("/api/tasks?sort=title:sideways", headers={"Authorization": ADMIN})
    assert r.status_code == 400


# ── negation filters ──────────────────────────────────────────────────────

def test_not_owner(client, seeded):
    body = client.get(
        "/api/tasks?project=qry&not_owner=alice", headers={"Authorization": ADMIN}
    ).json()
    titles = sorted(t["title"] for t in body["tasks"])
    assert titles == ["T3 task", "T4 task"]


def test_not_status(client, seeded):
    body = client.get(
        "/api/tasks?project=qry&not_status=done", headers={"Authorization": ADMIN}
    ).json()
    titles = sorted(t["title"] for t in body["tasks"])
    # Everything except T5 (status=done).
    assert titles == ["T1 task", "T2 task", "T3 task", "T4 task"]


def test_not_priority(client, seeded):
    body = client.get(
        "/api/tasks?project=qry&not_priority=P0", headers={"Authorization": ADMIN}
    ).json()
    titles = sorted(t["title"] for t in body["tasks"])
    # T1 and T3 are P0 — exclude both.
    assert titles == ["T2 task", "T4 task", "T5 task"]


# ── arbitrary @attr filters (the headline feature) ────────────────────────

def test_attr_eq(client, seeded):
    body = client.get(
        "/api/tasks?project=qry&attr=area:eq:fit-val",
        headers={"Authorization": ADMIN},
    ).json()
    titles = sorted(t["title"] for t in body["tasks"])
    assert titles == ["T1 task", "T2 task"]


def test_attr_ne_includes_tasks_missing_the_key(client, seeded):
    """Tasks without the key satisfy `ne`."""
    body = client.get(
        "/api/tasks?project=qry&attr=risk:ne:high",
        headers={"Authorization": ADMIN},
    ).json()
    titles = sorted(t["title"] for t in body["tasks"])
    # Excludes T1 and T3 (risk=high). T2 risk=low; T4 / T5 have no risk attr.
    assert titles == ["T2 task", "T4 task", "T5 task"]


def test_attr_in(client, seeded):
    body = client.get(
        "/api/tasks?project=qry&attr=risk:in:high,low",
        headers={"Authorization": ADMIN},
    ).json()
    titles = sorted(t["title"] for t in body["tasks"])
    assert titles == ["T1 task", "T2 task", "T3 task"]


def test_attr_nin(client, seeded):
    body = client.get(
        "/api/tasks?project=qry&attr=risk:nin:low",
        headers={"Authorization": ADMIN},
    ).json()
    titles = sorted(t["title"] for t in body["tasks"])
    # Drops T2 (risk=low). Others (no risk row OR risk=high) survive.
    assert titles == ["T1 task", "T3 task", "T4 task", "T5 task"]


def test_attr_exists_and_nexists(client, seeded):
    has = client.get(
        "/api/tasks?project=qry&attr=risk:exists:",
        headers={"Authorization": ADMIN},
    ).json()
    none = client.get(
        "/api/tasks?project=qry&attr=risk:nexists:",
        headers={"Authorization": ADMIN},
    ).json()
    assert sorted(t["title"] for t in has["tasks"]) == ["T1 task", "T2 task", "T3 task"]
    assert sorted(t["title"] for t in none["tasks"]) == ["T4 task", "T5 task"]


def test_attr_like(client, seeded):
    body = client.get(
        "/api/tasks?project=qry&attr=area:like:fit%25",
        headers={"Authorization": ADMIN},
    ).json()
    titles = sorted(t["title"] for t in body["tasks"])
    assert titles == ["T1 task", "T2 task"]


def test_attr_range_on_eta_uses_value_norm(client, seeded):
    body = client.get(
        "/api/tasks?project=qry&attr=eta:gte:2026-04-27",
        headers={"Authorization": ADMIN},
    ).json()
    # ww17 ≈ 2026-04-20, ww18 ≈ 2026-04-27, ww19 ≈ 2026-05-04.
    # >= 2026-04-27 keeps ww18 (T2) and ww19 (T4).
    titles = sorted(t["title"] for t in body["tasks"])
    assert titles == ["T2 task", "T4 task"]


def test_attr_range_on_unnormalized_key_400(client):
    r = client.get(
        "/api/tasks?attr=area:gte:fit-val",
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 400
    assert "value_norm" in r.json()["detail"]


def test_attr_combined_filters_AND(client, seeded):
    body = client.get(
        "/api/tasks?project=qry&attr=area:eq:fit-val&attr=risk:eq:high",
        headers={"Authorization": ADMIN},
    ).json()
    titles = [t["title"] for t in body["tasks"]]
    assert titles == ["T1 task"]


def test_attr_bad_op_400(client):
    r = client.get("/api/tasks?attr=area:bogus:x", headers={"Authorization": ADMIN})
    assert r.status_code == 400
    assert "unknown op" in r.json()["detail"]


def test_attr_malformed_400(client):
    r = client.get("/api/tasks?attr=justakey", headers={"Authorization": ADMIN})
    assert r.status_code == 400


# ── /api/attrs ────────────────────────────────────────────────────────────

def test_attrs_endpoint_lists_keys_with_samples(client, seeded):
    body = client.get("/api/attrs", headers={"Authorization": ADMIN}).json()
    by_key = {row["key"]: row for row in body}
    # All seeded keys should be present.
    assert {"area", "risk", "eta", "priority"}.issubset(by_key)
    area = by_key["area"]
    assert area["count"] >= 4
    assert set(area["sample_values"]) >= {"fit-val", "infra"}


# ── /api/me/views ─────────────────────────────────────────────────────────

def test_saved_views_roundtrip(client):
    # Empty by default.
    assert client.get("/api/me/views", headers={"Authorization": ADMIN}).json() == []

    payload = [
        {"name": "P0 backlog", "query": {"priority": "P0", "hide_done": True}},
        {"name": "Alice this week",
         "query": {"owner": "alice", "attr": ["eta:gte:2026-04-20"]}},
    ]
    r = client.put("/api/me/views", json=payload, headers={"Authorization": ADMIN})
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "count": 2}

    got = client.get("/api/me/views", headers={"Authorization": ADMIN}).json()
    assert got == payload

    # Replace with empty list — must clear.
    client.put("/api/me/views", json=[], headers={"Authorization": ADMIN})
    assert client.get("/api/me/views", headers={"Authorization": ADMIN}).json() == []


def test_saved_views_reject_empty_name(client):
    r = client.put(
        "/api/me/views",
        json=[{"name": "  ", "query": {}}],
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 400


def test_saved_views_reject_duplicate_names(client):
    r = client.put(
        "/api/me/views",
        json=[{"name": "v", "query": {}}, {"name": "v", "query": {"x": 1}}],
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 400
    assert "duplicate" in r.json()["detail"].lower()


def test_saved_views_require_auth(client):
    r = client.get("/api/me/views")
    assert r.status_code == 401


# ── existing behavior preserved (no regression) ───────────────────────────

def test_existing_owner_filter_still_works(client, seeded):
    body = client.get(
        "/api/tasks?project=qry&owner=alice", headers={"Authorization": ADMIN}
    ).json()
    titles = sorted(t["title"] for t in body["tasks"])
    assert titles == ["T1 task", "T2 task", "T5 task"]


def test_existing_aggregations_envelope_still_emitted(client, seeded):
    body = client.get(
        "/api/tasks?project=qry", headers={"Authorization": ADMIN}
    ).json()
    aggs = body["aggregations"]
    assert set(aggs["owners"]) == {"alice", "bob"}
    assert "qry" in aggs["projects"]
    assert aggs["status_breakdown"].get("done", 0) >= 1


# ── include_children: child shape carries owners/projects/features/eta_raw ──
# Issue #104: subtask owners rendered blank in `vn list --tree`.

@pytest.fixture(scope="module")
def seeded_with_subtasks(client):
    """Separate project so the parent/child topology doesn't perturb the
    `seeded` fixture's totals.  One parent + one subtask is enough to
    verify the child-shape bug fix (#104)."""
    client.post("/api/projects", json={"name": "kids"}, headers={"Authorization": ADMIN})
    md = (
        "# kids seed\n"
        "- !task Parent task @alice #priority P1 #eta ww18.2 #area fit-val #id T-PRNT01\n"
        "  - !task Child task @bob #eta ww19 #id T-CHLD01\n"
    )
    r = client.put(
        "/api/notes",
        json={"path": "kids/seed.md", "body_md": md},
        headers={"Authorization": ADMIN},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_include_children_carries_owners_projects_features(client, seeded_with_subtasks):
    body = client.get(
        "/api/tasks?project=kids&include_children=true&top_level_only=true",
        headers={"Authorization": ADMIN},
    ).json()
    parents = [t for t in body["tasks"] if t["title"] == "Parent task"]
    assert len(parents) == 1, body
    children = parents[0]["children"]
    assert len(children) >= 1, children
    child = children[0]

    # The original bug: child carried no `owners` key at all → vn list
    # rendered the OWNERS column blank.  After the fix, the field is
    # present and populated (parent's @alice inherits onto subtasks).
    assert "owners" in child
    assert isinstance(child["owners"], list)
    assert "alice" in child["owners"] or "bob" in child["owners"], child
    # parent's project propagates onto the child via inheritance.
    assert "projects" in child and "kids" in child["projects"]
    # features field is present (may be empty if no #area on the child).
    assert "features" in child and isinstance(child["features"], list)
    # eta_raw carries the user-typed ww-format alongside normalized eta.
    assert child.get("eta_raw")
    assert child["eta_raw"].startswith("ww")
    assert child["eta"]  # value_norm still present for back-compat
    # parent_task_id is present on the child (used by vn for dedup).
    assert child["parent_task_id"] == parents[0]["id"]
