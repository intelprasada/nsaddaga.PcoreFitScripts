# VegaNotes

A markdown-first note-taking and lightweight project tracker. Notes are plain
`.md` files on disk (the source of truth); inline tokens turn them into
trackable tasks with owners, ETAs, priorities, projects, features, and
bidirectional links.

```md
# Sprint 14

- !task Wire up SSO #project veganotes #owner alice #eta +3d #priority P1
  - !task Add login screen #owner bob #eta 2026-04-22
- !task Migrate index #feature search-rewrite #owner alice #status in-progress
  Blocked by #task sso-init
```

That snippet is parsed into 3 tasks, 2 owners, 2 projects/features, 2 due dates,
1 priority, 1 status, and 1 directed link — and is round-trippable.

## Quickstart

### Local (Docker Compose)
```bash
cd deploy/docker
docker compose up --build
# open http://localhost:8080  (default user: admin / admin — change in .env)
```

### Kubernetes (Kustomize)
```bash
kubectl apply -k deploy/k8s/overlays/dev
```

### Kubernetes (Helm)
```bash
helm install veganotes deploy/helm/veganotes
```

## What it does

- **Markdown is the source of truth** — your notes live as `.md` files on a PVC
  (or a git repo). A SQLite index is rebuilt on disk changes.
- **Inline token grammar** — `!task`, `#task`, `#eta`, `#priority`, `#project`,
  `#owner`, `#status`, `#estimate`, `#feature`, `#link`. See [docs/syntax.md](docs/syntax.md).
- **Drag & drop card UX** — every `!task` renders as an inline card; drag across
  Kanban columns and the underlying markdown is rewritten.
- **Powerful queries** — per-user task lists, "agenda for next week" sorted by
  ETA then priority, filter-by-feature with cross-user aggregation,
  bidirectional link graph. See [docs/queries.md](docs/queries.md).
- **Multiple views** — Editor, Kanban, Timeline (Gantt), Calendar, Graph, Agenda.

## Documentation
- [Syntax](docs/syntax.md) · [Queries](docs/queries.md) · [Architecture](docs/architecture.md)
- [API](docs/api.md) · [Deployment](docs/deployment.md) · [Development](docs/development.md)

## License
MIT — see [LICENSE](LICENSE).
