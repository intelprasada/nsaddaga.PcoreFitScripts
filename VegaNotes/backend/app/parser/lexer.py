"""Line-level lexer for VegaNotes tokens.

Splits a markdown line into a stream of ``TextChunk`` and ``Token`` items.
Tokens are introduced by ``!task`` (declaration) or ``#<name> <value>``
(attribute reference). Values run until the next whitespace, except when
quoted with double quotes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Union

from .tokens import is_known


@dataclass
class TextChunk:
    text: str


@dataclass
class Token:
    kind: str            # "task_decl" | "attr"
    name: str            # e.g. "task", "eta", "priority"
    value: str           # raw value text
    raw: str             # exact source slice for round-trip
    col: int


_TOKEN_RE = re.compile(
    r"""
    (?P<bang>!task|!AR)                   # task / action-required declaration
    |
    \#(?P<name>[a-zA-Z][\w-]*)            # #attribute
    |
    (?P<at_pre>(?<=^)|(?<=[\s(\[]))       # word boundary so emails (a@b.c) don't match
    (?:
      @"(?P<at_quoted>[^"\n]+)"            # @"Last, First"  (quoted)
      |
      @(?P<at>[a-zA-Z][\w.-]*)            # @user (sugar for #owner)
    )
    """,
    re.VERBOSE | re.MULTILINE,
)

# #226 / #233: GAL-style "@Last, First" without quotes — opportunistically
# consume the trailing ", Capitalized" only when the surname is clearly
# *standalone* (end-of-line, end-of-string, or directly followed by
# another @/# token). The earlier guard ("is next word capitalized?")
# fired on common prose ("@alice, Thanks", "@bob, OK", "@chris, I",
# "@dave, Monday", "@eve, PR"), silently mis-attributing ownership AND
# eating the next word from the surrounding text. The standalone rule
# keeps the GAL form working while leaving inline prose untouched.
_GAL_TAIL_RE = re.compile(
    r",\s+(?P<sur>[A-Z][\w.-]*)(?=\s*$|\s+[@#])"
)
# Additional stop-list for ambiguous one/two-letter surnames that *do*
# pass the standalone check but are almost always prose noise. Curated
# from the acceptance examples + common day/month abbreviations.
_GAL_TAIL_STOPWORDS = frozenset({
    "I", "OK", "PR", "AR", "FYI", "IIRC", "TBD", "EOM", "EOD", "WIP",
    "TODO", "ETA", "WW", "CC", "BCC", "RE", "FW", "FWD", "OOO",
    "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Sunday",
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
    "Sep", "Sept", "Oct", "Nov", "Dec",
    "January", "February", "March", "April", "June",
    "July", "August", "September", "October", "November", "December",
    "Thanks", "Thx", "Yes", "No", "OK", "Done",
})

_TOKEN_NAMES_NEEDING_VALUE = {"task"}  # !task always takes a value (the title)


def _read_value(s: str, i: int, *, until_hash: bool = False,
                stop_at_delimiter: bool = False) -> tuple[str, int]:
    """Read a value starting at index i. Returns (value, end_index).

    Skips leading whitespace. Supports double-quoted strings. For ``!task``
    titles (``until_hash=True``) the value runs until the next known
    ``#token`` or EOL. Otherwise it reads a single whitespace-delimited word.

    When ``stop_at_delimiter=True`` (used for ``#name`` attribute reads
    other than ``status`` / ``note``), if the first non-whitespace char
    after ``i`` is ``#`` or ``@`` the value is empty and we return the
    *original* i so the caller can decide how to advance past ``#name``
    without swallowing the next token. This makes ``#foo #bar`` lex as
    two separate bare hashtags instead of ``#foo`` with value ``#bar``.
    """
    n = len(s)
    orig_i = i
    while i < n and s[i] in " \t":
        i += 1
    if stop_at_delimiter and i < n and s[i] in ("#", "@"):
        return "", orig_i
    if i < n and s[i] == '"':
        j = i + 1
        out = []
        while j < n and s[j] != '"':
            if s[j] == "\\" and j + 1 < n:
                out.append(s[j + 1])
                j += 2
            else:
                out.append(s[j])
                j += 1
        return "".join(out), (j + 1 if j < n else j)
    if until_hash:
        j = i
        while j < n:
            if s[j] == "#":
                m = re.match(r"#([a-zA-Z][\w-]*)", s[j:])
                if m:
                    # Any attribute-shaped #name terminates the title — even
                    # for unknown names, which the parser will accept as
                    # generic attrs (issue #38 follow-up). The lexer will
                    # later drop empty-value attrs so prose hashtags
                    # (`#urgent` at EOL) are unaffected.
                    break
            if s[j] == "@" and (j == 0 or s[j - 1] in " \t([") and j + 1 < n and (s[j + 1].isalpha() or s[j + 1] == "_" or s[j + 1] == '"'):
                break
            j += 1
        return s[i:j].rstrip(), j
    j = i
    while j < n and s[j] not in " \t":
        j += 1
    return s[i:j], j


def lex(line: str) -> List[Union[TextChunk, Token]]:
    out: List[Union[TextChunk, Token]] = []
    i = 0
    last = 0
    n = len(line)
    while i < n:
        m = _TOKEN_RE.search(line, i)
        if not m:
            break
        start = m.start()
        if start > last:
            out.append(TextChunk(line[last:start]))

        if m.group("bang"):
            bang = m.group("bang")  # "!task" or "!AR"
            kind_name = "ar" if bang == "!AR" else "task"
            value, end = _read_value(line, m.end(), until_hash=True)
            raw = line[start:end]
            out.append(Token(kind="task_decl", name=kind_name, value=value, raw=raw, col=start))
            i = end
            last = end
        elif m.group("at_quoted") is not None:
            user = m.group("at_quoted").strip()
            end = m.end()
            raw = line[start:end]
            out.append(Token(kind="attr", name="owner", value=user, raw=raw, col=start))
            i = end
            last = end
        elif m.group("at"):
            user = m.group("at")
            end = m.end()
            # #226: opportunistically extend a bare @First into a GAL
            # "@First, Last" form when the next chars are ", " followed
            # by a capitalized word. The full token then becomes
            # "First Last" (which Phonebook._split_compound_token will
            # treat as a two-token form). Required because the GAL
            # display form Outlook produces is "Last, First" — we
            # support both orderings symmetrically.
            tail = _GAL_TAIL_RE.match(line, end)
            if tail and tail.group("sur") not in _GAL_TAIL_STOPWORDS:
                # Keep the comma so _split_compound_token recognises
                # this as GAL form (surname=First, given=Second word),
                # which is what Outlook emits for "Last, First".
                user = f"{user}, {tail.group('sur')}"
                end = tail.end()
            raw = line[start:end]
            out.append(Token(kind="attr", name="owner", value=user, raw=raw, col=start))
            i = end
            last = end
        else:
            name = m.group("name")
            # Read the value. For status/note we use until_hash-style so
            # multi-word values ("blocked by review") work; for everything
            # else we use single-word reads with stop_at_delimiter so that
            # `#foo #bar` lexes as two bare hashtags, not `foo="#bar"`.
            spec_known = is_known(name)
            use_until_hash = spec_known and name in ("status", "note")
            value, end = _read_value(
                line, m.end(),
                until_hash=use_until_hash,
                stop_at_delimiter=not use_until_hash,
            )
            if not value.strip():
                # Empty value: is it a bare hashtag (presence tag on a
                # task-attached line, #275) or a known-attr typo?
                #
                # Known/registry names (`#priority`, `#eta`, `#project`,
                # etc.) with no value are almost certainly a user typo
                # or in-progress edit — dropping them as prose avoids
                # corrupting attrs with empty values.
                #
                # Unknown names are the interesting case: emit as an
                # ``attr`` token with empty value so the parser can
                # attach it to the enclosing task as a presence tag
                # (rendered as a `#tag` chip on cards, queryable via
                # `@tag exists`). If the enclosing line is unattached
                # top-level prose, the parser will simply ignore the
                # token — matching prior behavior for narrative prose.
                if spec_known:
                    out.append(TextChunk(line[start:m.end()]))
                else:
                    raw = line[start:m.end()]
                    out.append(Token(kind="attr", name=name, value="",
                                     raw=raw, col=start))
                i = m.end()
                last = m.end()
                continue
            raw = line[start:end]
            if name == "status":
                value = value.strip()
            elif name == "note":
                value = value.strip()
                if not value:
                    i = end
                    last = end
                    continue
            out.append(Token(kind="attr", name=name, value=value, raw=raw, col=start))
            i = end
            last = end

    if last < n:
        out.append(TextChunk(line[last:]))
    return out


def iter_lines(md: str) -> Iterable[tuple[int, str]]:
    for idx, line in enumerate(md.splitlines()):
        yield idx, line
