"""Smoke tests for supertracker."""

import pathlib
import subprocess
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
TOOL_DIR = REPO_ROOT / "tools" / "supertracker"
SUPERCSV_DIR = REPO_ROOT / "tools" / "supercsv"


def test_source_file_exists():
    assert (TOOL_DIR / "supertracker.py").exists()


def test_supercsv_dep_exists():
    """supertracker depends on supercsv's widget layer; verify it is present."""
    assert (SUPERCSV_DIR / "supercsv.py").exists()
    assert (SUPERCSV_DIR / "filtered_table.py").exists()


def test_help_flag():
    """supertracker.py --help must exit 0."""
    env_patch = {**__import__("os").environ,
                 "PYTHONPATH": str(SUPERCSV_DIR)}
    result = subprocess.run(
        [sys.executable, str(TOOL_DIR / "supertracker.py"), "--help"],
        capture_output=True,
        text=True,
        env=env_patch,
    )
    assert result.returncode == 0, result.stderr
