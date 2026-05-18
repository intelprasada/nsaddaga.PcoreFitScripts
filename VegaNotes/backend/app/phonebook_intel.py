"""Intel Phonebook web scraper — anonymous, no-auth tier-3 resolver (#213).

Hits ``https://phonebook.intel.com/cgi-bin/phonebook?e=<query>`` and parses
the HTML row format. The page is anonymous-readable from the corp network
(no Kerberos, no service account), so this is the simplest possible source
of truth for owner resolution.

Behaviour gated by ``settings.phonebook_scraper_enabled`` — defaults to
``False`` so K8s deployments behind firewalls never make network calls
unless explicitly opted in. All HTTP errors are swallowed (logged at
``warning``) and treated as "no candidates" so a network blip degrades
gracefully to "unresolved" instead of 5xx-ing the API.

Returned candidates are normalized to the same shape as
:class:`app.phonebook.PhonebookEntry.to_dict` so the resolver can splice
them into existing response payloads with no shape juggling.
"""
from __future__ import annotations

import html as _html
import logging
import os
import re
import subprocess
import time
import threading
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Iterable

from .config import settings

log = logging.getLogger(__name__)

# NOTE: legacy single-pass regex (`_ROW_RE`) replaced by row-split + per-row
# parsing in `_parse_html` to fix #214 (cross-row mis-pairing when an
# email-less contractor row sits between two normal rows).

# A name like "Arlagadda Narasimharaju, <b>Niharika</b>" — strip <b>/<i>
# tags and HTML-decode. Phonebook URL-encodes commas as %2c (lowercase).
_TAG_RE = re.compile(r"<[^>]+>")

# Per-row split marker (#214). Each result row starts with this checkbox
# input. Splitting on it first ensures the row-internal regexes never
# accidentally span a row boundary when an email-less contractor row
# sits between two normal rows.
_ROW_SPLIT_RE = re.compile(
    r'-<input\s+type="checkbox"\s+name="cookie"\s+value="(\d+)">',
)

# Within ONE row: the name anchor is the first ``<a phonebook?e=...&k=WWID...>``
# whose ``k`` matches the row's leading WWID. Building/facility links
# also use ``phonebook?e=...&k=1...`` so we anchor on the WWID itself.
def _name_re_for_wwid(wwid: str) -> re.Pattern:
    return re.compile(
        r'<a\s+href="phonebook\?e=[^"]*&k=' + re.escape(wwid)
        + r'[^"]*">(?P<name_html>[^<]*(?:<[^>]+>[^<]*)*?)</a>',
        re.DOTALL,
    )

_MAILTO_RE = re.compile(r'<a\s+href="mailto:([^"?\s]+@[A-Za-z0-9.\-]+)"')

# Detail page (b=y): "MgrWWID : <a ...>10711985</a>". The WWID itself
# always shows as a digit-only run inside an <a href="phonebook?e=<wwid>...">
# anchor, so we anchor on the MgrWWID label and walk forward to the digits.
_MGR_WWID_RE = re.compile(
    r"MgrWWID\s*:\s*<a[^>]*>\s*(?P<wwid>\d{5,12})\s*</a>",
    re.IGNORECASE,
)
_OWN_WWID_RE = re.compile(
    r"\bWWID\s*:\s*<a[^>]*>\s*(?:<b>\s*)?(?P<wwid>\d{5,12})",
    re.IGNORECASE,
)
_DETAIL_EMAIL_RE = re.compile(
    r'mailto:(?P<email>[^"?\s]+@[A-Za-z0-9.\-]+)',
)


@dataclass(frozen=True)
class IntelPhonebookHit:
    display: str
    email: str
    idsid: str
    wwid: str
    # First-name portion of the original "LASTNAME, Firstname" BookName
    # (#215). Used for the first-name-only filter so bare tokens like
    # ``@Pavel`` don't match people with Pavel as a *last* name (Ioana
    # Pavel) or with Pavel only in their team metadata (Barak Agam).
    # Falls back to ``display`` when the source had no comma.
    first_name: str = ""
    # Last-name portion of the original "LASTNAME, Firstname" BookName
    # (#226). Used by ``filter_by_last_name`` so GAL "Last, First"
    # tokens and compound "First Last" tokens can be disambiguated by
    # surname. Empty when source had no comma.
    last_name: str = ""
    # True Linux/Unix login idsid for the user, resolved via NIS
    # (``ypcat passwd``) from the WWID. For some users this differs
    # from the email local-part (e.g. ``sachin.bhattad`` email →
    # ``sbhattad`` login). Empty when NIS lookup is unavailable
    # (off-corp-network, container without NIS, etc.) or when the
    # WWID isn't in the passwd map. When non-empty, callers should
    # prefer it as the canonical ``User.name`` so login ids line up
    # with task ownership.
    linux_idsid: str = ""

    def to_dict(self) -> dict:
        # Match the shape of PhonebookEntry.to_dict so callers can splice
        # results into the existing /api/phonebook/resolve response.
        return {
            "idsid": self.linux_idsid or self.idsid,
            "email_idsid": self.idsid,  # email local-part, kept for back-compat
            "linux_idsid": self.linux_idsid,
            "display": self.display,
            "email": self.email,
            "aliases": [],
            "manager_email": None,
            "wwid": self.wwid,
            "source": "intel_phonebook",
        }


# ────────────────────────── NIS / Linux idsid lookup ───────────────────────
# Many Intel users have a Linux login id that differs from their email
# local-part (e.g. ``sachin.bhattad`` email → ``sbhattad`` login). The NIS
# passwd map exposes the WWID→login mapping in the GECOS field, so a
# single ``ypcat passwd`` call gives us a full WWID→login table.
#
# Cached for the process lifetime — the NIS map changes rarely and the
# fetch is ~50ms for ~100K entries. Disable via VEGANOTES_NIS_DISABLED=1
# for tests / offline / non-corp environments where ``ypcat`` is missing.

_NIS_WWID_TO_LOGIN_LOCK = threading.Lock()
_NIS_WWID_TO_LOGIN: dict[str, str] | None = None


def _load_nis_passwd_map() -> dict[str, str]:
    """Return ``{wwid: linux_login}`` from ``ypcat passwd``.

    Cached after the first successful call. Returns ``{}`` on any error
    so callers degrade gracefully to email-local-part as the idsid.

    The passwd line format on VAS is::

        login:VAS:wwid:gid:fullname,wwid[,...]:homedir:shell
    """
    global _NIS_WWID_TO_LOGIN
    with _NIS_WWID_TO_LOGIN_LOCK:
        if _NIS_WWID_TO_LOGIN is not None:
            return _NIS_WWID_TO_LOGIN
        if os.environ.get("VEGANOTES_NIS_DISABLED") == "1":
            _NIS_WWID_TO_LOGIN = {}
            return _NIS_WWID_TO_LOGIN
        try:
            proc = subprocess.run(
                ["ypcat", "passwd"],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            log.warning("NIS lookup unavailable (ypcat passwd failed): %s", e)
            _NIS_WWID_TO_LOGIN = {}
            return _NIS_WWID_TO_LOGIN
        out: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            parts = line.split(":")
            if len(parts) < 7:
                continue
            login = parts[0]
            gecos = parts[4]
            fields = gecos.split(",")
            if len(fields) < 2:
                continue
            wwid = fields[1].strip()
            if wwid.isdigit():
                out.setdefault(wwid, login)
        log.info("NIS passwd map loaded: %d WWID→login entries", len(out))
        _NIS_WWID_TO_LOGIN = out
        return _NIS_WWID_TO_LOGIN


def reset_nis_cache_for_test() -> None:
    """Clear the in-process NIS cache. Test-only."""
    global _NIS_WWID_TO_LOGIN
    with _NIS_WWID_TO_LOGIN_LOCK:
        _NIS_WWID_TO_LOGIN = None


def linux_idsid_for_wwid(wwid: str) -> str:
    """Return the Linux login idsid for ``wwid``, or ``""`` if unknown."""
    if not wwid:
        return ""
    return _load_nis_passwd_map().get(wwid, "")


def _parse_html(body: str) -> list[IntelPhonebookHit]:
    """Row-based parser (#214).

    Splits the body on the per-row checkbox marker, then within each
    row pulls one name anchor (the one keyed by that row's WWID) and
    one ``mailto:`` anchor. Rows without a mailto are dropped — we have
    no way to email contractors who don't have an Intel address. The
    legacy single-pass regex used to walk across row boundaries when an
    email-less row sat between two normal rows, attaching the next
    row's email to the previous row's name.
    """
    out: list[IntelPhonebookHit] = []
    seen: set[str] = set()
    parts = _ROW_SPLIT_RE.split(body)
    # parts = [preamble, wwid1, body1, wwid2, body2, ...]
    for i in range(1, len(parts), 2):
        wwid = parts[i]
        # Truncate this row's body at the next checkbox marker. Although
        # split() already isolates rows, parts[i+1] is the text from
        # *after* the WWID-bearing checkbox up to the next one, so it's
        # already the per-row content. Defensive: bail at first sub-row
        # marker just in case.
        row = parts[i + 1] if i + 1 < len(parts) else ""
        # Skip footer text (after the last row, the table is closed).
        # The footer references author@intel.com?subject=phonebook which
        # the email regex already rejects, but shed it here too so the
        # name regex doesn't spuriously match a non-data anchor.
        if "</pre>" in row:
            row = row.split("</pre>", 1)[0]
        email_m = _MAILTO_RE.search(row)
        if not email_m:
            continue  # email-less row — skip
        email = email_m.group(1).strip().lower()
        if not email or email in seen:
            continue
        name_m = _name_re_for_wwid(wwid).search(row)
        if not name_m:
            # Defensive: row had a mailto but no recognizable name link.
            # Fall back to the email's local-part as display.
            display = email.split("@", 1)[0].replace(".", " ").title()
        else:
            display = _html.unescape(_TAG_RE.sub("", name_m.group("name_html"))).strip()
        # BookName format is "LASTNAME, Firstname[ Middle...]". Capture the
        # first-name half BEFORE we flip to "First Last" display order, so
        # downstream first-name-only filtering (#215) can distinguish
        # Pavel-as-firstname from Pavel-as-lastname.
        if "," in display:
            last, _, first = display.partition(",")
            first_name = first.strip()
            last_name = last.strip()
            display = f"{first.strip()} {last.strip()}".strip()
        else:
            # No comma — either fallback display from the email local-part
            # or a non-standard row. Treat the whole string as the first
            # name so we don't over-filter rare formats.
            first_name = display.strip()
            last_name = ""
        seen.add(email)
        out.append(IntelPhonebookHit(
            display=display,
            email=email,
            idsid=email.split("@", 1)[0].lower(),
            wwid=wwid,
            first_name=first_name,
            last_name=last_name,
            linux_idsid=linux_idsid_for_wwid(wwid),
        ))
    return out


def _fetch(url: str, timeout: float) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "VegaNotes/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        log.warning("Intel phonebook fetch failed for %s: %s", url, e)
        return None


def lookup(query: str, *, timeout: float | None = None) -> list[IntelPhonebookHit]:
    """Live HTTP lookup. Returns ``[]`` on any network failure or if the
    scraper is disabled in settings."""
    if not getattr(settings, "phonebook_scraper_enabled", False):
        return []
    q = (query or "").strip()
    if not q:
        return []
    base = getattr(settings, "phonebook_scraper_url",
                   "https://phonebook.intel.com")
    to = timeout if timeout is not None else getattr(
        settings, "phonebook_scraper_timeout_s", 8.0,
    )
    url = f"{base.rstrip('/')}/cgi-bin/phonebook?e={urllib.parse.quote(q)}"
    body = _fetch(url, to)
    if body is None:
        return []
    return _parse_html(body)


# ---------------------------------------------------------------------------
# Process-wide TTL cache. Keys are the lowercased query string.
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, list[IntelPhonebookHit]]] = {}
_CACHE_LOCK = threading.RLock()


def _ttl() -> float:
    return float(getattr(settings, "phonebook_cache_ttl_s", 86400))


def cached_lookup(query: str) -> list[IntelPhonebookHit]:
    """Lookup with TTL cache. Negative results are cached too (short
    queries that miss shouldn't re-hit the wire on every keystroke)."""
    if not getattr(settings, "phonebook_scraper_enabled", False):
        return []
    key = (query or "").strip().lower()
    if not key:
        return []
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and (now - hit[0]) < _ttl():
            return hit[1]
    fresh = lookup(query)
    with _CACHE_LOCK:
        _CACHE[key] = (now, fresh)
    return fresh


def filter_by_first_name(
    hits: list[IntelPhonebookHit], query: str,
) -> list[IntelPhonebookHit]:
    """Drop candidates whose first name doesn't start with the query (#215, #222).

    Phonebook server-side substring-matches BookName *and* org-chart
    metadata, so a query like ``Pavel`` returns Ioana Pavel (lastname
    match) and Barak Agam (teammate-of-a-Pavel match). We only ever
    want first-name matches, so post-filter client-side.

    Prefix match (case-insensitive) so partial typing like ``@Niha``
    still matches Niharika and ``@Pav`` matches Pavel — but mid-name
    accidents like ``@admin`` matching "Padmini" or ``@ana`` matching
    "Diana" are rejected. Empty queries return ``hits`` unchanged.
    """
    q = (query or "").strip().lower()
    if not q:
        return hits
    return [h for h in hits if (h.first_name or "").lower().startswith(q)]


def filter_by_last_name(
    hits: list[IntelPhonebookHit], surname: str,
) -> list[IntelPhonebookHit]:
    """Drop candidates whose last name doesn't start with ``surname`` (#226).

    Companion to :func:`filter_by_first_name`. Used by the resolver when
    the lookup token carries an explicit surname — either as GAL form
    (``"Last, First"``) or as a two-token compound (``"First Last"``).
    Hits with no recorded ``last_name`` (rare fallback rows that had no
    comma in BookName) are kept so we don't over-filter unusual data.

    Empty ``surname`` returns ``hits`` unchanged.
    """
    s = (surname or "").strip().lower()
    if not s:
        return hits
    return [
        h for h in hits
        if not (h.last_name or "")
        or (h.last_name or "").lower().startswith(s)
    ]


def cache_clear() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def cache_seed(entries: Iterable[tuple[str, list[IntelPhonebookHit]]]) -> None:
    """Test helper — preload the cache without going to the network."""
    now = time.monotonic()
    with _CACHE_LOCK:
        for q, hits in entries:
            _CACHE[q.strip().lower()] = (now, list(hits))


# ---------------------------------------------------------------------------
# Org-chart distance ranking (#213).
#
# Given an "anchor" person (typically the requesting user) and a set of
# candidate hits for an ambiguous name, we rank candidates by their
# distance in the management tree:
#
#     distance = depth(anchor -> LCA) + depth(candidate -> LCA)
#
# This makes a direct reportee/peer rank closer than your manager's
# manager's reportee, which is usually the right disambiguation when
# someone writes "@Niharika" in a note.
#
# Implementation: walk MgrWWID up from each side, capped at MAX_DEPTH
# levels. Per-WWID manager lookups are cached for the same TTL as the
# scrape cache so a session of resolving a Kanban board with N owners
# touches the network at most O(N + chain_length) times instead of
# O(N * chain_length).
# ---------------------------------------------------------------------------

MAX_CHAIN_DEPTH = 12

_DETAIL_CACHE: dict[str, tuple[float, dict | None]] = {}
_DETAIL_LOCK = threading.RLock()


def _fetch_detail_html(wwid: str) -> str | None:
    base = getattr(settings, "phonebook_scraper_url",
                   "https://phonebook.intel.com")
    to = float(getattr(settings, "phonebook_scraper_timeout_s", 8.0))
    url = (f"{base.rstrip('/')}/cgi-bin/phonebook?"
           f"e={urllib.parse.quote(wwid)}&k={urllib.parse.quote(wwid)}&b=y")
    return _fetch(url, to)


def fetch_detail(wwid: str) -> dict | None:
    """Return ``{"wwid", "mgr_wwid", "email"}`` or ``None`` on failure.
    Cached with the same TTL as the row scraper."""
    if not wwid:
        return None
    if not getattr(settings, "phonebook_scraper_enabled", False):
        return None
    key = str(wwid).strip()
    if not key:
        return None
    now = time.monotonic()
    ttl = _ttl()
    with _DETAIL_LOCK:
        hit = _DETAIL_CACHE.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
    html_body = _fetch_detail_html(key)
    if html_body is None:
        with _DETAIL_LOCK:
            _DETAIL_CACHE[key] = (now, None)
        return None
    mgr_m = _MGR_WWID_RE.search(html_body)
    own_m = _OWN_WWID_RE.search(html_body)
    email_m = _DETAIL_EMAIL_RE.search(html_body)
    detail = {
        "wwid": (own_m.group("wwid") if own_m else key),
        "mgr_wwid": (mgr_m.group("wwid") if mgr_m else None),
        "email": (email_m.group("email").lower() if email_m else None),
    }
    with _DETAIL_LOCK:
        _DETAIL_CACHE[key] = (now, detail)
    return detail


def manager_chain(wwid: str, *, max_depth: int = MAX_CHAIN_DEPTH) -> list[str]:
    """Return the management chain starting at ``wwid`` (inclusive),
    walking up via ``MgrWWID`` until the root is reached, ``max_depth``
    is hit, or a fetch fails. The first element is always ``wwid``.
    A self-loop or repeat (server bug) terminates the walk."""
    chain: list[str] = []
    seen: set[str] = set()
    cur: str | None = (wwid or "").strip() or None
    for _ in range(max_depth + 1):
        if not cur or cur in seen:
            break
        seen.add(cur)
        chain.append(cur)
        d = fetch_detail(cur)
        if not d:
            break
        cur = (d.get("mgr_wwid") or "").strip() or None
    return chain


def org_distance(a_wwid: str, b_wwid: str,
                 *, max_depth: int = MAX_CHAIN_DEPTH) -> int | None:
    """Symmetric LCA distance — kept for back-compat callers and the
    /phonebook/lookup ``org_distance`` field. New code should prefer
    ``org_distance_weighted`` (#224) which surfaces the up/down split."""
    r = org_distance_weighted(a_wwid, b_wwid, max_depth=max_depth)
    return None if r is None else r[2]


def org_distance_weighted(
    a_wwid: str, b_wwid: str,
    *, max_depth: int = MAX_CHAIN_DEPTH,
) -> tuple[int, int, int] | None:
    """Distance between two people, split into up/down components (#224).

    Returns ``(up, down, raw)`` where:
      - ``up``   = depth(``a_wwid`` → LCA), i.e. how many levels you climb
                   from the anchor to reach the lowest common ancestor.
      - ``down`` = depth(LCA → ``b_wwid``), i.e. how many levels you
                   descend from the LCA into the candidate's subtree.
      - ``raw``  = ``up + down`` (the symmetric LCA distance).

    ``(0, 0, 0)`` if same person. ``None`` if no common ancestor found
    within ``max_depth`` (or if either WWID is missing).
    """
    if not a_wwid or not b_wwid:
        return None
    if a_wwid == b_wwid:
        return (0, 0, 0)
    chain_a = manager_chain(a_wwid, max_depth=max_depth)
    chain_b = manager_chain(b_wwid, max_depth=max_depth)
    pos_in_a = {w: i for i, w in enumerate(chain_a)}
    best: tuple[int, int, int] | None = None
    for j, w in enumerate(chain_b):
        i = pos_in_a.get(w)
        if i is not None:
            cand = (i, j, i + j)
            if best is None or cand[2] < best[2]:
                best = cand
    return best


def seniority_score(up: int, down: int, *, penalty: int = 3) -> int:
    """Weighted distance from #224. ``score = (up + down) + (up - down) * penalty``.

    Negative ``sd`` (junior candidates) reduces the score; positive
    (senior) increases it. With penalty=3:
      - reportee (up=0, down=1) → -2 (cheapest, always wins among same-name)
      - same-level peer (up=k, down=k) → 2k
      - direct manager (up=1, down=0) → 4
      - senior-leader 5-up (up=5, down=0) → 20
    """
    return (up + down) + (up - down) * penalty


def rank_by_distance(
    candidates: list[IntelPhonebookHit],
    anchor_wwid: str | None,
    *,
    seniority_penalty: int = 3,
) -> list[tuple[IntelPhonebookHit, int | None]]:
    """Sort candidates by the seniority-weighted score (#224). Returned as
    ``(candidate, raw_distance)`` for back-compat with existing callers
    (the raw LCA distance is what the API has historically surfaced).

    Sort key: ``(score, raw, display.lower())``. ``None``-distance
    candidates (no common ancestor / missing WWID) sorted last.

    For full per-candidate detail (up/down/score), use
    ``rank_by_distance_full`` directly.
    """
    full = rank_by_distance_full(candidates, anchor_wwid,
                                 seniority_penalty=seniority_penalty)
    return [(c, raw) for c, _up, _down, raw, _score in full]


def chain_passes_through(wwid: str, ancestors: Iterable[str]) -> bool:
    """True if ``wwid``'s manager chain (including itself) contains any
    WWID in ``ancestors``. Used by the subtree-bias ranker (#225).

    Empty ``ancestors`` or empty/unknown ``wwid`` returns False.
    """
    if not wwid:
        return False
    anc = {a for a in ancestors if a}
    if not anc:
        return False
    chain = manager_chain(wwid)
    return any(w in anc for w in chain)


def rank_by_distance_full(
    candidates: list[IntelPhonebookHit],
    anchor_wwid: str | None,
    *,
    seniority_penalty: int = 3,
    subtree_bias_wwids: Iterable[str] | None = None,
    subtree_bias_score: int = 0,
) -> list[tuple[IntelPhonebookHit, int | None, int | None, int | None, int | None]]:
    """Same as ``rank_by_distance`` but returns ``(c, up, down, raw, score)``.
    All four ints are ``None`` when no common ancestor exists.

    If ``subtree_bias_wwids`` is provided, ``subtree_bias_score`` is added
    to the score of any candidate whose manager chain passes through one
    of those WWIDs (#225). Use a negative value to *prefer* those
    candidates."""
    if not anchor_wwid:
        return [(c, None, None, None, None) for c in candidates]
    bias_set = {w for w in (subtree_bias_wwids or []) if w}
    rows: list[tuple[IntelPhonebookHit, int | None, int | None, int | None, int | None]] = []
    for c in candidates:
        if not c.wwid:
            rows.append((c, None, None, None, None)); continue
        r = org_distance_weighted(anchor_wwid, c.wwid)
        if r is None:
            rows.append((c, None, None, None, None)); continue
        up, down, raw = r
        score = seniority_score(up, down, penalty=seniority_penalty)
        if bias_set and subtree_bias_score and chain_passes_through(c.wwid, bias_set):
            score += subtree_bias_score
        rows.append((c, up, down, raw, score))
    # Sort: resolved before unresolved; then (score, raw, name).
    rows.sort(key=lambda p: (
        p[4] is None,
        p[4] if p[4] is not None else 0,
        p[3] if p[3] is not None else 0,
        (p[0].display or "").lower(),
    ))
    return rows


def resolve_anchor_wwid(anchor: str | None) -> str | None:
    """Resolve an ``anchor`` (idsid, email, or numeric WWID) to a WWID
    via the same scraper. ``None`` if the scraper can't pin it down to
    exactly one person."""
    if not anchor:
        return None
    a = anchor.strip().lstrip("@")
    if not a:
        return None
    if a.isdigit():  # already a WWID
        return a
    hits = cached_lookup(a)
    if not hits:
        return None
    # Email-shaped anchors must hit by exact email; idsid anchors prefer
    # an exact email-localpart match. Falls back to single-hit ambiguity.
    a_lc = a.lower()
    if "@" in a_lc:
        for h in hits:
            if h.email == a_lc:
                return h.wwid
        return None
    for h in hits:
        if h.idsid == a_lc:
            return h.wwid
    return hits[0].wwid if len(hits) == 1 else None


def manager_email_for_wwid(wwid: str) -> str | None:
    """Resolve a person's manager's email via two cached detail fetches
    (#217). Returns ``None`` when the chain breaks (no MgrWWID, manager
    detail unavailable, or scraper disabled).
    """
    if not wwid:
        return None
    own = fetch_detail(wwid)
    if not own:
        return None
    mgr_wwid = (own.get("mgr_wwid") or "").strip()
    if not mgr_wwid:
        return None
    mgr = fetch_detail(mgr_wwid)
    if not mgr:
        return None
    em = (mgr.get("email") or "").strip().lower() or None
    return em


def detail_cache_clear() -> None:
    with _DETAIL_LOCK:
        _DETAIL_CACHE.clear()


def detail_cache_seed(entries: Iterable[tuple[str, dict]]) -> None:
    """Test helper — preload the manager-chain cache without HTTP."""
    now = time.monotonic()
    with _DETAIL_LOCK:
        for w, d in entries:
            _DETAIL_CACHE[str(w).strip()] = (now, d)
