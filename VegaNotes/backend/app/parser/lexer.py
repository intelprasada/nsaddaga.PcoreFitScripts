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
    (?P<bang>!task)                       # task declaration
    |
    \#(?P<name>[a-zA-Z][\w-]*)            # #attribute
    |
    (?P<at_pre>(?<=^)|(?<=[\s(\[]))       # word boundary so emails (a@b.c) don't match
    @(?P<at>[a-zA-Z][\w.-]*)              # @user (sugar for #owner)
    """,
    re.VERBOSE | re.MULTILINE,
)

_TOKEN_NAMES_NEEDING_VALUE = {"task"}  # !task always takes a value (the title)


def _read_value(s: str, i: int, *, until_hash: bool = False) -> tuple[str, int]:
    """Read a value starting at index i. Returns (value, end_index).

    Skips leading whitespace. Supports double-quoted strings. For ``!task``
    titles (``until_hash=True``) the value runs until the next known
    ``#token`` or EOL. Otherwise it reads a single whitespace-delimited word.
    """
    n = len(s)
    while i < n and s[i] in " \t":
        i += 1
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
                if m and is_known(m.group(1)):
                    break
            if s[j] == "@" and (j == 0 or s[j - 1] in " \t([") and j + 1 < n and (s[j + 1].isalpha() or s[j + 1] == "_"):
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
            value, end = _read_value(line, m.end(), until_hash=True)
            raw = line[start:end]
            out.append(Token(kind="task_decl", name="task", value=value, raw=raw, col=start))
            i = end
            last = end
        elif m.group("at"):
            user = m.group("at")
            end = m.end()
            raw = line[start:end]
            out.append(Token(kind="attr", name="owner", value=user, raw=raw, col=start))
            i = end
            last = end
        else:
            name = m.group("name")
            # only consume known attribute names; unknown stay as text
            if not is_known(name):
                # treat as plain text — copy through
                end = m.end()
                if start > last:
                    pass  # already emitted
                out.append(TextChunk(line[start:end]))
                i = end
                last = end
                continue
            value, end = _read_value(line, m.end())
            raw = line[start:end]
            out.append(Token(kind="attr", name=name, value=value, raw=raw, col=start))
            i = end
            last = end

    if last < n:
        out.append(TextChunk(line[last:]))
    return out


def iter_lines(md: str) -> Iterable[tuple[int, str]]:
    for idx, line in enumerate(md.splitlines()):
        yield idx, line
