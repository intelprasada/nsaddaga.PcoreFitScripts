"""Tests for markdown round-trip helpers."""
from app.markdown_ops import update_task_status, replace_attr


def test_replace_existing_status():
    md = "- !task Foo #status todo #owner alice\n"
    out = update_task_status(md, 0, "in-progress")
    assert "#status in-progress" in out
    assert out.endswith("\n")


def test_append_when_absent():
    md = "- !task Foo #owner alice\n"
    out = update_task_status(md, 0, "done")
    assert out.rstrip("\n").endswith("#status done")


def test_preserves_other_lines():
    md = "# Title\n- !task A #status todo\n- !task B\n"
    out = update_task_status(md, 1, "done")
    assert out.splitlines()[0] == "# Title"
    assert out.splitlines()[2] == "- !task B"
    assert "#status done" in out.splitlines()[1]


def test_replace_attr_eta():
    md = "- !task A #eta 2026-01-01\n"
    out = replace_attr(md, 0, "eta", "2026-12-31")
    assert "#eta 2026-12-31" in out
    assert "2026-01-01" not in out


def test_roll_to_next_week_strips_done_and_bumps_title_only():
    from app.markdown_ops import roll_to_next_week
    md = (
        "# Sprint ww16\n"
        "@nancy\n"
        "\n"
        "!task Done parent #status done #eta ww16.5\n"
        "\t!AR finished sub #status done\n"
        "\n"
        "!task Active #status wip #eta ww16.3\n"
        "\t!AR still open #status todo\n"
        "\n"
        "!task Future #status todo #eta ww17.2\n"
        "!task Continuation done\n"
        "\t#status done\n"
        "\t#eta ww16.1\n"
    )
    new_md, new_base, cur, nxt = roll_to_next_week(md, "sprint-ww16.md")
    assert (cur, nxt) == (16, 17)
    assert new_base == "sprint-ww17.md"
    # done parent + its (also-done) nested item dropped
    assert "Done parent" not in new_md
    assert "finished sub" not in new_md
    # done task whose status is on a continuation line is also dropped
    assert "Continuation done" not in new_md
    # active items survive
    assert "Active" in new_md
    assert "still open" in new_md
    # title bumped
    assert "# Sprint ww17" in new_md
    # ETA values are NOT bumped — user reviews/marks done or sets new ETA
    assert "#eta ww16.3" in new_md
    assert "#eta ww17.2" in new_md
    assert "#eta ww17.3" not in new_md


def test_roll_keeps_parent_when_children_unfinished():
    """A parent line literally written as `#status done` whose children are
    not done should survive the roll — parser-level rollup downgrades it."""
    from app.markdown_ops import roll_to_next_week
    md = (
        "# Sprint ww20\n"
        "!task Mostly done parent #status done #eta ww20.1\n"
        "\t!AR remaining work #status todo\n"
    )
    new_md, _, _, _ = roll_to_next_week(md, "x-ww20.md")
    assert "Mostly done parent" in new_md
    assert "remaining work" in new_md


def test_roll_rejects_non_ww_filename():
    from app.markdown_ops import roll_to_next_week
    import pytest
    with pytest.raises(ValueError):
        roll_to_next_week("# x\n", "no-week-tag.md")
