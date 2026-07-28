# Frontend container — Vite/React SPA built to static assets, served by nginx.
# No GPU/heavy deps. Multi-stage: Node builds; nginx serves the bundle and
# reverse-proxies /api and /ws to the `api` service so the browser only ever
# talks to one origin — mirrors frontend/vite.config.ts's dev proxy. This is
# required because frontend/src/api/ws.ts builds the WebSocket URL from
# window.location.host and cannot be pointed at a different origin without a
# frontend code change.

FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Baked in at build time (Vite env vars are compile-time, not runtime).
# Leave empty — REST calls stay same-origin-relative, matching the nginx
# proxy below and the WS URL construction in src/api/ws.ts. Only override
# this if the frontend and API are ever served from genuinely different
# origins (not the case in this compose topology).
ARG VITE_API_BASE=""
ENV VITE_API_BASE=${VITE_API_BASE}
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/frontend.nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
