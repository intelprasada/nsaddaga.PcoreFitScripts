# Deployment

## Local — Docker Compose

```bash
cd deploy/docker
docker compose up --build
# open http://localhost:8080  (admin / admin)
```
Notes & SQLite live in the named volume `veganotes-data`.

## Kubernetes — Kustomize

```bash
# 1. Build & push your image
docker build -f deploy/docker/backend.Dockerfile -t YOUR_REG/veganotes:0.1.0 .
docker push YOUR_REG/veganotes:0.1.0

# 2. Generate a real auth Secret (NEVER commit the real one!)
python -c "import bcrypt; print(bcrypt.hashpw(b'YOURPASS', bcrypt.gensalt()).decode())"
cp deploy/k8s/base/secret.example.yaml deploy/k8s/base/secret.yaml
# edit secret.yaml with your hash, then add `secret.yaml` to .gitignore

# 3. Apply (dev overlay shown)
kubectl apply -k deploy/k8s/overlays/dev
```

## Kubernetes — Helm

```bash
helm install veganotes deploy/helm/veganotes \
  --namespace veganotes --create-namespace \
  --set image.repository=YOUR_REG/veganotes \
  --set image.tag=0.1.0 \
  --set auth.passHash="$(python -c 'import bcrypt;print(bcrypt.hashpw(b"YOURPASS",bcrypt.gensalt()).decode())')" \
  --set ingress.host=veganotes.example.com
```

## Persistence

Single PVC `veganotes-data` mounted at `/data`:
- `notes/`        — your markdown (the source of truth)
- `index.sqlite`  — derived; deletable & rebuildable via `python scripts/reindex.py`
- `git/`          — optional commit history (when git mode is enabled)
- `exports/`      — tarballs from `/api/export.tar.gz`

### Backup

```bash
kubectl exec -n veganotes deploy/veganotes -- tar czf - -C /data notes \
  > veganotes-notes-$(date +%F).tgz
```

### Restore

```bash
kubectl cp veganotes-notes-2026-04-19.tgz veganotes/POD:/tmp/r.tgz
kubectl exec -n veganotes POD -- tar xzf /tmp/r.tgz -C /data
kubectl exec -n veganotes POD -- python scripts/reindex.py
```

## Notes on scaling

SQLite + RWO PVC ⇒ **single replica** (`strategy: Recreate`). For a
multi-tenant deployment switch the index to Postgres and the storage to RWX.
