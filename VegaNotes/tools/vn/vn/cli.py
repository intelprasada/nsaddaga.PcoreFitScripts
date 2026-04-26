"""``vn`` — terminal-friendly wrapper around the VegaNotes REST API.

Subcommands
-----------

* ``vn task <ID> key=value [...]`` — patch a task (status, priority, eta,
  owners, features, add-note).
* ``vn list [--owner ...] [--status ...] [...]`` — list tasks.
* ``vn note new --project <p> --title <t>`` — create a new markdown note.
* ``vn whoami`` — print the authenticated user.

Authentication is read from ``~/.veganotes/credentials`` or env vars; see
``vn.config`` for details.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from typing import Any, Optional, Sequence

from . import __version__
from .client import ApiError, Client
from .config import CredentialsError, load_credentials
from .query import DSLError, compile_clauses


# Attribute keys accepted by `vn task ID key=value ...`.
# Maps the user-facing key to the PATCH body field.
_TASK_KEYS = {
    "status": "status",
    "priority": "priority",
    "eta": "eta",
    "owner": "owners",     # comma-separated -> list
    "owners": "owners",
    "feature": "features",
    "features": "features",
    "note": "add_note",
    "add-note": "add_note",
    "notes": "notes",      # legacy overwrite
}

_LIST_VALUED = {"owners", "features"}


def _parse_kv(args: list[str]) -> dict[str, Any]:
    """Parse ``key=value`` pairs into a PATCH body."""
    body: dict[str, Any] = {}
    for raw in args:
        if "=" not in raw:
            raise SystemExit(f"vn: expected key=value, got {raw!r}")
        key, _, value = raw.partition("=")
        key = key.strip().lower()
        if key not in _TASK_KEYS:
            raise SystemExit(
                f"vn: unknown key {key!r}; valid keys: "
                + ", ".join(sorted(set(_TASK_KEYS)))
            )
        field = _TASK_KEYS[key]
        if field in _LIST_VALUED:
            body[field] = [v for v in (s.strip() for s in value.split(",")) if v]
        else:
            body[field] = value
    return body


def _print_json(data: Any) -> None:
    json.dump(data, sys.stdout, indent=2, default=str, sort_keys=True)
    sys.stdout.write("\n")


_DEFAULT_COLUMNS = ("id", "status", "priority", "eta", "owners", "title")
_TREE_DEFAULT_COLUMNS = ("id", "type", "status", "priority", "eta", "owners", "title")


def _task_type(task: dict[str, Any]) -> str:
    """Classify a task row as 'TASK', 'SUBTASK' or 'AR'.

    Kind always wins: a row with kind='ar' renders as 'AR' even when it
    appears nested under a parent task.  'SUBTASK' is reserved for plain
    tasks that have a parent.  Result is upper-cased for visual scanning.
    """
    kind = str(task.get("kind") or "task").lower()
    if kind == "ar":
        return "AR"
    if task.get("parent_task_id"):
        return "SUBTASK"
    return kind.upper()


def _task_field(task: dict[str, Any], col: str) -> str:
    """Render a single column value for a task as a flat string."""
    col = col.lower()
    if col == "id":
        return str(task.get("task_uuid") or task.get("ref") or task.get("id") or "")
    if col == "type":
        return _task_type(task)
    if col == "title":
        title = (task.get("title") or "").strip()
        prefix = task.get("_indent_prefix")
        return f"{prefix}{title}" if prefix else title
    if col == "status":
        return str(task.get("status") or "")
    if col == "owners":
        owners = task.get("owners") or []
        return ",".join(owners) if isinstance(owners, list) else str(owners)
    if col == "features":
        feats = task.get("features") or []
        return ",".join(feats) if isinstance(feats, list) else str(feats)
    attrs = task.get("attrs") or {}
    val = attrs.get(col)
    if val is None:
        # Fall back to top-level keys so e.g. `--columns project,id` works
        # whether `project` lives on the row or in attrs.
        val = task.get(col, "")
    if isinstance(val, list):
        val = ",".join(str(v) for v in val)
    return str(val)


def _print_task_table(tasks: list[dict[str, Any]], columns: tuple[str, ...] = _DEFAULT_COLUMNS) -> None:
    if not tasks:
        print("(no tasks)")
        return
    rows = [tuple(_task_field(t, c) for c in columns) for t in tasks]
    headers = tuple(c.upper() for c in columns)
    widths = [
        max(len(h), max((len(r[i]) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*r))


def _print_task_csv(tasks: list[dict[str, Any]], columns: tuple[str, ...]) -> None:
    import csv
    w = csv.writer(sys.stdout)
    w.writerow([c.lower() for c in columns])
    for t in tasks:
        w.writerow([_task_field(t, c) for c in columns])


def _print_task_jsonl(tasks: list[dict[str, Any]]) -> None:
    for t in tasks:
        sys.stdout.write(json.dumps(t, default=str, sort_keys=True))
        sys.stdout.write("\n")


def _print_task_ids(tasks: list[dict[str, Any]]) -> None:
    for t in tasks:
        sys.stdout.write(str(t.get("task_uuid") or t.get("id") or ""))
        sys.stdout.write("\n")


def _bucket_value(task: dict[str, Any], field: str) -> str:
    val = _task_field(task, field)
    return val or "—"


def _print_grouped(
    tasks: list[dict[str, Any]],
    field: str,
    columns: tuple[str, ...],
) -> None:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for t in tasks:
        buckets.setdefault(_bucket_value(t, field), []).append(t)
    first = True
    for key in sorted(buckets):
        if not first:
            print()
        first = False
        print(f"== {field}={key}  ({len(buckets[key])}) ==")
        _print_task_table(buckets[key], columns)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(s: str) -> str:
    s = _SLUG_RE.sub("-", s.lower()).strip("-")
    return s or "note"


# ---------------------------------------------------------------------------
# command handlers
# ---------------------------------------------------------------------------

def cmd_task(client: Client, args: argparse.Namespace) -> int:
    body = _parse_kv(args.attrs)
    if not body:
        # No attrs → just fetch and show.
        data = client.get(f"/api/tasks/{args.id}")
    else:
        data = client.patch(f"/api/tasks/{args.id}", body)
    if args.json:
        _print_json(data)
    else:
        _print_task_table([data]) if isinstance(data, dict) else _print_json(data)
    return 0


def _normalize_child(child: dict[str, Any], parent_id: Any) -> dict[str, Any]:
    """Make a slim child dict look like a top-level row for rendering.

    The API's nested children carry only a handful of fields and no
    `attrs` map; without this _task_field would render some columns
    differently for parents vs. children (e.g. `eta` falls back to the
    normalized value_norm rather than the raw user-typed string).
    Promote the known fields into a synthetic attrs map so the renderer
    finds the same key on both shapes.
    """
    out = dict(child)
    out.setdefault("parent_task_id", parent_id)
    if "attrs" not in out:
        attrs: dict[str, Any] = {}
        # Prefer the raw user-typed eta (eta_raw, added by the backend
        # alongside the back-compat normalized `eta`) so parents and
        # children render the same shape (e.g. "ww18.2" not "2026-05-04").
        eta_val = out.get("eta_raw") or out.get("eta")
        if eta_val:
            attrs["eta"] = eta_val
        for k in ("status", "priority"):
            v = out.get(k)
            if v is not None and v != "":
                attrs[k] = v
        out["attrs"] = attrs
    return out


def _flatten_tree(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten parents + their `children` into one row list, tagging each
    child with an `_indent_prefix` so the title column shows the hierarchy.

    De-duplication: the API returns subtasks twice when --tree is on (once
    as a top-level row matching kind=task,ar and once as a `children` entry
    of their parent).  We collect the IDs of all children whose parent is
    also present in the result, then skip those parents' children at the
    top level — so each row appears exactly once, preferentially nested
    under its parent.  Subtasks whose parent did NOT match the filter
    (orphans) are kept at the top level so they don't disappear.
    """
    parent_ids = {t.get("id") for t in tasks if t.get("id") is not None}
    nested_ids: set[Any] = set()
    for parent in tasks:
        for c in (parent.get("children") or []):
            cid = c.get("id")
            if cid is not None and parent.get("id") in parent_ids:
                nested_ids.add(cid)

    out: list[dict[str, Any]] = []
    for parent in tasks:
        if parent.get("id") in nested_ids:
            # This row will be emitted as someone else's child; skip the
            # duplicate top-level appearance.
            continue
        parent_copy = dict(parent)
        parent_copy.pop("_indent_prefix", None)
        out.append(parent_copy)
        kids = parent.get("children") or []
        last = len(kids) - 1
        for i, c in enumerate(kids):
            child = _normalize_child(c, parent.get("id"))
            child["_indent_prefix"] = ("└─ " if i == last else "├─ ")
            out.append(child)
    return out


def cmd_list(client: Client, args: argparse.Namespace) -> int:
    # Start with the explicit fixed-column flags; these win when both a
    # flag and a `--where` clause set the same key (last-write semantics
    # via the trailing list extend below).
    pairs: list[tuple[str, Any]] = []
    fixed = {
        "owner": args.owner,
        "project": args.project,
        "feature": args.feature,
        "priority": args.priority,
        "status": args.status,
        "eta_before": args.eta_before,
        "eta_after": args.eta_after,
        "hide_done": args.hide_done,
        "q": args.q,
        "kind": args.kind,
        "not_owner": args.not_owner,
        "not_project": args.not_project,
        "not_feature": args.not_feature,
        "not_status": args.not_status,
        "not_priority": args.not_priority,
        "sort": args.sort,
        "limit": args.limit,
        "offset": args.offset,
    }
    tree_mode = bool(getattr(args, "tree", False))
    with_children = tree_mode or bool(getattr(args, "with_children", False))
    if with_children:
        fixed["include_children"] = True
    if tree_mode and not args.kind:
        # Tree view is meant to show task / AR rows side-by-side.  Don't
        # silently override an explicit --kind the caller passed in.
        fixed["kind"] = "task,ar"
    for k, v in fixed.items():
        if v is None or v is False:
            continue
        pairs.append((k, v))

    # Compile --where clauses to additional wire pairs.
    try:
        pairs.extend(compile_clauses(args.where or []))
    except DSLError as e:
        print(f"vn: bad --where clause: {e}", file=sys.stderr)
        return 2

    # Re-shape into the dict the Client expects (lists for repeats).
    params: dict[str, Any] = {}
    for k, v in pairs:
        if k in params:
            cur = params[k]
            if isinstance(cur, list):
                cur.append(v)
            else:
                params[k] = [cur, v]
        else:
            params[k] = v

    data = client.get("/api/tasks", **params)
    tasks = (data or {}).get("tasks") if isinstance(data, dict) else data

    fmt = (args.format or ("json" if args.json else "table")).lower()
    default_cols = _TREE_DEFAULT_COLUMNS if tree_mode else _DEFAULT_COLUMNS
    columns = tuple(c.strip().lower() for c in (args.columns or ",".join(default_cols)).split(",") if c.strip())

    # Tree mode flattens parents + their `children` into a single row list
    # for table/csv output.  JSON/JSONL preserve the nested structure so
    # downstream consumers see the relationship intact.
    table_rows = _flatten_tree(tasks or []) if tree_mode and fmt in ("table", "csv", "ids") else (tasks or [])

    if fmt == "json":
        _print_json(data)
    elif fmt == "jsonl":
        _print_task_jsonl(tasks or [])
    elif fmt == "csv":
        _print_task_csv(table_rows, columns)
    elif fmt == "ids":
        _print_task_ids(table_rows)
    elif fmt == "table":
        if args.group_by:
            _print_grouped(table_rows, args.group_by.lower(), columns)
        else:
            _print_task_table(table_rows, columns)
    else:
        print(f"vn: unknown --format: {fmt}", file=sys.stderr)
        return 2
    return 0


def cmd_note_new(client: Client, args: argparse.Namespace) -> int:
    project = args.project.strip("/")
    slug = _slugify(args.title)
    if args.path:
        path = args.path
    else:
        path = f"{project}/{slug}.md"
    body_md = args.body
    if body_md is None:
        # Build a sensible default body.
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        body_md = f"# {args.title}\n\n_Created {ts} via `vn note new`._\n"
    payload = {"path": path, "body_md": body_md}
    data = client.put("/api/notes", payload)
    if args.json:
        _print_json(data)
    else:
        nid = data.get("id") if isinstance(data, dict) else "?"
        print(f"created note id={nid} path={path}")
    return 0


def cmd_whoami(client: Client, args: argparse.Namespace) -> int:
    data = client.get("/api/me")
    if args.json:
        _print_json(data)
    else:
        if isinstance(data, dict):
            tag = " (admin)" if data.get("is_admin") else ""
            print(f"{data.get('name', '?')}{tag}")
        else:
            print(data)
    return 0


# ---------------------------------------------------------------------------
# `vn show <resource>` — read-only inspector for the rest of the API.
# ---------------------------------------------------------------------------
#
# Goal: every endpoint the GUI calls should be reachable from the terminal
# without resorting to raw curl, so debugging "what's actually in the DB?"
# stops requiring a SQLite shell.  Each resource maps to one (or two) of
# the existing GET endpoints; rendering goes through one generic table
# helper plus the existing `_print_json` / CSV / JSONL helpers.
#
# All `vn show` subcommands are read-only.

def _print_records_table(
    records: list[dict[str, Any]],
    columns: tuple[str, ...],
) -> None:
    """Render a list of arbitrary dicts as an aligned text table.

    Unknown columns render as an empty string.  Lists are joined with
    commas; everything else is stringified.  Mirrors `_print_task_table`
    but is task-agnostic so `vn show projects/users/...` can reuse it.
    """
    if not records:
        print("(empty)")
        return

    def _val(rec: dict[str, Any], col: str) -> str:
        v = rec.get(col)
        if v is None:
            return ""
        if isinstance(v, list):
            return ",".join(str(x) for x in v)
        if isinstance(v, dict):
            return json.dumps(v, default=str, sort_keys=True)
        return str(v)

    rows = [tuple(_val(r, c) for c in columns) for r in records]
    headers = tuple(c.upper() for c in columns)
    widths = [
        max(len(h), max((len(r[i]) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*r))


def _print_records_csv(
    records: list[dict[str, Any]],
    columns: tuple[str, ...],
) -> None:
    import csv
    w = csv.writer(sys.stdout)
    w.writerow([c.lower() for c in columns])
    for r in records:
        w.writerow([
            ",".join(str(x) for x in v) if isinstance(v, list)
            else "" if v is None else str(v)
            for v in (r.get(c) for c in columns)
        ])


def _emit_records(
    args: argparse.Namespace,
    records: list[dict[str, Any]],
    default_columns: tuple[str, ...],
    raw: Any | None = None,
) -> None:
    """Render `records` according to --format / --columns / --json.

    `raw` is the API envelope to emit when --format=json (so callers that
    receive {"foo": [...]} can pass the dict verbatim instead of unwrapped
    records).  Defaults to `records` when not supplied.
    """
    fmt = (getattr(args, "format", None) or ("json" if args.json else "table")).lower()
    cols = getattr(args, "columns", None)
    columns = tuple(c.strip().lower() for c in (cols or ",".join(default_columns)).split(",") if c.strip())
    if fmt == "json":
        _print_json(raw if raw is not None else records)
    elif fmt == "jsonl":
        for r in records:
            sys.stdout.write(json.dumps(r, default=str, sort_keys=True))
            sys.stdout.write("\n")
    elif fmt == "csv":
        _print_records_csv(records, columns)
    elif fmt == "ids":
        for r in records:
            sys.stdout.write(str(r.get("id") or r.get("name") or r.get("path") or ""))
            sys.stdout.write("\n")
    else:
        _print_records_table(records, columns)


def _wrap_string_list(items: list[Any], key: str) -> list[dict[str, Any]]:
    """Some endpoints return a flat ['name1', 'name2', ...] list; lift it
    into [{key: 'name1'}, ...] so the generic renderer can show it."""
    return [{key: x} for x in items]


def cmd_show(client: Client, args: argparse.Namespace) -> int:
    resource = args.resource

    if resource == "projects":
        if args.target:
            # Project detail: members + notes for one project.
            members = client.get(f"/api/projects/{args.target}/members") or []
            notes = client.get(f"/api/projects/{args.target}/notes") or []
            envelope = {"project": args.target, "members": members, "notes": notes}
            if (args.format or ("json" if args.json else "table")).lower() == "json":
                _print_json(envelope)
                return 0
            print(f"== project {args.target} ==")
            print(f"members ({len(members)}):")
            _print_records_table(members, ("user_name", "role"))
            print()
            print(f"notes ({len(notes)}):")
            _print_records_table(notes, ("id", "path", "title"))
            return 0
        data = client.get("/api/projects") or []
        _emit_records(args, data, ("name", "role"))
        return 0

    if resource == "users":
        data = client.get("/api/users") or []
        # /api/users returns ['name1', 'name2'] — wrap for table rendering.
        records = _wrap_string_list(data, "name") if data and isinstance(data[0], str) else data
        _emit_records(args, records, ("name",), raw=data)
        return 0

    if resource == "features":
        if args.target:
            data = client.get(f"/api/features/{args.target}/tasks") or {}
            tasks = data.get("tasks", [])
            fmt = (args.format or ("json" if args.json else "table")).lower()
            if fmt == "json":
                _print_json(data)
                return 0
            cols = tuple(c.strip().lower() for c in (args.columns or ",".join(_DEFAULT_COLUMNS)).split(",") if c.strip())
            print(f"== feature {args.target}  ({len(tasks)} tasks) ==")
            _print_task_table(tasks, cols)
            aggs = data.get("aggregations") or {}
            if aggs and fmt == "table":
                print()
                print(f"owners:   {','.join(aggs.get('owners') or [])}")
                print(f"projects: {','.join(aggs.get('projects') or [])}")
                print(f"status:   {aggs.get('status_breakdown') or {}}")
            return 0
        data = client.get("/api/features") or []
        records = _wrap_string_list(data, "name") if data and isinstance(data[0], str) else data
        _emit_records(args, records, ("name",), raw=data)
        return 0

    if resource == "attrs":
        data = client.get("/api/attrs") or []
        # Each row: {key, count, sample_values}.  Render samples as joined.
        _emit_records(args, data, ("key", "count", "sample_values"))
        return 0

    if resource == "notes":
        if args.target:
            # Single note: id (int) or path (string).
            t = args.target
            if t.isdigit():
                data = client.get(f"/api/notes/{t}") or {}
            else:
                # /api/notes/{id} only accepts an int; resolve path -> id first.
                listing = client.get("/api/notes") or []
                match = next((n for n in listing if n.get("path") == t), None)
                if not match:
                    print(f"vn: note not found: {t}", file=sys.stderr)
                    return 1
                data = client.get(f"/api/notes/{match['id']}") or {}
            if args.json or (args.format or "").lower() == "json":
                _print_json(data)
                return 0
            print(f"id:    {data.get('id')}")
            print(f"path:  {data.get('path')}")
            print(f"title: {data.get('title')}")
            print(f"etag:  {data.get('etag')}")
            body = data.get("body_md") or ""
            if args.full:
                print()
                print(body)
            else:
                preview = "\n".join(body.splitlines()[:20])
                print()
                print(preview)
                if body.count("\n") > 20:
                    print(f"... ({body.count(chr(10)) + 1} lines total — pass --full to see all)")
            return 0
        data = client.get("/api/notes") or []
        _emit_records(args, data, ("id", "path", "title", "updated_at"))
        return 0

    if resource == "tree":
        data = client.get("/api/tree") or []
        if args.json or (args.format or "").lower() == "json":
            _print_json(data)
            return 0
        for proj in data:
            name = proj.get("project") or "(loose)"
            role = proj.get("role") or "?"
            notes = proj.get("notes") or []
            print(f"{name}  [{role}]  ({len(notes)} notes)")
            for i, n in enumerate(notes):
                glyph = "└─" if i == len(notes) - 1 else "├─"
                print(f"  {glyph} {n.get('path')}  (id={n.get('id')})")
        return 0

    if resource == "agenda":
        params: dict[str, Any] = {}
        if args.owner:
            params["owner"] = args.owner
        if args.days is not None:
            params["days"] = args.days
        data = client.get("/api/agenda", **params) or {}
        if args.json or (args.format or "").lower() == "json":
            _print_json(data)
            return 0
        win = data.get("window") or {}
        print(f"agenda {win.get('start')} → {win.get('end')}  ({win.get('days')} days)")
        for day in sorted((data.get("by_day") or {}).keys()):
            tasks = data["by_day"][day]
            print()
            print(f"== {day}  ({len(tasks)}) ==")
            _print_task_table(tasks, _DEFAULT_COLUMNS)
        return 0

    if resource == "task":
        if not args.target:
            print("vn: 'show task' requires a task ref (e.g. T-XXXXXX)", file=sys.stderr)
            return 2
        data = client.get(f"/api/tasks/{args.target}") or {}
        if args.json or (args.format or "").lower() == "json":
            _print_json(data)
            return 0
        _print_task_table([data] if data else [], _DEFAULT_COLUMNS)
        return 0

    if resource == "links":
        if not args.target:
            print("vn: 'show links' requires a task ref", file=sys.stderr)
            return 2
        data = client.get(f"/api/cards/{args.target}/links") or {}
        links = data.get("links") or []
        _emit_records(args, links, ("other_slug", "kind", "direction"), raw=data)
        return 0

    if resource == "me":
        data = client.get("/api/me") or {}
        views = client.get("/api/me/views") or {}
        envelope = {"me": data, "saved_views": views}
        if args.json or (args.format or "").lower() == "json":
            _print_json(envelope)
            return 0
        tag = " (admin)" if data.get("is_admin") else ""
        print(f"name: {data.get('name')}{tag}")
        print(f"saved views: {len(views)}")
        for name in sorted(views.keys()):
            print(f"  - {name}")
        return 0

    if resource == "search":
        if not args.target:
            print("vn: 'show search' requires a query string", file=sys.stderr)
            return 2
        data = client.get("/api/search", q=args.target) or []
        _emit_records(args, data, ("id", "path", "title"))
        return 0

    print(f"vn: unknown resource: {resource}", file=sys.stderr)
    return 2


def cmd_api(client: Client, args: argparse.Namespace) -> int:
    """Generic HTTP escape hatch — any method, any path.

    Mirrors `curl -u user:pass` semantics but with the auth handled by
    the Client.  Useful for endpoints `vn show` doesn't cover, prototyping
    new endpoints, or exercising admin routes.

    Examples:
      vn api GET  /api/admin/users
      vn api GET  /api/tasks --query 'project=ww18&kind=ar'
      vn api POST /api/projects --json-body '{"name":"ww19"}'
    """
    method = args.method.upper()
    path = args.path
    if not path.startswith("/"):
        path = "/" + path

    params: dict[str, Any] = {}
    if args.query:
        # Accept either repeated --query 'k=v' or a single 'k=v&k2=v2' string.
        for chunk in args.query:
            for piece in chunk.split("&"):
                piece = piece.strip()
                if not piece:
                    continue
                if "=" not in piece:
                    print(f"vn: bad --query clause (expected key=value): {piece!r}",
                          file=sys.stderr)
                    return 2
                k, v = piece.split("=", 1)
                k = k.strip()
                if k in params:
                    cur = params[k]
                    params[k] = (cur if isinstance(cur, list) else [cur]) + [v]
                else:
                    params[k] = v

    body: Any = None
    if args.json_body is not None:
        try:
            body = json.loads(args.json_body)
        except json.JSONDecodeError as e:
            print(f"vn: --json-body is not valid JSON: {e}", file=sys.stderr)
            return 2

    try:
        data = client.request(method, path, params=params or None, body=body)
    except ApiError as e:
        # Print body to stdout (in case it's machine-readable) and the
        # status to stderr; map HTTP class to a non-zero exit code so
        # scripts can branch on it.
        print(f"vn: HTTP {e.status}: {e.body or '(no body)'}", file=sys.stderr)
        if e.status >= 500:
            return 5
        if e.status >= 400:
            return 4
        return 1

    fmt = (args.format or "json").lower()
    if fmt == "json" or args.json:
        _print_json(data)
    elif fmt == "jsonl":
        if isinstance(data, list):
            for r in data:
                sys.stdout.write(json.dumps(r, default=str, sort_keys=True) + "\n")
        else:
            sys.stdout.write(json.dumps(data, default=str, sort_keys=True) + "\n")
    elif fmt == "raw":
        # Pass through whatever the server sent (already decoded by Client).
        if isinstance(data, (dict, list)):
            _print_json(data)
        elif data is None:
            pass
        else:
            sys.stdout.write(str(data))
            sys.stdout.write("\n")
    else:
        print(f"vn: --format must be json|jsonl|raw for `vn api` (got {fmt!r})",
              file=sys.stderr)
        return 2
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vn",
        description="Terminal CLI for VegaNotes.",
    )
    p.add_argument("--profile", help="credentials profile (default: VEGANOTES_PROFILE or 'default')")
    p.add_argument("--json", action="store_true", help="emit raw JSON")
    p.add_argument("--version", action="version", version=f"vn {__version__}")

    sub = p.add_subparsers(dest="command", required=True)

    pt = sub.add_parser(
        "task",
        help="patch (or fetch) a task by id/ref",
        description="Examples: vn task T-123 status=done priority=P1 eta=2026-W18",
    )
    pt.add_argument("id", help="task id or ref (e.g. T-123)")
    pt.add_argument("attrs", nargs="*", help="key=value pairs")
    pt.set_defaults(func=cmd_task)

    def _add_list_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--owner")
        target.add_argument("--project")
        target.add_argument("--feature")
        target.add_argument("--priority")
        target.add_argument("--status")
        target.add_argument("--kind")
        target.add_argument("--eta-before", dest="eta_before")
        target.add_argument("--eta-after", dest="eta_after")
        target.add_argument("--hide-done", action="store_true", dest="hide_done")
        target.add_argument("-q", "--query", dest="q")
        target.add_argument("--not-owner", dest="not_owner")
        target.add_argument("--not-project", dest="not_project")
        target.add_argument("--not-feature", dest="not_feature")
        target.add_argument("--not-status", dest="not_status")
        target.add_argument("--not-priority", dest="not_priority")
        target.add_argument(
            "-w", "--where", action="append", default=[],
            help="DSL clause, repeatable (e.g. --where '@area=fit-val' --where 'eta>=ww18')",
        )
        target.add_argument("--sort", help="sort spec, e.g. 'eta:desc' or 'priority,title'")
        target.add_argument("--limit", type=int)
        target.add_argument("--offset", type=int)
        target.add_argument(
            "--format", choices=("table", "json", "jsonl", "csv", "ids"),
            help="output format (default: table; 'json' if --json is set)",
        )
        target.add_argument(
            "--columns",
            help="comma-separated columns for table/csv "
                 f"(default: {','.join(_DEFAULT_COLUMNS)})",
        )
        target.add_argument(
            "--group-by", dest="group_by",
            help="bucket the table output by this field (table format only)",
        )
        target.add_argument(
            "--tree", action="store_true",
            help="show parent tasks with their subtasks indented underneath; "
                 "implies --kind task,ar and include_children=true",
        )
        target.add_argument(
            "--with-children", action="store_true", dest="with_children",
            help="ask the API to nest subtasks under each parent in the "
                 "JSON/JSONL response (no rendering change)",
        )
        target.set_defaults(func=cmd_list)

    pl = sub.add_parser("list", help="list tasks")
    _add_list_args(pl)

    pq = sub.add_parser(
        "query",
        help="alias for 'list' — discoverability for query-style usage",
        description="Examples:\n"
                    "  vn query -w 'owner=alice' -w '@area=fit-val' --sort eta:desc\n"
                    "  vn query -w 'priority in P0,P1' --format ids | xargs -n1 vn task",
    )
    _add_list_args(pq)

    pn = sub.add_parser("note", help="note operations")
    nsub = pn.add_subparsers(dest="note_command", required=True)

    pnn = nsub.add_parser("new", help="create a new note")
    pnn.add_argument("--project", required=True, help="project / folder (e.g. ww16)")
    pnn.add_argument("--title", required=True, help="note title")
    pnn.add_argument("--path", help="explicit path (overrides project/title slug)")
    pnn.add_argument("--body", help="markdown body (default: title heading)")
    pnn.set_defaults(func=cmd_note_new)

    pw = sub.add_parser("whoami", help="show authenticated user")
    pw.set_defaults(func=cmd_whoami)

    # `vn show <resource> [target]` — read-only inspector for the rest of
    # the API; reuses --format / --columns where it makes sense.
    show_resources = (
        "projects", "users", "features", "attrs", "notes", "tree",
        "agenda", "task", "links", "me", "search",
    )
    ps = sub.add_parser(
        "show",
        help="inspect a resource (projects, users, features, attrs, notes, tree, agenda, task, links, me, search)",
        description=(
            "Read-only inspector for the rest of the API.\n\n"
            "Examples:\n"
            "  vn show projects                # list all projects\n"
            "  vn show projects ww18           # one project's members + notes\n"
            "  vn show users\n"
            "  vn show features                # all feature names\n"
            "  vn show features ic-streamlining # tasks tagged with this feature\n"
            "  vn show attrs                   # known attribute keys + counts\n"
            "  vn show notes                   # all notes (id, path, title)\n"
            "  vn show notes ww18/standup.md   # one note (header + 20-line preview)\n"
            "  vn show notes 42 --full         # full body of note id=42\n"
            "  vn show tree                    # project -> notes hierarchy\n"
            "  vn show agenda --owner alice\n"
            "  vn show task T-ABC123\n"
            "  vn show links T-ABC123\n"
            "  vn show me                      # self info + saved views\n"
            "  vn show search 'carveout'       # FTS across notes\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ps.add_argument("resource", choices=show_resources)
    ps.add_argument("target", nargs="?",
                    help="optional resource id/name (project name, feature name, note id/path, task ref, search query)")
    ps.add_argument("--owner", help="(agenda only) filter to one owner")
    ps.add_argument("--days", type=int, help="(agenda only) days of look-ahead")
    ps.add_argument("--full", action="store_true",
                    help="(notes <id|path>) dump the full markdown body instead of a 20-line preview")
    ps.add_argument(
        "--format", choices=("table", "json", "jsonl", "csv", "ids"),
        help="output format (default: table; 'json' if --json is set)",
    )
    ps.add_argument("--columns", help="comma-separated columns for table/csv")
    ps.set_defaults(func=cmd_show)

    pa = sub.add_parser(
        "api",
        help="generic HTTP escape hatch — vn api METHOD /api/path [--query …] [--json-body …]",
        description=(
            "Call any backend endpoint by URL.  Use this for endpoints `vn show` "
            "doesn't cover or for prototyping.\n\n"
            "Examples:\n"
            "  vn api GET  /api/admin/users\n"
            "  vn api GET  /api/tasks --query 'project=ww18' --query 'kind=ar'\n"
            "  vn api POST /api/projects --json-body '{\"name\":\"ww19\"}'\n"
            "  vn api PATCH /api/tasks/T-ABC123 --json-body '{\"status\":\"done\"}'\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    pa.add_argument("method", choices=("GET", "POST", "PUT", "PATCH", "DELETE"),
                    type=str.upper)
    pa.add_argument("path", help="API path, e.g. /api/projects")
    pa.add_argument("--query", action="append", default=[],
                    help="repeatable key=value (or 'k=v&k2=v2')")
    pa.add_argument("--json-body", dest="json_body",
                    help="request body as a JSON string")
    pa.add_argument("--format", choices=("json", "jsonl", "raw"),
                    help="output format (default: json)")
    pa.set_defaults(func=cmd_api)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        creds = load_credentials(args.profile)
    except CredentialsError as e:
        print(f"vn: {e}", file=sys.stderr)
        return 2

    client = Client(creds)
    try:
        return args.func(client, args)
    except ApiError as e:
        print(f"vn: API error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
