"""Tests for #333 tag add/remove markdown ops."""
import pytest
from app.markdown_ops import add_tag, remove_tag


def test_add_bare_tag_to_task_line():
    md = "- !task Foo #status todo\n"
    out = add_tag(md, 0, "urgent")
    assert "#urgent" in out
    assert out.rstrip("\n").endswith("#urgent")


def test_add_tag_strips_leading_hash():
    md = "- !task Foo\n"
    assert "#hot" in add_tag(md, 0, "#hot")


def test_add_tag_is_idempotent():
    md = "- !task Foo #urgent\n"
    assert add_tag(md, 0, "urgent") == md


def test_add_tag_rejects_reserved_key():
    md = "- !task Foo\n"
    with pytest.raises(ValueError):
        add_tag(md, 0, "status")
    with pytest.raises(ValueError):
        add_tag(md, 0, "eta")


def test_add_tag_rejects_invalid_name():
    md = "- !task Foo\n"
    with pytest.raises(ValueError):
        add_tag(md, 0, "two words")
    with pytest.raises(ValueError):
        add_tag(md, 0, "1leadingdigit")


def test_remove_bare_tag_from_task_line():
    md = "- !task Foo #urgent #status todo\n"
    out = remove_tag(md, 0, "urgent")
    assert "#urgent" not in out
    assert "#status todo" in out
    # no double spaces left behind
    assert "  " not in out.split("!task")[1]


def test_remove_bare_tag_does_not_touch_valued_attr_of_same_prefix():
    # `#hotfix` must not be removed when removing bare `#hot`.
    md = "- !task Foo #hot #hotfix\n"
    out = remove_tag(md, 0, "hot")
    assert "#hotfix" in out
    assert "#hot " not in out and not out.rstrip("\n").endswith("#hot")


def test_remove_valued_tag_with_value():
    md = "- !task Foo #area fabric\n"
    out = remove_tag(md, 0, "area", "fabric")
    assert "#area" not in out


def test_remove_tag_from_continuation_line():
    md = "- !task Foo\n\t#urgent\n\t#note keep me\n"
    out = remove_tag(md, 0, "urgent")
    assert "#urgent" not in out
    assert "#note keep me" in out


def test_remove_absent_tag_is_noop():
    md = "- !task Foo #status todo\n"
    assert remove_tag(md, 0, "absent") == md
