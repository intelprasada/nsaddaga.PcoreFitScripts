"""Tests for the Intel Phonebook scraper (#213).

All HTTP is mocked — no live network calls. Two layers exercised:
* ``_parse_html`` against a fixture that mirrors the real page format.
* ``cached_lookup`` with a monkeypatched ``_fetch`` to verify the TTL
  cache and the enable/disable gate.
"""
from __future__ import annotations

# Side-effect: sets VEGANOTES_DATA_DIR before app.main loads.
import tests.api.test_api as _test_api  # noqa: F401
from tests.api.test_api import AUTH, client  # noqa: E402

import pytest  # noqa: E402

from app import phonebook_intel  # noqa: E402
from app.config import settings  # noqa: E402


# A trimmed version of the real phonebook.intel.com response for "Niharika".
# Two distinct rows + one re-occurring email should dedupe to 2 hits.
SAMPLE_HTML = """
<html><body>
<pre>
-<input type="checkbox" name="cookie" value="11576058"> <a href="phonebook?e=Arlagadda%20Narasimharaju%2c%20Niharika&k=11576058&f=ALL&d=ALL&b=y">Arlagadda Narasimharaju, <b>Niharika</b></a>|-|<a href="phonebook?e=JF3&f=ALL&d=ALL&k=1&u=/cgi-bin/phonefac?k=JF3">JF3</a>|HF3-55|<a href="mailto:niharika.arlagadda.narasimharaju@intel.com"><b>niharika</b>.arlagadda.narasimharaju@intel.com</a>
-<input type="checkbox" name="cookie" value="11627027"> <a href="phonebook?e=Chatla%2c%20Niharika&k=11627027&f=ALL&d=ALL&b=y">Chatla, <b>Niharika</b></a>|-|<a href="phonebook?e=FM5&f=ALL&d=ALL&k=1">FM5</a>|-|<a href="mailto:niharika1.chatla@intel.com"><b>niharika</b>1.chatla@intel.com</a>
-<input type="checkbox" name="cookie" value="11627027"> <a href="phonebook?e=Chatla%2c%20Niharika&k=11627027&f=ALL&d=ALL&b=y">Chatla, <b>Niharika</b></a>|-|<a href="phonebook?e=FM5&f=ALL&d=ALL&k=1">FM5</a>|-|<a href="mailto:niharika1.chatla@intel.com">duplicate row</a>
</pre>
</body></html>
"""


@pytest.fixture(autouse=True)
def _enable_scraper(monkeypatch):
    monkeypatch.setattr(settings, "phonebook_scraper_enabled", True, raising=False)
    monkeypatch.setattr(settings, "phonebook_cache_ttl_s", 60, raising=False)
    phonebook_intel.cache_clear()
    yield
    phonebook_intel.cache_clear()


def test_parse_html_extracts_rows_and_dedupes():
    hits = phonebook_intel._parse_html(SAMPLE_HTML)
    assert len(hits) == 2
    by_email = {h.email: h for h in hits}
    assert "niharika.arlagadda.narasimharaju@intel.com" in by_email
    assert "niharika1.chatla@intel.com" in by_email
    # Display name flipped from "Last, First" to "First Last".
    assert by_email["niharika1.chatla@intel.com"].display == "Niharika Chatla"
    # IDSID is the email local-part.
    assert by_email["niharika1.chatla@intel.com"].idsid == "niharika1.chatla"
    assert by_email["niharika.arlagadda.narasimharaju@intel.com"].wwid == "11576058"


def test_parse_html_empty_on_no_results():
    assert phonebook_intel._parse_html("<html><body>No hits</body></html>") == []


def test_to_dict_shape_matches_phonebookentry():
    hits = phonebook_intel._parse_html(SAMPLE_HTML)
    d = hits[0].to_dict()
    for k in ("idsid", "display", "email", "aliases", "manager_email"):
        assert k in d
    assert d["source"] == "intel_phonebook"
    assert d["aliases"] == []


def test_lookup_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "phonebook_scraper_enabled", False, raising=False)
    calls = []
    monkeypatch.setattr(phonebook_intel, "_fetch", lambda u, t: calls.append(u) or SAMPLE_HTML)
    assert phonebook_intel.lookup("anything") == []
    assert calls == []  # never went to "network"


def test_lookup_blank_query_returns_empty(monkeypatch):
    monkeypatch.setattr(phonebook_intel, "_fetch", lambda u, t: SAMPLE_HTML)
    assert phonebook_intel.lookup("") == []
    assert phonebook_intel.lookup("   ") == []


def test_lookup_network_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(phonebook_intel, "_fetch", lambda u, t: None)
    assert phonebook_intel.lookup("Niharika") == []


def test_lookup_passes_url_encoded_query(monkeypatch):
    captured = {}
    def fake_fetch(url, timeout):
        captured["url"] = url
        return SAMPLE_HTML
    monkeypatch.setattr(phonebook_intel, "_fetch", fake_fetch)
    phonebook_intel.lookup("Prasad Addagarla")
    assert "Prasad%20Addagarla" in captured["url"]
    assert captured["url"].startswith("https://phonebook.intel.com/cgi-bin/phonebook?e=")


def test_cached_lookup_reuses_result(monkeypatch):
    calls = {"n": 0}
    def fake_fetch(url, timeout):
        calls["n"] += 1
        return SAMPLE_HTML
    monkeypatch.setattr(phonebook_intel, "_fetch", fake_fetch)
    a = phonebook_intel.cached_lookup("Niharika")
    b = phonebook_intel.cached_lookup("niharika")  # case-insensitive cache key
    c = phonebook_intel.cached_lookup("  NIHARIKA  ")
    assert a == b == c
    assert calls["n"] == 1


def test_cached_lookup_disabled_skips_cache(monkeypatch):
    monkeypatch.setattr(settings, "phonebook_scraper_enabled", False, raising=False)
    calls = {"n": 0}
    monkeypatch.setattr(phonebook_intel, "_fetch",
                        lambda u, t: calls.update(n=calls["n"] + 1) or SAMPLE_HTML)
    assert phonebook_intel.cached_lookup("anything") == []
    assert calls["n"] == 0


def test_resolver_falls_back_to_scraper(monkeypatch, tmp_path):
    """Phonebook.resolve() should hit the scraper when JSON misses and
    the scraper is enabled. Single hit → resolved; multiple → ambiguous."""
    from app.phonebook import Phonebook

    # Empty JSON so every lookup misses the curated source.
    pb_file = tmp_path / "pb.json"
    pb_file.write_text("{}")
    pb = Phonebook(path=pb_file)

    single_html = """
    <html><body><pre>
    -<input type="checkbox" name="cookie" value="99999"> <a href="phonebook?e=Solo%2c%20Person&k=99999&f=ALL">Solo, <b>Person</b></a>|-|<a href="phonebook?e=X&k=1">X</a>|-|<a href="mailto:solo.person@intel.com">solo.person@intel.com</a>
    </pre></body></html>
    """
    monkeypatch.setattr(phonebook_intel, "_fetch", lambda u, t: single_html)
    phonebook_intel.cache_clear()
    # Query matches the first name "Person" so the #215 first-name filter
    # keeps the row.
    entry, candidates = pb.resolve("@person")
    assert entry is not None
    assert entry.email == "solo.person@intel.com"
    assert entry.idsid == "solo.person"
    assert candidates == []

    # Multi-hit scrape → ambiguous.
    phonebook_intel.cache_clear()
    monkeypatch.setattr(phonebook_intel, "_fetch", lambda u, t: SAMPLE_HTML)
    entry, candidates = pb.resolve("@niharika")
    assert entry is None
    assert len(candidates) == 2


def test_resolver_skips_scraper_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "phonebook_scraper_enabled", False, raising=False)
    from app.phonebook import Phonebook
    pb_file = tmp_path / "pb.json"
    pb_file.write_text("{}")
    pb = Phonebook(path=pb_file)
    calls = {"n": 0}
    monkeypatch.setattr(phonebook_intel, "_fetch",
                        lambda u, t: calls.update(n=calls["n"] + 1) or SAMPLE_HTML)
    entry, candidates = pb.resolve("@niharika")
    assert entry is None and candidates == []
    assert calls["n"] == 0


def test_api_lookup_endpoint(monkeypatch, client):
    monkeypatch.setattr(phonebook_intel, "_fetch", lambda u, t: SAMPLE_HTML)
    r = client.post("/api/phonebook/lookup", headers={"Authorization": AUTH}, json={"q": "Niharika"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == "Niharika"
    assert body["enabled"] is True
    assert len(body["candidates"]) == 2
    emails = sorted(c["email"] for c in body["candidates"])
    assert emails == [
        "niharika.arlagadda.narasimharaju@intel.com",
        "niharika1.chatla@intel.com",
    ]
    # Each candidate carries the source marker so the UI can badge it.
    assert all(c["source"] == "intel_phonebook" for c in body["candidates"])


def test_api_lookup_empty_query(client):
    r = client.post("/api/phonebook/lookup", headers={"Authorization": AUTH}, json={"q": "  "})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == ""
    assert body["candidates"] == []
    assert body["enabled"] is True  # autouse fixture turns it on


def test_api_lookup_too_long(client):
    r = client.post("/api/phonebook/lookup", headers={"Authorization": AUTH}, json={"q": "x" * 201})
    assert r.status_code == 400


def test_api_lookup_disabled(monkeypatch, client):
    monkeypatch.setattr(settings, "phonebook_scraper_enabled", False, raising=False)
    r = client.post("/api/phonebook/lookup", headers={"Authorization": AUTH}, json={"q": "Niharika"})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["candidates"] == []


# ---------------------------------------------------------------------------
# Org-distance ranking (#213).
# ---------------------------------------------------------------------------

# Synthetic 4-level org used by the manager-chain tests:
#
#                    1000 (root)
#                   /     \
#                2000      3000
#               /   \         \
#            2100  2200      3100
#           /  \     \         \
#         2110 2111  2210      3110
#
# Format: wwid -> {wwid, mgr_wwid, email}
ORG = {
    "1000": {"wwid": "1000", "mgr_wwid": None, "email": "ceo@intel.com"},
    "2000": {"wwid": "2000", "mgr_wwid": "1000", "email": "vp1@intel.com"},
    "3000": {"wwid": "3000", "mgr_wwid": "1000", "email": "vp2@intel.com"},
    "2100": {"wwid": "2100", "mgr_wwid": "2000", "email": "dir1@intel.com"},
    "2200": {"wwid": "2200", "mgr_wwid": "2000", "email": "dir2@intel.com"},
    "3100": {"wwid": "3100", "mgr_wwid": "3000", "email": "dir3@intel.com"},
    "2110": {"wwid": "2110", "mgr_wwid": "2100", "email": "anchor@intel.com"},
    "2111": {"wwid": "2111", "mgr_wwid": "2100", "email": "peer@intel.com"},
    "2210": {"wwid": "2210", "mgr_wwid": "2200", "email": "cousin@intel.com"},
    "3110": {"wwid": "3110", "mgr_wwid": "3100", "email": "stranger@intel.com"},
}


@pytest.fixture
def org_tree(monkeypatch):
    phonebook_intel.detail_cache_clear()
    phonebook_intel.detail_cache_seed(ORG.items())
    # Block any outbound HTTP — every chain step should hit the seeded cache.
    monkeypatch.setattr(phonebook_intel, "_fetch", lambda u, t: None)
    monkeypatch.setattr(phonebook_intel, "_fetch_detail_html",
                        lambda w: pytest.fail(f"unexpected detail fetch for {w}"))
    yield ORG
    phonebook_intel.detail_cache_clear()


def test_manager_chain_walks_to_root(org_tree):
    chain = phonebook_intel.manager_chain("2110")
    assert chain == ["2110", "2100", "2000", "1000"]


def test_manager_chain_self_loop_terminates(monkeypatch):
    phonebook_intel.detail_cache_clear()
    phonebook_intel.detail_cache_seed([
        ("X", {"wwid": "X", "mgr_wwid": "X", "email": "x@intel.com"}),
    ])
    monkeypatch.setattr(phonebook_intel, "_fetch_detail_html",
                        lambda w: pytest.fail("unexpected fetch"))
    assert phonebook_intel.manager_chain("X") == ["X"]


def test_manager_chain_stops_at_max_depth(monkeypatch):
    phonebook_intel.detail_cache_clear()
    # Linear chain A->B->C->D->E.
    phonebook_intel.detail_cache_seed([
        ("A", {"wwid": "A", "mgr_wwid": "B", "email": "a@i.com"}),
        ("B", {"wwid": "B", "mgr_wwid": "C", "email": "b@i.com"}),
        ("C", {"wwid": "C", "mgr_wwid": "D", "email": "c@i.com"}),
        ("D", {"wwid": "D", "mgr_wwid": "E", "email": "d@i.com"}),
        ("E", {"wwid": "E", "mgr_wwid": None, "email": "e@i.com"}),
    ])
    monkeypatch.setattr(phonebook_intel, "_fetch_detail_html",
                        lambda w: pytest.fail("unexpected fetch"))
    chain = phonebook_intel.manager_chain("A", max_depth=2)
    assert chain == ["A", "B", "C"]


def test_org_distance_basic_cases(org_tree):
    # Same person: 0.
    assert phonebook_intel.org_distance("2110", "2110") == 0
    # Manager / reportee (1 hop).
    assert phonebook_intel.org_distance("2110", "2100") == 1
    assert phonebook_intel.org_distance("2100", "2110") == 1
    # Peers under the same manager (LCA at parent): 1+1 = 2.
    assert phonebook_intel.org_distance("2110", "2111") == 2
    # Cousins (LCA two levels up): 2+2 = 4.
    assert phonebook_intel.org_distance("2110", "2210") == 4
    # Across VP boundary (LCA = root): 3+3 = 6.
    assert phonebook_intel.org_distance("2110", "3110") == 6


def test_org_distance_handles_missing(org_tree):
    assert phonebook_intel.org_distance("", "2110") is None
    assert phonebook_intel.org_distance("2110", None) is None


def test_rank_by_distance_orders_closest_first(org_tree):
    candidates = [
        phonebook_intel.IntelPhonebookHit(
            display="Stranger", email="stranger@intel.com",
            idsid="stranger", wwid="3110",
        ),
        phonebook_intel.IntelPhonebookHit(
            display="Peer", email="peer@intel.com",
            idsid="peer", wwid="2111",
        ),
        phonebook_intel.IntelPhonebookHit(
            display="Cousin", email="cousin@intel.com",
            idsid="cousin", wwid="2210",
        ),
    ]
    ranked = phonebook_intel.rank_by_distance(candidates, "2110")
    assert [r[0].idsid for r in ranked] == ["peer", "cousin", "stranger"]
    assert [r[1] for r in ranked] == [2, 4, 6]


def test_rank_by_distance_no_anchor(org_tree):
    candidates = [
        phonebook_intel.IntelPhonebookHit(
            display="Anyone", email="x@i.com", idsid="x", wwid="2110",
        ),
    ]
    assert phonebook_intel.rank_by_distance(candidates, None) == [(candidates[0], None)]


def test_resolver_picks_closest_when_anchor_set(monkeypatch, tmp_path, org_tree):
    """Phonebook.resolve(token, anchor=...) should auto-pick the
    org-tree-closest candidate when the scraper returns multiple hits."""
    from app.phonebook import Phonebook

    pb_file = tmp_path / "pb.json"
    pb_file.write_text("{}")
    pb = Phonebook(path=pb_file)

    multi_hit_html = """
    <html><body><pre>
    -<input type="checkbox" name="cookie" value="3110"> <a href="phonebook?e=Stranger&k=3110&f=ALL">Stranger, <b>Niharika</b></a>|-|<a href="phonebook?e=X&k=1">X</a>|-|<a href="mailto:stranger@intel.com">stranger@intel.com</a>
    -<input type="checkbox" name="cookie" value="2111"> <a href="phonebook?e=Peer&k=2111&f=ALL">Peer, <b>Niharika</b></a>|-|<a href="phonebook?e=X&k=1">X</a>|-|<a href="mailto:peer@intel.com">peer@intel.com</a>
    -<input type="checkbox" name="cookie" value="2210"> <a href="phonebook?e=Cousin&k=2210&f=ALL">Cousin, <b>Niharika</b></a>|-|<a href="phonebook?e=X&k=1">X</a>|-|<a href="mailto:cousin@intel.com">cousin@intel.com</a>
    </pre></body></html>
    """
    monkeypatch.setattr(phonebook_intel, "_fetch", lambda u, t: multi_hit_html)
    phonebook_intel.cache_clear()

    # Anchor is the WWID 2110 — peer (2111) is the closest at distance 2.
    entry, candidates = pb.resolve("@niharika", anchor="2110")
    assert entry is not None, "should pick the closest candidate"
    assert entry.email == "peer@intel.com"
    assert candidates == []


def test_resolver_returns_ranked_when_tied(monkeypatch, tmp_path, org_tree):
    """When two candidates are tied for closest, resolver returns them
    as ambiguous (ranked) instead of picking arbitrarily."""
    from app.phonebook import Phonebook

    pb_file = tmp_path / "pb.json"
    pb_file.write_text("{}")
    pb = Phonebook(path=pb_file)

    # Two peers under the same manager (both distance 2 from 2110).
    multi_hit_html = """
    <html><body><pre>
    -<input type="checkbox" name="cookie" value="2111"> <a href="phonebook?e=Peer1&k=2111&f=ALL">Peer1, X</a>|-|<a href="phonebook?e=X&k=1">X</a>|-|<a href="mailto:peer@intel.com">peer@intel.com</a>
    -<input type="checkbox" name="cookie" value="2210"> <a href="phonebook?e=Peer2&k=2210&f=ALL">Peer2, X</a>|-|<a href="phonebook?e=X&k=1">X</a>|-|<a href="mailto:cousin@intel.com">cousin@intel.com</a>
    </pre></body></html>
    """
    # Override cousin to be a sibling of 2111 under 2100 so distance ties at 2.
    phonebook_intel.detail_cache_seed([
        ("2210", {"wwid": "2210", "mgr_wwid": "2100", "email": "cousin@i.com"}),
    ])
    monkeypatch.setattr(phonebook_intel, "_fetch", lambda u, t: multi_hit_html)
    phonebook_intel.cache_clear()

    entry, candidates = pb.resolve("@x", anchor="2110")
    assert entry is None, "tie should not auto-pick"
    assert {c.email for c in candidates} == {"peer@intel.com", "cousin@intel.com"}


def test_resolve_anchor_wwid_via_idsid(monkeypatch, org_tree):
    """resolve_anchor_wwid('nsaddaga') should pick the unique row whose
    email local-part matches the idsid."""
    monkeypatch.setattr(phonebook_intel, "cached_lookup", lambda q: [
        phonebook_intel.IntelPhonebookHit(
            display="Other Person", email="other@intel.com",
            idsid="other", wwid="9999",
        ),
        phonebook_intel.IntelPhonebookHit(
            display="N Saddaga", email="nsaddaga@intel.com",
            idsid="nsaddaga", wwid="42",
        ),
    ])
    assert phonebook_intel.resolve_anchor_wwid("nsaddaga") == "42"
    assert phonebook_intel.resolve_anchor_wwid("@nsaddaga") == "42"


def test_resolve_anchor_wwid_passthrough_for_numeric():
    assert phonebook_intel.resolve_anchor_wwid("11342477") == "11342477"
    assert phonebook_intel.resolve_anchor_wwid("") is None
    assert phonebook_intel.resolve_anchor_wwid(None) is None


# ---------------------------------------------------------------------------
# Row-spanning regression (#214). Real Intel Phonebook rows for entries
# without an email end with "|-" instead of a mailto anchor. The previous
# single-pass regex consumed across the row boundary and attached the
# next row's email to the previous row's name.
# ---------------------------------------------------------------------------

INTERLEAVED_HTML = """
<html><body><pre>
-<input type="checkbox" name="cookie" value="11795389"> <a href="phonebook?e=Axlrud%2c%20Pavel&k=11795389&f=ALL&d=ALL&b=y">Axlrud, <b>Pavel</b></a>|-|<a href="phonebook?e=LC11&f=ALL&d=ALL&k=1">LC11</a>|-|-
-<input type="checkbox" name="cookie" value="11060107"> <a href="phonebook?e=BASS%2c%20PAVEL&k=11060107&f=ALL&d=ALL&b=y">BASS, <b>PAVEL</b></a>|-|<a href="phonebook?e=LC11&f=ALL&d=ALL&k=1">LC11</a>|LC2-4M|<a href="mailto:pavelx.bass@intel.com"><b>pavel</b>x.bass@intel.com</a>
-<input type="checkbox" name="cookie" value="12169417"> <a href="phonebook?e=BOLGARI%2c%20PAVEL&k=12169417&f=ALL&d=ALL&b=y">BOLGARI, <b>PAVEL</b></a>|-|<a href="phonebook?e=LC24&f=ALL&d=ALL&k=1">LC24</a>|-|-
-<input type="checkbox" name="cookie" value="12145788"> <a href="phonebook?e=Barel%2c%20Noam%20Avishay&k=12145788&f=ALL&d=ALL&b=y">Barel, Noam Avishay</a>|-|<a href="phonebook?e=IDC9&f=ALL&d=ALL&k=1">IDC9</a>|-|<a href="mailto:noam.avishay.barel@intel.com">noam.avishay.barel@intel.com</a>
</pre></body></html>
"""


def test_parse_html_skips_emailless_rows_no_cross_pairing():
    hits = phonebook_intel._parse_html(INTERLEAVED_HTML)
    by_email = {h.email: h for h in hits}
    # Email-less rows (Axlrud, Bolgari) are dropped — only the two with
    # real Intel addresses survive.
    assert set(by_email) == {
        "pavelx.bass@intel.com", "noam.avishay.barel@intel.com",
    }
    # Critical assertion (#214): each surviving email is paired with its
    # OWN row's name, not the previous email-less row's name.
    assert by_email["pavelx.bass@intel.com"].display == "PAVEL BASS"
    assert by_email["noam.avishay.barel@intel.com"].display == "Noam Avishay Barel"
    # WWIDs come from the row marker, not from the wrong row.
    assert by_email["pavelx.bass@intel.com"].wwid == "11060107"
    assert by_email["noam.avishay.barel@intel.com"].wwid == "12145788"


# ---------------------------------------------------------------------------
# First-name-only filtering (#215). Phonebook substring-matches BookName
# AND org-chart metadata server-side, so a query like "Pavel" returns
# Ioana Pavel (Pavel = lastname) and Barak Agam (Pavel only appears
# elsewhere in his team). We filter client-side so neither can ever
# become the closest candidate.
# ---------------------------------------------------------------------------

PAVEL_HTML = """
<html><body><pre>
-<input type="checkbox" name="cookie" value="1001"> <a href="phonebook?e=FRIDMAN%2c%20Pavel&k=1001&f=ALL">FRIDMAN, <b>Pavel</b></a>|-|<a href="phonebook?e=X&k=1">X</a>|-|<a href="mailto:pavel.fridman@intel.com">pavel.fridman@intel.com</a>
-<input type="checkbox" name="cookie" value="1002"> <a href="phonebook?e=PAVEL%2c%20Ioana&k=1002&f=ALL">PAVEL, Ioana</a>|-|<a href="phonebook?e=X&k=1">X</a>|-|<a href="mailto:ioana.pavel@intel.com">ioana.pavel@intel.com</a>
-<input type="checkbox" name="cookie" value="1003"> <a href="phonebook?e=AGAM%2c%20Barak&k=1003&f=ALL">AGAM, Barak</a>|-|<a href="phonebook?e=X&k=1">X</a>|-|<a href="mailto:barak.agam@intel.com">barak.agam@intel.com</a>
</pre></body></html>
"""


def test_parse_html_captures_first_name():
    hits = phonebook_intel._parse_html(PAVEL_HTML)
    by_email = {h.email: h for h in hits}
    assert by_email["pavel.fridman@intel.com"].first_name == "Pavel"
    assert by_email["ioana.pavel@intel.com"].first_name == "Ioana"
    assert by_email["barak.agam@intel.com"].first_name == "Barak"


def test_filter_by_first_name_drops_lastname_and_metadata_matches():
    hits = phonebook_intel._parse_html(PAVEL_HTML)
    filtered = phonebook_intel.filter_by_first_name(hits, "Pavel")
    assert [h.email for h in filtered] == ["pavel.fridman@intel.com"]


def test_filter_by_first_name_case_insensitive_and_partial():
    hits = phonebook_intel._parse_html(PAVEL_HTML)
    # Lowercase, partial substring still matches "Pavel" first name.
    assert {h.email for h in phonebook_intel.filter_by_first_name(hits, "pav")} == {
        "pavel.fridman@intel.com",
    }
    # Empty query → pass-through.
    assert len(phonebook_intel.filter_by_first_name(hits, "")) == len(hits)


def test_filter_by_first_name_prefix_only_rejects_mid_name_substrings():
    """Regression for #222: substring match was matching '@admin' against
    'Padmini' (because 'admin' is a substring of 'padmini'). Switched to
    prefix match so only first names starting with the query match."""
    hits = [
        phonebook_intel.IntelPhonebookHit(
            display="Padmini Last", first_name="Padmini",
            idsid="padminix", email="padmini@intel.com", wwid="W1",
        ),
        phonebook_intel.IntelPhonebookHit(
            display="Diana Last", first_name="Diana",
            idsid="dianax", email="diana@intel.com", wwid="W2",
        ),
    ]
    assert phonebook_intel.filter_by_first_name(hits, "admin") == []
    assert phonebook_intel.filter_by_first_name(hits, "ana") == []
    assert [h.first_name for h in phonebook_intel.filter_by_first_name(hits, "Pad")] == ["Padmini"]
    assert [h.first_name for h in phonebook_intel.filter_by_first_name(hits, "Dia")] == ["Diana"]


def test_filter_by_first_name_keeps_no_comma_rows():
    """Some rows have no comma in the link text (lowercase contractor
    rows like 'pavel mikhlin'). Treat the whole string as the first
    name so we don't over-filter."""
    no_comma = """
    <html><body><pre>
    -<input type="checkbox" name="cookie" value="2001"> <a href="phonebook?e=pavel%20mikhlin&k=2001&f=ALL">pavel mikhlin</a>|-|<a href="phonebook?e=X&k=1">X</a>|-|<a href="mailto:pavel.mikhlin@intel.com">pavel.mikhlin@intel.com</a>
    </pre></body></html>
    """
    hits = phonebook_intel._parse_html(no_comma)
    assert hits[0].first_name.lower() == "pavel mikhlin"
    assert phonebook_intel.filter_by_first_name(hits, "Pavel") == hits


def test_resolver_uses_first_name_filter_before_distance(monkeypatch, tmp_path):
    """End-to-end: bare ``@Pavel`` against three candidates (one true
    Pavel + Ioana Pavel + Barak Agam) resolves to the true Pavel even
    if Ioana/Barak would be closer in the org tree."""
    from app.phonebook import Phonebook

    pb_file = tmp_path / "pb.json"
    pb_file.write_text("{}")
    pb = Phonebook(path=pb_file)

    monkeypatch.setattr(phonebook_intel, "_fetch", lambda u, t: PAVEL_HTML)
    phonebook_intel.cache_clear()
    phonebook_intel.detail_cache_clear()
    # No anchor → after first-name filter only Pavel Fridman remains,
    # which is a unique single-hit win regardless of distance.
    entry, candidates = pb.resolve("@pavel")
    assert entry is not None
    assert entry.email == "pavel.fridman@intel.com"
    assert candidates == []


# ---------------------------------------------------------------------------
# manager_email backfill (#217). Resolved scraper hits should carry the
# email of their immediate manager so the Kanban Send Email modal can
# offer a "CC managers" checkbox.
# ---------------------------------------------------------------------------

def test_manager_email_for_wwid_returns_managers_email(monkeypatch):
    monkeypatch.setattr(settings, "phonebook_scraper_enabled", True, raising=False)
    phonebook_intel.detail_cache_clear()
    phonebook_intel.detail_cache_seed([
        ("100", {"wwid": "100", "mgr_wwid": "200", "email": "child@intel.com"}),
        ("200", {"wwid": "200", "mgr_wwid": "300", "email": "boss@intel.com"}),
    ])
    assert phonebook_intel.manager_email_for_wwid("100") == "boss@intel.com"


def test_manager_email_for_wwid_none_when_no_manager(monkeypatch):
    monkeypatch.setattr(settings, "phonebook_scraper_enabled", True, raising=False)
    phonebook_intel.detail_cache_clear()
    # No mgr_wwid => no manager (CEO etc.).
    phonebook_intel.detail_cache_seed([
        ("100", {"wwid": "100", "mgr_wwid": None, "email": "ceo@intel.com"}),
    ])
    assert phonebook_intel.manager_email_for_wwid("100") is None


def test_resolver_populates_manager_email(monkeypatch, tmp_path):
    """Phonebook.resolve()'s scraper path should backfill manager_email
    on the returned PhonebookEntry so the frontend can CC them."""
    from app.phonebook import Phonebook

    monkeypatch.setattr(settings, "phonebook_scraper_enabled", True, raising=False)
    pb_file = tmp_path / "pb.json"
    pb_file.write_text("{}")
    pb = Phonebook(path=pb_file)

    monkeypatch.setattr(phonebook_intel, "_fetch", lambda u, t: PAVEL_HTML)
    phonebook_intel.cache_clear()
    phonebook_intel.detail_cache_clear()
    # Seed manager chain: Pavel Fridman (WWID 1001) → manager 9001.
    phonebook_intel.detail_cache_seed([
        ("1001", {"wwid": "1001", "mgr_wwid": "9001", "email": "pavel.fridman@intel.com"}),
        ("9001", {"wwid": "9001", "mgr_wwid": None, "email": "elad.yitav@intel.com"}),
    ])
    entry, _ = pb.resolve("@pavel")
    assert entry is not None
    assert entry.manager_email == "elad.yitav@intel.com"
