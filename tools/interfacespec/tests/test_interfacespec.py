"""Smoke tests for interfacespec."""

import pathlib
import subprocess
import sys


TOOL_DIR = pathlib.Path(__file__).resolve().parent.parent


def test_pipeline_entry_point_exists():
    assert (TOOL_DIR / "run_cluster_pipeline.py").exists()


def test_gui_entry_point_exists():
    assert (TOOL_DIR / "qtgui" / "main.py").exists()


def test_pipeline_scripts_present():
    """All numbered pipeline scripts 01–07 must be present."""
    scripts = list(TOOL_DIR.glob("0[1-7]_*.py"))
    assert len(scripts) >= 7, f"Expected ≥7 pipeline scripts, found {len(scripts)}"


def test_pipeline_help():
    """run_cluster_pipeline.py --help must exit 0."""
    result = subprocess.run(
        [sys.executable, str(TOOL_DIR / "run_cluster_pipeline.py"), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
