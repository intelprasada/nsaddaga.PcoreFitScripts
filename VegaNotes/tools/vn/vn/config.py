"""Credential / endpoint loading for `vn`.

Lookup order (highest precedence first):

1. Environment variables: ``VEGANOTES_URL``, ``VEGANOTES_USER``, ``VEGANOTES_PASS``.
2. ``~/.veganotes/credentials`` — INI file with a ``[default]`` section
   (or another section selected via ``--profile`` / ``VEGANOTES_PROFILE``).

Example credentials file::

    [default]
    url = http://localhost:8000
    user = alice
    password = s3cret
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


CREDENTIALS_PATH = Path.home() / ".veganotes" / "credentials"


@dataclass
class Credentials:
    url: str
    user: str
    password: str


class CredentialsError(RuntimeError):
    pass


def _read_file(profile: str) -> dict[str, str]:
    if not CREDENTIALS_PATH.exists():
        return {}
    cp = configparser.ConfigParser()
    try:
        cp.read(CREDENTIALS_PATH)
    except configparser.Error as e:
        raise CredentialsError(f"failed to parse {CREDENTIALS_PATH}: {e}") from e
    if profile not in cp:
        return {}
    return {k.lower(): v for k, v in cp[profile].items()}


def load_credentials(profile: Optional[str] = None) -> Credentials:
    profile = profile or os.environ.get("VEGANOTES_PROFILE", "default")
    file_vals = _read_file(profile)

    url = os.environ.get("VEGANOTES_URL") or file_vals.get("url")
    user = os.environ.get("VEGANOTES_USER") or file_vals.get("user")
    password = os.environ.get("VEGANOTES_PASS") or file_vals.get("password")

    missing = [n for n, v in (("url", url), ("user", user), ("password", password)) if not v]
    if missing:
        raise CredentialsError(
            "missing credentials: " + ", ".join(missing)
            + f". Set VEGANOTES_URL/USER/PASS or populate {CREDENTIALS_PATH} "
            f"(profile [{profile}])."
        )

    assert url and user and password  # for type-checkers
    return Credentials(url=url.rstrip("/"), user=user, password=password)
