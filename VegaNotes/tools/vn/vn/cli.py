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


def _print_task_table(tasks: list[dict[str, Any]]) -> None:
    if not tasks:
        print("(no tasks)")
        return
    rows = []
    for t in tasks:
        attrs = t.get("attrs") or {}
        prio = attrs.get("priority", "")
        if isinstance(prio, list):
            prio = prio[0] if prio else ""
        eta = attrs.get("eta", "")
        if isinstance(eta, list):
            eta = eta[0] if eta else ""
        # Prefer the T-XXXXXX uuid (what `vn task <ID>` expects); fall
        # back to the integer PK for unstamped tasks.
        ident = t.get("task_uuid") or t.get("ref") or t.get("id") or ""
        rows.append((
            str(ident),
            str(t.get("status") or ""),
            str(prio),
            str(eta),
            ",".join(t.get("owners") or []),
            (t.get("title") or "").strip(),
        ))
    headers = ("ID", "STATUS", "PRIO", "ETA", "OWNERS", "TITLE")
    widths = [
        max(len(h), max((len(r[i]) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*r))


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


def cmd_list(client: Client, args: argparse.Namespace) -> int:
    params: dict[str, Any] = {
        "owner": args.owner,
        "project": args.project,
        "feature": args.feature,
        "priority": args.priority,
        "status": args.status,
        "eta_before": args.eta_before,
        "eta_after": args.eta_after,
        "hide_done": args.hide_done,
        "q": args.q,
        "limit": args.limit,
    }
    data = client.get("/api/tasks", **params)
    tasks = (data or {}).get("tasks") if isinstance(data, dict) else data
    if args.json:
        _print_json(data)
    else:
        _print_task_table(tasks or [])
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

    pl = sub.add_parser("list", help="list tasks")
    pl.add_argument("--owner")
    pl.add_argument("--project")
    pl.add_argument("--feature")
    pl.add_argument("--priority")
    pl.add_argument("--status")
    pl.add_argument("--eta-before", dest="eta_before")
    pl.add_argument("--eta-after", dest="eta_after")
    pl.add_argument("--hide-done", action="store_true", dest="hide_done")
    pl.add_argument("-q", "--query", dest="q")
    pl.add_argument("--limit", type=int)
    pl.set_defaults(func=cmd_list)

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
