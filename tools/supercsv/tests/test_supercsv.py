"""Smoke tests for supercsv."""

import pathlib
import sys


TOOL_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOL_DIR))


def test_source_files_exist():
    for name in ("supercsv.py", "email_sender.py", "filtered_table.py",
                 "font_manager.py", "theme_manager.py"):
        assert (TOOL_DIR / name).exists(), f"{name} missing"


def test_supertracker_is_separate_tool():
    """supertracker.py must NOT live in the supercsv tool dir; it has its own tool."""
    assert not (TOOL_DIR / "supertracker.py").exists(), \
        "supertracker.py belongs in tools/supertracker/, not tools/supercsv/"


def test_wildcard_filter_matches_all_rows():
    """A bare '*' column-filter expression should match every row."""
    import pandas as pd
    from filtered_table import _apply_col_filter

    s = pd.Series(["foo", "bar", "", "baz"])
    mask = _apply_col_filter(s, "*")
    assert mask.tolist() == [True, True, True, True]

    # Whitespace around the star is also accepted.
    mask = _apply_col_filter(s, "  *  ")
    assert mask.tolist() == [True, True, True, True]


def test_wildcard_does_not_break_substring_or_empty():
    """Empty string still all-True; non-wildcard substrings unaffected."""
    import pandas as pd
    from filtered_table import _apply_col_filter

    s = pd.Series(["foo", "bar", "foobar"])
    assert _apply_col_filter(s, "").tolist() == [True, True, True]
    assert _apply_col_filter(s, "foo").tolist() == [True, False, True]
