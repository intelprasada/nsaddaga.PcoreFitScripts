"""Top-level pytest configuration.

NIS lookups (``ypcat passwd``) are disabled during the test suite via
``VEGANOTES_NIS_DISABLED=1`` so phonebook tests get deterministic
``linux_idsid=""`` regardless of whether the test host is on the Intel
corporate network. Tests that exercise the NIS path explicitly should
monkeypatch ``_load_nis_passwd_map`` directly.
"""
from __future__ import annotations

import os

os.environ.setdefault("VEGANOTES_NIS_DISABLED", "1")
