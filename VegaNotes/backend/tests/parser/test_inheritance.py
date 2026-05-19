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


def test_indented_attr_only_line_attaches_to_current_task_only():
    """Regression: a #eta line indented under a task must NOT propagate to
    sibling tasks declared later at a shallower indent."""
    md = (
        "@aboli\n"
        "\t!task First\n"
        "\t\t#eta 2026-04-17\n"
        "\t!task Second\n"
        "\t!task Third\n"
    )
    out = parse(md)
    first, second, third = out["tasks"]
    assert first["attrs"].get("eta") == "2026-04-17"
    assert "eta" not in second["attrs"]
    assert "eta" not in third["attrs"]
    # Owner from top-level @aboli still inherits to all three.
    for t in (first, second, third):
        assert t["attrs"].get("owner") == ["aboli"]


def test_parent_eta_rolls_up_to_max_child_eta():
    from app.parser import parse
    md = (
        "!task parent #eta 2026-04-22\n"
        "\t!AR a #eta 2026-04-25\n"
        "\t!AR b #eta 2026-04-23\n"
    )
    tasks = {t["title"]: t for t in parse(md)["tasks"]}
    assert tasks["parent"]["attrs_norm"]["eta"] == "2026-04-25"


def test_parent_eta_stays_when_already_latest():
    from app.parser import parse
    md = (
        "!task parent #eta 2026-05-01\n"
        "\t!AR a #eta 2026-04-25\n"
    )
    tasks = {t["title"]: t for t in parse(md)["tasks"]}
    assert tasks["parent"]["attrs_norm"]["eta"] == "2026-05-01"


def test_parent_done_downgrades_when_child_open():
    from app.parser import parse
    md = (
        "!task parent #status done\n"
        "\t!AR a #status todo\n"
    )
    tasks = {t["title"]: t for t in parse(md)["tasks"]}
    assert tasks["parent"]["status"] == "in-progress"


def test_parent_done_stays_when_all_children_done():
    from app.parser import parse
    md = (
        "!task parent #status done\n"
        "\t!AR a #status done\n"
    )
    tasks = {t["title"]: t for t in parse(md)["tasks"]}
    assert tasks["parent"]["status"] == "done"


def test_sibling_context_lines_at_same_indent_replace_each_other():
    """`#project gfc` then `#project jnc` at the same indent must NOT union;
    each scopes to the subsequent indented block until the next sibling
    overrides it. Tasks under @namratha (a new top-level @owner) must not
    inherit either project."""
    md = (
        "@aboli\n"
        "\t#project gfc\n"
        "\t\t!task A\n"
        "\t#project jnc\n"
        "\t\t!task B\n"
        "@namratha\n"
        "\t!task C\n"
    )
    out = parse(md)
    by_title = {t["title"]: t for t in out["tasks"]}
    assert by_title["A"]["attrs"].get("project") == ["gfc"]
    assert by_title["A"]["attrs"].get("owner") == ["aboli"]
    assert by_title["B"]["attrs"].get("project") == ["jnc"]
    assert by_title["B"]["attrs"].get("owner") == ["aboli"]
    # @namratha at indent 0 must cancel both deeper #project frames
    # AND replace the prior @aboli frame at the same indent.
    assert "project" not in by_title["C"]["attrs"]
    assert by_title["C"]["attrs"].get("owner") == ["namratha"]


# ---------------------------------------------------------------------------
# #226 — GAL "Last, First" support and quoted @"..." form.
# ---------------------------------------------------------------------------

def test_at_gal_form_lastname_first():
    md = "- !task Foo @Chatla, Niharika\n"
    out = parse(md)
    assert out["tasks"][0]["attrs"]["owner"] == ["Chatla, Niharika"]


def test_at_gal_form_with_outlook_digit_suffix():
    md = "- !task Foo @Chatla, Niharika1\n"
    out = parse(md)
    assert out["tasks"][0]["attrs"]["owner"] == ["Chatla, Niharika1"]


def test_at_gal_form_does_not_eat_lowercase_prose():
    """`@john, can you ...` must NOT be treated as compound — the second
    word is lowercase, so it's prose."""
    md = "- !task Foo @john, can you check?\n"
    out = parse(md)
    assert out["tasks"][0]["attrs"]["owner"] == ["john"]


def test_at_quoted_form():
    md = '- !task Foo @"Chatla, Niharika"\n'
    out = parse(md)
    assert out["tasks"][0]["attrs"]["owner"] == ["Chatla, Niharika"]


def test_at_quoted_form_two_token():
    md = '- !task Foo @"Niharika Chatla"\n'
    out = parse(md)
    assert out["tasks"][0]["attrs"]["owner"] == ["Niharika Chatla"]


# ---------------------------------------------------------------------------
# #233 — narrowed GAL heuristic must not eat prose words.
# ---------------------------------------------------------------------------

import pytest


@pytest.mark.parametrize(
    "md,expected_owner",
    [
        ("- !task t @alice, Thanks for the help\n", "alice"),
        ("- !task t @bob, OK lets merge\n", "bob"),
        ("- !task t @chris, I think this is fine\n", "chris"),
        ("- !task t @dave, Monday works\n", "dave"),
        ("- !task t @eve, PR is ready\n", "eve"),
    ],
)
def test_gal_heuristic_no_longer_eats_prose(md, expected_owner):
    out = parse(md)
    assert out["tasks"], f"task not parsed for {md!r}"
    task = out["tasks"][0]
    assert task["attrs"]["owner"] == [expected_owner]


def test_gal_heuristic_still_works_at_eol():
    """The legitimate "@Last, First" form at end-of-line still extends."""
    md = "- !task Foo @Addagarla, Prasad\n"
    out = parse(md)
    assert out["tasks"][0]["attrs"]["owner"] == ["Addagarla, Prasad"]


def test_gal_heuristic_still_works_before_next_token():
    """"@Last, First" followed by another #attr token still extends."""
    md = "- !task Foo @Addagarla, Prasad #status todo\n"
    out = parse(md)
    assert out["tasks"][0]["attrs"]["owner"] == ["Addagarla, Prasad"]


def test_gal_heuristic_still_works_before_next_owner():
    """"@Last, First @other" — first owner extends, second is its own token."""
    md = "- !task Foo @Last, First @bob\n"
    out = parse(md)
    assert out["tasks"][0]["attrs"]["owner"] == ["Last, First", "bob"]
