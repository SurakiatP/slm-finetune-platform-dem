#!/usr/bin/env bash
# =============================================================================
# scripts/deploy_pasaflow_vm.sh
#
# One-shot deploy of the SLM Fine-Tuning Platform onto the in-office permanent
# VM accessed via wetty (slmpc.pasaflow.com / pporkaew-3090).
#
# Differences from /vm-deployment skill (vast.ai):
#   - All persistent data goes to ~/drive (external 5 TB HDD, customer-mounted),
#     not docker's default /var/lib/docker/volumes
#   - Script is invoked through wetty (no SSH from outside) — runs end-to-end
#     in a single paste; key is requested via `read -s` to avoid chat history
#   - slmuser has no sudo; this script does NOT attempt nvidia-toolkit install
#     or apt operations (verified pre-installed on this host)
#
# Usage from wetty:
#   curl -fsSL https://raw.githubusercontent.com/SurakiatP/slm-finetune-platform-dem/dev/scripts/deploy_pasaflow_vm.sh -o /tmp/slm-deploy.sh
#   bash /tmp/slm-deploy.sh
#
# Optional env overrides:
#   SLM_BRANCH=dev                 (branch to deploy — default: dev)
#   SLM_OLLAMA_MODEL=llama3.2:3b   (24 GB VRAM default — use 1b for smoke)
#   SLM_SKIP_TESTS=1               (skip pytest at the end)
#   SLM_SKIP_OLLAMA_PULL=1         (skip base-model download)
# =============================================================================

set -euo pipefail

# --------- Configuration ----------------------------------------------------
DRIVE_DIR="${HOME}/drive"
DATA_DIR="${DRIVE_DIR}/slm-data"
REPO_DIR="${DRIVE_DIR}/slm-platform"
BRANCH="${SLM_BRANCH:-dev}"
OLLAMA_MODEL="${SLM_OLLAMA_MODEL:-llama3.2:3b}"
REPO_URL="https://github.com/SurakiatP/slm-finetune-platform-dem.git"
LOG="${DRIVE_DIR}/slm-deploy-$(date +%Y%m%d-%H%M%S).log"

# --------- Pretty-print helpers ---------------------------------------------
if [[ -t 1 ]]; then
  C_INFO='\033[1;36m'; C_OK='\033[1;32m'; C_WARN='\033[1;33m'; C_ERR='\033[1;31m'; C_END='\033[0m'
else
  C_INFO=''; C_OK=''; C_WARN=''; C_ERR=''; C_END=''
fi

say()  { printf "${C_INFO}▸ %s${C_END}\n" "$*" | tee -a "$LOG"; }
ok()   { printf "${C_OK}✓ %s${C_END}\n" "$*" | tee -a "$LOG"; }
warn() { printf "${C_WARN}⚠ %s${C_END}\n" "$*" | tee -a "$LOG"; }
fail() { printf "${C_ERR}✗ %s${C_END}\n" "$*" | tee -a "$LOG"; exit 1; }

mkdir -p "$DRIVE_DIR" 2>/dev/null || true
: > "$LOG"
chmod 600 "$LOG" || true
say "Logging to $LOG"
say "Branch: $BRANCH | Ollama base: $OLLAMA_MODEL"

# --------- Phase 0: Pre-flight ----------------------------------------------
say "==== Phase 0: Pre-flight ===="

# ~/drive exists + writable
[[ -d "$DRIVE_DIR" ]] || fail "$DRIVE_DIR does not exist — ask admin to mount external HDD"
if ! ( touch "$DRIVE_DIR/.slm-write-test" 2>/dev/null && rm -f "$DRIVE_DIR/.slm-write-test" ); then
  fail "$DRIVE_DIR not writable by $USER — ask admin to chown / chmod"
fi
ok "$DRIVE_DIR writable"

# Disk space on ~/drive
FREE_GB=$(df -BG --output=avail "$DRIVE_DIR" 2>/dev/null | tail -1 | tr -dc '0-9')
if [[ -z "${FREE_GB:-}" ]]; then
  warn "Cannot read free space on $DRIVE_DIR"
elif [[ "$FREE_GB" -lt 50 ]]; then
  warn "Only ${FREE_GB} GB free on $DRIVE_DIR (recommend ≥ 50 GB for models + artifacts)"
else
  ok "${FREE_GB} GB free on $DRIVE_DIR"
fi

# Docker permission
if ! docker ps >/dev/null 2>&1; then
  fail "Cannot run 'docker ps' — ask admin to run: sudo usermod -aG docker $USER (then logout/login)"
fi
ok "docker daemon reachable"

# Docker compose plugin
if ! docker compose version >/dev/null 2>&1; then
  fail "'docker compose' subcommand missing — only legacy docker-compose installed?"
fi
ok "$(docker compose version | head -1)"

# GPU visible to host
if ! nvidia-smi >/dev/null 2>&1; then
  fail "nvidia-smi not found — driver not installed?"
fi
GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
GPU_VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader | head -1)
ok "GPU: $GPU_NAME ($GPU_VRAM)"

# GPU visible to docker
if ! docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi >/dev/null 2>&1; then
  fail "Container cannot access GPU — NVIDIA Container Toolkit not wired into docker daemon"
fi
ok "GPU accessible from containers"

# openssl — used in Phase 4 to generate real credentials on first boot
# (openssl rand -hex 24), rather than shipping .env.example's minioadmin /
# slm:slm defaults onto a box that is about to sit behind a public
# Cloudflare hostname. Falls back to /dev/urandom + base64 if openssl is
# somehow absent, but openssl is expected on every Ubuntu box.
if ! command -v openssl >/dev/null 2>&1; then
  warn "openssl not found — will fall back to /dev/urandom for credential generation"
else
  ok "openssl available ($(openssl version))"
fi

# Network egress (already checked once; sanity-only here)
for host in github.com registry-1.docker.io openrouter.ai; do
  if ! curl -sf -o /dev/null --max-time 10 "https://$host/" 2>&1; then
    # Some return 401/404 for root — accept any 2xx-5xx HTTP response
    if ! curl -sI --max-time 10 "https://$host/" 2>&1 | grep -q "HTTP/"; then
      warn "Cannot reach https://$host — may slow build"
    fi
  fi
done
ok "egress to github/dockerhub/openrouter OK"

# --------- Phase 1: Setup data directories ----------------------------------
say "==== Phase 1: Data directories under $DATA_DIR ===="
for sub in postgres redis minio ollama hf-cache; do
  mkdir -p "$DATA_DIR/$sub"
done
ok "5 data subdirs ready (postgres, redis, minio, ollama, hf-cache)"

# --------- Phase 2: Clone or update repo ------------------------------------
say "==== Phase 2: Repo at $REPO_DIR ===="
if [[ -d "$REPO_DIR/.git" ]]; then
  say "Repo exists — fetching + checking out $BRANCH"
  cd "$REPO_DIR"
  git fetch origin --prune
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
else
  say "Cloning fresh ($BRANCH)..."
  git clone -b "$BRANCH" "$REPO_URL" "$REPO_DIR"
  cd "$REPO_DIR"
fi
HEAD_SHA=$(git rev-parse --short HEAD)
ok "repo at $REPO_DIR @ $HEAD_SHA ($BRANCH)"

# --------- Phase 3: docker-compose.override.yml -----------------------------
say "==== Phase 3: Write docker-compose.override.yml ===="
cat > "$REPO_DIR/docker-compose.override.yml" <<EOF
# Auto-generated by scripts/deploy_pasaflow_vm.sh
# DO NOT COMMIT — host-specific paths (~/drive on pporkaew-3090).
#
# Re-binds every named volume in the base compose to the external 5 TB HDD,
# so postgres / redis / minio / ollama / huggingface caches all live on
# $DATA_DIR rather than docker's default /var/lib/docker/volumes
# (which is on the root partition with limited free space).
volumes:
  postgres-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: $DATA_DIR/postgres
  redis-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: $DATA_DIR/redis
  minio-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: $DATA_DIR/minio
  ollama-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: $DATA_DIR/ollama
  hf-cache:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: $DATA_DIR/hf-cache
EOF
ok "override.yml written ($(wc -l < "$REPO_DIR/docker-compose.override.yml") lines)"

# --------- Phase 4: .env ----------------------------------------------------
say "==== Phase 4: .env ===="
ENV_FILE="$REPO_DIR/.env"
POSTGRES_DATA_DIR="$DATA_DIR/postgres"

# Postgres reads POSTGRES_PASSWORD ONLY when bootstrapping an EMPTY data
# directory (docker-entrypoint.sh in the postgres image; same story for
# MLFLOW_DB_USER/PASSWORD via docker/postgres-init.sql, which also only runs
# on first init). $POSTGRES_DATA_DIR being non-empty means Postgres already
# has a real password baked into its own catalogs — writing a different
# value into .env at that point changes the app's DSN but not the database,
# and every service that talks to Postgres starts failing to connect. MinIO
# has no such constraint: it reads MINIO_ROOT_USER/PASSWORD from the
# environment on every boot, so it is always safe to (re)generate.
POSTGRES_DATA_EXISTS=0
if [[ -d "$POSTGRES_DATA_DIR" ]] && [[ -n "$(ls -A "$POSTGRES_DATA_DIR" 2>/dev/null)" ]]; then
  POSTGRES_DATA_EXISTS=1
fi

# Real credential generator — openssl first, /dev/urandom fallback if it's
# somehow missing. Output is 1 line, no trailing newline issues for the sed
# substitutions below. NEVER echo the return value — callers must pipe it
# straight into a `sed -i` against $ENV_FILE, never to stdout or $LOG.
gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
  else
    head -c 32 /dev/urandom | base64 | tr -d '/+=\n'
  fi
}

# Hidden-input prompt for one .env var — factored out of the original
# OPENROUTER_API_KEY-only flow so SUPABASE_URL / MINIO_PUBLIC_URL /
# CLOUDFLARE_TUNNEL_ID get the exact same UX (TTY guard, `read -rsp`,
# 3 attempts, sed-escape-then-substitute) instead of three near-duplicate
# copies drifting apart over time. $ENV_FILE must already exist.
prompt_env_var() {
  local var_name="$1" validate_re="$2"
  if [[ ! -t 0 ]]; then
    fail "stdin is not a TTY — run this script via 'bash /tmp/slm-deploy.sh', NOT 'curl | bash'"
  fi
  local value="" attempt escaped
  for attempt in 1 2 3; do
    read -rsp "  ${var_name}: " value
    echo
    if [[ -z "$validate_re" ]] || [[ "$value" =~ $validate_re ]]; then
      break
    fi
    warn "${var_name} did not match the expected format (attempt $attempt/3)"
    value=""
  done
  [[ -n "$value" ]] || fail "no valid ${var_name} after 3 attempts"
  escaped=$(printf '%s\n' "$value" | sed -e 's/[\/&|]/\\&/g')
  if grep -q "^${var_name}=" "$ENV_FILE"; then
    sed -i "s|^${var_name}=.*|${var_name}=${escaped}|" "$ENV_FILE"
  else
    echo "${var_name}=${escaped}" >> "$ENV_FILE"
  fi
  unset value escaped
}

ENV_IS_NEW=0
if [[ ! -f "$ENV_FILE" ]]; then
  ENV_IS_NEW=1
  # umask before the file is created (not after) so there is no window
  # where it briefly exists world/group-readable; chmod below is
  # belt-and-suspenders for whatever umask the shell already had.
  umask 077
  cp "$REPO_DIR/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  ok ".env created from .env.example (mode 600)"
else
  chmod 600 "$ENV_FILE" || true
  ok ".env already exists — reusing (mode enforced to 600)"
fi

# --- MinIO: safe to (re)generate on every fresh .env, volume state doesn't matter
if [[ "$ENV_IS_NEW" -eq 1 ]]; then
  say "Generating real credentials into the new .env (never printed, never logged)"
  MINIO_PASS="$(gen_secret)"
  sed -i "s|^MINIO_ROOT_PASSWORD=.*|MINIO_ROOT_PASSWORD=${MINIO_PASS}|" "$ENV_FILE"
  unset MINIO_PASS
  ok "MINIO_ROOT_PASSWORD generated — MinIO re-reads this from the environment on every boot"

  # --- Postgres (+ mlflow's own role): only safe on a genuinely empty volume
  if [[ "$POSTGRES_DATA_EXISTS" -eq 0 ]]; then
    PG_PASS="$(gen_secret)"
    MLFLOW_DB_PASS="$(gen_secret)"
    sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PG_PASS}|" "$ENV_FILE"
    if grep -q "^MLFLOW_DB_PASSWORD=" "$ENV_FILE"; then
      sed -i "s|^MLFLOW_DB_PASSWORD=.*|MLFLOW_DB_PASSWORD=${MLFLOW_DB_PASS}|" "$ENV_FILE"
    else
      # Not in .env.example (compose falls back to mlflow/mlflow) — append
      # so the pasaflow deploy still gets a real value without touching
      # .env.example itself.
      {
        echo "MLFLOW_DB_USER=mlflow"
        echo "MLFLOW_DB_PASSWORD=${MLFLOW_DB_PASS}"
      } >> "$ENV_FILE"
    fi
    unset PG_PASS MLFLOW_DB_PASS
    ok "POSTGRES_PASSWORD + MLFLOW_DB_PASSWORD generated for the empty $POSTGRES_DATA_DIR"
  else
    warn "$POSTGRES_DATA_DIR already has data but .env was just (re)created — POSTGRES_PASSWORD / MLFLOW_DB_PASSWORD were left at the .env.example default, which almost certainly does NOT match what Postgres was actually initialised with."
    warn "Recover the real password, or rotate it properly via docs/runbooks/secret_rotation.md (ALTER USER first, THEN .env), before this box is exposed to the internet."
  fi
elif [[ "$POSTGRES_DATA_EXISTS" -eq 1 ]]; then
  say "$POSTGRES_DATA_DIR has data and .env already exists — POSTGRES_PASSWORD left untouched (see docs/runbooks/secret_rotation.md to rotate it properly)"
fi

# --- OpenRouter API key (unchanged flow, now against a .env that always exists) ---
if grep -q "^OPENROUTER_API_KEY=sk-or-v1-" "$ENV_FILE" 2>/dev/null; then
  ok ".env already has an OPENROUTER_API_KEY — keeping existing"
else
  echo ""
  echo "OpenRouter API key required for SDG + LLM judge."
  echo "Paste it now — input is hidden, NOT logged, NOT echoed:"
  prompt_env_var "OPENROUTER_API_KEY" '^sk-or-v1-[A-Za-z0-9_-]{20,}$'
  ok "OPENROUTER_API_KEY set (hidden)"
fi

# --- Supabase URL, MinIO public URL, Cloudflare tunnel ID — same hidden-prompt pattern ---
if grep -qE "^SUPABASE_URL=.+" "$ENV_FILE" 2>/dev/null; then
  ok ".env already has a SUPABASE_URL — keeping existing"
else
  echo ""
  echo "Supabase project URL (e.g. https://<ref>.supabase.co) — used for JWKS verification once AUTH_REQUIRED is on."
  echo "Paste it now — input is hidden, NOT logged, NOT echoed:"
  prompt_env_var "SUPABASE_URL" '^https?://.+'
  ok "SUPABASE_URL set (hidden)"
fi

if grep -qE "^MINIO_PUBLIC_URL=.+" "$ENV_FILE" 2>/dev/null; then
  ok ".env already has a MINIO_PUBLIC_URL — keeping existing"
else
  echo ""
  echo "Public storage subdomain for presigned downloads (e.g. https://storage.slmpc.pasaflow.com — no path, no explicit default port)."
  echo "Paste it now — input is hidden, NOT logged, NOT echoed:"
  prompt_env_var "MINIO_PUBLIC_URL" '^https?://[^/]+$'
  ok "MINIO_PUBLIC_URL set (hidden)"
fi

if grep -qE "^CLOUDFLARE_TUNNEL_ID=.+" "$ENV_FILE" 2>/dev/null; then
  ok ".env already has a CLOUDFLARE_TUNNEL_ID — keeping existing"
else
  echo ""
  echo "Cloudflare Tunnel UUID (from 'cloudflared tunnel create', see docker/cloudflared/config.yml)."
  echo "Paste it now — input is hidden, NOT logged, NOT echoed:"
  prompt_env_var "CLOUDFLARE_TUNNEL_ID" '^[A-Za-z0-9-]{8,}$'
  ok "CLOUDFLARE_TUNNEL_ID set (hidden)"
fi

# --------- Phase 4.5: production credential guard ---------------------------
# Mirrors api/core/config.py's `_reject_unsafe_production_config` so a
# misconfigured ENVIRONMENT=production fails HERE, before any container
# starts, instead of the operator's only signal being an `api` container
# crash-looping at boot with this same error. This is a pre-flight
# convenience, not a replacement for the app's own guard — the app checks
# again at boot regardless.
say "==== Phase 4.5: Production credential guard ===="
env_val() {
  grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r'
}

DEPLOY_ENVIRONMENT="$(env_val ENVIRONMENT)"
if [[ "$DEPLOY_ENVIRONMENT" == "production" ]]; then
  PG_USER_VAL="$(env_val POSTGRES_USER)"; PG_USER_VAL="${PG_USER_VAL:-slm}"
  PG_PASSWORD_VAL="$(env_val POSTGRES_PASSWORD)"; PG_PASSWORD_VAL="${PG_PASSWORD_VAL:-slm}"
  MINIO_USER_VAL="$(env_val MINIO_ROOT_USER)"; MINIO_USER_VAL="${MINIO_USER_VAL:-minioadmin}"
  MINIO_PASSWORD_VAL="$(env_val MINIO_ROOT_PASSWORD)"; MINIO_PASSWORD_VAL="${MINIO_PASSWORD_VAL:-minioadmin}"
  AUTH_REQUIRED_VAL="$(env_val AUTH_REQUIRED)"; AUTH_REQUIRED_VAL="${AUTH_REQUIRED_VAL:-false}"
  SUPABASE_URL_VAL="$(env_val SUPABASE_URL)"
  SUPABASE_JWT_SECRET_VAL="$(env_val SUPABASE_JWT_SECRET)"

  OFFENDERS=()
  [[ "$MINIO_USER_VAL" == "minioadmin" ]] && OFFENDERS+=("MINIO_ACCESS_KEY")
  [[ "$MINIO_PASSWORD_VAL" == "minioadmin" ]] && OFFENDERS+=("MINIO_SECRET_KEY")
  if [[ "$PG_USER_VAL" == "slm" && "$PG_PASSWORD_VAL" == "slm" ]]; then
    OFFENDERS+=("DATABASE_URL (still carries the example slm:slm credentials)")
  fi
  if [[ "$AUTH_REQUIRED_VAL" == "false" ]]; then
    OFFENDERS+=("AUTH_REQUIRED=false — every request would be anonymous and every row it creates would have owner_id NULL. Back-fill existing rows with scripts/backfill_project_owner.py, ship the frontend Authorization header, then set AUTH_REQUIRED=true.")
  fi
  if [[ "$AUTH_REQUIRED_VAL" == "true" && -z "$SUPABASE_URL_VAL" && -z "$SUPABASE_JWT_SECRET_VAL" ]]; then
    OFFENDERS+=("AUTH_REQUIRED=true with neither SUPABASE_URL nor SUPABASE_JWT_SECRET set — there is no JWKS and no HS256 fallback to verify tokens against, so every authenticated request would be rejected.")
  fi

  if [[ "${#OFFENDERS[@]}" -gt 0 ]]; then
    JOINED=$(IFS=', '; echo "${OFFENDERS[*]}")
    fail "ENVIRONMENT=production but these still hold the values shipped in .env.example: ${JOINED}. Set real secrets, or use ENVIRONMENT=dev/staging if this is not a production deployment."
  fi
  unset PG_USER_VAL PG_PASSWORD_VAL MINIO_USER_VAL MINIO_PASSWORD_VAL AUTH_REQUIRED_VAL SUPABASE_URL_VAL SUPABASE_JWT_SECRET_VAL OFFENDERS JOINED
  ok "production credential guard passed"
else
  ok "ENVIRONMENT=${DEPLOY_ENVIRONMENT:-dev} — production credential guard not applicable (this script never sets ENVIRONMENT=production itself; see docs/runbooks/auth_cutover.md for that step)"
fi

# --------- Phase 4.6: tunnel prerequisites ----------------------------------
# cloudflared is the ONLY ingress -- api and edge both bind 127.0.0.1 -- so a
# deploy without a tunnel id produces a stack that is up, healthy, and
# unreachable from anywhere. That must fail here, loudly, rather than at the
# end of a 3-minute wait loop.
#
# This check lives in the deploy script and NOT as `${VAR:?}` in
# docker-compose.yml on purpose. Compose interpolates the whole file before
# it selects services or profiles, and treats `:?` as an error when the
# variable is unset *or empty* -- and .env.example ships it empty. Putting it
# there broke `docker compose up/build/config/ps` for anyone following the
# README quickstart. Requiring it belongs where a deployment happens.
say "==== Phase 4.6: Tunnel prerequisites ===="
TUNNEL_ID_VAL=$(grep -E '^CLOUDFLARE_TUNNEL_ID=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)
if [[ -z "$TUNNEL_ID_VAL" ]]; then
  fail "CLOUDFLARE_TUNNEL_ID is empty in ${ENV_FILE}. cloudflared is the only ingress (api and edge both bind 127.0.0.1), so without it the stack comes up completely unreachable. Create a tunnel with 'cloudflared tunnel create', put its UUID here, and place the credentials JSON in CLOUDFLARE_TUNNEL_CREDS_DIR."
fi
CREDS_DIR_VAL=$(grep -E '^CLOUDFLARE_TUNNEL_CREDS_DIR=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)
CREDS_DIR_VAL="${CREDS_DIR_VAL:-./.cloudflared}"
if [[ ! -f "${CREDS_DIR_VAL}/creds.json" ]]; then
  warn "no creds.json under ${CREDS_DIR_VAL} — cloudflared will start and immediately fail to authenticate. docker/cloudflared/config.yml expects it at /etc/cloudflared/creds.json inside the container."
fi
ok "tunnel id present"
unset TUNNEL_ID_VAL CREDS_DIR_VAL

# --------- Phase 5: docker compose pull + up --------------------------------
# Images are pre-built in CI and pushed to GHCR (see
# .github/workflows/build-images.yml). We PULL, never build: a from-scratch
# `docker compose build` on this 15 GB / 12-core box spikes memory during the
# worker image's `cmake -j` llama.cpp compile + torch/unsloth install and
# reboots the VM (observed: 3 reboots in 34 min). Pulling is download-only —
# no compile, no memory spike.
say "==== Phase 5: Pull images + start stack ===="
warn "First pull is ~6-10 GB over this network (HF ~11 MB/s) — expect ~15 min."
warn "Subsequent pulls fetch only changed layers and are fast."
warn "Safe to detach if running inside tmux/screen. Re-attach to monitor."
# Public GHCR packages — no `docker login` needed. postgres/redis/minio/ollama
# pull from Docker Hub as before; api/worker/mlflow pull from GHCR.
# `--profile tunnel` on every compose invocation from here on: cloudflared
# is opt-in so that a plain `docker compose up` (local dev, no tunnel
# credentials) does not crash-loop it. A deploy is exactly the case that
# wants it, so the profile is enabled here rather than in the compose file.
docker compose --profile tunnel pull 2>&1 | tee -a "$LOG"
ok "images pulled"

docker compose --profile tunnel up -d 2>&1 | tee -a "$LOG"

# Wait for all 10 long-running services: postgres, redis, minio, mlflow, api,
# edge, cloudflared, worker, worker-cpu, ollama. (`minio-init` is an 11th
# compose service but is a one-shot job — `depends_on: service_completed_
# successfully` — that exits after seeding buckets, so it never shows
# "running" and is deliberately not counted here.) This was 7 before the
# round-3 edge/cloudflared/worker-cpu split; counting only 7 now would
# report success while 3 real services are still starting.
say "Waiting for stack to be healthy (up to 3 min)..."
for i in $(seq 1 36); do
  STATE=$(docker compose --profile tunnel ps --format json 2>/dev/null || true)
  UP=$(echo "$STATE" | grep -c '"State":"running"' || true)
  if [[ "$UP" -ge 10 ]]; then
    ok "$UP/10 containers running"
    break
  fi
  sleep 5
done

docker compose --profile tunnel ps | tee -a "$LOG"

# --------- Phase 6: Migrations + ollama pull --------------------------------
say "==== Phase 6: Migrations ===="
docker compose exec -T api alembic upgrade head 2>&1 | tee -a "$LOG"
ok "migrations applied"

if [[ "${SLM_SKIP_OLLAMA_PULL:-0}" != "1" ]]; then
  say "==== Phase 6.5: Pull Ollama base model: $OLLAMA_MODEL ===="
  docker compose exec -T ollama ollama pull "$OLLAMA_MODEL" 2>&1 | tee -a "$LOG"
  ok "$OLLAMA_MODEL pulled"
else
  warn "SLM_SKIP_OLLAMA_PULL=1 — skipping ollama pull"
fi

# --------- Phase 7: Sanity --------------------------------------------------
say "==== Phase 7: Sanity ===="
# Round 3 made `edge` the sole ingress and moved `api` to a loopback-only
# port with nothing routed to it directly (see docker-compose.yml's `api`
# ports comment and tests/unit/test_compose_port_exposure.py). `edge`
# proxies /health and /api/v1/... straight through (docker/edge.nginx.conf),
# so the sanity checks below must hit EDGE_PORT, not the host-side API_PORT
# — the container's own uvicorn always listens on :8000 *inside* the
# container regardless of what API_PORT maps it to on the host, which is
# why the Phase 8 banner's `docker compose exec api curl ...` recipe can
# hardcode :8000 while this section reads EDGE_PORT back from .env.
EDGE_PORT=$(grep -E '^EDGE_PORT=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '[:space:]')
EDGE_PORT="${EDGE_PORT:-8088}"
say "Checking API through edge on host port $EDGE_PORT"

HEALTH=$(curl -fs "http://localhost:${EDGE_PORT}/health" || echo "FAIL")
[[ "$HEALTH" =~ ok ]] || fail "/health returned: $HEALTH"
ok "/health -> $HEALTH"

PROJ=$(curl -fs "http://localhost:${EDGE_PORT}/api/v1/projects?limit=5" || echo "FAIL")
echo "$PROJ" | grep -q '"items"' || fail "/projects returned unexpected: $PROJ"
ok "/projects -> empty list as expected"

if [[ "${SLM_SKIP_TESTS:-0}" != "1" ]]; then
  say "Installing [dev] extras for pytest..."
  docker compose exec -T api pip install -q -e '.[dev]' 2>&1 | tee -a "$LOG" || warn "pip install [dev] had issues"
  say "Running unit tests (target: 302 passed, 3 skipped)..."
  docker compose exec -T api python -m pytest tests/unit/ -q 2>&1 | tee -a "$LOG" | tail -10
else
  warn "SLM_SKIP_TESTS=1 — skipping pytest"
fi

# --------- Phase 8: Summary -------------------------------------------------
say "==== DONE ===="
# As of round 3, every service binds 127.0.0.1 (or publishes no port at
# all — cloudflared has none, by design) and `edge` is the only thing an
# external caller can reach, via the two Cloudflare hostnames in
# docker/cloudflared/config.yml. Port 22 is filtered on this host and there
# is no SSH-tunnel path from a laptop any more, so the old
# `ssh -L 9001:localhost:9001 ...` advice below is dead: the only way to
# reach a loopback-bound service now is from *inside* the VM, e.g. via
# `docker compose exec`, or from outside via the Cloudflare hostname for
# whatever `edge` actually proxies (API + SPA; MLflow/MinIO-console/Ollama
# are not proxied and are wetty/`docker compose exec`-only).
cat <<EOF | tee -a "$LOG"

✅ SLM platform deployed on pporkaew-3090

  Repo:        $REPO_DIR @ $HEAD_SHA ($BRANCH)
  Data:        $DATA_DIR (postgres, redis, minio, ollama, hf-cache)
  GPU:         $GPU_NAME ($GPU_VRAM)
  Ollama base: $OLLAMA_MODEL

  Public ingress (Cloudflare Tunnel -> edge -> api, nothing else exposed):
    API + SPA -> https://slmpc.pasaflow.com
    Storage   -> https://storage.slmpc.pasaflow.com  (MinIO presigned GETs only)

  Everything else binds 127.0.0.1 with no external route at all. There is
  no SSH tunnel path from a laptop any more (port 22 is filtered) — reach
  these from inside the VM (wetty) with docker compose exec, e.g.:
    docker compose exec postgres psql -U \${POSTGRES_USER:-slm} -d \${POSTGRES_DB:-slm}
    docker compose exec redis redis-cli
    docker compose exec api curl -fs http://localhost:8000/health
    curl -fs http://localhost:${EDGE_PORT}/health          # via edge, same as Phase 7
    curl -fs http://localhost:9001/                        # MinIO console, loopback-only
    curl -fs http://localhost:5000/health                  # MLflow, loopback-only
    curl -fs http://localhost:11434/api/version             # Ollama, loopback-only

  Log:         $LOG

Next:
  - This script does NOT set ENVIRONMENT=production (and never will) —
    promotion is a manual step after the null-owner backfill
    (scripts/backfill_project_owner.py) and once the frontend ships its
    Authorization header. See docs/runbooks/auth_cutover.md for the
    sequence, and docs/runbooks/secret_rotation.md before rotating anything
    on this box.
  - Paste the "tail -20 $LOG" output back to Claude for triage if anything looked off
  - If everything green → run 11-node E2E from docs/guidebook-e2e/test-e2e-on-vm.html
  - To restart stack later:   cd $REPO_DIR && docker compose --profile tunnel up -d
    (the --profile flag is REQUIRED: without it cloudflared is skipped and the
     box comes up healthy but unreachable — it is the only ingress)
  - To stop:                   cd $REPO_DIR && docker compose --profile tunnel stop
EOF
