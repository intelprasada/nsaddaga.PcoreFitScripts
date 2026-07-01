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
    # Per-user preferred org subtrees (#225). Each entry is an idsid or
    # numeric WWID; candidates whose manager-chain passes through any of
    # these (or whose own WWID matches one) get a score discount during
    # scraper-based resolution. Resolution from idsid → WWID is lazy.
    prefer_subtrees: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "idsid": self.idsid,
            "display": self.display,
            "email": self.email,
            "aliases": list(self.aliases),
            "manager_email": self.manager_email,
            "prefer_subtrees": list(self.prefer_subtrees),
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
            prefer_raw = val.get("prefer_subtrees") or []
            if not isinstance(prefer_raw, list):
                prefer_raw = []
            prefer = tuple(
                p.strip() for p in prefer_raw
                if isinstance(p, str) and p.strip()
            )
            entry = PhonebookEntry(
                idsid=idsid, display=display, email=email,
                aliases=aliases, manager_email=mgr,
                prefer_subtrees=prefer,
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

    def resolve(
        self,
        token: str,
        *,
        anchor: str | None = None,
        local_only: bool = False,
    ) -> tuple[PhonebookEntry | None, list[PhonebookEntry]]:
        """Return ``(entry, candidates)``.

        - ``entry`` is the unambiguous match (or ``None`` if missing/ambiguous).
        - ``candidates`` is the full list of matches when ambiguous (>1).
          Empty when ``entry`` is set or when nothing matched.

        ``anchor`` (idsid / email / wwid) is used as the perspective for
        the org-distance ranking applied to scraper hits — if exactly one
        candidate is strictly closest in the management tree it wins
        outright, otherwise candidates are returned ranked-but-ambiguous.

        ``local_only=True`` skips the Tier-3 scraper fallback. Used by
        the indexer / parser canonicalization path (#174) which must be
        deterministic, fast and offline-safe.

        Match rules (case-insensitive, in order):
        1. Strip leading ``@``.
        2. Email-shaped tokens match by ``email`` field.
        3. Exact ``idsid`` match wins outright.
        4. Alias map lookup. 1 hit → resolved. >1 hit → ambiguous.
        5. Tier-3 fallback (#213): if scraper is enabled, hit the Intel
           Phonebook web app. 1 hit → resolved. >1 hits → optionally
           ranked by org distance from ``anchor``; closest unique → resolved.
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
        if local_only:
            return None, []
        # Tier-3: Intel phonebook scraper (no-op if disabled).
        return self._scraper_resolve(t, anchor=anchor)

    @staticmethod
    def _split_compound_token(token: str) -> tuple[str, str | None]:
        """Parse a lookup token into ``(given_name, surname)`` (#226).

        - GAL form ``"Last, First[ Middle...]"`` → ``(First, Last)``.
          The first comma splits surname (left) from given (right).
        - Two-token form ``"First Last"`` (no comma, exactly 2 tokens) →
          ``(First, Last)``.
        - Single token, three+ tokens, or empty/falsy → ``(token, None)``
          so the legacy single-name path is preserved unchanged.

        Multi-token last names (e.g. "van der Berg") aren't disambiguated
        further — we send the first token as given and the *rest* joined
        as surname for the GAL form, but for the no-comma form we only
        accept exactly two tokens to avoid mangling free-text like
        ``"Niharika Sanjay Kamath"``.
        """
        t = (token or "").strip()
        if not t:
            return t, None
        if "," in t:
            last, _, first = t.partition(",")
            last = last.strip()
            first = first.strip()
            if first and last:
                # First word of the right-of-comma side is the given
                # name; ignore middle names for the scraper query.
                given = first.split()[0]
                # Outlook GAL adds a numeric disambiguation suffix when
                # multiple people share a name (e.g. "Niharika1"). The
                # phonebook server's BookName has no such suffix, so the
                # substring search would miss. Strip trailing digits when
                # an explicit surname is present — the surname filter
                # narrows further so this can't broaden ambiguity.
                given = re.sub(r"\d+$", "", given) or given
                return given, last
            return t, None
        parts = t.split()
        if len(parts) == 2:
            given = re.sub(r"\d+$", "", parts[0]) or parts[0]
            return given, parts[1]
        return t, None

    def _scraper_resolve(
        self,
        query: str,
        *,
        anchor: str | None = None,
    ) -> tuple[PhonebookEntry | None, list[PhonebookEntry]]:
        # Imported lazily so the scraper module's settings access happens
        # after any test monkeypatching of ``settings``.
        from . import phonebook_intel
        # #226: pre-parse compound tokens into (given, surname).
        # GAL form "Last, First"  -> surname=Last,  given=First
        # Two-token "First Last"  -> given=First,   surname=Last
        # Single token            -> given=token,   surname=None (legacy path)
        given, surname = self._split_compound_token(query)
        try:
            hits = phonebook_intel.cached_lookup(given)
        except Exception as e:  # pragma: no cover — defensive
            log.warning("phonebook scraper raised on %r: %s", query, e)
            return None, []
        if not hits:
            return None, []
        # #271: exact-idsid short-circuit. If the caller typed a fully
        # qualified handle (linux idsid like ``avruddhu`` or dotted
        # corporate idsid like ``akash.kumar.vruddhula``) and the
        # scraper returned that person, use it directly — the name
        # filters below are only meaningful for name-shaped tokens and
        # would otherwise drop this exact match when the first name
        # doesn't share a prefix with the idsid.
        q_lower = query.strip().lower()
        exact_idsid = [
            h for h in hits
            if (h.linux_idsid or "").lower() == q_lower
            or (h.idsid or "").lower() == q_lower
        ]
        if len(exact_idsid) == 1:
            h = exact_idsid[0]
            return PhonebookEntry(
                idsid=(h.linux_idsid or h.idsid),
                display=h.display, email=h.email,
                manager_email=phonebook_intel.manager_email_for_wwid(h.wwid),
            ), []
        # First-name-only filter (#215). Phonebook returns last-name
        # matches and team-metadata matches too, which we never want for
        # an owner token like ``@Pavel``. Filter before ranking so a
        # mis-matched Ioana Pavel or Barak Agam can't accidentally be
        # the unique closest candidate.
        hits = phonebook_intel.filter_by_first_name(hits, given)
        # Surname filter (#226) — only when token carried an explicit
        # surname. Single-token path skips this so behavior is unchanged.
        if surname:
            hits = phonebook_intel.filter_by_last_name(hits, surname)
        if not hits:
            return None, []
        if len(hits) == 1:
            h = hits[0]
            return PhonebookEntry(
                idsid=(h.linux_idsid or h.idsid),
                display=h.display, email=h.email,
                manager_email=phonebook_intel.manager_email_for_wwid(h.wwid),
            ), []
        # Multiple hits: try to disambiguate via seniority-weighted distance.
        ranked_full: list[tuple] = []
        if anchor:
            try:
                anchor_wwid = phonebook_intel.resolve_anchor_wwid(anchor)
                if anchor_wwid:
                    from .config import settings as _s
                    pen = int(getattr(_s, "phonebook_seniority_penalty", 3))
                    bias_score = int(getattr(_s, "phonebook_subtree_bias", 0))
                    bias_wwids = self._anchor_bias_wwids(anchor) if bias_score else []
                    ranked_full = phonebook_intel.rank_by_distance_full(
                        hits, anchor_wwid, seniority_penalty=pen,
                        subtree_bias_wwids=bias_wwids,
                        subtree_bias_score=bias_score,
                    )
            except Exception as e:  # pragma: no cover — defensive
                log.warning("phonebook anchor rank failed for %r: %s",
                            anchor, e)
                ranked_full = []
        # Pick a strict winner if the lowest score is unique (#224 — uses
        # the seniority-weighted score, not raw LCA distance).
        if ranked_full:
            scores = [s for _, _u, _d, _r, s in ranked_full if s is not None]
            if scores:
                best = min(scores)
                winners = [h for h, _u, _d, _r, s in ranked_full if s == best]
                if len(winners) == 1:
                    h = winners[0]
                    return PhonebookEntry(
                        idsid=(h.linux_idsid or h.idsid),
                        display=h.display, email=h.email,
                        manager_email=phonebook_intel.manager_email_for_wwid(h.wwid),
                    ), []
            ordered_hits = [h for h, _u, _d, _r, _s in ranked_full]
        else:
            ordered_hits = hits
        entries = [
            PhonebookEntry(
                idsid=(h.linux_idsid or h.idsid),
                display=h.display, email=h.email,
            )
            for h in ordered_hits
        ]
        return None, entries

    def _anchor_entry(self, anchor: str) -> "PhonebookEntry | None":
        """Find the curated phonebook entry matching ``anchor`` (idsid,
        email, or alias). Used by the subtree-bias ranker (#225) to look
        up the anchor's preferred subtrees."""
        if not anchor:
            return None
        a = anchor.strip().lstrip("@").lower()
        if not a:
            return None
        self._maybe_reload()
        with self._lock:
            if a in self._by_idsid:
                return self._by_idsid[a]
            hits = self._by_alias.get(a, [])
            if len(hits) == 1:
                return self._by_idsid[hits[0]]
        return None

    def _anchor_bias_wwids(self, anchor: str) -> list[str]:
        """Resolve the anchor's ``prefer_subtrees`` list (mix of idsids
        and WWIDs) to a flat list of WWIDs (#225). Numeric tokens are
        passed through; idsids are looked up via the scraper. Failures
        are logged and skipped — bias is best-effort, never fatal."""
        entry = self._anchor_entry(anchor)
        if not entry or not entry.prefer_subtrees:
            return []
        from . import phonebook_intel
        out: list[str] = []
        for tok in entry.prefer_subtrees:
            t = (tok or "").strip()
            if not t:
                continue
            if t.isdigit():
                out.append(t)
                continue
            try:
                wwid = phonebook_intel.resolve_anchor_wwid(t)
            except Exception as e:  # pragma: no cover — defensive
                log.warning("bias subtree %r failed to resolve: %s", t, e)
                wwid = None
            if wwid:
                out.append(wwid)
            else:
                log.info("bias subtree %r did not resolve for anchor=%s",
                         t, entry.idsid)
        # Dedupe while preserving order.
        seen, dedup = set(), []
        for w in out:
            if w not in seen:
                seen.add(w); dedup.append(w)
        return dedup

    def resolve_many(
        self,
        tokens: Iterable[str],
        *,
        anchor: str | None = None,
    ) -> dict:
        """Bulk resolve. Returns:
            {
              "resolved":  {token: entry_dict, ...},
              "ambiguous": {token: [entry_dict, ...], ...},
              "unresolved": [token, ...],
            }
        Tokens are returned in their original (untrimmed) form as keys.
        ``anchor`` is forwarded to ``resolve()`` for org-distance ranking
        of scraper hits — typically the requesting user's idsid/email.
        """
        resolved: dict[str, dict] = {}
        ambiguous: dict[str, list[dict]] = {}
        unresolved: list[str] = []
        seen: set[str] = set()
        for tok in tokens:
            if tok in seen:
                continue
            seen.add(tok)
            entry, candidates = self.resolve(tok, anchor=anchor)
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
