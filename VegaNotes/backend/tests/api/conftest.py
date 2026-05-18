"""Per-test-package fixtures.

Ensures the phonebook singleton points at a clean, test-controlled
file rather than the curated production phonebook bundled in the
repo. Without this, tests that auth as ``admin`` would have their
owner names canonicalized to ``nsaddaga`` via the production alias
map (#174 owner normalization). Tests that exercise the phonebook
itself override this fixture explicitly via
``reset_phonebook_for_test`` and re-establish their own state in
teardown.

Imports of ``app`` modules are kept inside the fixture body so the
test file's module-level ``os.environ`` setup (data dir, etc.)
runs before ``app.config`` is imported.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_phonebook():
    data_env = os.environ.get("VEGANOTES_DATA_DIR")
    if not data_env:
        yield
        return
    from app.phonebook import reset_phonebook_for_test  # lazy import
    pb = Path(data_env) / "phonebook.json"
    pre_existed = pb.exists()
    if not pre_existed:
        pb.parent.mkdir(parents=True, exist_ok=True)
        pb.write_text("{}", encoding="utf-8")
    reset_phonebook_for_test(pb)
    try:
        yield
    finally:
        reset_phonebook_for_test(None)
        if not pre_existed:
            try:
                pb.unlink()
            except FileNotFoundError:
                pass
