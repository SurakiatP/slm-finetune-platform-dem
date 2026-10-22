# Hashed bundles of the release being replaced. Browsers that still hold the
# old index.html request these; without them the page renders blank (404 on
# the JS). Pass the currently deployed image, e.g.
#   --build-arg PREVIOUS_IMAGE=sady567/slm-frontend:20261021.4
# The default keeps the build self-contained (an empty assets folder).
ARG PREVIOUS_IMAGE=nginxinc/nginx-unprivileged:1.27-alpine

FROM ${PREVIOUS_IMAGE} AS previous
USER root
RUN mkdir -p /usr/share/nginx/html/assets

FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

ARG VITE_ENGINE_HOST=""
ARG VITE_SUPABASE_PROJECT_ID
ARG VITE_SUPABASE_PUBLISHABLE_KEY
ARG VITE_SUPABASE_URL

ENV VITE_ENGINE_HOST=$VITE_ENGINE_HOST \
    VITE_SUPABASE_PROJECT_ID=$VITE_SUPABASE_PROJECT_ID \
    VITE_SUPABASE_PUBLISHABLE_KEY=$VITE_SUPABASE_PUBLISHABLE_KEY \
    VITE_SUPABASE_URL=$VITE_SUPABASE_URL

RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine

ENV ENGINE_UPSTREAM=slm-edge:80 \
    ENGINE_UPSTREAM_HOST=edge

COPY --from=previous /usr/share/nginx/html/assets /usr/share/nginx/html/assets
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 8080
