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
        task.status = spec.normalize(tok.value) if spec.normalize else (tok.value.strip().lower() or "todo")
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


def _title_from_items(items: list, decl: Token) -> str:
    """Recover task title when ``decl.value`` is empty (new format: ``!task #id T-XXXX <title>``).

    The lexer stops reading the title at the first known ``#token``, so for
    lines like ``!task #id T-XXXX My Title #priority P1`` the title ends up
    as a ``TextChunk`` *after* the ``#id`` attribute token.  Collect those
    chunks (stopping at the next real attribute) and return the joined text.
    """
    after_decl = False
    after_id = False
    chunks: list[str] = []
    for item in items:
        if item is decl:
            after_decl = True
            continue
        if not after_decl:
            continue
        if isinstance(item, Token):
            if item.name == "id" and not after_id:
                after_id = True
                continue
            # Any other token ends the title zone.
            break
        if isinstance(item, TextChunk) and after_id:
            chunks.append(item.text)
    return "".join(chunks).strip()


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


def _is_ref_row(items: list) -> Token | None:
    """If the line's first non-whitespace/bullet item is a ``#task`` or ``#ar``
    token, return that token (it's an agenda reference row). Otherwise None.
    """
    for x in items:
        if isinstance(x, TextChunk):
            t = _BULLET_STRIP.sub("", x.text).strip()
            if t:
                return None
            continue
        if isinstance(x, Token):
            if x.kind == "attr" and x.name in ("task", "ar"):
                return x
            return None
    return None


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
    # Reference rows: lines that point to an existing task by `#task <ID>`
    # (no `!`-prefix declaration). Each row may carry override attrs that
    # the indexer applies to the referenced task.
    ref_rows: List[Dict[str, Any]] = []
    # Each frame: (indent, {key: [values]}). Frames are scoped by indent so
    # siblings replace and shallower lines cancel deeper ones.
    ctx_stack: List[tuple[int, Dict[str, list]]] = []

    def gather_inherited() -> Dict[str, list]:
        merged: Dict[str, list] = {}
        for _, attrs in ctx_stack:
            for k, vs in attrs.items():
                bucket = merged.setdefault(k, [])
                for v in vs:
                    if v not in bucket:
                        bucket.append(v)
        return merged

    def prune_for_task(line_indent: int) -> None:
        """A task at indent L invalidates ctx frames at indent > L (deeper,
        therefore out of scope). Same-indent and shallower frames still apply."""
        while ctx_stack and ctx_stack[-1][0] > line_indent:
            ctx_stack.pop()
        # Also drop any non-tail deeper frames (shouldn't happen post-fix but
        # guards against odd interleavings).
        ctx_stack[:] = [f for f in ctx_stack if f[0] <= line_indent]

    def prune_for_ctx(line_indent: int, new_keys: set) -> None:
        """A ctx-only line at indent L:
          * pops every frame deeper than L (out of scope), and
          * pops same-indent frames whose key set overlaps the new line's
            keys (sibling replaces sibling: e.g. `#project jnc` after
            `#project gfc`, or `@namratha` after `@aboli`).
        Same-indent frames with disjoint keys (e.g. an `@owner` line after
        a `#project` line at the same indent) are kept additive."""
        ctx_stack[:] = [
            (k_indent, attrs)
            for (k_indent, attrs) in ctx_stack
            if not (
                k_indent > line_indent
                or (k_indent == line_indent and (set(attrs.keys()) & new_keys))
            )
        ]

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
            prune_for_task(line_indent)
            raw_title = decl.value.strip() or _title_from_items(items, decl)
            slug = _slug_collisions(slugify(raw_title), taken_slugs)
            kind = decl.name if decl.name in {"task", "ar"} else "task"
            task = ParsedTask(slug=slug, title=raw_title, line=line_no, indent=line_indent, kind=kind)
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
            # Agenda reference row: line whose leading token (after any
            # leading whitespace/bullet) is `#task <ID>`. The ID points to
            # an existing task declared elsewhere with `!task ... #id <ID>`.
            # Other attrs on the same line are write-through overrides.
            task_ref = _is_ref_row(items)
            if task_ref is not None:
                overrides: Dict[str, Any] = {}
                for t in attr_toks:
                    if t is task_ref:
                        continue
                    spec = REGISTRY.get(t.name)
                    if spec is None:
                        continue
                    if spec.multi:
                        overrides.setdefault(t.name, []).append(t.value)
                    else:
                        overrides[t.name] = t.value
                ref_rows.append({
                    "ref_id": task_ref.value,
                    "line": line_no,
                    "indent": line_indent,
                    "attrs": overrides,
                })
                continue
            if _is_context_only_line(items):
                # Hierarchical attachment: if this attr-only line is indented
                # *deeper* than the current task, treat it as a continuation
                # of that task (attach the tokens to it) rather than a global
                # context line. This prevents a `#eta` indented under task A
                # from leaking onto sibling tasks B, C that follow A.
                if current is not None and line_indent > current.indent:
                    for tok in attr_toks:
                        _attach_attr(current, tok)
                else:
                    new_keys = {tok.name for tok in attr_toks}
                    prune_for_ctx(line_indent, new_keys)
                    ctx_attrs: Dict[str, list] = {}
                    for tok in attr_toks:
                        ctx_attrs.setdefault(tok.name, []).append(tok.value)
                    ctx_stack.append((line_indent, ctx_attrs))
            elif current is not None:
                for tok in attr_toks:
                    _attach_attr(current, tok)
        # else: plain prose — do nothing

    refs_out: List[Dict[str, str]] = []
    for t in tasks:
        for r in t.refs:
            refs_out.append({"src_slug": t.slug, "dst_slug": r["dst_slug"], "kind": r["kind"]})

    _rollup_to_parents(tasks)

    return {
        "tasks": [t.to_dict() for t in tasks],
        "refs": refs_out,
        "ref_rows": ref_rows,
    }


def _rollup_to_parents(tasks: List[ParsedTask]) -> None:
    """Back-propagate child state to parents:

    1. **ETA**: a parent inherits the latest (maximum) ETA of any descendant
       if that ETA is later than the parent's own (or the parent has no ETA).
       This means a parent task is never marked as completing earlier than its
       longest child.
    2. **Status**: if a parent is marked `done` but any descendant is *not*
       done, the parent's status is downgraded to `in-progress` (with a
       fall-back to `todo` if all non-done children are still `todo`). The
       written markdown is left unchanged — only the parsed view reflects
       the rolled-up state.

    Processed bottom-up (deepest first) so transitive rollups converge in a
    single pass.
    """
    by_slug = {t.slug: t for t in tasks}
    children: Dict[str, List[ParsedTask]] = {}
    for t in tasks:
        if t.parent_slug:
            children.setdefault(t.parent_slug, []).append(t)
    # Deepest first.
    ordered = sorted(tasks, key=lambda t: t.indent, reverse=True)
    for t in ordered:
        kids = children.get(t.slug)
        if not kids:
            continue
        # ETA rollup: take max(ISO date) across kids' attrs_norm.eta.
        kid_etas = [k.attrs_norm.get("eta") for k in kids if k.attrs_norm.get("eta")]
        if kid_etas:
            max_kid_eta = max(kid_etas)  # ISO-date strings sort lexically
            cur_eta = t.attrs_norm.get("eta")
            if cur_eta is None or max_kid_eta > cur_eta:
                t.attrs_norm["eta"] = max_kid_eta
                # Mirror back into raw attrs so consumers that read .attrs
                # also see the rolled-up value (use ISO; original ww-string
                # form is not reconstructable here).
                t.attrs["eta"] = max_kid_eta
        # Status rollup: if parent says done but any kid isn't, downgrade.
        if t.status == "done":
            non_done = [k for k in kids if k.status != "done"]
            if non_done:
                if any(k.status == "in-progress" for k in non_done):
                    t.status = "in-progress"
                elif any(k.status == "blocked" for k in non_done):
                    t.status = "blocked"
                else:
                    t.status = "in-progress"  # mixed todo+done parent → in-progress
                t.attrs["status"] = t.status
                t.attrs_norm["status"] = t.status
