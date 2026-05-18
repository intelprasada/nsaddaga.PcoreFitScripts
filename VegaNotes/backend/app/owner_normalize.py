"""Owner-token canonicalization for the indexer (#174).

The lexer emits raw owner strings exactly as the user typed them
(``Prasad``, ``Addagarla``, ``nsaddaga``, ``PRASAD``, etc.). The
indexer calls :func:`canonical_idsid` on every such string before
creating a ``User`` row, so all spellings of the same person
collapse to a single identity in the database. Downstream views
("My Tasks", calendar, owner-filter queries) then group correctly
without any UI-level merging.

Scraper lookups are intentionally **not** consulted from this path
— indexing runs frequently (every save, every reindex) and must
be deterministic, fast, and offline-safe. Only the local curated
phonebook (``backend/data/phonebook.json``) is consulted. Tokens
that don't resolve locally are passed through unchanged so nothing
is silently dropped; callers can later upgrade to the scraper for
interactive UX (Send Email, picker, etc.).
"""
from __future__ import annotations

from .phonebook import get_phonebook


def canonical_idsid(token: str) -> tuple[str, str]:
    """Resolve ``token`` to a canonical IDSID via the local phonebook.

    Returns ``(name, status)`` where ``status`` is one of:
      * ``"resolved"``   — exact unique alias/idsid match found.
      * ``"ambiguous"``  — multiple curated entries claim this alias.
      * ``"unresolved"`` — no curated entry; ``name`` is the input
        with any leading ``@`` and surrounding whitespace stripped.

    The returned ``name`` is always safe to use as a ``User.name``
    primary key — for resolved tokens it is the canonical idsid;
    for ambiguous and unresolved tokens it preserves the user's
    original spelling (post-strip) so nothing is silently merged
    or dropped.
    """
    raw = (token or "").strip()
    if raw.startswith("@"):
        raw = raw[1:].strip()
    if not raw:
        return "", "unresolved"
    pb = get_phonebook()
    entry, candidates = pb.resolve(raw, local_only=True)
    if entry is not None:
        return entry.idsid, "resolved"
    if candidates:
        return raw, "ambiguous"
    return raw, "unresolved"
