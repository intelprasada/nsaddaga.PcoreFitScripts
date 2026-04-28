"""Per-profile vn CLI settings (gamification toggles, etc).

Stored in ``~/.veganotes/config`` as an INI file, mirroring the
credentials file. Keep it separate from credentials so the config
file can be checked in / shared via dotfiles without leaking secrets.

Currently exposes:

* ``gamify`` — master toggle. When ``off``, ``vn me ...`` commands
  short-circuit before hitting the API and write commands skip the
  celebration line. Server-side recording is unaffected; the user
  can flip it back on at any time and the prior data is still there.
* ``gamify.notify`` — when ``off``, write commands stay silent even
  if the API hands back a freshly-earned badge.

Both default to ``on`` for new profiles.

Example::

    [default]
    gamify = on
    gamify.notify = on
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


CONFIG_PATH = Path.home() / ".veganotes" / "config"

# Public list of (key, default-bool) tuples. The CLI uses this to render
# `vn config` listings deterministically and to decide which keys are
# valid to set.
KNOWN_BOOL_KEYS: tuple[tuple[str, bool], ...] = (
    ("gamify", True),
    ("gamify.notify", True),
)
_DEFAULTS: dict[str, bool] = dict(KNOWN_BOOL_KEYS)


@dataclass
class Settings:
    gamify_enabled: bool = True
    gamify_notify: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, str]) -> "Settings":
        return cls(
            gamify_enabled=_parse_bool(raw.get("gamify"), default=True),
            gamify_notify=_parse_bool(raw.get("gamify.notify"), default=True),
        )


def _parse_bool(val: Optional[str], *, default: bool) -> bool:
    if val is None:
        return default
    s = val.strip().lower()
    if s in {"1", "on", "true", "yes", "y"}:
        return True
    if s in {"0", "off", "false", "no", "n"}:
        return False
    return default


def _format_bool(b: bool) -> str:
    return "on" if b else "off"


def _resolve_path(path: Optional[Path]) -> Path:
    """Read CONFIG_PATH lazily so test fixtures that monkeypatch the
    module-level constant after import still take effect."""
    return path if path is not None else CONFIG_PATH


def _read_all(path: Optional[Path] = None) -> configparser.ConfigParser:
    p = _resolve_path(path)
    cp = configparser.ConfigParser()
    if p.exists():
        cp.read(p)
    return cp


def _profile_dict(profile: str, path: Optional[Path] = None) -> dict[str, str]:
    cp = _read_all(path)
    if profile not in cp:
        return {}
    return {k.lower(): v for k, v in cp[profile].items()}


def load_settings(profile: str = "default", *, path: Optional[Path] = None) -> Settings:
    """Load this profile's settings, applying defaults for missing keys."""
    return Settings.from_dict(_profile_dict(profile, path))


def get_setting(
    key: str, profile: str = "default", *, path: Optional[Path] = None,
) -> str:
    """Return the stored string value for ``key`` (or its default).

    Raises ``KeyError`` if ``key`` is not a known config key.
    """
    if key not in _DEFAULTS:
        raise KeyError(key)
    raw = _profile_dict(profile, path).get(key.lower())
    if raw is not None:
        return _format_bool(_parse_bool(raw, default=_DEFAULTS[key]))
    return _format_bool(_DEFAULTS[key])


def set_setting(
    key: str, value: str, profile: str = "default", *, path: Optional[Path] = None,
) -> str:
    """Persist ``key=value`` for ``profile``. Returns the canonical
    stored form ('on' / 'off').

    Raises ``KeyError`` if ``key`` is not known or ``ValueError`` if
    ``value`` doesn't parse as a boolean.
    """
    if key not in _DEFAULTS:
        raise KeyError(key)
    s = (value or "").strip().lower()
    if s in {"1", "on", "true", "yes", "y"}:
        parsed = True
    elif s in {"0", "off", "false", "no", "n"}:
        parsed = False
    else:
        raise ValueError(f"invalid boolean: {value!r}")
    canon = _format_bool(parsed)
    p = _resolve_path(path)
    cp = _read_all(p)
    if profile not in cp:
        cp[profile] = {}
    cp[profile][key] = canon
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        cp.write(f)
    return canon


def list_settings(profile: str = "default", *, path: Optional[Path] = None) -> list[tuple[str, str, bool]]:
    """Return ``[(key, value, is_default), ...]`` for every known key."""
    raw = _profile_dict(profile, path)
    out: list[tuple[str, str, bool]] = []
    for key, default in KNOWN_BOOL_KEYS:
        stored = raw.get(key.lower())
        if stored is None:
            out.append((key, _format_bool(default), True))
        else:
            out.append((key, _format_bool(_parse_bool(stored, default=default)), False))
    return out
