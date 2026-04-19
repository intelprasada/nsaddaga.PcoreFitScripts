"""Tests for v3 hierarchical attribute inheritance and @user shorthand."""
from app.parser import parse


def test_at_user_shorthand_is_owner():
    md = "- !task Foo @alice\n"
    out = parse(md)
    assert out["tasks"][0]["attrs"]["owner"] == ["alice"]


def test_context_line_at_top_propagates():
    md = "@nancy\n- !task A\n- !task B\n"
    out = parse(md)
    for t in out["tasks"]:
        assert t["attrs"]["owner"] == ["nancy"]


def test_context_cleared_by_blank_line():
    md = "@nancy\n- !task A\n\n- !task B\n"
    out = parse(md)
    a, b = out["tasks"]
    assert a["attrs"].get("owner") == ["nancy"]
    assert "owner" not in b["attrs"]


def test_parent_task_owner_inherited_by_subtasks():
    md = """\
- !task Parent #owner alice
  - !task Child1
  - !task Child2 #owner bob
"""
    out = parse(md)
    parent, c1, c2 = out["tasks"]
    assert parent["attrs"]["owner"] == ["alice"]
    assert c1["attrs"]["owner"] == ["alice"]
    assert sorted(c2["attrs"]["owner"]) == ["alice", "bob"]


def test_eta_is_not_inherited_from_parent():
    md = """\
- !task Parent #eta 2026-05-01
  - !task Child
"""
    out = parse(md)
    parent, child = out["tasks"]
    assert parent["attrs"].get("eta") == "2026-05-01"
    assert "eta" not in child["attrs"]


def test_blocked_by_prose_still_attaches_to_current():
    md = """\
- !task Migrate index #owner alice
  Blocked by #task wire-up-sso and tracked in #link rfc-2026-04
"""
    out = parse(md)
    refs = out["tasks"][0]["refs"]
    slugs = sorted(r["dst_slug"] for r in refs)
    assert slugs == ["rfc-2026-04", "wire-up-sso"]


def test_email_in_text_is_not_at_user():
    md = "- !task Email alice@example.com about it\n"
    out = parse(md)
    # @example would NOT be matched (preceded by alphanumeric) so no owner.
    assert "owner" not in out["tasks"][0]["attrs"]


def test_context_line_with_multiple_tokens():
    md = "@nancy #project foo\n- !task A\n"
    out = parse(md)
    a = out["tasks"][0]
    assert a["attrs"]["owner"] == ["nancy"]
    assert a["attrs"]["project"] == ["foo"]
