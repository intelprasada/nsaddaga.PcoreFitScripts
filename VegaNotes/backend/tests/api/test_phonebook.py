"""Tests for the phonebook module + API endpoint (#174 MVP, #210 Phase 2)."""
from __future__ import annotations

import json
import time
from pathlib import Path

# Ensure the in-process app uses the same temp data dir as test_api.
# Importing test_api below establishes that env BEFORE app.main loads.
import tests.api.test_api as _test_api  # noqa: F401  -- side-effect: env + client fixture
from tests.api.test_api import AUTH, client  # noqa: E402  reuse module-scoped client

import pytest  # noqa: E402

from app.phonebook import Phonebook, reset_phonebook_for_test  # noqa: E402


@pytest.fixture
def pb_file(tmp_path: Path) -> Path:
    p = tmp_path / "phonebook.json"
    p.write_text(json.dumps({
        "nsaddaga": {
            "idsid": "nsaddaga",
            "display": "Prasad Addagarla",
            "email": "prasad.addagarla@intel.com",
            "aliases": ["prasad", "addagarla", "p addagarla"],
        },
        "jdoe": {
            "idsid": "jdoe",
            "display": "Jane Doe",
            "email": "jane.doe@intel.com",
            "aliases": ["jane", "doe"],
            "manager_email": "boss@intel.com",
        },
        "pkumar2": {
            "idsid": "pkumar2",
            "display": "Prasad Kumar",
            "email": "prasad.kumar@intel.com",
            "aliases": ["prasad"],  # collides w/ nsaddaga's alias on purpose
        },
    }), encoding="utf-8")
    return p


def test_resolve_idsid_wins(pb_file):
    pb = Phonebook(pb_file)
    e, cands = pb.resolve("nsaddaga")
    assert e is not None and e.idsid == "nsaddaga"
    assert cands == []


def test_resolve_strips_at_sign(pb_file):
    pb = Phonebook(pb_file)
    e, _ = pb.resolve("@nsaddaga")
    assert e is not None and e.idsid == "nsaddaga"


def test_resolve_case_insensitive_alias(pb_file):
    pb = Phonebook(pb_file)
    e, _ = pb.resolve("@Addagarla")
    assert e is not None and e.idsid == "nsaddaga"


def test_resolve_display_name_with_space(pb_file):
    pb = Phonebook(pb_file)
    e, _ = pb.resolve("Prasad Addagarla")
    assert e is not None and e.idsid == "nsaddaga"


def test_resolve_email_lookup(pb_file):
    pb = Phonebook(pb_file)
    e, _ = pb.resolve("jane.doe@intel.com")
    assert e is not None and e.idsid == "jdoe"


def test_resolve_ambiguous(pb_file):
    pb = Phonebook(pb_file)
    e, cands = pb.resolve("prasad")
    assert e is None
    assert sorted(c.idsid for c in cands) == ["nsaddaga", "pkumar2"]


def test_resolve_unknown(pb_file):
    pb = Phonebook(pb_file)
    e, cands = pb.resolve("nobody")
    assert e is None and cands == []


def test_resolve_empty_returns_none(pb_file):
    pb = Phonebook(pb_file)
    assert pb.resolve("")[0] is None
    assert pb.resolve("@")[0] is None
    assert pb.resolve("   ")[0] is None


def test_resolve_many(pb_file):
    pb = Phonebook(pb_file)
    out = pb.resolve_many(["@nsaddaga", "@Jane", "@prasad", "@nobody", "@nsaddaga"])
    assert "@nsaddaga" in out["resolved"]
    assert out["resolved"]["@nsaddaga"]["email"] == "prasad.addagarla@intel.com"
    assert "@Jane" in out["resolved"]
    assert "@prasad" in out["ambiguous"]
    assert len(out["ambiguous"]["@prasad"]) == 2
    assert out["unresolved"] == ["@nobody"]


def test_hot_reload_on_mtime_change(pb_file):
    pb = Phonebook(pb_file)
    assert pb.resolve("@nsaddaga")[0] is not None
    # Replace file with a smaller payload.
    time.sleep(0.01)  # ensure mtime tick
    pb_file.write_text(json.dumps({
        "newperson": {"idsid": "newperson", "display": "New Person",
                      "email": "new@intel.com", "aliases": []},
    }), encoding="utf-8")
    # Bump mtime explicitly in case fs resolution swallows the write.
    import os
    new_mtime = pb_file.stat().st_mtime + 1
    os.utime(pb_file, (new_mtime, new_mtime))
    assert pb.resolve("@nsaddaga")[0] is None
    assert pb.resolve("@newperson")[0] is not None


def test_missing_file_returns_unresolved(tmp_path: Path):
    pb = Phonebook(tmp_path / "nope.json")
    e, cands = pb.resolve("@anyone")
    assert e is None and cands == []


def test_bad_entries_skipped(tmp_path: Path):
    p = tmp_path / "pb.json"
    p.write_text(json.dumps({
        "good": {"idsid": "good", "display": "Good", "email": "good@x.com", "aliases": []},
        "noemail": {"idsid": "noemail", "display": "X"},
        "bademail": {"idsid": "bademail", "display": "X", "email": "not-an-email"},
        "stringval": "oops",
    }), encoding="utf-8")
    pb = Phonebook(p)
    assert pb.resolve("@good")[0] is not None
    assert pb.resolve("@noemail")[0] is None
    assert pb.resolve("@bademail")[0] is None


def test_api_phonebook_resolve(client, pb_file):
    """Endpoint returns the same shape as resolve_many."""
    reset_phonebook_for_test(pb_file)
    try:
        r = client.post("/api/phonebook/resolve",
                        json={"tokens": ["@nsaddaga", "@Jane", "@prasad", "@nobody"]},
                        headers={"Authorization": AUTH})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["resolved"]["@nsaddaga"]["email"] == "prasad.addagarla@intel.com"
        assert "@prasad" in data["ambiguous"]
        assert data["unresolved"] == ["@nobody"]
    finally:
        reset_phonebook_for_test(None)


def test_api_phonebook_empty_tokens(client):
    r = client.post("/api/phonebook/resolve", json={"tokens": []},
                    headers={"Authorization": AUTH})
    assert r.status_code == 200
    assert r.json() == {"resolved": {}, "ambiguous": {}, "unresolved": []}


def test_api_phonebook_too_many_tokens(client):
    r = client.post("/api/phonebook/resolve",
                    json={"tokens": [f"t{i}" for i in range(501)]},
                    headers={"Authorization": AUTH})
    assert r.status_code == 400
