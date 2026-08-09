#!/usr/bin/env bash
# =============================================================================
# scripts/backup.sh
#
# Cold, unencrypted, on-box backup of the SLM Fine-Tuning Platform's durable
# state: Postgres (app db + mlflow db + global/role objects) and MinIO
# (datasets/models/mlflow buckets). Run from the repo root on the deploy box
# (the same host running `docker compose`, e.g. via wetty on the pasaflow
# VM). Conventions (set -euo pipefail, say/ok/warn/fail helpers tee'd to a
# chmod 600 log, env_val() reading .env with process-env override, secrets
# only ever passed via env vars, unset after use) are copied from
# scripts/deploy_pasaflow_vm.sh — see that file for the originals.
#
# --------------------------------------------------------------------------
# ORDERING IS LOAD-BEARING: this script ALWAYS dumps Postgres before it
# mirrors MinIO, on purpose, and that ordering is what makes the backup
# consistent WITHOUT ever quiescing the stack. Two facts combine to give
# that guarantee:
#
#   1. Every write path that produces a durable artifact (see
#      workers/tasks/training.py, workers/tasks/model_export.py,
#      workers/tasks/data_generation.py — the `uploaded_prefix`/`committed`
#      pair, "Gap-analysis item 13") uploads the artifact to MinIO BEFORE
#      it commits the DB row that references it. A committed row can
#      therefore never reference a MinIO object that doesn't exist yet.
#   2. Nothing in this stack deletes a MinIO object once uploaded, except
#      the item-13 cleanup path — and that path only fires for a row that
#      never committed at all.
#
# Dump DB first (at time T1), mirror MinIO second (at time T2 > T1): every
# row in the T1 DB dump was committed at or before T1, so by fact (1) the
# object it references was uploaded strictly before T1 too. By fact (2)
# that object is still present in MinIO at T2. So the T2 MinIO mirror is
# GUARANTEED to be a superset of everything the T1 DB dump can reference —
# every foreign reference resolves — even with writers actively uploading
# and committing the entire time this script runs. Reversing the order
# breaks the guarantee: a row committed between a MinIO-first mirror and a
# DB-second dump could reference an object the mirror never saw.
#
# This script does NOT stop/quiesce docker compose, does NOT pause workers,
# and does NOT encrypt the backup set. The last one is deliberate: this
# runs unattended on the same box as the data it backs up, so a key stored
# alongside the backup buys nothing over leaving it unencrypted — real
# protection has to come from full-disk/volume encryption or an offsite
# copy, not from this script.
# =============================================================================

set -euo pipefail

# --------- Pretty-print helpers (tee'd to a chmod 600 log once it exists) ---
if [[ -t 1 ]]; then
  C_INFO='\033[1;36m'; C_OK='\033[1;32m'; C_WARN='\033[1;33m'; C_ERR='\033[1;31m'; C_END='\033[0m'
else
  C_INFO=''; C_OK=''; C_WARN=''; C_ERR=''; C_END=''
fi

say()  { printf "${C_INFO}▸ %s${C_END}\n" "$*" | tee -a "$LOG"; }
ok()   { printf "${C_OK}✓ %s${C_END}\n" "$*" | tee -a "$LOG"; }
warn() { printf "${C_WARN}⚠ %s${C_END}\n" "$*" | tee -a "$LOG"; }
fail() { printf "${C_ERR}✗ %s${C_END}\n" "$*" | tee -a "$LOG"; exit 1; }

# env_val NAME DEFAULT — process-env override wins over the repo .env file,
# which wins over DEFAULT. Never echoes a secret to stdout/log itself: the
# result is only ever captured into a shell var by the caller.
ENV_FILE=".env"
env_val() {
  local var_name="$1" default_val="${2:-}"
  local process_val="${!var_name:-}"
  if [[ -n "$process_val" ]]; then
    printf '%s' "$process_val"
    return
  fi
  if [[ -f "$ENV_FILE" ]]; then
    local file_val
    file_val=$(grep -E "^${var_name}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r')
    if [[ -n "$file_val" ]]; then
      printf '%s' "$file_val"
      return
    fi
  fi
  printf '%s' "$default_val"
}

# --------- Phase 0: bare-minimum preflight (before the log file exists) -----
[[ -f docker-compose.yml ]] || { printf 'FAIL: run this from the repo root (docker-compose.yml not found in %s)\n' "$PWD" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { printf 'FAIL: docker not found on PATH\n' >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { printf "FAIL: 'docker compose' subcommand not available\n" >&2; exit 1; }

# --------- Phase 1: env (all :--defaulted to compose defaults) --------------
POSTGRES_USER="$(env_val POSTGRES_USER slm)"
POSTGRES_DB="$(env_val POSTGRES_DB slm)"
MLFLOW_DB="$(env_val MLFLOW_DB mlflow)"
MINIO_ROOT_USER="$(env_val MINIO_ROOT_USER minioadmin)"
MINIO_ROOT_PASSWORD="$(env_val MINIO_ROOT_PASSWORD minioadmin)"
MINIO_DATASETS_BUCKET="$(env_val MINIO_DATASETS_BUCKET datasets)"
MINIO_MODELS_BUCKET="$(env_val MINIO_MODELS_BUCKET models)"
MLFLOW_S3_BUCKET="$(env_val MLFLOW_S3_BUCKET mlflow)"
BACKUP_ROOT="$(env_val BACKUP_ROOT "${HOME}/drive/slm-backups")"
BACKUP_RETAIN="$(env_val BACKUP_RETAIN 7)"

# BACKUP_ROOT must be absolute: SET_DIR is handed to `docker compose run -v`
# as a bind-mount source in Phase 4, and a relative source is not a host path
# to docker — it is either rejected or silently turned into a named volume,
# which would leave the mirror empty while the script reported success.
[[ "$BACKUP_ROOT" == /* ]] || { printf 'FAIL: BACKUP_ROOT must be an absolute path, got: %s\n' "$BACKUP_ROOT" >&2; exit 1; }

# --------- Phase 2: set dir --------------------------------------------------
TS="$(date -u +%Y%m%d-%H%M%S)"
SET_DIR="${BACKUP_ROOT}/${TS}"
mkdir -p "$SET_DIR/db" "$SET_DIR/minio/datasets" "$SET_DIR/minio/models" "$SET_DIR/minio/mlflow"
# 0700 on the whole tree: db/globals.sql (below) contains role password
# hashes, so the set dir is private from the moment it exists, not just
# once that file lands.
chmod 700 "$SET_DIR" "$SET_DIR/db" "$SET_DIR/minio" \
  "$SET_DIR/minio/datasets" "$SET_DIR/minio/models" "$SET_DIR/minio/mlflow"

LOG="$SET_DIR/backup.log"
: > "$LOG"
chmod 600 "$LOG"

say "Backup set: $SET_DIR"
say "Postgres: user=$POSTGRES_USER db=$POSTGRES_DB mlflow_db=$MLFLOW_DB"
say "MinIO buckets: datasets=$MINIO_DATASETS_BUCKET models=$MINIO_MODELS_BUCKET mlflow=$MLFLOW_S3_BUCKET"
say "Retention: keep newest $BACKUP_RETAIN completed set(s) under $BACKUP_ROOT"
# Must be a POSITIVE integer. Phase 7 computes how many sets to delete as
# (complete - retain) over a chronologically sorted list; a 0 or a
# non-numeric value (which bash arithmetic silently reads as 0) makes that
# count equal the whole list — deleting every complete set INCLUDING the
# one this run just wrote. A typo in .env must not be a backup-wipe.
[[ "$BACKUP_RETAIN" =~ ^[1-9][0-9]*$ ]] || fail "BACKUP_RETAIN must be a positive integer, got: '$BACKUP_RETAIN'"

# --------- Phase 3: Postgres (DB PHASE FIRST — see header comment) ---------
say "==== Phase 3: Postgres dump ===="
DB_T0=$(date +%s)

say "Dumping global objects (roles incl. password hashes) -> db/globals.sql"
docker compose exec -T postgres pg_dumpall --globals-only -U "$POSTGRES_USER" > "$SET_DIR/db/globals.sql"
ok "globals.sql written"

say "Dumping app database ($POSTGRES_DB) -> db/slm.dump"
docker compose exec -T postgres pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB" > "$SET_DIR/db/slm.dump"
ok "slm.dump written"

say "Dumping MLflow database ($MLFLOW_DB) -> db/mlflow.dump"
docker compose exec -T postgres pg_dump -Fc -U "$POSTGRES_USER" "$MLFLOW_DB" > "$SET_DIR/db/mlflow.dump"
ok "mlflow.dump written"

say "Counting rows per table in $POSTGRES_DB -> db/rowcounts.tsv"
# Generate one `SELECT 'table', count(*) FROM public."table"` per public
# table via format()/%I/%L, then \gexec runs each one; -At -F <tab> makes
# each result line exactly "table<TAB>count".
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v ON_ERROR_STOP=1 -At -F $'\t' <<'SQL' > "$SET_DIR/db/rowcounts.tsv"
SELECT format('SELECT %L::text, count(*) FROM public.%I', tablename, tablename)
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename
\gexec
SQL
ok "rowcounts.tsv written ($(wc -l < "$SET_DIR/db/rowcounts.tsv" | tr -d '[:space:]') tables)"

DB_T1=$(date +%s)
DB_ELAPSED=$((DB_T1 - DB_T0))
ok "Postgres phase done in ${DB_ELAPSED}s"

# --------- Phase 4: MinIO mirror --------------------------------------------
say "==== Phase 4: MinIO mirror ===="
MINIO_T0=$(date +%s)

# mirror_bucket LOGICAL_NAME BUCKET_VAR_NAME — reuses the `minio-init`
# service's own image (minio/mc) already in the stack, overriding its
# entrypoint/command rather than adding a new image. Credentials travel
# ONLY inside the MC_HOST_live env assignment — never in argv beyond that,
# never printed. Bucket operand is always a variable expansion.
mirror_bucket() {
  local logical="$1" bucket_var_name="$2"
  local bucket="${!bucket_var_name}"
  say "Mirroring bucket ($logical) -> minio/${logical}/"
  # -T for parity with every `docker compose exec -T` in this repo's scripts:
  # backup.sh's documented invocation is the uid-1001 crontab entry, where
  # there is no TTY to allocate.
  docker compose run --rm --no-deps -T \
    -v "$SET_DIR/minio/${logical}:/backup" \
    -e MC_HOST_live="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000" \
    --entrypoint mc minio-init mirror --overwrite "live/${bucket}" /backup \
    >> "$LOG" 2>&1
  ok "$logical mirrored"
}

mirror_bucket datasets MINIO_DATASETS_BUCKET
mirror_bucket models MINIO_MODELS_BUCKET
mirror_bucket mlflow MLFLOW_S3_BUCKET

# Secrets held only as long as needed.
unset MINIO_ROOT_PASSWORD MINIO_ROOT_USER

say "Counting objects per bucket -> minio/objectcounts.tsv"
: > "$SET_DIR/minio/objectcounts.tsv"
for logical in datasets models mlflow; do
  count=$(find "$SET_DIR/minio/$logical" -type f | wc -l | tr -d '[:space:]')
  printf '%s\t%s\n' "$logical" "$count" >> "$SET_DIR/minio/objectcounts.tsv"
done
ok "objectcounts.tsv written"

MINIO_T1=$(date +%s)
MINIO_ELAPSED=$((MINIO_T1 - MINIO_T0))
ok "MinIO phase done in ${MINIO_ELAPSED}s"

# --------- Phase 5: manifest -------------------------------------------------
say "==== Phase 5: Manifest ===="
MANIFEST="$SET_DIR/manifest.tsv"
: > "$MANIFEST"
while IFS= read -r -d '' f; do
  case "$f" in
    "$MANIFEST"|"$LOG") continue ;;
  esac
  rel="${f#"$SET_DIR"/}"
  sha=$(sha256sum "$f" | awk '{print $1}')
  bytes=$(stat -c%s "$f")
  printf '%s\t%s\t%s\n' "$sha" "$bytes" "$rel" >> "$MANIFEST"
done < <(find "$SET_DIR" -type f -print0)
ok "manifest.tsv written ($(wc -l < "$MANIFEST" | tr -d '[:space:]') files)"

# --------- Phase 6: completeness marker (written LAST, on purpose) ---------
say "==== Phase 6: Completeness marker ===="
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$SET_DIR/BACKUP_COMPLETE"
ok "BACKUP_COMPLETE written — this set is now considered complete"

# --------- Phase 7: retention -------------------------------------------------
say "==== Phase 7: Retention (keep newest $BACKUP_RETAIN complete set(s)) ===="
mapfile -t ALL_SETS < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d | sort)
COMPLETE_SETS=()
for d in "${ALL_SETS[@]}"; do
  if [[ -f "$d/BACKUP_COMPLETE" ]]; then
    COMPLETE_SETS+=("$d")
  else
    warn "no BACKUP_COMPLETE marker in $d — leaving it alone (could be an in-flight backup)"
  fi
done

NUM_COMPLETE=${#COMPLETE_SETS[@]}
if (( NUM_COMPLETE > BACKUP_RETAIN )); then
  NUM_TO_DELETE=$((NUM_COMPLETE - BACKUP_RETAIN))
  for ((i = 0; i < NUM_TO_DELETE; i++)); do
    say "Retention: removing old backup set ${COMPLETE_SETS[$i]}"
    rm -rf "${COMPLETE_SETS[$i]}"
  done
  ok "retention: removed $NUM_TO_DELETE old set(s), kept newest $BACKUP_RETAIN"
else
  ok "retention: $NUM_COMPLETE complete set(s) present, within retain=$BACKUP_RETAIN — nothing removed"
fi

# --------- Phase 8: summary ---------------------------------------------------
say "==== Phase 8: Summary ===="
TOTAL_SIZE=$(du -sh "$SET_DIR" 2>/dev/null | cut -f1)
say "Backup set:  $SET_DIR"
say "Total size:  $TOTAL_SIZE"
say "Postgres:    ${DB_ELAPSED}s"
say "MinIO:       ${MINIO_ELAPSED}s"
ok "backup complete: $SET_DIR"
