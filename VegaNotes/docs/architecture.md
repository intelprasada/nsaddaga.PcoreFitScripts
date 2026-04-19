# Architecture

```
              ┌─────────────────────────────────────────────────────┐
              │                       PVC /data                     │
   user edits │  notes/  ←─────────── source of truth (md files)    │
   in UI/IDE  │  index.sqlite ←────── derived index (rebuildable)   │
              │  git/ (optional)  ←── auto-commit history           │
              └────────────▲───────────────────────▲────────────────┘
                           │                       │
                  watchfiles│ debounced            │SQL
                           │                       │
              ┌────────────┴───────────┐  ┌────────┴────────┐
              │ backend/app/indexer    │  │ backend/app/api │
              │  parse → upsert        │  │  REST + WS      │
              └────────────────────────┘  └────────┬────────┘
                                                   │ HTTP/WS
                                          ┌────────┴────────┐
                                          │ frontend (React)│
                                          │ TipTap · dnd-kit│
                                          │ Kanban/Timeline │
                                          │ Calendar/Graph  │
                                          └─────────────────┘
```

## Components

- **Notes (PVC)** — plain markdown files. Anything else is rebuildable.
- **Parser** — two implementations (Python, TypeScript) sharing a golden-fixture
  test suite. The TS parser drives editor rendering and DnD AST mutations; the
  Python parser drives the indexer and API.
- **Indexer** — `watchfiles` coroutine in `backend/app/indexer`. On any change:
  parse the file, upsert `notes`, replace its `tasks`/`task_attrs`/`task_owners`/
  `task_projects`/`task_features`/`links`, refresh FTS5.
- **API** — FastAPI in `backend/app/api`. Issues SQL against the index; never
  the markdown directly. WebSocket `/ws` pushes change events.
- **Frontend** — React + TipTap. UI changes (drag a card, change status) mutate
  the AST and POST the rewritten markdown back; the indexer re-syncs it.
- **Auth** — HTTP Basic, password as bcrypt hash in a K8s Secret.

## Data flow on a card move

1. User drags task from `todo` → `in-progress`.
2. Frontend loads the source markdown for that note, runs `parse()`,
   mutates `task.attrs.status`, runs `serialize()`, PUTs the new body.
3. API writes the file. `watchfiles` fires within ~100 ms.
4. Indexer reparses, replaces the task's rows, broadcasts `note:changed` over WS.
5. All connected clients refresh affected views via TanStack Query.

## ER (index)

```
notes ─< tasks ─< task_attrs
              ─< task_owners >─ users
              ─< task_projects >─ projects
              ─< task_features >─ features
              ─< links (src,dst,kind)   ← view: links_bidir
notes_fts (FTS5: title, body_md)
```

## Round-trip guarantee

`serialize(parse(md)) == md` (modulo whitespace normalization) is enforced by
golden fixtures. This is what makes the editor safe — DnD never corrupts
your notes.
