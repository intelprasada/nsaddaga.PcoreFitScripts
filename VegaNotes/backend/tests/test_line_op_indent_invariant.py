"""Property/invariant: every line-rewrite markdown op preserves a line's
leading indentation.

This is the invariant that was silently broken for months (T-800631): a task
or AR that keeps its content but loses a level of indent gets reparented by
the indent-driven parser, hiding it and its siblings. We now assert it
directly across the full matrix of ops × indentation styles — the shape real
notes actually use (deeply-nested ARs), not just the flat column-0 tasks the
older tests used.
"""
import pytest
from app.markdown_ops import (
    remove_attr, replace_attr, replace_multi_attr, update_task_status,
    add_tag, remove_tag, replace_task_title,
)


def lead(line: str) -> str:
    """Leading whitespace of a single line (no trailing newline needed)."""
    s = line.rstrip("\n")
    return s[: len(s) - len(s.lstrip())]


# Indentation styles that show up in real notes: tabs at several depths,
# space indents, and mixed. Each has >= 2 leading whitespace chars where it
# matters, which is exactly what the old `\s{2,}` collapse corrupted.
INDENTS = ["\t", "\t\t", "\t\t\t", "  ", "    ", "        ", "\t  ", "  \t"]

# (id, core line without indent/newline, op applied to line 0)
SCENARIOS = [
    ("remove_attr_eta",    "!AR foo #eta ww29 #status todo",              lambda md: remove_attr(md, 0, "eta")),
    ("remove_attr_prio",   "!AR foo #priority P1 #status todo",           lambda md: remove_attr(md, 0, "priority")),
    ("remove_attr_owner",  "!AR foo @alice #status todo",                 lambda md: remove_attr(md, 0, "owner")),
    ("remove_attr_url",    "!AR foo #url [L](https://x.io) #status todo", lambda md: remove_attr(md, 0, "url")),
    ("remove_attr_hsd",    "!AR foo #hsd 123 #status todo",               lambda md: remove_attr(md, 0, "hsd")),
    ("remove_attr_prog",   "!AR foo #progress 3/5 #status todo",          lambda md: remove_attr(md, 0, "progress")),
    ("replace_attr_eta",   "!AR foo #eta ww29",                          lambda md: replace_attr(md, 0, "eta", "ww30")),
    ("replace_attr_prio",  "!AR foo #priority P1",                       lambda md: replace_attr(md, 0, "priority", "P2")),
    ("multi_owner",        "!AR foo @alice #status todo",                lambda md: replace_multi_attr(md, 0, "owner", ["bob"])),
    ("multi_feature",      "!AR foo #feature x #status todo",            lambda md: replace_multi_attr(md, 0, "feature", ["y"])),
    ("multi_hsd_add",      "!AR foo #status todo",                       lambda md: replace_multi_attr(md, 0, "hsd", ["123"])),
    ("multi_jira_add",     "!AR foo #status todo",                       lambda md: replace_multi_attr(md, 0, "jira", ["ABC-1"])),
    ("multi_pr_add",       "!AR foo #status todo",                       lambda md: replace_multi_attr(md, 0, "pr", ["o/r#1"])),
    ("multi_url_add",      "!AR foo #status todo",                       lambda md: replace_multi_attr(md, 0, "url", ["[L](https://x.io)"])),
    ("multi_hsd_clear",    "!AR foo #hsd 123 #status todo",              lambda md: replace_multi_attr(md, 0, "hsd", [])),
    ("multi_owner_clear",  "!AR foo @alice #status todo",                lambda md: replace_multi_attr(md, 0, "owner", [])),
    ("status",             "!AR foo #status todo",                       lambda md: update_task_status(md, 0, "done")),
    ("add_tag",            "!AR foo #status todo",                       lambda md: add_tag(md, 0, "urgent")),
    ("remove_tag",         "!AR foo #urgent #status todo",               lambda md: remove_tag(md, 0, "urgent")),
    ("title",              "!AR foo #status todo",                       lambda md: replace_task_title(md, 0, "bar baz")),
]


@pytest.mark.parametrize("indent", INDENTS)
@pytest.mark.parametrize("sid,core,apply", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_line_op_preserves_leading_indent(indent, sid, core, apply):
    md = indent + core + "\n"
    out = apply(md)
    first = out.split("\n", 1)[0]
    assert lead(first) == indent, (
        f"{sid} @ indent={indent!r}: lead became {lead(first)!r} | line={first!r}"
    )


# Bullet-prefixed lines ("\t\t- !AR …") are common in these notes too. Verify
# the whitespace-collapse-prone attr ops keep the leading *whitespace* intact
# (the "- " bullet is body, not indent).
BULLET_INDENTS = ["\t\t", "    ", "\t  "]
BULLET_OPS = [
    ("remove_attr_eta", "- !AR foo #eta ww29 #status todo", lambda md: remove_attr(md, 0, "eta")),
    ("multi_hsd_add",   "- !AR foo #status todo",           lambda md: replace_multi_attr(md, 0, "hsd", ["123"])),
    ("remove_tag",      "- !AR foo #urgent #status todo",   lambda md: remove_tag(md, 0, "urgent")),
]


@pytest.mark.parametrize("indent", BULLET_INDENTS)
@pytest.mark.parametrize("sid,core,apply", BULLET_OPS, ids=[s[0] for s in BULLET_OPS])
def test_bulleted_line_op_preserves_leading_indent(indent, sid, core, apply):
    md = indent + core + "\n"
    out = apply(md)
    first = out.split("\n", 1)[0]
    assert lead(first) == indent, (
        f"{sid} @ bullet indent={indent!r}: lead became {lead(first)!r} | line={first!r}"
    )


def test_internal_double_space_still_collapses():
    """Guard the other direction: the fix must NOT stop collapsing genuine
    internal double-spaces left after a token is removed."""
    md = "\t\t!AR foo  bar   baz #eta ww29\n"
    out = remove_attr(md, 0, "eta")
    assert out == "\t\t!AR foo bar baz\n", repr(out)
