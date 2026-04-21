# SDLC & Deployment Training Guide

A walk-through of every Software Development Life Cycle stage as it applies to
VegaNotes, from a clean checkout to a running pod in production. Pair this
with [`command-reference.md`](./command-reference.md) for the exact commands.

---

## Stage 0 — Mental model

```
                    ┌────────── git main ──────────┐
                    │                              │
   plan ─► code ─► review ─► CI ─► artifact ─► deploy ─► observe
                                  (image)        (k8s)     (logs / health)
                                     │              │
                                     └─ rollback ◄──┘
```

VegaNotes is a single Docker image (FastAPI + bundled React) backed by a
PVC that holds the markdown notes (source of truth) and a derived SQLite
index. Single replica, `Recreate` strategy, RWO volume.

---

## Stage 1 — Plan

**Inputs:** issue / requirement, target users, acceptance criteria.

1. Open or pick up an issue. Capture user-visible behavior, not implementation.
2. Decide scope:
   - Is the change behind a flag?
   - Does it touch the parser? If yes, **both** `backend/app/parser/` and
     `packages/parser-ts/src/` must change in lockstep with a fixture update —
     see `docs/development.md`.
   - Does it require a schema migration? (Today: drop `index.sqlite` and let
     the indexer rebuild. For future Postgres mode you'll need Alembic.)
3. Note risks & rollback. SQLite + RWO ⇒ **no zero-downtime rolling
   upgrades** — a 5–30 s blip is expected.

**Definition of Ready:** acceptance bullets + test plan + rollout note.

---

## Stage 2 — Develop

```bash
git switch -c feat/<short-name>
./scripts/dev.sh                      # dual server with hot reload
```

Workflow:

- Backend changes → `uvicorn --reload` re-runs on save.
- Frontend changes → Vite HMR; if a module gets stuck, see Troubleshooting in
  `command-reference.md` §9.
- Touching the markdown grammar? Update **both** parsers and the
  `sprint14.md` / `sprint14.json` golden fixtures.
- Touching the API? Update `docs/api.md` in the same commit.

**Local quality gate before pushing:**

```bash
cd backend && pytest -q && ruff check . && mypy app
cd ../packages/parser-ts && node --test --experimental-strip-types tests/parser.test.ts
pnpm --filter @veganotes/frontend lint
pnpm --filter @veganotes/frontend tsc --noEmit
pnpm --filter @veganotes/frontend build
```

If any of those fail, CI will fail too — fix locally first.

---

## Stage 3 — Review

```bash
git push -u origin feat/<short-name>
gh pr create --fill --base main
```

Reviewer checklist:

- [ ] Tests cover the new behavior (happy path **and** at least one edge case).
- [ ] Parser fixtures updated in both languages if grammar changed.
- [ ] Docs updated (`docs/syntax.md`, `docs/api.md`, `docs/user-guide.md` as
      applicable).
- [ ] No new dependencies without a one-line justification in the PR.
- [ ] No secrets, no `secret.yaml`, no `.devdata/` committed.
- [ ] Manifests/Helm values updated if a new env var or port was added.

Squash-merge with a conventional title (`feat:`, `fix:`, `chore:`, `docs:`).

---

## Stage 4 — Continuous Integration

Triggered on every PR and on `main`:

| Job          | What it runs                                                      |
| ------------ | ----------------------------------------------------------------- |
| `backend`    | `pip install -e backend[dev]`, `ruff check`, `pytest -q`          |
| `parser-ts`  | `node --test --experimental-strip-types tests/parser.test.ts`     |
| `frontend`   | `pnpm install`, `pnpm lint`, `pnpm build`                         |

A merge to `main` is the implicit promotion to **main-built**: the next time
someone tags a release, that commit becomes a candidate.

---

## Stage 5 — Build the artifact

Releases are tag-driven. The `.github/workflows/docker.yml` workflow fires on
any `v*` tag and pushes to GHCR.

```bash
# From a clean main
git switch main && git pull --ff-only
git tag v0.2.0 -m "v0.2.0: stamp-ids button"
git push origin v0.2.0
```

What CI does:

```bash
docker build -f deploy/docker/backend.Dockerfile \
  -t ghcr.io/<org>/veganotes:0.2.0 \
  -t ghcr.io/<org>/veganotes:latest .
docker push ghcr.io/<org>/veganotes:0.2.0
docker push ghcr.io/<org>/veganotes:latest
```

Verify the artifact locally before promoting:

```bash
docker pull ghcr.io/<org>/veganotes:0.2.0
docker run --rm -p 8080:8000 -v $PWD/.smoke:/data ghcr.io/<org>/veganotes:0.2.0
curl -sf -u admin:admin http://localhost:8080/api/notes
```

---

## Stage 6 — Deploy to dev

Two paths; pick one per cluster and stick with it.

### A) Kustomize

```bash
kubectl config use-context dev
kubectl apply -k deploy/k8s/overlays/dev
kubectl -n veganotes set image deploy/veganotes veganotes=ghcr.io/<org>/veganotes:0.2.0
kubectl -n veganotes rollout status deploy/veganotes
```

### B) Helm

```bash
helm upgrade --install veganotes deploy/helm/veganotes \
  --namespace veganotes --create-namespace \
  --set image.repository=ghcr.io/<org>/veganotes \
  --set image.tag=0.2.0 \
  --set auth.passHash="$(python -c 'import bcrypt;print(bcrypt.hashpw(b"changeme",bcrypt.gensalt()).decode())')" \
  --set ingress.host=veganotes.dev.example.com
```

**Smoke checks (must all pass before promoting):**

```bash
kubectl -n veganotes get pods                              # 1/1 Running
curl -sf  https://veganotes.dev.example.com/healthz
curl -sf  https://veganotes.dev.example.com/readyz
curl -sfu admin:$DEVPASS https://veganotes.dev.example.com/api/notes | jq length
```

---

## Stage 7 — Deploy to production

Same workflow as dev, with the `prod` overlay or `prod-values.yaml`. Always:

1. **Snapshot** the PVC or run the `tar czf` backup (see
   command-reference §7).
2. Bump the image tag. Never use `:latest` in prod manifests — pin to the
   immutable version tag.
3. `kubectl rollout status` and the smoke checks. The pod restarts (Recreate
   strategy) — expect a short 503 window.
4. Announce in the team channel: tag, commit SHA, who deployed, what changed.

**Rollback:**

```bash
# Kustomize: pin the previous image
kubectl -n veganotes set image deploy/veganotes veganotes=ghcr.io/<org>/veganotes:0.1.9
# Helm: roll the release back
helm -n veganotes history  veganotes
helm -n veganotes rollback veganotes <REV>
```

The PVC is untouched by either path; your notes survive any roll-forward or
rollback.

---

## Stage 8 — Observe & operate

- **Health:** `/healthz` (liveness), `/readyz` (readiness). Both wired into
  the Deployment probes already.
- **Logs:** `kubectl -n veganotes logs -f deploy/veganotes`. Application
  logs are unstructured stdout today — ship via your cluster's standard
  `Fluent Bit` / `Loki` pipeline.
- **Backups:** at minimum nightly `tar czf` of `/data/notes`. The SQLite
  index is recoverable with `python scripts/reindex.py`.
- **Capacity:** SQLite + RWO PVC is single-pod by design. If you outgrow
  it, the migration path is Postgres + RWX storage; the indexer module is
  the only place schema bound to SQLite.

---

## Stage 9 — Retire / Decommission

```bash
# Final backup
kubectl -n veganotes exec deploy/veganotes -- tar czf - -C /data . \
  > veganotes-final-$(date +%F).tgz

# Tear down
helm -n veganotes uninstall veganotes        # or: kubectl delete -k deploy/k8s/overlays/prod
kubectl delete ns veganotes                  # PVC goes with the namespace
```

Keep the final tarball in your long-term store; the markdown is human
readable and fully self-contained.

---

## Appendix A — Environment variables

| Variable                          | Default       | Notes                                       |
| --------------------------------- | ------------- | ------------------------------------------- |
| `VEGANOTES_DATA_DIR`              | `/data`       | Root of `notes/`, `index.sqlite`, `git/`.   |
| `VEGANOTES_BASIC_AUTH_USER`       | `admin`       | HTTP Basic username. **Bootstrap-only**: on first boot this user is auto-created in the DB as an admin; subsequent password changes happen via the Admin tab and persist in `index.sqlite`. |
| `VEGANOTES_BASIC_AUTH_PASS_HASH`  | bcrypt(admin) | bcrypt hash used to seed the bootstrap admin's password. Generate with `passlib`/`bcrypt`. Not consulted after the admin row exists. |
| `VEGANOTES_SERVE_STATIC`          | `true`        | Set `false` in dev (Vite serves the SPA).   |

---

## Appendix B — Branch & tag policy

- Trunk-based on `main`. Short-lived feature branches (`feat/*`, `fix/*`).
- Squash-merge only.
- Releases use SemVer git tags `vMAJOR.MINOR.PATCH`. The `docker.yml`
  workflow turns the tag into the image tag.
- Hotfix path: branch from the release tag, fix, tag `vX.Y.Z+1`, deploy.

---

## Appendix C — A picture of the pipeline

```
   dev laptop                GitHub                  Container registry           Kubernetes
  ┌──────────┐    push     ┌────────┐    tag v*    ┌──────────────────┐  pull    ┌────────────┐
  │  feat/*  ├───────────► │   CI   ├─────────────►│ ghcr.io/.../X.Y.Z├─────────►│ Deployment │
  └──────────┘             └───┬────┘              └──────────────────┘          │ + PVC /data│
                               │ green                                            └─────┬──────┘
                               ▼                                                        │
                           merge to main                                          probes/healthz
```
