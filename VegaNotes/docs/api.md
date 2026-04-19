# API

FastAPI auto-generates an interactive spec at **`/docs`** (Swagger) and
**`/redoc`** when the backend is running. This page is a high-level summary;
prefer the live docs for exact request/response schemas.

## Auth

HTTP Basic on every endpoint except `/healthz` and `/readyz`.

## Endpoints

### Health
- `GET /healthz` — liveness, no auth
- `GET /readyz`  — readiness (DB + notes dir reachable), no auth

### Notes
- `GET    /api/notes`            — list notes (path, title, mtime)
- `GET    /api/notes/{path}`     — raw markdown
- `PUT    /api/notes/{path}`     — write markdown (triggers reindex)
- `DELETE /api/notes/{path}`

### Tasks
- `GET /api/tasks` — composable filters:
  `owner`, `project`, `feature`, `priority`, `status`, `eta_before`, `eta_after`,
  `hide_done`, `q` (FTS), `limit`, `offset`. Multi-value: comma-separated.
  Response includes the **aggregation envelope** (owners/projects/features/
  eta_range/status_breakdown/priority_breakdown).
- `GET /api/tasks/{id}`
- `PATCH /api/tasks/{id}` — convenience attribute update (round-tripped to md).

### Agenda
- `GET /api/agenda?owner=alice&days=7` — grouped by day, sorted by `eta`+`priority`.

### Features
- `GET /api/features` — all features
- `GET /api/features/{name}/tasks` — tasks + aggregation (cross-user pull)

### Cards / links
- `GET /api/cards/{id}/links?direction=both` — bidirectional links via `links_bidir`

### Search
- `GET /api/search?q=...` — FTS5 over notes

### Export
- `GET /api/export.tar.gz` — full notes tarball

### Realtime
- `WS /ws` — push events on file changes
  ```json
  { "type": "note:changed", "path": "ops/runbook.md" }
  { "type": "note:deleted", "path": "old.md" }
  ```
