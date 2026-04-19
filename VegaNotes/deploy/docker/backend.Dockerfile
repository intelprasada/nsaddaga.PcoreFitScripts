# Single-pod image: build the frontend, then bake the dist into the
# Python image so FastAPI can serve it via StaticFiles.

FROM node:22-alpine AS frontend
WORKDIR /work
RUN corepack enable && corepack prepare pnpm@9 --activate
COPY pnpm-workspace.yaml turbo.json ./
COPY packages/ packages/
COPY frontend/ frontend/
RUN pnpm install --frozen-lockfile=false && pnpm --filter @veganotes/frontend build

FROM python:3.12-slim AS backend
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir .
COPY backend/app ./app
COPY --from=frontend /work/frontend/dist ./app/static

ENV VEGANOTES_DATA_DIR=/data \
    VEGANOTES_SERVE_STATIC=true
RUN mkdir -p /data/notes
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
