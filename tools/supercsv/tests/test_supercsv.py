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
