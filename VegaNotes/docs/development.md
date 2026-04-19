# Development

## Prereqs
- Python 3.12, Node 22+, pnpm 9 (`corepack enable`)

## First-time setup

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
pytest                                          # 10 tests should pass

# Shared TS parser + frontend
cd ..
pnpm install
cd packages/parser-ts && node --test --experimental-strip-types tests/parser.test.ts
cd ../../frontend && pnpm build
```

## Run dev server (both)

```bash
./scripts/dev.sh              # backend on :8000, frontend on :5173
python scripts/seed.py        # add a couple of sample notes
```

## Adding a new token

The two parser implementations must agree. Workflow:

1. **Edit the registry** in both languages:
   - `backend/app/parser/tokens.py`
   - `packages/parser-ts/src/tokens.ts`
2. **Add a fixture line** in `backend/tests/fixtures/sprint14.md` that uses it,
   and update the expected `sprint14.json`.
3. Run `pytest` and `node --test` — both must pass before merging.

## Layout

- `backend/app/parser/` — Python parser (canonical for indexing)
- `packages/parser-ts/` — TS parser (used by the editor for AST mutations)
- `backend/app/indexer/` — `watchfiles` md→SQLite sync
- `backend/app/api/`     — REST + WebSocket
- `frontend/src/`        — React app

## Testing

| Layer       | Command                                              |
| ----------- | ---------------------------------------------------- |
| Parser (Py) | `cd backend && pytest tests/parser`                  |
| Parser (TS) | `node --test --experimental-strip-types packages/parser-ts/tests/parser.test.ts` |
| API         | `cd backend && pytest tests/api`                     |
| Frontend    | `pnpm --filter @veganotes/frontend build`            |
| E2E         | `pnpm --filter @veganotes/frontend test:e2e`         |

## Linting

```bash
cd backend && ruff check . && mypy app
pnpm --filter @veganotes/frontend lint
```
