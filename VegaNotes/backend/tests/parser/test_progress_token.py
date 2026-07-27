"""Parser coverage for the ``#progress`` recurring metric token (#320).

`#progress` is a single-valued attribute that carries an ``N`` counter,
an ``N/D`` ratio, or ``N/D label`` where ``label`` is a single short
word.  It shares the multi-word lexer path with ``#status`` / ``#note``
so the optional trailing label is captured on the same line without
being confused for prose.
"""
from __future__ import annotations

from app.parser import parse


def _first_task(md: str) -> dict:
    return parse(md)["tasks"][0]


def test_progress_ratio() -> None:
    task = _first_task("# H\n!task Ship #progress 12/35\n")
    assert task["attrs"]["progress"] == "12/35"


def test_progress_ratio_with_label() -> None:
    task = _first_task("# H\n!task Ship #progress 30/54 fixed\n")
    assert task["attrs"]["progress"] == "30/54 fixed"


def test_progress_bare_counter() -> None:
    task = _first_task("# H\n!task Ship #progress 42\n")
    assert task["attrs"]["progress"] == "42"


def test_progress_stops_at_next_attribute() -> None:
    """Trailing tokens after ``#progress`` must be picked up by *their*
    parsers, not swallowed into the progress value."""
    task = _first_task(
        "# H\n!task Ship #progress 12/35 fixed #eta 2026-01-01 @admin\n"
    )
    assert task["attrs"]["progress"] == "12/35 fixed"
    assert task["attrs"]["eta"] == "2026-01-01"
    assert task["attrs"]["owner"] == ["admin"]


def test_progress_coexists_with_link_tokens() -> None:
    task = _first_task(
        "# H\n"
        "!task Debug #progress 30/54 fixed #hsd 12345 #priority p1\n"
    )
    assert task["attrs"]["progress"] == "30/54 fixed"
    assert task["attrs"]["hsd"] == ["12345"]
    assert task["attrs"]["priority"] == "p1"


def test_progress_single_valued_last_wins() -> None:
    """Two ``#progress`` on one line collapse to the last (single-valued)."""
    task = _first_task("# H\n!task Ship #progress 1/2 #progress 3/4\n")
    assert task["attrs"]["progress"] == "3/4"
