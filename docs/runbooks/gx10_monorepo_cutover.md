# gx10 cutover: run the frontend from this repo's `frontend/`

Branch `release/gx10-monorepo` (= PR #22 + this runbook + the gx10 compose
overlay). Goal: one checkout, `~/slm-finetune-platform-dem`, runs the whole
system. Nothing about ports, volumes, networks, URLs or users changes.

**Do not run any of this until the frontend team has reviewed the branch.**

## What is live today (inspected read-only 2026-11-25)

gx10 is a shared box; dozens of non-SLM containers (agenta, govagent,
thoughtmind, WeKnora, hiclaw, ...) run there. Touch only what is listed here.

| Container | Compose project | Started from | Notes |
|---|---|---|---|
| `slm-api`, `slm-worker`, `slm-edge`, ... (12) | `slm-platform` | `~/slm-finetune-platform-dem` (`dev` 794beaa) | unchanged by this cutover except `git checkout` |
| `slm-frontend` | `tunelab-main` | `~/slm-frontend-src/5447090` (no git, copied tree) | image `slm-frontend:uat1-5447090`, `127.0.0.1:8082->8080` |
| `tunelab-main-keycloak-1` | `tunelab-main` | `~/slm-frontend-src/724e550` | realm/theme bind-mounted **from that folder** |
| `tunelab-main-postgres-1` | `tunelab-main` | `~/slm-frontend-src/ed71e76` | **all Keycloak users** in volume `tunelab-main_auth-postgres` |
| `slm-ngrok` | none (`docker run`) | | forwards to `slm-frontend:8080` on `slm-platform_slm-net` |

All three `tunelab-main` containers use env file `~/.tunelab-main-auth.env`
(server secrets; never commit or print it) and overlays
`docker-compose.production.yml` + `docker-compose.gx10-live.yml`.

Source check: `~/slm-frontend-src/5447090` vs this branch's `frontend/` is
**identical** except (a) `.gitignore` here also ignores `.claude/`, and
(b) `docker-compose.gx10-live.yml`, which only existed on gx10 and is now
committed. So the cutover redeploys the same UI code from a new path.

Verified locally on this branch: `docker compose ... config` resolves to
project `tunelab-main`, container `slm-frontend`, alias `slm-frontend`,
networks `tunelab-main-auth` + `slm-platform_slm-net`, volume
`tunelab-main_auth-postgres` (same as live). `docker build` of
`frontend/` succeeds and the image serves `/` with 200.

## Cutover (≈5 min, frontend down ~10 s)

```bash
ssh gx10
cd ~/slm-finetune-platform-dem

# 0. Safety: backend state + current frontend image tag
git status -sb            # expect only host-local untracked files
docker inspect slm-frontend --format '{{.Config.Image}}'   # note it for rollback

# 1. Get the branch. Backend containers are not restarted by this.
git fetch origin
git checkout release/gx10-monorepo   # later: dev, once PR #22 merges
git log --oneline -1

# 2. Build the frontend image from the repo (ARM64 native on gx10).
#    PREVIOUS_IMAGE keeps the old hashed bundles so open tabs don't blank.
SHA=$(git rev-parse --short HEAD)
docker build -t slm-frontend:mono-$SHA \
  --build-arg PREVIOUS_IMAGE=$(docker inspect slm-frontend --format '{{.Config.Image}}') \
  frontend/

# 3. Recreate ONLY the frontend container from frontend/ (--no-deps: Keycloak
#    and its Postgres are NOT touched).
cd frontend
FRONTEND_IMAGE=slm-frontend:mono-$SHA docker compose \
  --env-file ~/.tunelab-main-auth.env \
  -f docker-compose.production.yml -f docker-compose.gx10-live.yml \
  up -d --no-deps frontend
```

`FRONTEND_IMAGE=` on the command line overrides the value in the env file
(shell env wins over `--env-file`). Optionally update `FRONTEND_IMAGE` in
`~/.tunelab-main-auth.env` afterwards so the next `up` doesn't revert it.

## Verify

```bash
docker ps --filter name=slm-frontend --format '{{.Image}} {{.Status}}'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8082/          # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8082/ready     # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8082/auth/realms/tunelab   # 200
curl -s -o /dev/null -w '%{http_code}\n' https://life-crawling-giver.ngrok-free.dev/ready   # 200
```

Then in a browser: log in through Keycloak, open Projects, open one
training's live log (WebSocket), call `/inference` with an `sk-slm-` key.
ngrok needs no change: it targets the `slm-frontend` alias, which the new
container keeps.

## Rollback (≈10 s)

```bash
cd ~/slm-frontend-src/5447090
FRONTEND_IMAGE=slm-frontend:uat1-5447090 docker compose \
  --env-file ~/.tunelab-main-auth.env \
  -f docker-compose.production.yml -f docker-compose.gx10-live.yml \
  up -d --no-deps frontend
cd ~/slm-finetune-platform-dem && git checkout dev
```

## Later, optional: move Keycloak + its Postgres onto the repo path

Today Keycloak bind-mounts its realm/theme from `~/slm-frontend-src/724e550`.
**Do not delete `~/slm-frontend-src/*`** until this step is done, or the
next Keycloak restart fails. To move it (Keycloak down ~30 s; users and
sessions survive because they live in the `tunelab-main_auth-postgres`
volume, and `--import-realm` skips a realm that already exists):

```bash
cd ~/slm-finetune-platform-dem/frontend
docker compose --env-file ~/.tunelab-main-auth.env \
  -f docker-compose.production.yml -f docker-compose.gx10-live.yml \
  up -d --no-deps keycloak postgres
```

Take a `pg_dump` of `tunelab_auth` first (see `backup_restore.md`). After
it verifies, the `~/slm-frontend-src/` copies can be archived.

## Rules after the cutover

- Never pass `-v` to `docker compose down` in `frontend/`; it deletes
  every Keycloak user.
- Never change `name: tunelab-main` in the frontend compose files; a new
  project name means a fresh, empty Keycloak database.
- Frontend changes now go through PRs on this repo touching `frontend/**`,
  reviewed by the frontend team.
