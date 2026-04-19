# VegaNotes — Requirements

This document is the source-of-truth feature reference for tool development.
Each requirement has a stable ID (e.g. `R-PARSE-005`) so test cases, PRs,
and follow-up work can cite it.

---

## R-FILE — File model

| ID            | Requirement |
| ------------- | ----------- |
| R-FILE-001    | The on-disk `.md` files are the canonical source of truth. The SQLite index is a derived cache that must be rebuildable from disk at any time (`scripts/reindex.py`). |
| R-FILE-002    | A "project" is defined as a top-level subdirectory of `notes/`. Nested subdirectories are part of the parent project. |
| R-FILE-003    | A loose `.md` file directly under `notes/` belongs to no project (open access). |
| R-FILE-004    | The watcher (`watchfiles`) MUST detect external edits (CLI, IDE, git) and reindex within 1s. |
| R-FILE-005    | Round-trip invariant: any file mutated by the API (status drop, etc.) MUST differ from the input only in the specific tokens that were edited; whitespace and line ordering MUST be preserved. |

## R-PARSE — Parser semantics

| ID            | Requirement |
| ------------- | ----------- |
| R-PARSE-001   | A line containing `!task <title>` declares a task. The title runs from after `!task` until the next known `#token`, `@user` shorthand, or end-of-line. |
| R-PARSE-002   | Recognized attribute tokens: `eta`, `priority`, `project`, `owner`, `status`, `estimate`, `feature`, `task`, `link`. |
| R-PARSE-003   | `@<word>` is a shorthand for `#owner <word>`. The `@` MUST be at start-of-string or immediately after whitespace / `(` / `[`; this prevents matching email addresses such as `alice@example.com`. |
| R-PARSE-004   | Multi-valued attributes (`owner`, `project`, `feature`, `task`, `link`): when applied repeatedly, values are accumulated and **deduplicated**. |
| R-PARSE-005   | Single-valued attributes (`eta`, `priority`, `status`, `estimate`): the *last* value wins on a single line. |
| R-PARSE-006   | **Parent-task inheritance**: a child task (deeper indent than its enclosing `!task`) inherits *only multi-valued* attrs from its parent. Single-valued attrs are NOT inherited. |
| R-PARSE-007   | **Context-line inheritance**: a line whose non-token content is empty (only whitespace and bullet markers) is a "context" line. Its attributes are pushed onto a stack and inherited by every subsequent `!task` declaration until cleared. |
| R-PARSE-008   | A blank line clears the context stack and the parent stack. |
| R-PARSE-009   | A line with prose AND attribute tokens (e.g. `Blocked by #task wire-up-sso`) attaches the tokens to the **current task** (not the context stack). |
| R-PARSE-010   | The parser is implemented identically in Python and TypeScript. Parity is enforced by a shared golden-fixture test suite (`backend/tests/fixtures/*.{md,json}` symlinked from `packages/parser-ts/tests/fixtures`). |
| R-PARSE-011   | Slug generation: `lowercase, replace [^a-z0-9]+ -> -, strip leading/trailing -`. Empty result fall back to `task`. Collisions within a single file get `-2`, `-3` suffixes. |
| R-PARSE-012   | **Folder-derived project**: every task in `notes/<project>/...` is automatically associated with `<project>` at index time, in addition to any explicit `#project` tokens. The implicit value MUST be queryable via `GET /api/tasks?project=<project>`. |

## R-AUTH — Authentication & RBAC

| ID            | Requirement |
| ------------- | ----------- |
| R-AUTH-001    | All `/api/*` and `/ws` endpoints require HTTP Basic auth. |
| R-AUTH-002    | Credentials are validated against `VEGANOTES_BASIC_AUTH_USER` and a bcrypt hash in `VEGANOTES_BASIC_AUTH_PASS_HASH`. |
| R-AUTH-003    | The bootstrap admin (`VEGANOTES_BASIC_AUTH_USER`) is implicitly a manager of every project, regardless of the `projectmember` table. |
| R-AUTH-004    | Two project roles exist: `manager` and `member`. |
| R-AUTH-005    | A **manager** of a project may create, update, and delete any note within that project, and may add/remove members. |
| R-AUTH-006    | A **member** of a project may NOT create, rename, or delete notes. They may PATCH only those tasks where their username appears in the task's `#owner` set. |
| R-AUTH-007    | Users with no membership entry for a project receive `403` on any project-scoped endpoint. |
| R-AUTH-008    | Loose root-level notes (no project) are open to any authenticated user. |

## R-API — REST surface

All endpoints are JSON, prefixed `/api`.

| ID            | Endpoint                                   | Notes |
| ------------- | ------------------------------------------ | ----- |
| R-API-001     | `GET    /healthz`                          | Always 200 `{status:"ok"}`. No auth. |
| R-API-002     | `GET    /api/notes`                        | List all notes (auth-gated, no per-project filter — used by Kanban). |
| R-API-003     | `GET    /api/notes/{id}`                   | Returns full body. |
| R-API-004     | `PUT    /api/notes`                        | Upsert; body `{path, body_md}`. RBAC: manager only inside a project. |
| R-API-005     | `DELETE /api/notes/{id}`                   | RBAC: manager only. |
| R-API-006     | `POST   /api/parse`                        | Pure parser preview, no side effects. |
| R-API-007     | `GET    /api/tasks`                        | Composable filters: `owner`, `project`, `feature`, `priority`, `status`, `eta_before`, `eta_after`, `hide_done`, `q`. CSV in any value = OR. |
| R-API-008     | `PATCH  /api/tasks/{id}`                   | Body `{status?}`. Mutates the underlying `.md` line and reindexes. RBAC enforced. |
| R-API-009     | `GET    /api/agenda?owner&days`            | Tasks with eta in `[today, today+days]`, sorted by eta then priority. Excludes `done`. |
| R-API-010     | `GET    /api/features` / `.../{name}/tasks`| Cross-user pull; second form returns aggregations. |
| R-API-011     | `GET    /api/cards/{task_id}/links`        | Bidirectional refs via `links_bidir` view. |
| R-API-012     | `GET    /api/projects`                     | Returns `[{name, role}]` filtered by RBAC. |
| R-API-013     | `POST   /api/projects`                     | Body `{name}`. Caller becomes manager. Rejects names with `/`, leading `.`, or `..`. |
| R-API-014     | `GET    /api/projects/{p}/notes`           | Notes within a project; RBAC. |
| R-API-015     | `GET    /api/tree`                         | Project → notes hierarchy, RBAC-filtered. Includes a `null`-project bucket for loose notes. |
| R-API-016     | `GET/PUT/DELETE /api/projects/{p}/members` | Manager only. |
| R-API-016a    | `DELETE /api/projects/{p}`                 | Manager only. Removes the on-disk folder, deletes every note's index row, and clears all member rows for that project. Returns `{status, project, notes_removed}`. |
| R-API-017     | `GET    /api/users`                        | List of known users (anyone who has appeared as `#owner`). |
| R-API-018     | `GET    /api/search?q`                     | FTS5 over title + body_md. |

## R-UI — Frontend

| ID            | Requirement |
| ------------- | ----------- |
| R-UI-001      | A persistent **Sidebar** lists projects → notes. Each project shows the caller's role badge (`manager` / `member`). |
| R-UI-002      | Selecting a note in the Sidebar opens it in the editor view (auto-switches the active view). |
| R-UI-003      | The Sidebar has a "+ new" affordance to create a project (calls `POST /api/projects`). |
| R-UI-004      | The editor MUST highlight tokens with distinct colors: `!task` emerald, `#attr` sky, `@user` violet, headings slate-bold. |
| R-UI-005      | The Kanban board MUST allow dragging any card from any column to any column. On drop, it calls `PATCH /api/tasks/{id}` with the new status. |
| R-UI-006      | After a successful drop, the Kanban / Agenda / Editor queries MUST be invalidated so the UI re-fetches the canonical state. |
| R-UI-007      | The FilterBar populates owner/project/feature dropdowns from `/api/users`, `/api/projects`, `/api/features`. |
| R-UI-008      | Command Palette (⌘K) supports search across notes via `/api/search`. |
| R-UI-009      | **Sidebar context menu (right-click)**: on a project node — "New note in project" (any role with access) and "Delete project…" (manager only). On a note — "Delete note…" (manager only). All destructive actions MUST require an explicit `confirm()` dialog. |
| R-UI-010      | "New note in project" prompts for a filename inline; if the name omits `.md`, the suffix is appended. The new note is seeded with a `# <title>` heading and immediately opened in the editor. |
| R-UI-011      | After a project / note is deleted, the tree, project list, task list, and agenda queries MUST be invalidated. If the currently-open note belonged to the deleted project, the editor selection MUST be cleared. |
| R-UI-012      | The context menu MUST be dismissed on outside click and on the `Escape` key. |
| R-UI-013      | **Editor key handling**: pressing `Tab` inside the editor MUST insert a literal tab character at the caret rather than moving focus to the next form control. `Shift-Tab` MUST outdent the current line by one leading tab or up to four leading spaces. |
| R-UI-014      | The editor surface MUST NOT apply Tailwind Typography (`prose`) styling to the underlying `<pre>/<code>` block — that styling forces a dark background and white text that hides token colors. The editor uses a transparent background with slate-900 default text so that the `vega-task` / `vega-attr` / `vega-user` / `vega-heading` decoration colors render correctly. Tab width MUST be 4. |

## R-DEPLOY — Deployment

| ID            | Requirement |
| ------------- | ----------- |
| R-DEPLOY-001  | Single-pod default: FastAPI serves the React static bundle via `StaticFiles` (`frontend/dist`). The bundle directory is configurable via `VEGANOTES_STATIC_DIR`; if the env var is unset, the default `backend/app/static` is used. Visiting `/` MUST return the SPA `index.html` (HTTP 200) when the bundle is present. |
| R-DEPLOY-002  | Docker / Kustomize / Helm artifacts MUST mount a PVC at `/data` containing `notes/`, `index.sqlite`, and (optional) `git/`. |
| R-DEPLOY-003  | Liveness probe: `GET /healthz`. Readiness probe: `GET /readyz`. |
| R-DEPLOY-004  | Default port `8765` (override via `PORT`). |

## R-TEST — Testability

| ID            | Requirement |
| ------------- | ----------- |
| R-TEST-001    | Parser parity: every change to the Python parser MUST be mirrored in the TS parser; the shared golden fixtures MUST pass on both. |
| R-TEST-002    | Markdown round-trip: `update_task_status` and `replace_attr` are pure functions covered by unit tests; the only mutation is the targeted token. |
| R-TEST-003    | RBAC: API tests cover at least (a) project creation, (b) task PATCH happy path, (c) member add/remove, (d) invalid project names, (e) duplicate project rejection. |

## R-FUTURE — Deferred (tracked, not blocking v3)

* `R-FUT-001` Real-time multi-user collaboration via Yjs / CRDTs.
* `R-FUT-002` Mobile-native client.
* `R-FUT-003` Plugin system beyond the token registry.
* `R-FUT-004` Per-attribute custom validators (e.g. enforce `#priority` vocabulary from `ConfigMap`).

## Changelog

Each user request that adds or changes a requirement is recorded here in
chronological order. Update this section in lockstep with the requirement
tables above.

| Date       | Request summary | Requirements touched |
| ---------- | --------------- | -------------------- |
| 2026-04-19 | Serve frontend on the same port as the API; document `VEGANOTES_STATIC_DIR`. | R-DEPLOY-001 (clarified). |
| 2026-04-19 | Right-click in the Sidebar to add a note in a project, delete a project, or delete a note. | R-API-016a (new), R-UI-009..R-UI-012 (new). |
| 2026-04-19 | Pressing `Tab` in the editor must insert a tab instead of moving focus. | R-UI-013 (new). |
| 2026-04-19 | Folder-name → implicit `#project` for tasks; remove Tailwind `prose` from editor so token colors are visible. | R-PARSE-012 (new), R-UI-014 (new). |
