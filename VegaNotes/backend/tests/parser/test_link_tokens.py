"""Tests for #314 link-token parsing (url / hsd / jira / pr).

Confirms:
- Each token is recognized as multi-valued.
- Values with typical URL syntax (://, /, #, -) are preserved verbatim.
- Multiple values on the same task line accumulate into a list.
- Values with whitespace after are NOT swallowed (lexer stops at ws).
- Tokens co-exist with existing tokens (eta / priority / owner) without
  cross-contamination.
"""
from __future__ import annotations

from app.parser import parse


def _task(body: str) -> dict:
    return parse(body)["tasks"][0]


def test_hsd_token_is_recognized_and_multi_valued():
    body = "# T\n!task Ship #hsd 1234567 #hsd 9876543\n"
    t = _task(body)
    assert t["attrs"]["hsd"] == ["1234567", "9876543"]


def test_jira_token_preserves_uppercase_key():
    body = "# T\n!task Ship #jira ABC-42\n"
    t = _task(body)
    assert t["attrs"]["jira"] == ["ABC-42"]


def test_pr_token_preserves_hash_in_value():
    """``#pr owner/repo#42`` must survive lex+parse verbatim; the ``#42``
    fragment is a URL suffix here, not a new token.
    """
    body = "# T\n!task Ship #pr intelprasada/veganotes#309\n"
    t = _task(body)
    assert t["attrs"]["pr"] == ["intelprasada/veganotes#309"]


def test_url_token_preserves_scheme_slashes_and_query():
    body = "# T\n!task Ship #url https://example.com/foo/bar?x=1&y=2\n"
    t = _task(body)
    assert t["attrs"]["url"] == ["https://example.com/foo/bar?x=1&y=2"]


def test_url_token_supports_label_prefix():
    body = "# T\n!task Ship #url Design:https://example.com/design\n"
    t = _task(body)
    assert t["attrs"]["url"] == ["Design:https://example.com/design"]


def test_link_tokens_coexist_with_other_attrs():
    body = (
        "# T\n"
        "!task Ship v1 @alice #priority p1 #eta 2026-W20 "
        "#hsd 1234567 #jira ABC-42 #pr owner/repo#7 #url https://example.com\n"
    )
    t = _task(body)
    assert t["attrs"]["owner"] == ["alice"]
    assert t["attrs"]["priority"] == "p1"
    assert t["attrs"]["eta"] == "2026-W20"
    assert t["attrs"]["hsd"] == ["1234567"]
    assert t["attrs"]["jira"] == ["ABC-42"]
    assert t["attrs"]["pr"] == ["owner/repo#7"]
    assert t["attrs"]["url"] == ["https://example.com"]


def test_link_token_value_stops_at_whitespace():
    """Given ``#hsd 123 #jira ABC-1`` the hsd value must be ``123``, not
    swallow the following ``#jira`` token.
    """
    body = "# T\n!task Ship #hsd 123 #jira ABC-1\n"
    t = _task(body)
    assert t["attrs"]["hsd"] == ["123"]
    assert t["attrs"]["jira"] == ["ABC-1"]


def test_existing_link_token_task_ref_still_creates_a_ref_not_a_url():
    """The pre-existing ``#link`` token (task-to-task ref) must not have
    been broken by adding the four new tokens.
    """
    body = "# T\n!task A #id T-AAA0001\n!task B #link T-AAA0001\n"
    parsed = parse(body)
    task_b = parsed["tasks"][1]
    refs = [r for r in task_b["refs"] if r["kind"] == "link"]
    assert len(refs) == 1
    assert refs[0]["dst_slug"] in {"t-aaa0001", "t-aaa-0001"} or "t-aaa" in refs[0]["dst_slug"]
