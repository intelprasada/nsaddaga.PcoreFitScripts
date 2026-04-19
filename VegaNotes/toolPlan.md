# VegaNotes — Implementation Plan (v2)

## v3 — Active feature batch (2026-04-19 PM)

1. **Projects = folders.** Top-level subfolders of `notes/` are projects. New `POST /api/projects` (mkdir), `GET /api/projects` (list folders), `GET /api/projects/{p}/notes` (tree of .md inside).
2. **Sidebar nav** in the editor — tree view of projects → notes. Click to open.
3. **Kanban any-col → any-col** drag (already supported by dnd-kit; ensure all status columns are droppable).
4. **Status round-trip on drop.** `PATCH /api/tasks/{id} {status}` mutates the actual `!task` line in the .md (replace existing `#status x` or append a new one), then re-indexes.
5. **Project sharing & RBAC.** New table `projectmember(project_name, user_name, role)`. Roles: `manager` (full CRUD on the project's notes/tasks) and `member` (can only edit tasks they own). Enforced in API guard.
6. **Editor coloring** — TipTap regex decorations: distinct colors for `!task` (emerald), `#attrs` (sky), `@user` (violet), markdown headings, prose. Applies in both editing and read modes.
7. **Hierarchical attribute inheritance.** Lines containing only tokens (e.g. `@nancy` or `#project veganotes`) at indent N form an "attr context" inherited by all `!task` declarations at indent > N until popped. Existing free-form-attr-attaches-to-current-task behavior preserved when current task is at deeper indent.
8. **`@user` shorthand** for `#owner user` (lexer extension; word-boundary safe vs emails).
9. **Docs.** New `docs/user-guide.md` (worked examples) and `docs/requirements.md` (feature reference for development).

## Status (snapshot — 2026-04-19)
- ✅ Backend runs natively on this SLES box: `/tmp/vega-venv/bin/uvicorn app.main:app` (10/10 pytest pass).
- ✅ Parsers: Python + TS parity, 7+7 tests pass.
- ✅ Frontend: `npm install --cache=/tmp/vega-npm-cache` + `npm run build` produce `frontend/dist`.
- ✅ Single-pod end-to-end verified — backend serves React SPA at `/`, `/api/*` returns indexed tasks/agenda. Currently running on **port 8765** (login `admin`/`admin`).
- ✅ Deploy artifacts (Dockerfiles, compose, K8s base+overlays, Helm chart) and docs (`docs/{syntax,queries,architecture,api,deployment,development}.md`) created. **Untestable on this host** — no docker/kubectl/helm here; use them on a Docker-enabled machine.
- 🔧 Remaining: TipTap custom block, Kanban AST mutator (replace text rewriter), TimelineView gantt, Graph edges via `/api/cards/{id}/links`, saved views, Playwright e2e, WS broadcast in `watch_loop()`.

## 1. Problem & Approach

A markdown-first, visually rich note-taking + lightweight project tracker.
Notes are plain `.md` files on disk (the **source of truth**); inline tokens
(`!task`, `#task`, `#eta`, `#priority`, `#project`, `#owner`, `#status`,
`#estimate`, `#feature`, `#link`) drive structured task/attribute extraction.
A SQLite index, rebuilt from disk by a file-watcher, powers fast queries:
per-user task lists, "agenda for next week", filter-by-feature, bidirectional
links between cards, and Kanban / Timeline / Calendar / Graph views with
drag-and-drop that round-trips back to the underlying markdown.

## 2. Tech Stack

### Backend
- **Python 3.12 + FastAPI + SQLModel** (Pydantic-native, less boilerplate).
- **SQLite (FTS5)** as a *derived index/cache* — never the source of truth.
- **`watchfiles`** (Rust-backed) for md → index sync, debounced.
- **Markdown:** `markdown-it-py` with a custom plugin for the token grammar.
- **WebSocket** `/ws` to push index updates to the UI when files change on
  disk (edit in your IDE → board updates live).
- **Auth:** HTTP Basic, single shared instance, bcrypt-hashed password from a K8s `Secret`.
- **Optional git mode:** notes dir is a bare-backed git repo on the PVC; every
  save = a commit (free history, blame, audit).

### Frontend
- **Vite + React + TypeScript**.
- **shadcn/ui + Tailwind + Radix** for an accessible, themeable, modern look.
- **TipTap** (ProseMirror) editor with custom block nodes so each `!task`
  renders as an inline interactive **card** inside the markdown; `tiptap-markdown`
  for round-trip serialization through our token serializer.
- **dnd-kit** for accessible drag-and-drop (Kanban columns, sortable lists,
  cross-container moves, keyboard DnD).
- **TanStack Query** for server state, **Zustand** for local UI state.
- **Framer Motion** for card/layout transitions.
- **cmdk** ⌘K command palette.
- **Visualizations:**
  - Kanban — dnd-kit `<SortableContext>`.
  - Timeline / Gantt — `frappe-gantt-react` or `vis-timeline`.
  - Calendar — `@fullcalendar/react`.
  - Graph of card-to-card links — `react-flow`.

### Shared parser
- Two implementations (TS for the editor, Python for the backend) sharing a
  **golden-fixture test suite** to guarantee parity. Token registry on both
  sides so adding a new `#token` is a one-file change per language.

### Packaging / Deploy
- **Single-pod option:** FastAPI serves the React static bundle via
  `StaticFiles` → one Deployment, no CORS. Multi-pod option kept for scale.
- Multi-stage Docker builds; `python:3.12-slim` + (optional) `nginx:alpine`.
- **Kubernetes:** plain manifests + **Kustomize** overlays (`dev`, `prod`); a
  **Helm chart** as an alternative install path.
- **PVC** at `/data` holds: `notes/` (md files), `index.sqlite`, `git/` (if enabled).
- Liveness `/healthz`, readiness `/readyz`, resource requests/limits.

## 3. Token / Syntax Spec

Inline tokens are parsed line-by-line inside markdown:

| Token              | Meaning                                                               | Example                          |
| ------------------ | --------------------------------------------------------------------- | -------------------------------- |
| `!task <title>`    | Declares a task or subtask (list-nesting => subtask)                  | `- !task Wire up auth`           |
| `#task <id\|slug>` | Reference to an existing task (creates a directed link)               | `Blocked by #task auth-init`     |
| `#eta <when>`      | Due date; multi-format (ISO, `+2d`, `next fri`) via `dateparser`      | `#eta 2026-05-01`, `#eta +3d`    |
| `#priority <val>`  | User-defined vocabulary (P0/P1/high/low/…); ordering set in ConfigMap | `#priority P1`                   |
| `#project <name>`  | User-defined project bucket                                           | `#project vega-core`             |
| `#owner <name>`    | Ownership (one or many)                                               | `#owner nsaddaga`                |
| `#status <val>`    | Status (todo/in-progress/done/blocked)                                | `#status in-progress`            |
| `#estimate <dur>`  | Effort estimate (`2h`, `1d`, `0.5w`)                                  | `#estimate 4h`                   |
| `#feature <name>`  | Feature tag — primary axis for "pull all related tasks across users"  | `#feature search-rewrite`        |
| `#link <slug>`     | Generic bidirectional link to any card/note                           | `#link rfc-2026-04`              |

Rules
- A token attaches to the nearest enclosing `!task` (same line, or most recent
  `!task` above in the same list block).
- Unknown `#foo bar` tokens are preserved as free-form attributes (round-trippable).
- Parser is a pure function: `parse(md) -> {tasks[], attrs{}, refs[]}`.
- DnD/state changes in the UI mutate the **AST**, not the string, then re-serialize.

## 4. Data Model (SQLite index)

- `notes(id, path, title, body_md, frontmatter_json, mtime, created_at, updated_at)`
- `tasks(id, note_id, parent_task_id, slug, title, status, body_md_offset, created_at, updated_at)`
- `task_attrs(task_id, key, value, value_norm)` — `value_norm` holds the parsed
  date/duration/priority-rank for indexing.
- `users(id, name)`; `task_owners(task_id, user_id)`
- `projects(id, name)`; `task_projects(task_id, project_id)`
- `features(id, name)`; `task_features(task_id, feature_id)`
- `links(src_id, dst_id, kind)` — `kind ∈ {task, link, blocks, blocked_by}`.
  Stored once but **queried bidirectionally** via `UNION` view `links_bidir`.
- FTS5 virtual table `notes_fts(title, body_md)` for full-text search.
- Indexes: `task_attrs(key, value_norm)`, `tasks(status)`,
  `task_owners(user_id)`, `task_features(feature_id)`.

## 5. Query / Filter Capabilities (first-class features)

All exposed via REST + reflected in UI as saved-filter chips.

1. **Tasks for a user**
   `GET /api/tasks?owner=alice` → all tasks owned by `alice`.
2. **Hide done**
   `GET /api/tasks?owner=alice&status!=done` (sugar: `&hide_done=1`).
3. **Agenda — next 7 days**
   `GET /api/agenda?owner=alice&days=7`
   - Filters: `eta` within `[today, today+days]`, `status != done`.
   - Order: `ORDER BY eta ASC, priority_rank ASC, created_at ASC`.
   - Returns grouped by day for direct rendering.
4. **By feature (cross-user pull)**
   `GET /api/features/{name}/tasks` →
   returns the feature's tasks **plus** an aggregation:
   `owners[]`, `projects[]`, `eta_range`, `status_breakdown`, `priority_breakdown`.
5. **By due date**
   `GET /api/tasks?eta_before=2026-05-01&eta_after=2026-04-19`
   → response includes the same aggregation envelope (owners, projects, features)
   so the UI can render a "who/what/where" sidebar from a single call.
6. **By project / owner / priority / status** — composable query string,
   AND across keys, OR within multi-valued keys
   (e.g., `?owner=alice,bob&priority=P0,P1`).
7. **Bidirectional links**
   `GET /api/cards/{id}/links?direction=both` →
   merges outgoing (`#task` / `#link` from this card) and incoming references
   (other cards mentioning this one) into a single list with `direction` field.
   Powers the **Graph view** and the "Related" panel on every card.
8. **Saved views**
   Filters are URL-encoded; UI lets users name and pin them
   (stored in `users` table as `saved_views_json`).
9. **Full-text search**
   `GET /api/search?q=...` (FTS5), with optional filter overlay.

### Example: "Agenda for alice next week" SQL
```sql
SELECT t.id, t.title, eta.value_norm AS eta, COALESCE(pri.value_norm, 999) AS pri_rank
FROM tasks t
JOIN task_owners o ON o.task_id = t.id
JOIN users u ON u.id = o.user_id AND u.name = :user
JOIN task_attrs eta ON eta.task_id = t.id AND eta.key = 'eta'
LEFT JOIN task_attrs pri ON pri.task_id = t.id AND pri.key = 'priority'
WHERE t.status != 'done'
  AND eta.value_norm BETWEEN :today AND :today_plus_7
ORDER BY eta.value_norm ASC, pri_rank ASC, t.created_at ASC;
```

## 6. Repository Layout

```
VegaNotes/
├── README.md
├── LICENSE
├── .gitignore  .editorconfig  pnpm-workspace.yaml  turbo.json
├── docs/
│   ├── architecture.md       # Components, data flow, ER diagram
│   ├── syntax.md             # Full token grammar + examples
│   ├── queries.md            # All filter/agenda/link query recipes
│   ├── api.md                # REST + WS reference
│   ├── deployment.md         # Local, Docker, K8s (Kustomize + Helm)
│   ├── development.md        # Dev setup, tests, adding a token
│   └── design/               # Screenshots, wireframes
├── packages/
│   └── parser-ts/            # TS parser (used by editor & DnD mutations)
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py           # FastAPI + StaticFiles (single-pod)
│   │   ├── config.py
│   │   ├── db.py             # SQLModel engine/session + FTS5 setup
│   │   ├── auth.py           # HTTP Basic dep
│   │   ├── models/           # Note, Task, Attr, User, Project, Feature, Link
│   │   ├── schemas/
│   │   ├── api/              # routers: notes, tasks, agenda, features,
│   │   │                     #          cards, search, export, ws
│   │   ├── parser/           # Python parser (token registry, lexer, time_parse)
│   │   ├── indexer/          # watchfiles-based md → SQLite sync
│   │   ├── services/
│   │   └── git_store/        # optional git-backed save/commit
│   └── tests/
│       ├── parser/           # share fixtures with parser-ts via tests/fixtures/
│       ├── api/
│       └── fixtures/*.md
├── frontend/
│   ├── package.json  vite.config.ts  tailwind.config.ts
│   ├── src/
│   │   ├── main.tsx  App.tsx
│   │   ├── api/                  # client + WS hook + TanStack Query keys
│   │   ├── components/
│   │   │   ├── Editor/           # TipTap + token nodes + autocomplete
│   │   │   ├── Card/             # Task card (DnD source/target)
│   │   │   ├── Kanban/           # dnd-kit board (status / project / feature)
│   │   │   ├── Timeline/         # Gantt by #eta
│   │   │   ├── Calendar/         # FullCalendar view
│   │   │   ├── Graph/            # react-flow link graph
│   │   │   ├── Agenda/           # next-N-days view
│   │   │   ├── FilterBar/        # composable chips, saved views
│   │   │   └── CommandPalette/   # cmdk
│   │   ├── hooks/  store/  styles/
│   └── tests/  (Playwright e2e)
├── deploy/
│   ├── docker/  (backend.Dockerfile, frontend.Dockerfile, docker-compose.yml)
│   ├── k8s/
│   │   ├── base/  (deployments, services, pvc, secret.example, configmap, ingress)
│   │   └── overlays/{dev,prod}/
│   └── helm/veganotes/        # values.yaml, templates/
├── scripts/  (dev.sh, seed.py, export_all.py, reindex.py)
└── .github/workflows/         (ci.yml, docker.yml, e2e.yml)
```

## 7. Kubernetes Deployment

- Default: **one Deployment** (FastAPI serves API + static frontend) → simpler.
- Optional split: separate frontend Deployment behind nginx for scale.
- **PVC** `veganotes-data` mounted at `/data`:
  `notes/`, `index.sqlite`, `git/` (if enabled), `exports/`.
- `Secret veganotes-auth` — basic-auth bcrypt hash + cookie signing key.
- `ConfigMap` — token vocab (priority order, status names), timezone, agenda window.
- Probes, resource requests/limits set in `base`; overlays tune host/replicas.
- Backup: `kubectl exec` + `tar` of `/data`, or git push of the notes repo.

## 8. Documentation Plan

- `README.md` — pitch, screenshot, 60-second quickstart (compose + kubectl + helm).
- `docs/syntax.md` — token grammar + worked examples + edge cases.
- `docs/queries.md` — recipe book: agenda, filter-by-feature, bidirectional
  links, by-due-date aggregations, saved views.
- `docs/architecture.md` — md → watcher → index → API → UI; ER diagram.
- `docs/api.md` — auto-generated from FastAPI OpenAPI + WS contract.
- `docs/deployment.md` — local/docker/k8s/helm + PVC backup/restore.
- `docs/development.md` — adding a new token (one file per language + a fixture).

## 9. Milestones (todos)

1. `repo-skeleton` — Dirs, license, root README, .gitignore, editorconfig, pnpm/turbo workspace.
2. `parser-ts` — Shared TS parser package + golden fixtures.
3. `parser-py` — Python parser; reuse fixtures; parity tests.
4. `backend-scaffold` — FastAPI + SQLModel + Basic auth + healthz.
5. `indexer` — `watchfiles` md→SQLite sync (incl. FTS5), reindex script.
6. `backend-api` — notes, tasks, agenda, features, cards/links, search, export, WS.
7. `backend-tests` — pytest (parser + api + indexer); ruff + mypy; CI workflow.
8. `frontend-scaffold` — Vite + React + TS + Tailwind + shadcn + TanStack Query + Zustand.
9. `frontend-editor` — TipTap with custom token nodes, autocomplete, chip rendering.
10. `frontend-cards-dnd` — Card component + dnd-kit; AST mutation on drop → save.
11. `frontend-views` — Kanban, Timeline, Calendar, Graph, Agenda, FilterBar, ⌘K palette.
12. `frontend-saved-views` — URL-encoded filters + named/pinned saved views.
13. `docker-images` — Multi-stage Dockerfiles; single-pod compose.
14. `k8s-manifests` — Kustomize base + dev/prod overlays + PVC + Secret + ConfigMap + Ingress.
15. `helm-chart` — `deploy/helm/veganotes` chart.
16. `e2e-tests` — Playwright DnD + agenda + link flows.
17. `docs` — README + syntax/queries/architecture/api/deployment/development.
18. `ci-cd` — GitHub Actions: ci, e2e, docker (build/push on tag).

## 10. Notes / Deferred

- `#priority` ordering is configurable (ConfigMap) — `value_norm` stores the rank.
- `#eta` parsing via `dateparser` (ISO, relative, natural language); store UTC.
- Round-trip guarantee: re-export of a parsed note must equal the input modulo
  whitespace normalization — covered by golden tests.
- Defer: real-time multi-user CRDT collab (Yjs), mobile native app, plugin
  system beyond the token registry.
