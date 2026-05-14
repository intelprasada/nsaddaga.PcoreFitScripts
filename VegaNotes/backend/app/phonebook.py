"""Phonebook: alias-tolerant owner-token resolution backed by a JSON file.

MVP for issue #174 — local JSON map of canonical IDSID → entry, with
case-insensitive alias lookup. Used by the Kanban "Send Email" flow (#210)
to resolve `@nsaddaga` / `@Prasad` style mentions to email addresses, and
will eventually back parser-level owner normalization.

Schema (``backend/data/phonebook.json``):

    {
      "nsaddaga": {
        "idsid": "nsaddaga",
        "display": "Prasad Addagarla",
        "email":   "prasad.addagarla@intel.com",
        "aliases": ["prasad", "addagarla", "prasad addagarla"],
        "manager_email": "manager@intel.com"   // optional
      },
      ...
    }

Loading is lazy + cached. ``mtime`` of the JSON is checked on every
``resolve()`` so an admin can hot-edit the file without bouncing the
backend (NFS-friendly: stat-based, not inotify-based).
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .config import settings

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


@dataclass(frozen=True)
class PhonebookEntry:
    idsid: str
    display: str
    email: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    manager_email: str | None = None

    def to_dict(self) -> dict:
        return {
            "idsid": self.idsid,
            "display": self.display,
            "email": self.email,
            "aliases": list(self.aliases),
            "manager_email": self.manager_email,
        }


def _phonebook_path() -> Path:
    """Default location: ``<data_dir>/phonebook.json``. Falls back to the
    bundled module-relative ``data/phonebook.json`` for dev/test."""
    candidate = settings.data_dir / "phonebook.json"
    if candidate.exists():
        return candidate
    return Path(__file__).parent.parent / "data" / "phonebook.json"


class Phonebook:
    """Thread-safe cache of phonebook entries with mtime-based hot reload."""

    def __init__(self, path: Path | None = None):
        self._path = path or _phonebook_path()
        self._lock = threading.RLock()
        self._mtime: float | None = None
        self._by_idsid: dict[str, PhonebookEntry] = {}
        self._by_alias: dict[str, list[str]] = {}  # alias-lc -> [idsid, ...]

    @property
    def path(self) -> Path:
        return self._path

    def _maybe_reload(self) -> None:
        with self._lock:
            try:
                mtime = self._path.stat().st_mtime
            except FileNotFoundError:
                if self._mtime is not None:
                    log.warning("Phonebook file disappeared: %s", self._path)
                self._mtime = None
                self._by_idsid.clear()
                self._by_alias.clear()
                return
            if mtime == self._mtime:
                return
            self._load_locked()
            self._mtime = mtime

    def _load_locked(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.error("Phonebook load failed (%s): %s", self._path, e)
            return
        if not isinstance(raw, dict):
            log.error("Phonebook root must be an object, got %s", type(raw).__name__)
            return
        by_idsid: dict[str, PhonebookEntry] = {}
        by_alias: dict[str, list[str]] = {}
        for key, val in raw.items():
            if not isinstance(val, dict):
                continue
            idsid = (val.get("idsid") or key or "").strip().lower()
            email = (val.get("email") or "").strip().lower()
            display = (val.get("display") or idsid or "").strip()
            if not idsid or not email or not _EMAIL_RE.match(email):
                log.warning("Phonebook entry skipped (bad idsid/email): %r", key)
                continue
            aliases_raw = val.get("aliases") or []
            if not isinstance(aliases_raw, list):
                aliases_raw = []
            aliases = tuple(
                a.strip().lower() for a in aliases_raw
                if isinstance(a, str) and a.strip()
            )
            mgr = val.get("manager_email")
            mgr = mgr.strip().lower() if isinstance(mgr, str) and mgr.strip() else None
            entry = PhonebookEntry(
                idsid=idsid, display=display, email=email,
                aliases=aliases, manager_email=mgr,
            )
            by_idsid[idsid] = entry
            # Auto-register canonical lookups.
            for alias in {idsid, display.lower(), *aliases, email}:
                if not alias:
                    continue
                by_alias.setdefault(alias, []).append(idsid)
        # Dedupe alias targets while preserving order.
        for k, v in by_alias.items():
            seen, out = set(), []
            for t in v:
                if t not in seen:
                    seen.add(t)
                    out.append(t)
            by_alias[k] = out
        self._by_idsid = by_idsid
        self._by_alias = by_alias
        log.info("Phonebook loaded: %d entries from %s", len(by_idsid), self._path)

    def resolve(self, token: str) -> tuple[PhonebookEntry | None, list[PhonebookEntry]]:
        """Return ``(entry, candidates)``.

        - ``entry`` is the unambiguous match (or ``None`` if missing/ambiguous).
        - ``candidates`` is the full list of matches when ambiguous (>1).
          Empty when ``entry`` is set or when nothing matched.

        Match rules (case-insensitive, in order):
        1. Strip leading ``@``.
        2. Email-shaped tokens match by ``email`` field.
        3. Exact ``idsid`` match wins outright.
        4. Otherwise alias map lookup. 1 hit → resolved. >1 hit → ambiguous.
        """
        self._maybe_reload()
        if not token:
            return None, []
        t = token.strip()
        if t.startswith("@"):
            t = t[1:].strip()
        if not t:
            return None, []
        key = t.lower()
        with self._lock:
            # Idsid wins.
            if key in self._by_idsid:
                return self._by_idsid[key], []
            hits = self._by_alias.get(key, [])
            if len(hits) == 1:
                return self._by_idsid[hits[0]], []
            if len(hits) > 1:
                return None, [self._by_idsid[h] for h in hits]
            return None, []

    def resolve_many(self, tokens: Iterable[str]) -> dict:
        """Bulk resolve. Returns:
            {
              "resolved":  {token: entry_dict, ...},
              "ambiguous": {token: [entry_dict, ...], ...},
              "unresolved": [token, ...],
            }
        Tokens are returned in their original (untrimmed) form as keys.
        """
        resolved: dict[str, dict] = {}
        ambiguous: dict[str, list[dict]] = {}
        unresolved: list[str] = []
        seen: set[str] = set()
        for tok in tokens:
            if tok in seen:
                continue
            seen.add(tok)
            entry, candidates = self.resolve(tok)
            if entry is not None:
                resolved[tok] = entry.to_dict()
            elif candidates:
                ambiguous[tok] = [c.to_dict() for c in candidates]
            else:
                unresolved.append(tok)
        return {"resolved": resolved, "ambiguous": ambiguous, "unresolved": unresolved}

    def all_entries(self) -> list[PhonebookEntry]:
        self._maybe_reload()
        with self._lock:
            return list(self._by_idsid.values())


# Process-wide singleton — cheap to import everywhere.
_PHONEBOOK: Phonebook | None = None
_SINGLETON_LOCK = threading.Lock()


def get_phonebook() -> Phonebook:
    global _PHONEBOOK
    if _PHONEBOOK is None:
        with _SINGLETON_LOCK:
            if _PHONEBOOK is None:
                _PHONEBOOK = Phonebook()
    return _PHONEBOOK


def reset_phonebook_for_test(path: Path | None = None) -> Phonebook:
    """Replace the singleton — used by tests that point at a temp JSON."""
    global _PHONEBOOK
    with _SINGLETON_LOCK:
        _PHONEBOOK = Phonebook(path=path)
    return _PHONEBOOK
