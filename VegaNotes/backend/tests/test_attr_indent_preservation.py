"""Regression: attr/tag removal must preserve a line's leading indentation.

Bug (T-800631): adding an #hsd to an AR ran replace_multi_attr -> remove_attr,
whose `re.sub(r"\\s{2,}", " ", body)` collapsed the leading indent (e.g. 2 tabs
-> 1 space). That changed the AR's indent level, so the parser reparented it as
a sibling of its task and every other AR became a child of the moved AR —
the task card then showed zero ARs even though the .md still had them.
"""
from app.markdown_ops import (
    remove_attr, replace_multi_attr, remove_tag, replace_attr,
)


def _lead(line: str) -> str:
    body = line.rstrip("\n")
    return body[: len(body) - len(body.lstrip())]


def test_remove_attr_preserves_tab_indent():
    md = "\t\t!AR #id T-X foo #eta ww29 #status todo\n"
    out = remove_attr(md, 0, "eta")
    assert _lead(out) == "\t\t", repr(out)
    assert "#eta" not in out
    assert "#status todo" in out


def test_remove_attr_preserves_space_indent():
    md = "    !AR #id T-X foo #eta ww29\n"
    out = remove_attr(md, 0, "eta")
    assert _lead(out) == "    ", repr(out)


def test_remove_attr_still_collapses_internal_double_space():
    md = "\t\t!AR foo  bar #eta ww29\n"
    out = remove_attr(md, 0, "eta")
    assert _lead(out) == "\t\t"
    # internal double space collapsed, indent untouched
    assert "foo bar" in out


def test_add_link_attr_to_indented_ar_keeps_indent():
    """The exact T-800631 scenario: adding #hsd to a 2-tab AR."""
    md = "\t\t!AR #id T-W7M4KZ MRN counter bucket debug  #status todo\n"
    out = replace_multi_attr(md, 0, "hsd", ["14028322043"])
    assert _lead(out) == "\t\t", repr(out)
    assert "#hsd 14028322043" in out
    assert "debug #status" in out  # internal double space collapsed


def test_remove_tag_preserves_indent():
    md = "\t\t!AR foo #urgent #status todo\n"
    out = remove_tag(md, 0, "urgent")
    assert _lead(out) == "\t\t", repr(out)
    assert "#urgent" not in out


def test_replace_attr_preserves_indent():
    md = "\t\t!AR foo #status todo\n"
    out = replace_attr(md, 0, "status", "done")
    assert _lead(out) == "\t\t"
    assert "#status done" in out


def test_hierarchy_survives_link_add_end_to_end():
    """Parse-level proof: after adding #hsd to the first AR, all ARs keep an
    indent deeper than the task, so they stay its children (not reparented)."""
    from app.parser import parse
    md = (
        "# note\n"
        "\t!task #id T-PARENT Debug\n"
        "\t\t!AR #id T-A first ar #status todo\n"
        "\t\t!AR #id T-B second ar #status todo\n"
        "\t\t!AR #id T-C third ar #status todo\n"
    )
    md2 = replace_multi_attr(md, 2, "hsd", ["12345"])
    # Every AR line must still carry two tabs.
    for line in md2.splitlines():
        if "!AR" in line:
            assert line.startswith("\t\t"), f"AR reparented: {line!r}"
    tasks = parse(md2)["tasks"]
    task = next(t for t in tasks if t["kind"] == "task")
    ars = [t for t in tasks if t["kind"] == "ar"]
    assert len(ars) == 3
    # Each AR must be strictly deeper than the task — the bug dropped the edited
    # AR to the task's own indent, which reparents it and hides the siblings.
    for ar in ars:
        assert ar["indent"] > task["indent"], (ar["slug"], ar["indent"], task["indent"])
    # All three ARs share the same indent (none got flattened).
    assert len({ar["indent"] for ar in ars}) == 1
