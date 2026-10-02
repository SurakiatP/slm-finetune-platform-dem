FROM node:20-alpine AS builder

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

FROM nginxinc/nginx-unprivileged:1.27-alpine

ENV ENGINE_UPSTREAM=slm-edge:80 \
    ENGINE_UPSTREAM_HOST=edge \
    AUTH_UPSTREAM=keycloak:8080

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 8080
