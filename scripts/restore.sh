#!/usr/bin/env bash
# =============================================================================
# scripts/restore.sh
#
# Restores a backup set produced by scripts/backup.sh (Postgres dumps +
# MinIO bucket mirrors) into a target Postgres container + MinIO endpoint.
#
# Designed for throwaway-container rehearsal: spin up a scratch postgres/
# minio pair, restore a backup set into it, verify, tear down. Restoring
# into the LIVE slm-postgres / slm-minio stack is the documented Disaster
# Recovery procedure only — this script refuses to target either unless
# RESTORE_ALLOW_LIVE=1 is explicitly set (see the safety guard below).
#
# Usage:
#   RESTORE_PG_CONTAINER=<container> \
#   RESTORE_MINIO_URL=http://restore-minio:9000 \
#   RESTORE_MINIO_USER=<user> \
#   RESTORE_MINIO_PASSWORD=<password> \
#   RESTORE_MC_NETWORK=<docker network> \
#   ./scripts/restore.sh <backup-set-dir> [--db slm|mlflow|all] [--bucket datasets|models|mlflow|all] [--skip-globals]
#
# Required env vars (NO defaults — restore targets must always be explicit,
# never silently inferred, to keep a mistyped/missing var from ever
# defaulting onto a real container):
#   RESTORE_PG_CONTAINER     docker exec target for postgres (name or id)
#   RESTORE_MINIO_URL        container-DNS URL, e.g. http://restore-minio:9000
#   RESTORE_MINIO_USER
#   RESTORE_MINIO_PASSWORD
#   RESTORE_MC_NETWORK       docker network to attach the `mc` container to,
#                            e.g. restore-net (rehearsal) or slm-platform_slm-net
#                            (live). See the --network note in section (c).
#
# Optional env vars (DB-side / bucket-side names — defaults match the
# backup.sh source-side names, i.e. what the dumps/mirrors were taken
# FROM; these control what they are restored INTO on the target):
#   POSTGRES_USER             default: slm
#   POSTGRES_DB                default: slm
#   MLFLOW_DB                  default: mlflow
#   MINIO_DATASETS_BUCKET      default: datasets
#   MINIO_MODELS_BUCKET        default: models
#   MLFLOW_S3_BUCKET           default: mlflow
#
# Input contract (produced by scripts/backup.sh — a complete set has
# BACKUP_COMPLETE present):
#   db/globals.sql, db/slm.dump, db/mlflow.dump (pg_dump -Fc),
#   db/rowcounts.tsv, minio/datasets/, minio/models/, minio/mlflow/,
#   minio/objectcounts.tsv, manifest.tsv, BACKUP_COMPLETE
# =============================================================================

set -euo pipefail

# --------- Pretty-print helpers ---------------------------------------------
LOG="/tmp/slm-restore-$(date +%Y%m%d-%H%M%S).log"
: > "$LOG"
chmod 600 "$LOG" || true

if [[ -t 1 ]]; then
  C_INFO='\033[1;36m'; C_OK='\033[1;32m'; C_WARN='\033[1;33m'; C_ERR='\033[1;31m'; C_END='\033[0m'
else
  C_INFO=''; C_OK=''; C_WARN=''; C_ERR=''; C_END=''
fi

say()  { printf "${C_INFO}▸ %s${C_END}\n" "$*" | tee -a "$LOG"; }
ok()   { printf "${C_OK}✓ %s${C_END}\n" "$*" | tee -a "$LOG"; }
warn() { printf "${C_WARN}⚠ %s${C_END}\n" "$*" | tee -a "$LOG"; }
fail() { printf "${C_ERR}✗ %s${C_END}\n" "$*" | tee -a "$LOG"; exit 1; }

say "Logging to $LOG"

usage() {
  cat <<'EOF'
Usage: restore.sh <backup-set-dir> [--db slm|mlflow|all] [--bucket datasets|models|mlflow|all] [--skip-globals]

Required env vars: RESTORE_PG_CONTAINER, RESTORE_MINIO_URL, RESTORE_MINIO_USER,
                   RESTORE_MINIO_PASSWORD, RESTORE_MC_NETWORK
EOF
}

# --------- Argument parsing --------------------------------------------------
SET_DIR=""
DB_SELECT="all"
BUCKET_SELECT="all"
SKIP_GLOBALS=0

if [[ $# -lt 1 ]]; then
  usage
  fail "missing required <backup-set-dir> argument"
fi
SET_DIR="$1"
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)
      DB_SELECT="${2:-}"
      [[ "$DB_SELECT" =~ ^(slm|mlflow|all)$ ]] || fail "--db must be one of slm|mlflow|all, got: $DB_SELECT"
      shift 2
      ;;
    --bucket)
      BUCKET_SELECT="${2:-}"
      [[ "$BUCKET_SELECT" =~ ^(datasets|models|mlflow|all)$ ]] || fail "--bucket must be one of datasets|models|mlflow|all, got: $BUCKET_SELECT"
      shift 2
      ;;
    --skip-globals)
      SKIP_GLOBALS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      fail "unrecognized argument: $1"
      ;;
  esac
done

say "Backup set:  $SET_DIR"
say "DB select:   $DB_SELECT | Bucket select: $BUCKET_SELECT | Skip globals: $SKIP_GLOBALS"

# --------- Validate backup set -----------------------------------------------
[[ -d "$SET_DIR" ]] || fail "backup set dir does not exist: $SET_DIR"
# Canonicalize before anything uses it: SET_DIR/minio/<logical> becomes a
# `docker run -v` bind-mount source below, and docker only accepts an
# absolute host path there — a relative set dir (./20260808-020000/) would
# be misread as a named volume and mirror an empty directory into the
# target bucket while every step still reported success.
SET_DIR="$(cd "$SET_DIR" && pwd)"
[[ -f "$SET_DIR/BACKUP_COMPLETE" ]] || fail "$SET_DIR is missing BACKUP_COMPLETE — this backup set is not complete (backup.sh did not finish), refusing to restore from a partial set"
ok "backup set looks complete (BACKUP_COMPLETE present)"

# --------- Required env vars (no defaults) -----------------------------------
: "${RESTORE_PG_CONTAINER:?RESTORE_PG_CONTAINER is required (docker exec target for postgres) — no default}"
: "${RESTORE_MINIO_URL:?RESTORE_MINIO_URL is required (container-DNS URL, e.g. http://restore-minio:9000) — no default}"
: "${RESTORE_MINIO_USER:?RESTORE_MINIO_USER is required — no default}"
: "${RESTORE_MINIO_PASSWORD:?RESTORE_MINIO_PASSWORD is required — no default}"
# Deliberately has NO default, not even "host". Rootless docker (this box runs
# rootless dockerd as uid 1001) makes --network host actively wrong: joining
# "the host netns" lands the container in RootlessKit's CHILD namespace, while
# ports published as 127.0.0.1:<port> live in the PARENT namespace — so a
# 127.0.0.1 target is unreachable from inside the mc container. Defaulting this
# to `host` would silently reinstate exactly that broken path. Attach mc to a
# real docker network and address MinIO by container DNS instead.
: "${RESTORE_MC_NETWORK:?RESTORE_MC_NETWORK is required (docker network for the mc container, e.g. restore-net or slm-platform_slm-net) — no default; see docs/runbooks/backup_restore.md}"

# --------- Optional DB-side / bucket-side name defaults ----------------------
POSTGRES_USER="${POSTGRES_USER:-slm}"
POSTGRES_DB="${POSTGRES_DB:-slm}"
MLFLOW_DB="${MLFLOW_DB:-mlflow}"
MINIO_DATASETS_BUCKET="${MINIO_DATASETS_BUCKET:-datasets}"
MINIO_MODELS_BUCKET="${MINIO_MODELS_BUCKET:-models}"
MLFLOW_S3_BUCKET="${MLFLOW_S3_BUCKET:-mlflow}"

# Host:port portion of RESTORE_MINIO_URL, stripped of scheme — used both by
# the live-target guard immediately below and to build MC_HOST_target later.
# Credentials are assembled into that single env assignment only; never
# printed or logged anywhere else.
MINIO_HOSTPORT="${RESTORE_MINIO_URL#http://}"
MINIO_HOSTPORT="${MINIO_HOSTPORT#https://}"
MINIO_HOSTPORT="${MINIO_HOSTPORT%%/*}"
[[ -n "$MINIO_HOSTPORT" ]] || fail "could not parse host:port out of RESTORE_MINIO_URL=$RESTORE_MINIO_URL"

# --------- Safety guard: throwaway-container rehearsal only, by default -----
# This script is meant to be pointed at a scratch postgres/minio pair spun
# up just for restore rehearsal. Restoring straight into the live stack is
# the documented DR procedure and requires a human to opt in explicitly —
# a mistyped RESTORE_PG_CONTAINER/RESTORE_MINIO_URL must never silently
# clobber production data.
IS_LIVE=0
if [[ "$RESTORE_PG_CONTAINER" == "slm-postgres" ]]; then
  IS_LIVE=1
fi
# Matched against the parsed host:port as WHOLE-STRING case patterns, never as
# substrings. The rehearsal MinIO is addressed as restore-minio:9000, which
# *contains* "minio:9000" — a substring test would classify every rehearsal as
# the live stack and refuse to run without RESTORE_ALLOW_LIVE=1, training
# operators to set that flag by reflex. So enumerate the live identities
# exactly: the compose service name (minio), its container_name (slm-minio),
# and the loopback address docker-compose.yml publishes it on
# (127.0.0.1:${MINIO_PORT:-9000}). localhost:19000-style rehearsal ports and
# restore-* container names do not match any of these.
case "$MINIO_HOSTPORT" in
  minio|minio:*|slm-minio|slm-minio:*|localhost:9000|127.0.0.1:9000)
    IS_LIVE=1 ;;
esac
if [[ "$IS_LIVE" -eq 1 && "${RESTORE_ALLOW_LIVE:-0}" != "1" ]]; then
  fail "RESTORE_PG_CONTAINER/RESTORE_MINIO_URL points at what looks like the LIVE stack (slm-postgres / slm-minio / minio:9000). This script is designed for throwaway-container rehearsal only. Set RESTORE_ALLOW_LIVE=1 to proceed with the documented DR procedure — do this only if you mean it."
fi
if [[ "$IS_LIVE" -eq 1 ]]; then
  warn "RESTORE_ALLOW_LIVE=1 — proceeding against what looks like the LIVE stack. This is the DR procedure, not rehearsal."
fi

DBS_RESTORED=0
BUCKETS_RESTORED=0

# =============================================================================
# (a) GLOBALS FIRST (unless --skip-globals)
#
# Must run before any per-DB createdb/pg_restore: db/globals.sql carries
# role definitions (CREATE ROLE ...), and the mlflow role in particular
# MUST exist before the mlflow database is created in step (b) — otherwise
# pg_restore's --role=/ownership statements for that DB have nothing to
# attach to (the Phase 5.5 lesson from scripts/deploy_pasaflow_vm.sh:
# a database provisioned before its owning role exists ends up with
# broken ownership that only surfaces later as InsufficientPrivilege).
#
# ON_ERROR_STOP is OFF for this one step only: globals.sql re-declares
# roles that may already exist on the target (e.g. a rehearsal container
# reused across runs), and "role already exists" noise here is expected,
# not fatal. Every step after this one restores ON_ERROR_STOP=1 (strict).
# =============================================================================
if [[ "$SKIP_GLOBALS" -eq 1 ]]; then
  warn "--skip-globals set — skipping db/globals.sql"
else
  say "==== Restoring globals (db/globals.sql) ===="
  GLOBALS_FILE="$SET_DIR/db/globals.sql"
  [[ -f "$GLOBALS_FILE" ]] || fail "missing $GLOBALS_FILE"
  docker exec -i "$RESTORE_PG_CONTAINER" psql -v ON_ERROR_STOP=0 -U "$POSTGRES_USER" -d postgres < "$GLOBALS_FILE" 2>&1 | tee -a "$LOG"
  ok "globals restored (role-already-exists noise above, if any, is expected)"
fi

# =============================================================================
# (b) Per selected DB: dropdb --if-exists, createdb, then pg_restore.
#
# ON_ERROR_STOP semantics are STRICT from here on (--exit-on-error on
# pg_restore) — the globals step is the only place partial failure is
# tolerated.
# =============================================================================
case "$DB_SELECT" in
  slm)    DB_LOGICALS=(slm) ;;
  mlflow) DB_LOGICALS=(mlflow) ;;
  all)    DB_LOGICALS=(slm mlflow) ;;
esac

for logical in "${DB_LOGICALS[@]}"; do
  if [[ "$logical" == "slm" ]]; then
    DB_NAME="$POSTGRES_DB"
    DUMP_FILE="$SET_DIR/db/slm.dump"
  else
    DB_NAME="$MLFLOW_DB"
    DUMP_FILE="$SET_DIR/db/mlflow.dump"
  fi

  say "==== Restoring DB '$logical' -> database '$DB_NAME' ===="
  [[ -f "$DUMP_FILE" ]] || fail "missing dump file: $DUMP_FILE"

  docker exec -i "$RESTORE_PG_CONTAINER" dropdb --if-exists -U "$POSTGRES_USER" "$DB_NAME" 2>&1 | tee -a "$LOG"
  ok "dropped '$DB_NAME' (if it existed)"

  docker exec -i "$RESTORE_PG_CONTAINER" createdb -U "$POSTGRES_USER" "$DB_NAME" 2>&1 | tee -a "$LOG"
  ok "created '$DB_NAME'"

  docker exec -i "$RESTORE_PG_CONTAINER" pg_restore --no-owner --role="$POSTGRES_USER" -U "$POSTGRES_USER" -d "$DB_NAME" --exit-on-error < "$DUMP_FILE" 2>&1 | tee -a "$LOG"
  ok "pg_restore complete for '$DB_NAME'"

  DBS_RESTORED=$((DBS_RESTORED + 1))
done

# =============================================================================
# (c) Per selected bucket: mb --ignore-existing, then mirror --overwrite.
#
# Credentials live ONLY inside the MC_HOST_target env assignment passed to
# `docker run -e` — never in a say/ok/warn/fail line, never echoed.
# =============================================================================
case "$BUCKET_SELECT" in
  datasets) BUCKET_LOGICALS=(datasets) ;;
  models)   BUCKET_LOGICALS=(models) ;;
  mlflow)   BUCKET_LOGICALS=(mlflow) ;;
  all)      BUCKET_LOGICALS=(datasets models mlflow) ;;
esac

for logical in "${BUCKET_LOGICALS[@]}"; do
  case "$logical" in
    datasets) TARGET_BUCKET="$MINIO_DATASETS_BUCKET" ;;
    models)   TARGET_BUCKET="$MINIO_MODELS_BUCKET" ;;
    mlflow)   TARGET_BUCKET="$MLFLOW_S3_BUCKET" ;;
  esac
  SOURCE_DIR="$SET_DIR/minio/$logical"

  say "==== Restoring bucket '$logical' -> target bucket '$TARGET_BUCKET' ===="
  [[ -d "$SOURCE_DIR" ]] || fail "missing source dir: $SOURCE_DIR"

  docker run --rm \
    -v "$SOURCE_DIR:/backup:ro" \
    -e MC_HOST_target="http://${RESTORE_MINIO_USER}:${RESTORE_MINIO_PASSWORD}@${MINIO_HOSTPORT}" \
    --network "$RESTORE_MC_NETWORK" \
    --entrypoint mc \
    minio/mc:latest \
    mb --ignore-existing "target/$TARGET_BUCKET" 2>&1 | tee -a "$LOG"
  ok "bucket '$TARGET_BUCKET' present"

  docker run --rm \
    -v "$SOURCE_DIR:/backup:ro" \
    -e MC_HOST_target="http://${RESTORE_MINIO_USER}:${RESTORE_MINIO_PASSWORD}@${MINIO_HOSTPORT}" \
    --network "$RESTORE_MC_NETWORK" \
    --entrypoint mc \
    minio/mc:latest \
    mirror --overwrite /backup "target/$TARGET_BUCKET" 2>&1 | tee -a "$LOG"
  ok "mirrored '$logical' -> '$TARGET_BUCKET'"

  BUCKETS_RESTORED=$((BUCKETS_RESTORED + 1))
done

# Secrets no longer needed past this point.
unset RESTORE_MINIO_PASSWORD

# --------- Summary -------------------------------------------------------
say "==== DONE ===="
ok "Restored $DBS_RESTORED database(s) into container '$RESTORE_PG_CONTAINER' (selection: $DB_SELECT)"
ok "Restored $BUCKETS_RESTORED bucket(s) into MinIO at $MINIO_HOSTPORT (selection: $BUCKET_SELECT)"
ok "Log: $LOG"
