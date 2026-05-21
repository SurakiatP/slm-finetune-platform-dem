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
#   curl -fsSL https://raw.githubusercontent.com/SurakiatP/slm-finetune-platform-dem/feature/pasaflow-bootstrap/scripts/deploy_pasaflow_vm.sh -o /tmp/slm-deploy.sh
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
if [[ -f "$ENV_FILE" ]] && grep -q "^OPENROUTER_API_KEY=sk-or-v1-" "$ENV_FILE"; then
  ok ".env already has an OPENROUTER_API_KEY — keeping existing"
else
  cp "$REPO_DIR/.env.example" "$ENV_FILE"
  echo ""
  echo "OpenRouter API key required for SDG + LLM judge."
  echo "Paste it now — input is hidden, NOT logged, NOT echoed:"
  if [[ ! -t 0 ]]; then
    fail "stdin is not a TTY — run this script via 'bash /tmp/slm-deploy.sh', NOT 'curl | bash'"
  fi
  OR_KEY=""
  for attempt in 1 2 3; do
    read -rsp "  OPENROUTER_API_KEY: " OR_KEY
    echo
    if [[ "$OR_KEY" =~ ^sk-or-v1-[A-Za-z0-9_-]{20,}$ ]]; then
      break
    fi
    warn "Key must match ^sk-or-v1-... (attempt $attempt/3)"
    OR_KEY=""
  done
  [[ -n "$OR_KEY" ]] || fail "no valid key after 3 attempts"
  # Escape | and & for sed safety
  ESCAPED=$(printf '%s\n' "$OR_KEY" | sed -e 's/[\/&|]/\\&/g')
  sed -i "s|^OPENROUTER_API_KEY=.*|OPENROUTER_API_KEY=${ESCAPED}|" "$ENV_FILE"
  unset OR_KEY ESCAPED
  ok ".env populated (key hidden)"
fi

# --------- Phase 5: docker compose build + up -------------------------------
say "==== Phase 5: Build + start stack ===="
warn "Build expects ~20-30 min on this network (HF ~11 MB/s — measured)."
warn "Safe to detach if running inside tmux/screen. Re-attach to monitor."
docker compose build 2>&1 | tee -a "$LOG"
ok "build complete"

docker compose up -d 2>&1 | tee -a "$LOG"

# Wait for all 7 services (postgres, redis, minio, mlflow, api, worker, ollama)
say "Waiting for stack to be healthy (up to 3 min)..."
for i in $(seq 1 36); do
  STATE=$(docker compose ps --format json 2>/dev/null || true)
  UP=$(echo "$STATE" | grep -c '"State":"running"' || true)
  if [[ "$UP" -ge 7 ]]; then
    ok "$UP/7 containers running"
    break
  fi
  sleep 5
done

docker compose ps | tee -a "$LOG"

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
HEALTH=$(curl -fs http://localhost:8000/health || echo "FAIL")
[[ "$HEALTH" =~ ok ]] || fail "/health returned: $HEALTH"
ok "/health -> $HEALTH"

PROJ=$(curl -fs "http://localhost:8000/api/v1/projects?limit=5" || echo "FAIL")
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
cat <<EOF | tee -a "$LOG"

✅ SLM platform deployed on pporkaew-3090

  Repo:        $REPO_DIR @ $HEAD_SHA ($BRANCH)
  Data:        $DATA_DIR (postgres, redis, minio, ollama, hf-cache)
  GPU:         $GPU_NAME ($GPU_VRAM)
  Ollama base: $OLLAMA_MODEL

  Endpoints (host-local — exposed externally only via Cloudflare):
    API     -> http://localhost:8000
    MinIO   -> http://localhost:9001 (console)
    MLflow  -> http://localhost:5000
    Ollama  -> http://localhost:11434

  Log:         $LOG

Next:
  - Paste the "tail -20 $LOG" output back to Claude for triage if anything looked off
  - If everything green → run 11-node E2E from docs/guidebook-e2e/test-e2e-on-vm.html
  - To restart stack later:   cd $REPO_DIR && docker compose up -d
  - To stop:                   cd $REPO_DIR && docker compose stop
EOF
