#!/usr/bin/env bash
# =============================================================================
# scripts/verify_restore.sh
#
# Proves that a restore performed by scripts/restore.sh into throwaway
# containers is COMPLETE and INTERNALLY CONSISTENT — by comparing the
# RESTORED stores (Postgres + MinIO) against the backup SET's own metadata
# files (db/rowcounts.tsv, minio/objectcounts.tsv, manifest.tsv).
#
# This script does not touch or trust "live" defaults for any credential —
# every target connection value is required via env, same contract as
# restore.sh.
#
# Usage:
#   RESTORE_PG_CONTAINER=restore-pg \
#   RESTORE_MINIO_URL=http://restore-minio:9000 \
#   RESTORE_MINIO_USER=minioadmin \
#   RESTORE_MINIO_PASSWORD=*** \
#   RESTORE_MC_NETWORK=restore-net \
#   scripts/verify_restore.sh /path/to/backup-set-dir
#
# Required env (no live defaults — restoring into the wrong target silently
# would be worse than failing loudly):
#   RESTORE_PG_CONTAINER     name of the throwaway postgres container
#   RESTORE_MINIO_URL        container-DNS URL, e.g. http://restore-minio:9000
#   RESTORE_MINIO_USER       MinIO access key for the restored instance
#   RESTORE_MINIO_PASSWORD   MinIO secret key for the restored instance
#   RESTORE_MC_NETWORK       docker network to attach the `mc` container to,
#                            e.g. restore-net or slm-platform_slm-net
#
# Optional env (safe defaults):
#   MINIO_DATASETS_BUCKET    default: datasets
#   MINIO_MODELS_BUCKET      default: models
#   MLFLOW_S3_BUCKET         default: mlflow
#   POSTGRES_USER            default: slm
#   POSTGRES_DB              default: slm
#   VERIFY_SAMPLE_N          default: 20 (sample checksums to verify)
# =============================================================================

set -euo pipefail

# --------- Pretty-print helpers (mirrors scripts/deploy_pasaflow_vm.sh) -----
if [[ -t 1 ]]; then
  C_INFO='\033[1;36m'; C_OK='\033[1;32m'; C_WARN='\033[1;33m'; C_ERR='\033[1;31m'; C_END='\033[0m'
else
  C_INFO=''; C_OK=''; C_WARN=''; C_ERR=''; C_END=''
fi

say()  { printf "${C_INFO}▸ %s${C_END}\n" "$*"; }
ok()   { printf "${C_OK}✓ %s${C_END}\n" "$*"; }
warn() { printf "${C_WARN}⚠ %s${C_END}\n" "$*"; }
fail() { printf "${C_ERR}✗ %s${C_END}\n" "$*"; }

# --------- Usage / args -------------------------------------------------------
if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <backup-set-dir>" >&2
  exit 1
fi
BACKUP_SET_DIR="$1"
[[ -d "$BACKUP_SET_DIR" ]] || { fail "backup set dir not found: $BACKUP_SET_DIR"; exit 1; }

ROWCOUNTS_TSV="$BACKUP_SET_DIR/db/rowcounts.tsv"
OBJECTCOUNTS_TSV="$BACKUP_SET_DIR/minio/objectcounts.tsv"
MANIFEST_TSV="$BACKUP_SET_DIR/manifest.tsv"

for f in "$ROWCOUNTS_TSV" "$OBJECTCOUNTS_TSV" "$MANIFEST_TSV"; do
  [[ -f "$f" ]] || { fail "expected metadata file missing: $f"; exit 1; }
done

# --------- Required env (no live defaults) -----------------------------------
: "${RESTORE_PG_CONTAINER:?RESTORE_PG_CONTAINER is required (name of the throwaway postgres container)}"
: "${RESTORE_MINIO_URL:?RESTORE_MINIO_URL is required (container-DNS URL, e.g. http://restore-minio:9000)}"
: "${RESTORE_MINIO_USER:?RESTORE_MINIO_USER is required}"
: "${RESTORE_MINIO_PASSWORD:?RESTORE_MINIO_PASSWORD is required}"
# No default, not even "host" — see the identical note in restore.sh. Under
# rootless docker, --network host joins RootlessKit's CHILD netns while
# 127.0.0.1-published ports live in the PARENT netns, so a loopback target is
# unreachable from the mc container. mc must join a real docker network and
# resolve MinIO by container DNS.
: "${RESTORE_MC_NETWORK:?RESTORE_MC_NETWORK is required (docker network for the mc container, e.g. restore-net or slm-platform_slm-net) — no default; see docs/runbooks/backup_restore.md}"

# --------- Optional env (safe defaults) ---------------------------------------
MINIO_DATASETS_BUCKET="${MINIO_DATASETS_BUCKET:-datasets}"
MINIO_MODELS_BUCKET="${MINIO_MODELS_BUCKET:-models}"
MLFLOW_S3_BUCKET="${MLFLOW_S3_BUCKET:-mlflow}"
POSTGRES_USER="${POSTGRES_USER:-slm}"
POSTGRES_DB="${POSTGRES_DB:-slm}"
VERIFY_SAMPLE_N="${VERIFY_SAMPLE_N:-20}"

say "Verifying restore of backup set: $BACKUP_SET_DIR"
say "Target postgres container: $RESTORE_PG_CONTAINER | DB: $POSTGRES_DB | user: $POSTGRES_USER"
say "Target MinIO: $RESTORE_MINIO_URL"

# --------- Failure counters ---------------------------------------------------
FAIL_COUNT=0
declare -a GROUP_RESULT=()   # human-readable pass/fail lines for the summary

record_group() {
  local name="$1" status="$2"
  GROUP_RESULT+=("$name: $status")
}

# --------- psql helper (never prints RESTORE_MINIO_PASSWORD; psql args here
# never include it anyway — this helper is postgres-only) --------------------
pg_query() {
  local sql="$1"
  docker exec "$RESTORE_PG_CONTAINER" psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$sql"
}

# --------- mc helper — credentials only inside MC_HOST_target, never echoed -
# Strip the scheme off RESTORE_MINIO_URL (http:// or https://) so it can be
# re-assembled as scheme://user:pass@host:port for MC_HOST_target.
MC_SCHEME="http"
MC_HOSTPORT="$RESTORE_MINIO_URL"
if [[ "$RESTORE_MINIO_URL" == https://* ]]; then
  MC_SCHEME="https"
  MC_HOSTPORT="${RESTORE_MINIO_URL#https://}"
elif [[ "$RESTORE_MINIO_URL" == http://* ]]; then
  MC_HOSTPORT="${RESTORE_MINIO_URL#http://}"
fi

mc_run() {
  docker run --rm \
    -e MC_HOST_target="${MC_SCHEME}://${RESTORE_MINIO_USER}:${RESTORE_MINIO_PASSWORD}@${MC_HOSTPORT}" \
    --network "$RESTORE_MC_NETWORK" \
    --entrypoint mc \
    minio/mc:latest "$@"
}

# relpath under minio/ looks like: minio/<logical>/<rest...> where <logical>
# mirrors one of datasets/models/mlflow. Map that logical name to the actual
# restored bucket name (which may differ per env).
logical_to_bucket() {
  case "$1" in
    datasets) echo "$MINIO_DATASETS_BUCKET" ;;
    models) echo "$MINIO_MODELS_BUCKET" ;;
    mlflow) echo "$MLFLOW_S3_BUCKET" ;;
    *) echo "$1" ;;
  esac
}

# =============================================================================
# GROUP 1: ROW COUNTS
# =============================================================================
say "==== Group 1: Row counts (db/rowcounts.tsv) ===="
GROUP1_FAIL=0
while IFS=$'\t' read -r table expected_count; do
  [[ -z "$table" ]] && continue
  actual_count="$(pg_query "SELECT count(*) FROM ${table}" 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ "$actual_count" == "$expected_count" ]]; then
    ok "row count $table: $actual_count (expected $expected_count)"
  else
    fail "row count $table: got $actual_count, expected $expected_count"
    GROUP1_FAIL=$((GROUP1_FAIL + 1))
  fi
done < "$ROWCOUNTS_TSV"

if [[ "$GROUP1_FAIL" -eq 0 ]]; then
  record_group "1. Row counts" "PASS"
else
  record_group "1. Row counts" "FAIL ($GROUP1_FAIL mismatch(es))"
  FAIL_COUNT=$((FAIL_COUNT + GROUP1_FAIL))
fi

# =============================================================================
# GROUP 2: OBJECT COUNTS
# =============================================================================
say "==== Group 2: Object counts (minio/objectcounts.tsv) ===="
GROUP2_FAIL=0
while IFS=$'\t' read -r logical expected_count; do
  [[ -z "$logical" ]] && continue
  bucket="$(logical_to_bucket "$logical")"
  actual_count="$(mc_run ls --recursive "target/${bucket}" 2>/dev/null | wc -l | tr -d '[:space:]' || true)"
  if [[ "$actual_count" == "$expected_count" ]]; then
    ok "object count $logical (bucket=$bucket): $actual_count (expected $expected_count)"
  else
    fail "object count $logical (bucket=$bucket): got $actual_count, expected $expected_count"
    GROUP2_FAIL=$((GROUP2_FAIL + 1))
  fi
done < "$OBJECTCOUNTS_TSV"

if [[ "$GROUP2_FAIL" -eq 0 ]]; then
  record_group "2. Object counts" "PASS"
else
  record_group "2. Object counts" "FAIL ($GROUP2_FAIL mismatch(es))"
  FAIL_COUNT=$((FAIL_COUNT + GROUP2_FAIL))
fi

# =============================================================================
# GROUP 3: SAMPLE CHECKSUMS
# =============================================================================
say "==== Group 3: Sample checksums ($VERIFY_SAMPLE_N of manifest.tsv, minio/ entries) ===="
GROUP3_FAIL=0
# manifest.tsv columns: sha256<TAB>bytes<TAB>relpath — select rows whose
# relpath (3rd field) is a mirrored OBJECT, i.e. minio/<logical>/<key>,
# deterministic sort, take top N. The <logical>/ path component is
# required on purpose: backup.sh also writes minio/objectcounts.tsv (its
# own metadata, not a bucket object), which matches a bare ^minio/ and
# would parse into an empty key against a bucket named "objectcounts.tsv"
# — an `mc cat` that can only ever fail, failing group 3 on every good
# restore of a set with fewer than VERIFY_SAMPLE_N mirrored objects.
# The `/` inside the bracket expression MUST stay backslash-escaped: an awk
# ERE literal ends at the first unescaped `/` even inside `[...]`, so
# `[^/]` is a syntax error on BSD awk and unportable elsewhere.
SAMPLE_LINES="$(awk -F'\t' '$3 ~ /^minio\/[^\/]+\/./ {print}' "$MANIFEST_TSV" | sort | head -n "$VERIFY_SAMPLE_N")"

if [[ -z "$SAMPLE_LINES" ]]; then
  warn "no minio/ entries found in manifest.tsv to sample"
else
  while IFS=$'\t' read -r sha256 bytes relpath; do
    [[ -z "$relpath" ]] && continue
    # relpath: minio/<logical>/<rest of key...>
    logical="$(echo "$relpath" | cut -d/ -f2)"
    key="$(echo "$relpath" | cut -d/ -f3-)"
    bucket="$(logical_to_bucket "$logical")"
    actual_sha256="$(mc_run cat "target/${bucket}/${key}" 2>/dev/null | sha256sum | awk '{print $1}' || true)"
    if [[ "$actual_sha256" == "$sha256" ]]; then
      ok "checksum OK: $relpath"
    else
      fail "checksum MISMATCH: $relpath (expected $sha256, got ${actual_sha256:-<missing>})"
      GROUP3_FAIL=$((GROUP3_FAIL + 1))
    fi
  done <<< "$SAMPLE_LINES"
fi

if [[ "$GROUP3_FAIL" -eq 0 ]]; then
  record_group "3. Sample checksums" "PASS"
else
  record_group "3. Sample checksums" "FAIL ($GROUP3_FAIL mismatch(es))"
  FAIL_COUNT=$((FAIL_COUNT + GROUP3_FAIL))
fi

# =============================================================================
# GROUP 4: SMART-MODEL-TUNE CHAIN
# =============================================================================
say "==== Group 4: smart-model-tune chain (projects/datasets/model_artifacts/usage_events/audit_events) ===="
GROUP4_FAIL=0

# (a) >=1 project with external_project_id set — the Supabase<->Engine link.
proj_with_ext="$(pg_query "SELECT count(*) FROM projects WHERE external_project_id IS NOT NULL" 2>/dev/null | tr -d '[:space:]' || true)"
if [[ "${proj_with_ext:-0}" -ge 1 ]]; then
  ok "projects.external_project_id populated on $proj_with_ext row(s)"
else
  fail "no projects row has external_project_id set (Supabase<->Engine link is missing)"
  GROUP4_FAIL=$((GROUP4_FAIL + 1))
fi

# (b) >=1 dataset joined to a project.
ds_with_proj="$(pg_query "SELECT count(*) FROM datasets d JOIN projects p ON d.project_id = p.id" 2>/dev/null | tr -d '[:space:]' || true)"
if [[ "${ds_with_proj:-0}" -ge 1 ]]; then
  ok "datasets joined to a project: $ds_with_proj row(s)"
else
  fail "no datasets row joins to a projects row"
  GROUP4_FAIL=$((GROUP4_FAIL + 1))
fi

# (c) every non-null model_artifacts.gguf_uri must exist in restored MinIO.
# Same "query failed" vs "zero rows" distinction as check_uri_column below:
# `|| true` here would turn a missing model_artifacts table into a silent pass.
if ! GGUF_URIS="$(pg_query "SELECT gguf_uri FROM model_artifacts WHERE gguf_uri IS NOT NULL" 2>/dev/null)"; then
  fail "model_artifacts.gguf_uri could not be queried in the restored DB (missing table or column?)"
  GROUP4_FAIL=$((GROUP4_FAIL + 1))
elif [[ -z "$GGUF_URIS" ]]; then
  warn "no model_artifacts.gguf_uri set — nothing to check (pass)"
else
  while IFS= read -r uri; do
    [[ -z "$uri" ]] && continue
    # uri: s3://bucket/key...
    stripped="${uri#s3://}"
    bucket="${stripped%%/*}"
    key="${stripped#*/}"
    if mc_run stat "target/${bucket}/${key}" >/dev/null 2>&1; then
      ok "gguf_uri exists in restored MinIO: $uri"
    else
      fail "gguf_uri MISSING in restored MinIO: $uri"
      GROUP4_FAIL=$((GROUP4_FAIL + 1))
    fi
  done <<< "$GGUF_URIS"
fi

# (d) usage_events / audit_events readable (missing table = fail, zero rows = pass+warn)
for tbl in usage_events audit_events; do
  if cnt="$(pg_query "SELECT count(*) FROM ${tbl}" 2>/dev/null | tr -d '[:space:]')"; then
    if [[ "${cnt:-0}" -eq 0 ]]; then
      warn "$tbl exists and is readable but has 0 rows (pass with note)"
    else
      ok "$tbl exists and is readable ($cnt rows)"
    fi
  else
    fail "$tbl is missing or unreadable in restored DB"
    GROUP4_FAIL=$((GROUP4_FAIL + 1))
  fi
done

if [[ "$GROUP4_FAIL" -eq 0 ]]; then
  record_group "4. smart-model-tune chain" "PASS"
else
  record_group "4. smart-model-tune chain" "FAIL ($GROUP4_FAIL issue(s))"
  FAIL_COUNT=$((FAIL_COUNT + GROUP4_FAIL))
fi

# =============================================================================
# GROUP 5: DANGLING-URI SWEEP
# =============================================================================
say "==== Group 5: Dangling-URI sweep (storage_uri, gguf_uri, lora_adapter_uri, safetensors_uri) ===="
GROUP5_FAIL=0

check_uri_column() {
  local table="$1" column="$2"
  local uris
  # `if uris=$(...)` — NOT `$(...) || true`. A failed query (table or column
  # missing from the restored DB) and a successful query returning zero rows
  # both yield an empty string; only the exit status tells them apart, and
  # swallowing it would let a renamed/dropped column pass this sweep
  # vacuously — the exact failure this group exists to catch.
  if ! uris="$(pg_query "SELECT ${column} FROM ${table} WHERE ${column} IS NOT NULL" 2>/dev/null)"; then
    fail "$table.$column could not be queried in the restored DB (missing table or column?)"
    GROUP5_FAIL=$((GROUP5_FAIL + 1))
    return 0
  fi
  [[ -z "$uris" ]] && return 0
  while IFS= read -r uri; do
    [[ -z "$uri" ]] && continue
    local stripped bucket key
    stripped="${uri#s3://}"
    bucket="${stripped%%/*}"
    key="${stripped#*/}"
    if mc_run stat "target/${bucket}/${key}" >/dev/null 2>&1; then
      ok "$table.$column resolves: $uri"
    else
      fail "$table.$column DANGLING: $uri"
      GROUP5_FAIL=$((GROUP5_FAIL + 1))
    fi
  done <<< "$uris"
}

check_uri_column datasets storage_uri
check_uri_column model_artifacts gguf_uri
check_uri_column model_artifacts lora_adapter_uri
check_uri_column model_artifacts safetensors_uri

if [[ "$GROUP5_FAIL" -eq 0 ]]; then
  record_group "5. Dangling-URI sweep" "PASS"
else
  record_group "5. Dangling-URI sweep" "FAIL ($GROUP5_FAIL dangling URI(s))"
  FAIL_COUNT=$((FAIL_COUNT + GROUP5_FAIL))
fi

# =============================================================================
# SUMMARY
# =============================================================================
say "==== Summary ===="
for line in "${GROUP_RESULT[@]}"; do
  if [[ "$line" == *"PASS"* ]]; then
    ok "$line"
  else
    fail "$line"
  fi
done

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  ok "restore verification PASSED — all 5 check groups clean"
  exit 0
else
  fail "restore verification FAILED — $FAIL_COUNT total check failure(s) across the groups above"
  exit 1
fi
