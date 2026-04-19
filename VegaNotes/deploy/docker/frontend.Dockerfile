# Standalone frontend image (only used when running the frontend separately
# from the backend; single-pod mode bakes the build into backend.Dockerfile).
FROM node:22-alpine AS build
WORKDIR /work
RUN corepack enable && corepack prepare pnpm@9 --activate
COPY pnpm-workspace.yaml turbo.json ./
COPY packages/ packages/
COPY frontend/ frontend/
RUN pnpm install --frozen-lockfile=false && pnpm --filter @veganotes/frontend build

FROM nginx:alpine
COPY --from=build /work/frontend/dist /usr/share/nginx/html
COPY deploy/docker/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
