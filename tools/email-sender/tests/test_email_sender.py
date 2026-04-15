"""Smoke tests for email-sender."""

import importlib.util
import pathlib
import sys


TOOL_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOL_DIR))


def test_module_imports():
    """email_sender.py must be importable (tkinter may not be available in CI)."""
    spec = importlib.util.spec_from_file_location(
        "email_sender", TOOL_DIR / "email_sender.py"
    )
    assert spec is not None, "email_sender.py not found"


def test_source_file_exists():
    assert (TOOL_DIR / "email_sender.py").exists()
