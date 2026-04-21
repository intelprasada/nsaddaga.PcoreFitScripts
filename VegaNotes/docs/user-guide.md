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

`#eta` accepts several formats, all normalized to ISO `YYYY-MM-DD` for
sorting and the agenda view:

| Form                  | Example         | Resolves to                       |
| --------------------- | --------------- | --------------------------------- |
| ISO date              | `2026-05-01`    | `2026-05-01`                      |
| Relative              | `+3d`, `+1w`    | today + offset                    |
| Words                 | `today`, `tomorrow`, `next fri` | natural date  |
| **Intel work week**   | `WW17`, `WW17.3`, `2026WW17.0` | the matching weekday (see below) |

**Intel work-week notation** — Intel weeks run **Sunday → Saturday**, with
Sunday being day `.0` and Saturday day `.6`. WW1 is the week containing the
first Saturday of the calendar year (so e.g. WW1 of 2026 starts on
2025-12-28). Examples for 2026:

- `WW17` → `2026-04-24` (Friday — day defaults to `.5`)
- `WW17.0` → `2026-04-19` (Sunday)
- `WW17.1` → `2026-04-20` (Monday)
- `WW17.5` → `2026-04-24` (Friday)
- `WW16.6` → `2026-04-18` (the previous Saturday)
- `2025WW53.0` → `2025-12-28` (explicit year prefix)

```md
- !task Tape-out review #eta WW18.3 #priority P0
- !task Send agenda #eta WW17.4
```

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

## Action Required (`!AR`) sub-items

For checklist-style follow-ups under a top-level task, declare them with
`!AR <title>` instead of `!task`. An `!AR` is itself a task (it gets a slug,
status, owners, and `#eta`), but its `kind` is `"ar"` so the Kanban groups
it under its parent rather than rendering a separate card.

```md
- !task Stabilize FIT validation #owner nancy #priority P1
  - !AR Why is the suite failing now?
  - !AR Bisect to find offending commit #owner alice
  - !AR Add regression test once green
```

In the Kanban board the parent card "Stabilize FIT validation" shows a
`▸ 3 ARs (0 done / 3 open)` strip. Click it to expand, then click the status
pill on each AR to cycle `todo → in-progress → done → todo`. Status changes
round-trip back into the markdown file as a `#status` attribute on the
underlying `!AR` line — exactly like top-level tasks.

ARs participate in agendas and filters too (e.g. `?owner=alice` returns
Alice's ARs), so use `?kind=task` to restrict to top-level tasks only or
`?kind=ar` to see only Action-Required items.

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

### Free-form `#status` values still bucket correctly

You can hand-write descriptive statuses like
`#status blocked by HSD approval` or `#status done but pending QA` — the
parser scans the text for keywords and groups it into the right column on
the Kanban while preserving your wording on disk. Trigger words and
priority order are documented in `docs/syntax.md` → *Status keywords*.

### Click a card to edit attributes inline

Click anywhere on a card (the small **⋮⋮** handle in the top-right is for
dragging). A popover opens with editable **status / priority / ETA / owners
/ features / notes** fields. Each save fires one `PATCH /api/tasks/{id}`
sending only the fields you changed; ownership rules are enforced server-
side, so members get a friendly *"can't edit — not your task"* message
when they try to edit a task they don't own.

The **Notes** textarea writes free-form per-task notes as `#note <line>`
continuation entries indented under the task in the markdown file (one
entry per line in the textarea). Clearing the textarea removes the block.

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

### Roll to next week (`Next Week Agenda` button)

Open any note whose filename contains an Intel work-week tag (e.g.
`FIT weekly ww16.md`) and click **Next Week Agenda** in the editor toolbar.
VegaNotes will:

1. **Inject stable `#id` tokens** into every `!task` / `!AR` line in the
   *current* file that doesn't already have one. The IDs use the format
   `T-XXXXXX` (six Crockford-base32 chars). The source file is rewritten in
   place — keep the IDs as-is.
2. **Strip every done item** (a task or AR whose normalized `#status` is
   `done`) including its nested children.
3. **Bump `wwNN[.D]` references** in the title and prose by +1 — but **not**
   inside `#eta` values. ETAs intentionally survive so you can review each
   surviving item: either mark it done now or set a fresh future ETA.
4. **Write the new file** (`FIT weekly ww17.md`) where every surviving
   `!task` / `!AR` becomes an *agenda reference row*:

   ```md
   - #task T-A3F9K2 Wire up SSO #status in-progress #eta ww18
   ```

The reference row has no `!task`, so the indexer does **not** create a
duplicate Task. Instead, the override attrs (`#status`, `#eta`, …) are
applied write-through to the canonical task that lives in ww16.

This means:
- Marking `T-A3F9K2` done in `ww17` updates the same task. Re-rolling
  `ww17 → ww18` will silently drop it — no orphans.
- The original `ww16` note remains the system-of-record for the task title,
  owners, and history.
- Hierarchies are preserved: a child `!AR` becomes a child `#task` reference
  at the same indent level.

Tip: the new note is created **above** the source in the navigation tree so
the latest week is always on top.

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

Refer to other tasks with `#task <slug-or-id>` or arbitrary anchors with
`#link <slug>`:

```md
- !task Migrate index #feature search-rewrite
  Blocked by #task wire-up-sso, tracked in #link rfc-2026-04
```

When `#task` appears **mid-prose** (as above) it is a directed link to the
target. When it is the **leading token** of a line, it is an *agenda
reference row* (see §6 — Roll to next week) and the parser does not create a
new task. Both forms accept either a slug (`wire-up-sso`) or a stable ID
(`T-A3F9K2`).

The backend stores the edge once but serves it both ways via the
`links_bidir` SQL view. Hit `GET /api/cards/{task_id}/links` and you get
both outgoing and incoming references with a `direction` field; the Graph
view renders them.

---

## 9. Users, sharing & roles

VegaNotes is multi-user. There are two layers of authorization:

1. **Account role** (DB-wide): `admin` or regular user.
2. **Project role** (per project): `manager` or `member`.

### Logging in

Authentication is HTTP Basic (the browser pops a username/password dialog).

* On first boot the user named in `VEGANOTES_BASIC_AUTH_USER` (default
  `admin`) is auto-created in the DB as an admin, with the password from
  `VEGANOTES_BASIC_AUTH_PASS_HASH` (default `admin`). After that, the admin's
  password lives in the DB — rotate it from the Admin tab, no redeploy
  needed.
* Every other user must be created (or have a password set) via the **Admin**
  tab before they can log in.

#### Logging out

HTTP Basic has no real "logout"; browsers cache credentials until they're
restarted. The **logout** button in the navbar overwrites the cached creds
with bogus ones and reloads, which causes the browser to re-prompt.

| Browser     | Behavior                                                      |
| ----------- | ------------------------------------------------------------- |
| Firefox     | Works every time.                                             |
| Chrome/Edge | Usually works. If the prompt doesn't reappear, hard-refresh   |
|             | (`Ctrl+Shift+R`) or close the tab.                            |
| Safari      | Same as Chrome — occasionally needs a hard-refresh.           |

For testing **multiple identities side-by-side**, the most reliable trick is
an incognito/private window per identity (or a separate browser profile).

### Admin tab (admins only)

Only visible to users with the `admin` flag. Lets you:

* Create a new login (username + password, optionally admin).
* Set or reset any user's password.
* Promote a user to admin / demote them.
* Delete a user (their `ProjectMember` rows are removed too).

Guardrails enforced by the backend:

* You cannot demote, delete, or remove your own admin flag.
* You cannot remove admin from the **last remaining** admin.

> **Heads-up about pre-existing users.** Names that appeared as `@owner`
> mentions in your notes were auto-created **without a password** and show
> up as `not set — cannot log in` in the Admin tab. They can be assigned to
> tasks and projects, but won't be able to authenticate until you set a
> password for them.

### Project membership

Each project has its own member list. Two project roles:

* **manager** — full CRUD on the project's notes and tasks.
* **member** — can only edit tasks where they appear in `@user` / `#owner`.

Admins are implicitly managers of every project.

Manage members from the Sidebar's project right-click menu, or via the API:

```sh
# add bob as a member
curl -u admin:admin -X PUT -H 'content-type: application/json' \
  -d '{"user_name":"bob","role":"member"}' \
  http://localhost:8000/api/projects/acme/members

# promote bob to manager
curl -u admin:admin -X PUT -H 'content-type: application/json' \
  -d '{"user_name":"bob","role":"manager"}' \
  http://localhost:8000/api/projects/acme/members

# remove bob
curl -u admin:admin -X DELETE \
  http://localhost:8000/api/projects/acme/members/bob
```

When bob logs in he sees only `acme` in his Sidebar. He can drag his own
tasks across columns; if he tries to drag someone else's task the API
returns `403 members can only edit their own tasks`.

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
