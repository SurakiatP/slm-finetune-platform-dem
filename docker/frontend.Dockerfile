# Frontend BUILD container — Vite/React SPA build-only image. No GPU/heavy
# deps. This used to be a second stage that also served the bundle with its
# own nginx (docker/frontend.nginx.conf), reverse-proxying /api and /ws to
# `api` so the browser only ever talked to one origin. Round 3 replaced that
# model repo-wide: a single `edge` nginx (docker/edge.nginx.conf) is now the
# sole HTTP surface, serving whatever static root `SPA_DIST_DIR` points at
# and proxying /api/ + /ws/ itself — see ADR-011. Two nginxes answering the
# same paths was a maintenance trap, not a redundancy worth keeping, so
# docker/frontend.nginx.conf was deleted and this file no longer runs one.
#
# `smart-model-tune` is the canonical frontend now (see ADR-011's note);
# `frontend/` here is retained only as a backup and is not wired to any
# compose service that starts by default — see the `frontend-build` service
# in docker-compose.yml, gated behind the `build-spa` profile.
#
# Output contract: the final stage holds the built assets at `/dist` and
# copies them to `/out` on `docker run`/`docker compose run`. It does NOT
# serve anything — nothing listens on any port, so this image needs no
# entry in tests/unit/test_compose_port_exposure.py's port-exposure sets
# beyond confirming it publishes none.

FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Baked in at build time (Vite env vars are compile-time, not runtime).
# Leave empty — REST calls stay same-origin-relative, matching `edge`'s /api/
# proxy and the WS URL construction in frontend/src/api/ws.ts (which derives
# from window.location.host). Only override this if the SPA and API are ever
# served from genuinely different origins (not the case in this topology).
ARG VITE_API_BASE=""
ENV VITE_API_BASE=${VITE_API_BASE}
# Supabase auth (compile-time, like every Vite var). Both empty (default)
# builds the auth-less bundle for an AUTH_REQUIRED=false backend. Set BOTH
# to build a bundle that gates behind Supabase login and sends the session
# JWT on /api (Authorization header) and /ws (["bearer", <jwt>]
# subprotocol) — required against a backend running AUTH_REQUIRED=true.
# The publishable (anon) key is public by design; never pass service_role
# or the JWT secret here.
ARG VITE_SUPABASE_URL=""
ARG VITE_SUPABASE_PUBLISHABLE_KEY=""
ENV VITE_SUPABASE_URL=${VITE_SUPABASE_URL} \
    VITE_SUPABASE_PUBLISHABLE_KEY=${VITE_SUPABASE_PUBLISHABLE_KEY}
RUN npm run build

# Emit-only stage — no web server. `docker compose --profile build-spa run
# --rm frontend-build` bind-mounts a host directory at /out; the CMD below
# copies the built dist/ into it so `edge` (pointed at that same host
# directory via SPA_DIST_DIR) can serve it. A plain `docker compose up`
# never starts this service at all (see the `build-spa` profile).
FROM busybox:1.36 AS export
COPY --from=build /app/dist /dist
CMD ["sh", "-c", "cp -r /dist/. /out/ && echo 'frontend/ dist emitted to /out'"]
