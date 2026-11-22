# Hashed bundles of the release being replaced. Browsers that still hold the
# old index.html request these; without them the page renders blank (404 on
# the JS). Pass the currently deployed image, e.g.
#   --build-arg PREVIOUS_IMAGE=sady567/slm-frontend:20261021.4
# The default keeps the build self-contained (an empty assets folder).
ARG PREVIOUS_IMAGE=nginxinc/nginx-unprivileged:1.27-alpine

FROM ${PREVIOUS_IMAGE} AS previous
USER root
RUN mkdir -p /usr/share/nginx/html/assets

FROM node:22-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

ARG VITE_ENGINE_HOST=""
ARG VITE_AUTH_URL=/auth
ARG VITE_AUTH_REALM=tunelab
ARG VITE_AUTH_CLIENT_ID=tunelab-web

ENV VITE_ENGINE_HOST=$VITE_ENGINE_HOST \
    VITE_AUTH_URL=$VITE_AUTH_URL \
    VITE_AUTH_REALM=$VITE_AUTH_REALM \
    VITE_AUTH_CLIENT_ID=$VITE_AUTH_CLIENT_ID

RUN npm run build

FROM nginxinc/nginx-unprivileged:1.30.5-alpine3.24

ENV ENGINE_UPSTREAM=slm-edge:80 \
    ENGINE_UPSTREAM_HOST=edge \
    AUTH_UPSTREAM=keycloak:8080

COPY --from=previous /usr/share/nginx/html/assets /usr/share/nginx/html/assets
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 8080
