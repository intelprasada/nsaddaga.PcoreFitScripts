# Command Reference

Every command you'll need, grouped by phase. Run from the repo root unless
otherwise noted.

> **Conventions**
> - `$REG` = your container registry (e.g. `ghcr.io/acme`).
> - `$VER` = release version (e.g. `0.2.0`).
> - `$NS`  = Kubernetes namespace (default: `veganotes`).
> - Lines starting with `#` are comments; everything else is a real command.

---

## 1. Setup (one-time)

```bash
# Tooling versions
python --version        # 3.11+
node --version          # 22+
pnpm --version          # 9.x  (corepack enable)
docker --version
kubectl version --client

# Clone & install
git clone <repo-url> veganotes && cd veganotes
python -m venv backend/.venv && source backend/.venv/bin/activate
pip install -e backend[dev]
pnpm install
```

---

## 2. Local development

```bash
# Both servers (backend :8000, frontend :5173 with /api proxy)
./scripts/dev.sh

# Backend only (notes dir overridable)
VEGANOTES_DATA_DIR=$PWD/.devdata \
  uvicorn --app-dir backend app.main:app --reload --port 8000

# Frontend only
pnpm --filter @veganotes/frontend dev

# Seed sample notes (idempotent)
python scripts/seed.py

# Rebuild SQLite index from notes on disk
python scripts/reindex.py

# Default credentials (change before deploy)
#   user: admin   password: admin
```

---

## 3. Build & test

```bash
# Backend tests + lint + types
cd backend
pytest -q
ruff check .
mypy app

# Shared parser tests (Python + TypeScript must agree)
cd ../packages/parser-ts
node --test --experimental-strip-types tests/parser.test.ts

# Frontend
pnpm --filter @veganotes/frontend lint
pnpm --filter @veganotes/frontend tsc --noEmit
pnpm --filter @veganotes/frontend build         # emits frontend/dist
```

---

## 4. Containers

```bash
# Build the all-in-one image (backend + built frontend)
docker build -f deploy/docker/backend.Dockerfile -t $REG/veganotes:$VER .

# Run locally
docker run --rm -p 8080:8000 \
  -e VEGANOTES_BASIC_AUTH_USER=admin \
  -e VEGANOTES_BASIC_AUTH_PASS_HASH="$(python -c 'import bcrypt;print(bcrypt.hashpw(b"changeme",bcrypt.gensalt()).decode())')" \
  -v $PWD/.devdata:/data \
  $REG/veganotes:$VER

# Compose (backend + nginx + named volume)
docker compose -f deploy/docker/docker-compose.yml up --build
docker compose -f deploy/docker/docker-compose.yml down

# Push
docker push $REG/veganotes:$VER
```

---

## 5. Kubernetes — Kustomize

```bash
# Generate auth Secret (do NOT commit secret.yaml)
python -c "import bcrypt;print(bcrypt.hashpw(b'changeme',bcrypt.gensalt()).decode())"
cp deploy/k8s/base/secret.example.yaml deploy/k8s/base/secret.yaml
$EDITOR deploy/k8s/base/secret.yaml

# Preview
kubectl kustomize deploy/k8s/overlays/dev

# Apply
kubectl apply -k deploy/k8s/overlays/dev          # dev cluster
kubectl apply -k deploy/k8s/overlays/prod         # prod cluster

# Update image without changing manifests
kubectl -n $NS set image deploy/veganotes veganotes=$REG/veganotes:$VER

# Status
kubectl -n $NS get pods,svc,ingress,pvc
kubectl -n $NS rollout status deploy/veganotes
kubectl -n $NS logs -f deploy/veganotes

# Tear down
kubectl delete -k deploy/k8s/overlays/dev
```

---

## 6. Kubernetes — Helm

```bash
# Render to stdout (dry run)
helm template veganotes deploy/helm/veganotes \
  --set image.repository=$REG/veganotes --set image.tag=$VER

# Install / upgrade
helm upgrade --install veganotes deploy/helm/veganotes \
  --namespace $NS --create-namespace \
  --set image.repository=$REG/veganotes --set image.tag=$VER \
  --set auth.passHash="$(python -c 'import bcrypt;print(bcrypt.hashpw(b"changeme",bcrypt.gensalt()).decode())')" \
  --set ingress.host=veganotes.example.com

# Roll back / uninstall
helm history  veganotes -n $NS
helm rollback veganotes 1 -n $NS
helm uninstall veganotes -n $NS
```

---

## 7. Operations

```bash
# Backup (notes are the source of truth — sqlite is rebuildable)
kubectl -n $NS exec deploy/veganotes -- tar czf - -C /data notes \
  > veganotes-notes-$(date +%F).tgz

# Restore
POD=$(kubectl -n $NS get pod -l app=veganotes -o jsonpath='{.items[0].metadata.name}')
kubectl -n $NS cp veganotes-notes-2026-04-19.tgz $POD:/tmp/r.tgz
kubectl -n $NS exec $POD -- tar xzf /tmp/r.tgz -C /data
kubectl -n $NS exec $POD -- python scripts/reindex.py

# Health
curl -sf http://<host>/healthz
curl -sf http://<host>/readyz
curl -su admin:admin http://<host>/api/notes | jq '.[0]'
```

---

## 8. API quick hits (`curl`)

```bash
BASE=http://localhost:8000/api
AUTH="-u admin:admin"

# List notes / read one
curl -s $AUTH $BASE/notes | jq
curl -s $AUTH $BASE/notes/1 | jq .body_md

# Save a note
curl -s $AUTH -X PUT $BASE/notes \
  -H 'content-type: application/json' \
  -d '{"path":"demo/hello.md","body_md":"# hi"}'

# Roll a weekly note forward
curl -s $AUTH -X POST $BASE/notes/next-week \
  -H 'content-type: application/json' \
  -d '{"path":"FIT Val Weekly/FIT weekly ww16.md"}'

# Stamp #id tokens into existing tasks (opt into dedup)
curl -s $AUTH -X POST $BASE/notes/stamp-ids \
  -H 'content-type: application/json' \
  -d '{"path":"FIT Val Weekly/FIT weekly ww16.md"}'

# Delete a note
curl -s $AUTH -X DELETE $BASE/notes/42
```

---

## 9. Troubleshooting

```bash
# Vite serves 0-byte module / "doesn't provide an export named ..."
rm -rf frontend/node_modules/.vite && pnpm --filter @veganotes/frontend dev

# Stale SQLite index (parsed structure looks wrong)
rm $VEGANOTES_DATA_DIR/index.sqlite && python scripts/reindex.py

# uvicorn ModuleNotFoundError: app
cd backend && uvicorn app.main:app ...
# OR: uvicorn --app-dir backend app.main:app ...

# K8s pod stuck pending
kubectl -n $NS describe pod -l app=veganotes
kubectl -n $NS get pvc                            # PVC must be Bound

# Image not picked up after push (same tag)
kubectl -n $NS rollout restart deploy/veganotes
```
