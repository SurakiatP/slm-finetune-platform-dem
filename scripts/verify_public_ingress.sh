#!/usr/bin/env bash
# Verify the platform is correctly reachable — and correctly UNreachable —
# once the Cloudflare tunnel is up.
#
# Run this the moment `cloudflared` starts serving, BEFORE telling the
# smart-model-tune team to point at the box. Everything the platform has been
# verified for so far was checked over loopback from inside the box, where
# CORS, TLS, the edge nginx and the tunnel are all bypassed. This script is
# the first thing that exercises the path a browser actually takes.
#
# It checks four things, and the last one matters as much as the first:
#   A. the public surface answers at all (tunnel + edge + TrustedHost)
#   B. CORS lets the frontend's real origin through (the classic silent
#      blocker: every endpoint 200s to curl and every call fails in a browser)
#   C. every endpoint the frontend's 12 wired screens need answers publicly
#   D. everything that must NOT be public still isn't (P0 acceptance
#      criterion: "a port scan from outside reaches only intended services")
#
# Usage:
#   PUBLIC_API_URL=https://api.pasaflow.com \
#   FRONTEND_ORIGIN=https://<the-real-deployed-frontend-origin> \
#   [STORAGE_URL=https://storage.pasaflow.com] \
#   [BOX_HOST=slmpc.pasaflow.com] \
#     bash scripts/verify_public_ingress.sh
#
# Run it from ANYWHERE WITH INTERNET — a laptop is better than the box,
# because from the box you may reach things through loopback that the
# outside world cannot, which would make group D pass for the wrong reason.
# If you must run it on the box, group D is advisory only; the script says so.

set -euo pipefail

# NOTE: keep these :? messages free of apostrophes and parentheses — bash
# mis-parses both inside ${VAR:?word} and the whole script fails to load with
# an error pointing at an unrelated line 30 lines further down.
PUBLIC_API_URL="${PUBLIC_API_URL:?set PUBLIC_API_URL, e.g. https://api.pasaflow.com}"
FRONTEND_ORIGIN="${FRONTEND_ORIGIN:?set FRONTEND_ORIGIN to the real deployed frontend origin — scheme plus host, no trailing slash}"
STORAGE_URL="${STORAGE_URL:-}"
BOX_HOST="${BOX_HOST:-slmpc.pasaflow.com}"
CURL_TIMEOUT="${CURL_TIMEOUT:-20}"

PUBLIC_API_URL="${PUBLIC_API_URL%/}"
FRONTEND_ORIGIN="${FRONTEND_ORIGIN%/}"

PASS=0
FAIL=0

say()  { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; PASS=$((PASS + 1)); }
bad()  { printf '\033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); }
note() { printf '\033[33m⚠\033[0m %s\n' "$*"; }

code_for() {  # code_for <url> [extra curl args...]
  # curl already prints 000 on a connection failure AND exits non-zero, so a
  # trailing `|| echo 000` concatenates into "000000" and every comparison
  # below silently stops matching. Capture first, then default.
  local url="$1"; shift
  local out
  out="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$CURL_TIMEOUT" "$@" "$url" 2>/dev/null)" || true
  printf '%s' "${out:-000}"
}

expect_code() {  # expect_code <label> <url> <expected-csv>
  local label="$1" url="$2" expected="$3" got
  got="$(code_for "$url")"
  if [[ ",$expected," == *",$got,"* ]]; then
    ok "$label → $got"
  else
    bad "$label → $got (expected one of $expected)  [$url]"
  fi
}

# ---------------------------------------------------------------- A: reachable

say "==== A. Public reachability (tunnel → edge → api) ===="

health_code="$(code_for "$PUBLIC_API_URL/health")"
if [[ "$health_code" == "200" ]]; then
  ok "GET /health through the tunnel → 200"
elif [[ "$health_code" == "400" ]]; then
  bad "GET /health → 400. This is almost certainly TrustedHostMiddleware
      rejecting the tunnel hostname, NOT the app being down (round-3.5
      finding S1 — the failure mode that looks like an outage). Add the
      public hostname to API_ALLOWED_HOSTS in .env, or leave the variable
      absent/empty, which means allow-all."
elif [[ "$health_code" == "000" ]]; then
  bad "GET /health → no response at all. The tunnel is not routing:
      check 'docker compose --profile tunnel ps' and 'docker compose logs
      cloudflared' on the box, and that the DNS record points at the tunnel."
else
  bad "GET /health → $health_code (expected 200)"
fi

if [[ "$(curl -s --max-time "$CURL_TIMEOUT" -o /dev/null -w '%{scheme}' "$PUBLIC_API_URL/health" 2>/dev/null)" == "https" ]]; then
  ok "served over HTTPS (TLS terminated at Cloudflare)"
else
  bad "not HTTPS — a browser will block mixed content from an https frontend"
fi

expect_code "GET / (SPA root through the edge)" "$PUBLIC_API_URL/" "200,301,302,304"

# ---------------------------------------------------------------------- B: CORS

say "==== B. CORS for the frontend's real origin ===="
say "    origin under test: $FRONTEND_ORIGIN"

preflight_headers="$(curl -s -i -X OPTIONS --max-time "$CURL_TIMEOUT" \
  -H "Origin: $FRONTEND_ORIGIN" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type" \
  "$PUBLIC_API_URL/api/v1/projects" 2>/dev/null || true)"

allow_origin="$(printf '%s' "$preflight_headers" \
  | tr -d '\r' | grep -i '^access-control-allow-origin:' | head -1 | cut -d' ' -f2- || true)"

if [[ -z "$allow_origin" ]]; then
  bad "CORS preflight returned NO Access-Control-Allow-Origin.
      Every endpoint below can answer 200 to curl and STILL fail in the
      browser with an opaque 'Failed to fetch'. Fix: add
      $FRONTEND_ORIGIN to API_CORS_ORIGINS in the box's .env, then
      'docker compose up -d --no-deps api'.
      NOTE: API_CORS_ORIGINS REPLACES the default list once set — it does
      not merge. Include every origin you need, dev ones too."
elif [[ "$allow_origin" == "$FRONTEND_ORIGIN" || "$allow_origin" == "*" ]]; then
  ok "CORS preflight allows the origin (Access-Control-Allow-Origin: $allow_origin)"
else
  bad "CORS preflight allowed '$allow_origin', not '$FRONTEND_ORIGIN'"
fi

actual_allow="$(curl -s -i --max-time "$CURL_TIMEOUT" -H "Origin: $FRONTEND_ORIGIN" \
  "$PUBLIC_API_URL/api/v1/projects" 2>/dev/null | tr -d '\r' \
  | grep -ic '^access-control-allow-origin:' || true)"
if [[ "${actual_allow:-0}" -ge 1 ]]; then
  ok "CORS header present on the actual GET too (not just the preflight)"
else
  bad "the preflight may pass while the real response carries no CORS header — the browser still blocks it"
fi

# ------------------------------------------------------- C: the 12 wired screens

say "==== C. Endpoints the frontend's 12 screens need ===="

expect_code "Dashboard/Projects   GET /projects"        "$PUBLIC_API_URL/api/v1/projects" "200"
expect_code "ProjectDetail        GET /datasets"        "$PUBLIC_API_URL/api/v1/datasets" "200"
expect_code "TrainingMonitor      GET /trainings"       "$PUBLIC_API_URL/api/v1/trainings" "200"
expect_code "Models               GET /models"          "$PUBLIC_API_URL/api/v1/models" "200"
expect_code "Leaderboard/Compare  GET /evaluations"     "$PUBLIC_API_URL/api/v1/evaluations" "200"
expect_code "NewProject/Templates GET /base-models"     "$PUBLIC_API_URL/api/v1/base-models" "200"
expect_code "Templates            GET /tasks"           "$PUBLIC_API_URL/api/v1/tasks" "200"
expect_code "Analytics(partial)   GET /usage"           "$PUBLIC_API_URL/api/v1/usage" "200"
expect_code "NewProject           GET /sdg-pipeline"    "$PUBLIC_API_URL/api/v1/sdg-pipeline" "200"

# Playground depends on Ollama, which is blocked by the host no-cgroups bug.
# Report it honestly rather than failing the whole run for a known outage.
inf_code="$(code_for "$PUBLIC_API_URL/api/v1/inference/models")"
if [[ "$inf_code" == "200" ]]; then
  ok "Playground           GET /inference/models → 200"
elif [[ "$inf_code" == "502" || "$inf_code" == "503" ]]; then
  note "Playground           GET /inference/models → $inf_code — Ollama is down.
      Expected while the host 'no-cgroups = true' fix is outstanding (needs
      root). Playground and ModelDetail's chat cannot work until then; this
      is NOT an ingress problem."
else
  bad "Playground           GET /inference/models → $inf_code"
fi

# WebSocket: a browser needs the Upgrade to survive Cloudflare AND the edge.
# 101 = upgraded. 403/4403 = reached the app and it refused (auth/unknown job)
# — still proof the upgrade path works. 404/502 = the proxy never upgraded.
ws_url="${PUBLIC_API_URL/https:/wss:}"; ws_url="${ws_url/http:/ws:}"
ws_probe="$(curl -s -i --max-time "$CURL_TIMEOUT" \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  "$PUBLIC_API_URL/ws/jobs/00000000-0000-0000-0000-000000000000" 2>/dev/null \
  | head -1 | tr -d '\r' || true)"
case "$ws_probe" in
  *101*)          ok "WebSocket upgrade survives Cloudflare + edge (101)" ;;
  *403*|*4403*)   ok "WebSocket reached the app and was refused ($ws_probe) — upgrade path works" ;;
  *)              bad "WebSocket handshake did not upgrade: '${ws_probe:-no response}'.
      TrainingMonitor's live progress will silently never arrive.
      Check the edge nginx location for /ws/ (Upgrade/Connection headers)
      and that the Cloudflare tunnel ingress rule covers it." ;;
esac

if [[ -n "$STORAGE_URL" ]]; then
  say "    storage subdomain: $STORAGE_URL"
  st="$(code_for "${STORAGE_URL%/}/")"
  if [[ "$st" == "000" ]]; then
    bad "storage subdomain unreachable — every presigned download URL will be
      a dead link in the browser (ADR-011: downloads go to a dedicated
      storage hostname, not through the API)"
  else
    ok "storage subdomain answers ($st) — presigned URLs have somewhere to point"
  fi
else
  note "STORAGE_URL not given — skipped. Set it once the storage hostname
      exists, or Download buttons will 'work' in curl and fail for users."
fi

# ------------------------------------------------- D: what must NOT be public

say "==== D. Negative checks — these MUST fail to connect ===="

if [[ "$(hostname -f 2>/dev/null || hostname)" == *"pporkaew"* ]]; then
  note "Running ON the box: group D is ADVISORY only. Loopback reaches
      services the internet cannot, so a 'reachable' result here may be a
      false alarm — re-run from a laptop for a real answer."
  ON_BOX=1
else
  ON_BOX=0
fi

for probe in "5432:PostgreSQL" "6379:Redis" "9000:MinIO" "9001:MinIO console" "5000:MLflow" "9090:Prometheus" "11434:Ollama"; do
  port="${probe%%:*}"; name="${probe#*:}"
  if timeout 6 bash -c "</dev/tcp/$BOX_HOST/$port" 2>/dev/null; then
    if [[ "$ON_BOX" == "1" ]]; then
      note "$name port $port open (from the box — verify externally)"
    else
      bad "$name is REACHABLE from the internet on $BOX_HOST:$port — P0 violation"
    fi
  else
    ok "$name port $port not reachable on $BOX_HOST"
  fi
done

# The two checks below ask "is this leaking?" — and an unreachable host makes
# both answer "no" for the wrong reason. A check that can pass because
# nothing is up is the exact anti-pattern this repo keeps getting bitten by,
# so they are skipped, loudly, unless group A proved the surface is live.
if [[ "$health_code" != "200" ]]; then
  note "SKIPPED the /docs and /metrics exposure checks: /health did not
      answer 200, so 'nothing leaked' would only mean 'nothing responded'.
      Re-run once group A passes — these are the checks that prove decision
      #12 and that metrics stay private."
else
  # Decision #12: /docs must not expose Swagger through the public edge. The
  # status code is NOT the check — the edge falls through to the SPA, so
  # /docs returns 200 with the SPA index. Compare bodies instead.
  root_body="$(curl -s --max-time "$CURL_TIMEOUT" "$PUBLIC_API_URL/" 2>/dev/null | head -c 2000 || true)"
  docs_body="$(curl -s --max-time "$CURL_TIMEOUT" "$PUBLIC_API_URL/docs" 2>/dev/null | head -c 2000 || true)"
  if printf '%s' "$docs_body" | grep -qi 'swagger\|redoc\|openapi'; then
    bad "/docs serves real API documentation publicly — decision #12 says the
      docs surface is not proxied through the edge"
  elif [[ -n "$root_body" && "$docs_body" == "$root_body" ]]; then
    ok "/docs falls through to the SPA — identical body to / — decision #12 holds"
  else
    note "/docs body differs from / but shows no Swagger markers — inspect manually"
  fi

  metrics_body="$(curl -s --max-time "$CURL_TIMEOUT" "$PUBLIC_API_URL/metrics" 2>/dev/null | head -c 500 || true)"
  if printf '%s' "$metrics_body" | grep -q '^slm_\|# HELP slm_'; then
    bad "/metrics is exposed publicly — it leaks queue depths, job counts and
      OpenRouter spend to anyone. It is meant to be scraped over loopback."
  else
    ok "/metrics does not serve Prometheus output publicly"
  fi
fi

# ------------------------------------------------------------------- summary

say "==== Summary ===="
printf '   passed: %s\n   failed: %s\n' "$PASS" "$FAIL"
if [[ "$FAIL" -gt 0 ]]; then
  bad "public ingress verification FAILED — do not hand the URL to the frontend team yet"
  exit 1
fi
ok "public ingress verification PASSED"
say "Next: record the result in docs/runbooks/backup_restore.md's sibling"
say "      section — docs/runbooks/public_ingress.md → Verification log."
exit 0
