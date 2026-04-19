# VegaNotes — User Guide

VegaNotes turns plain Markdown files into a project-tracking board. Notes are
the source of truth on disk; everything else (Kanban, Agenda, Graph) is a
view rebuilt from them.

This guide walks through the most common workflows with worked examples.
For the formal token grammar see [`syntax.md`](syntax.md); for the
machine-readable spec see [`requirements.md`](requirements.md).

---

## 1. The two-second model

A line starting with `!task` declares a task. Anything beginning with
`#name` or `@user` on the same (or an indented child) line attaches an
attribute to it.

```md
- !task Wire up SSO #owner alice #priority P1 #eta 2026-05-01
  - !task Add login screen #status in-progress
  - !task Add OAuth callback
```

Result on the Kanban: three cards (one parent, two children). All three
inherit `#owner alice` from the parent because `owner` is a multi-valued
attribute (subtasks always inherit multi-valued attrs).

`#eta` and `#priority` are **single-valued** and are not inherited — only
the parent has them.

---

## 2. Projects = folders

Every top-level folder under `notes/` is a project. Every `.md` file inside
it (any depth) belongs to that project.

```
notes/
├── acme/                ← project "acme"
│   ├── sprint1.md
│   └── design/api.md
└── personal/            ← project "personal"
    └── todo.md
```

Create a project from the **Sidebar → "+ new"**, or:

```sh
curl -u admin:admin -X POST -H 'content-type: application/json' \
  -d '{"name":"acme"}' http://localhost:8765/api/projects
```

The Sidebar shows projects you can see, with a role badge (`manager` /
`member`). Click any note to open it in the editor.

---

## 3. The `@user` shorthand

`@alice` is sugar for `#owner alice`. It plays nicely inside prose but is
**not** matched inside email addresses.

```md
- !task Review pricing deck @alice @bob
```

Both alice and bob are now owners of this task.

### Hierarchical owner inheritance

A line that contains *only* tokens (no prose) is a **context line**. Its
attributes propagate to every `!task` declared after it, until a blank line
clears the context. This is perfect for assigning many tasks to one person:

```md
@nancy
- !task Triage P0 bugs
- !task Update Q3 forecast
- !task Schedule review with finance

(blank line above resets the context)

@oscar
- !task Renew SSL certs
```

Result: the first three tasks are owned by `nancy`; the fourth by `oscar`.

You can stack multiple context attrs on a single line:

```md
@nancy #project acme #feature search-rewrite
- !task Spec the new ranker
- !task Migrate old corpus
```

---

## 4. Editor coloring

The editor highlights tokens so they're easy to find at a glance:

| Token            | Color     |
| ---------------- | --------- |
| `!task`          | emerald   |
| `#anything`      | sky       |
| `@user`          | violet    |
| `# heading`      | slate-bold|

This is regex-driven (no parser round-trip), so the colors update as you
type with no perceptible lag.

---

## 5. Kanban — drag any card, any column

Open **Kanban** in the nav bar. Cards are grouped by `#status`:

* `todo` (default if no status is given)
* `in-progress`
* `blocked`
* `done`

Drag a card from any column to any other column. The browser sends
`PATCH /api/tasks/{id}` with the new status; the backend rewrites the
`#status` token in the underlying `.md` file (or appends one if the task
didn't have a status), then re-indexes. The change is **byte-stable** —
nothing else in the file is touched.

Tip: if a task has no `#status` token, it shows up in `todo` and a `#status`
token will be appended on the first drop.

---

## 6. Agenda — what's due next

Open **Agenda**. It calls
`GET /api/agenda?owner=<you>&days=7` and groups tasks by their `#eta`
date, in ascending order, with priority as the tiebreaker.

```md
- !task File quarterly taxes #eta 2026-05-01 #priority P0
- !task Backup laptop      #eta 2026-05-01 #priority P2
- !task Renew passport      #eta 2026-05-03
```

Renders:
```
2026-05-01
  • File quarterly taxes (P0)
  • Backup laptop (P2)
2026-05-03
  • Renew passport
```

Done tasks are excluded automatically.

---

## 7. Pull tasks by feature (cross-team view)

Tag work spanning multiple owners with `#feature`:

```md
@alice
- !task Add ranking debug API #feature search-rewrite

@bob
- !task Migrate index format #feature search-rewrite
```

Then either filter by feature in the FilterBar or hit
`GET /api/features/search-rewrite/tasks` — you get every task plus an
aggregation envelope (owners, projects, status breakdown, eta range) so you
can render a "who/what/where" panel from a single call.

---

## 8. Bidirectional links

Refer to other tasks with `#task <slug>` or arbitrary anchors with
`#link <slug>`:

```md
- !task Migrate index #feature search-rewrite
  Blocked by #task wire-up-sso, tracked in #link rfc-2026-04
```

The backend stores the edge once but serves it both ways via the
`links_bidir` SQL view. Hit `GET /api/cards/{task_id}/links` and you get
both outgoing and incoming references with a `direction` field; the Graph
view renders them.

---

## 9. Project sharing & roles

Each project has a member list. Two roles:

* **manager** — full CRUD on the project's notes and tasks.
* **member** — can only edit tasks they own (i.e. tasks where their
  username appears in `#owner` / `@user`).

Manage members:

```sh
# add bob as a member
curl -u admin:admin -X PUT -H 'content-type: application/json' \
  -d '{"user_name":"bob","role":"member"}' \
  http://localhost:8765/api/projects/acme/members

# promote bob to manager
curl -u admin:admin -X PUT -H 'content-type: application/json' \
  -d '{"user_name":"bob","role":"manager"}' \
  http://localhost:8765/api/projects/acme/members

# remove bob
curl -u admin:admin -X DELETE \
  http://localhost:8765/api/projects/acme/members/bob
```

When bob logs in he sees only `acme` in his Sidebar. He can drag his own
tasks across columns; if he tries to drag someone else's task the API
returns `403 members can only edit their own tasks`.

The bootstrap admin (set by `VEGANOTES_BASIC_AUTH_USER`) is implicitly a
manager of every project.

---

## 10. Search

Full-text search over titles + bodies via SQLite FTS5:

```sh
curl -u admin:admin "http://localhost:8765/api/search?q=ranking"
```

The Command Palette (⌘K) wires this up in the UI.

---

## 11. Worked example: a sprint board from scratch

```md
# Sprint 17

@alice #project acme #feature checkout-v2

- !task Wire payment provider #priority P0 #eta 2026-05-08
  - !task Stripe webhook handler #status in-progress
  - !task Refund flow

@bob

- !task QA the new checkout #priority P1 #eta 2026-05-10
- !task Update help center copy #priority P2

(blank line)

- !task Post-launch review #eta 2026-05-15 #status blocked
```

Saving this as `acme/sprint17.md` produces:

* 6 tasks under project `acme`, feature `checkout-v2`.
* The first three are owned by alice (parent + 2 inherited).
* The next two by bob.
* The last is unowned (after the blank line, the `@bob` context was cleared).

Open the Kanban and you'll see all 6 cards, draggable across `todo` /
`in-progress` / `blocked` / `done`.
