"""Test-wide fixtures.

We force ``vn.settings.CONFIG_PATH`` to a non-existent temp path for
every test so the user's real ``~/.veganotes/config`` (which may have
``gamify=off`` set) doesn't leak into the test run.
"""
from __future__ import annotations

import pytest

from vn import settings as _vn_settings
from vn import cli as _cli


@pytest.fixture(autouse=True)
def _isolate_vn_config(monkeypatch, tmp_path_factory):
    cfg = tmp_path_factory.mktemp("vn-cfg") / "config"
    monkeypatch.setattr(_vn_settings, "CONFIG_PATH", cfg)
    # Reset cli's snapshot so commands that don't otherwise touch
    # _settings start from defaults.
    monkeypatch.setattr(_cli, "_settings", _vn_settings.Settings())
