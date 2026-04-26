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
    """Classify a task row as 'task', 'subtask' or 'ar'.

    A row with parent_task_id set is always a subtask (regardless of kind);
    otherwise the value of `kind` (default 'task') is returned.
    """
    if task.get("parent_task_id"):
        return "subtask"
    return str(task.get("kind") or "task")


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


def _flatten_tree(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten parents + their `children` into one row list, tagging each
    child with an `_indent_prefix` so the title column shows the hierarchy.

    Children come straight from the API's `include_children=true` response
    (a shallow `children` array per parent).  Parents that have no children
    are returned as-is.
    """
    out: list[dict[str, Any]] = []
    for parent in tasks:
        parent_copy = dict(parent)
        parent_copy.pop("_indent_prefix", None)
        out.append(parent_copy)
        kids = parent.get("children") or []
        last = len(kids) - 1
        for i, c in enumerate(kids):
            child = dict(c)
            child["_indent_prefix"] = ("└─ " if i == last else "├─ ")
            child.setdefault("parent_task_id", parent.get("id"))
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
