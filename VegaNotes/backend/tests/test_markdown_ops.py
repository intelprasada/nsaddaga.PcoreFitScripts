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
