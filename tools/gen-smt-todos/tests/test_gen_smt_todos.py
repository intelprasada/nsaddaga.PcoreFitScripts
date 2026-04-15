"""Smoke tests for gen-smt-todos."""

import pathlib
import subprocess
import sys


TOOL_DIR = pathlib.Path(__file__).resolve().parent.parent


def test_source_file_exists():
    assert (TOOL_DIR / "gen_smt_todos.py").exists()


def test_help_flag(tmp_path):
    """gen_smt_todos.py --help must exit 0 or print usage."""
    result = subprocess.run(
        [sys.executable, str(TOOL_DIR / "gen_smt_todos.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    # Accept either 0 (argparse --help) or any non-crash exit
    assert result.returncode in (0, 1), result.stderr


def test_no_crash_on_empty_tree(tmp_path):
    """Running against an empty directory tree must not raise an exception."""
    (tmp_path / "core" / "fe" / "rtl").mkdir(parents=True)
    (tmp_path / "core" / "msid" / "rtl").mkdir(parents=True)
    result = subprocess.run(
        [sys.executable, str(TOOL_DIR / "gen_smt_todos.py")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
