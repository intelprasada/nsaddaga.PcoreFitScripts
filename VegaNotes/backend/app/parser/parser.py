"""High-level VegaNotes markdown parser.

Turns markdown text into a structured representation:

    {
      "tasks": [ { id, slug, title, status, parent, line, attrs, refs } ],
      "refs":  [ { src_slug, dst_slug, kind } ]
    }

Inheritance rules (v3):

1. **Parent-task inheritance** — subtasks (children of a `!task` by indentation)
   inherit multi-valued attrs (owner, project, feature) from their parent.
   Single-valued attrs (eta, priority, status, estimate) are NOT inherited.

2. **Context-line inheritance** — a line that contains ONLY attribute tokens
   (e.g. ``@nancy`` or ``#project foo``) at any indent pushes its tokens onto
   a context stack. All `!task` declarations after it inherit those attrs
   until a blank line clears the stack.

3. **Free-form continuation** — a line with prose AND attr tokens still
   attaches the tokens to the current task (so ``Blocked by #task x`` works).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .lexer import TextChunk, Token, lex
from .tokens import REGISTRY


_LIST_PREFIX = re.compile(r"^(?P<indent>\s*)(?P<bullet>[-*+]\s+|\d+[.)]\s+)?")
_BULLET_STRIP = re.compile(r"^\s*([-*+]\s+|\d+[.)]\s+)?")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    s = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return s or "task"


@dataclass
class ParsedTask:
    slug: str
    title: str
    line: int
    indent: int
    kind: str = "task"  # "task" | "ar"
    parent_slug: Optional[str] = None
    status: str = "todo"
    attrs: Dict[str, Any] = field(default_factory=dict)
    attrs_norm: Dict[str, Any] = field(default_factory=dict)
    refs: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "line": self.line,
            "indent": self.indent,
            "kind": self.kind,
            "parent_slug": self.parent_slug,
            "status": self.status,
            "attrs": self.attrs,
            "attrs_norm": self.attrs_norm,
            "refs": self.refs,
        }


def _attach_attr(task: ParsedTask, tok: Token) -> None:
    spec = REGISTRY[tok.name]
    if tok.name == "status":
        task.status = tok.value.strip().lower() or "todo"
    if tok.name in {"task", "link"}:
        kind = "task" if tok.name == "task" else "link"
        task.refs.append({"kind": kind, "dst_slug": slugify(tok.value)})
        return
    if spec.multi:
        existing = task.attrs.setdefault(tok.name, [])
        if tok.value not in existing:
            existing.append(tok.value)
    else:
        task.attrs[tok.name] = tok.value
    if spec.normalize:
        norm = spec.normalize(tok.value)
        if spec.multi:
            existing_norm = task.attrs_norm.setdefault(tok.name, [])
            if norm not in existing_norm:
                existing_norm.append(norm)
        else:
            task.attrs_norm[tok.name] = norm


def _indent_level(line: str) -> int:
    m = _LIST_PREFIX.match(line)
    if not m:
        return 0
    indent = m.group("indent") or ""
    width = sum(4 if ch == "\t" else 1 for ch in indent)
    return width // 2


def _slug_collisions(slug: str, taken: Dict[str, int]) -> str:
    if slug not in taken:
        taken[slug] = 1
        return slug
    taken[slug] += 1
    return f"{slug}-{taken[slug]}"


def _is_context_only_line(items: list) -> bool:
    """True iff non-token content of the line is empty (just whitespace + bullets)."""
    for x in items:
        if isinstance(x, TextChunk):
            t = _BULLET_STRIP.sub("", x.text).strip()
            if t:
                return False
    return True


def _merge_inherited(task: ParsedTask, inherited: Dict[str, list]) -> None:
    for key, values in inherited.items():
        spec = REGISTRY.get(key)
        if spec is None:
            continue
        if spec.multi:
            existing = task.attrs.get(key, [])
            if not isinstance(existing, list):
                existing = [existing]
            merged = list(existing)
            for v in values:
                if v not in merged:
                    merged.append(v)
            if merged:
                task.attrs[key] = merged
                if spec.normalize:
                    task.attrs_norm[key] = [spec.normalize(v) for v in merged]
        else:
            if key not in task.attrs and values:
                task.attrs[key] = values[0]
                if spec.normalize:
                    task.attrs_norm[key] = spec.normalize(values[0])


def _inherit_from_parent(task: ParsedTask, parent: ParsedTask) -> None:
    for k, v in parent.attrs.items():
        spec = REGISTRY.get(k)
        if spec is None or not spec.multi:
            continue
        existing = task.attrs.get(k, [])
        if not isinstance(existing, list):
            existing = [existing]
        merged = list(existing)
        for item in (v if isinstance(v, list) else [v]):
            if item not in merged:
                merged.append(item)
        task.attrs[k] = merged
        if spec.normalize:
            task.attrs_norm[k] = [spec.normalize(item) for item in merged]


def parse(md: str) -> Dict[str, Any]:
    tasks: List[ParsedTask] = []
    stack: List[ParsedTask] = []
    current: Optional[ParsedTask] = None
    taken_slugs: Dict[str, int] = {}
    ctx_stack: List[Dict[str, list]] = []  # cleared on blank line

    def gather_inherited() -> Dict[str, list]:
        merged: Dict[str, list] = {}
        for attrs in ctx_stack:
            for k, vs in attrs.items():
                bucket = merged.setdefault(k, [])
                for v in vs:
                    if v not in bucket:
                        bucket.append(v)
        return merged

    for line_no, raw_line in enumerate(md.splitlines()):
        if not raw_line.strip():
            current = None
            stack.clear()
            ctx_stack.clear()
            continue

        line_indent = _indent_level(raw_line)
        items = lex(raw_line)
        decl = next((x for x in items if isinstance(x, Token) and x.kind == "task_decl"), None)
        attr_toks = [x for x in items if isinstance(x, Token) and x.kind == "attr"]

        if decl is not None:
            slug = _slug_collisions(slugify(decl.value), taken_slugs)
            kind = decl.name if decl.name in {"task", "ar"} else "task"
            task = ParsedTask(slug=slug, title=decl.value.strip(), line=line_no, indent=line_indent, kind=kind)
            while stack and stack[-1].indent >= line_indent:
                stack.pop()
            if stack:
                task.parent_slug = stack[-1].slug
            for tok in attr_toks:
                _attach_attr(task, tok)
            _merge_inherited(task, gather_inherited())
            if stack:
                _inherit_from_parent(task, stack[-1])
            stack.append(task)
            tasks.append(task)
            current = task
        elif attr_toks:
            if _is_context_only_line(items):
                ctx_attrs: Dict[str, list] = {}
                for tok in attr_toks:
                    ctx_attrs.setdefault(tok.name, []).append(tok.value)
                ctx_stack.append(ctx_attrs)
            elif current is not None:
                for tok in attr_toks:
                    _attach_attr(current, tok)
        # else: plain prose — do nothing

    refs_out: List[Dict[str, str]] = []
    for t in tasks:
        for r in t.refs:
            refs_out.append({"src_slug": t.slug, "dst_slug": r["dst_slug"], "kind": r["kind"]})

    return {
        "tasks": [t.to_dict() for t in tasks],
        "refs": refs_out,
    }
