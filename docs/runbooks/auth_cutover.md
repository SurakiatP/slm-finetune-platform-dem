# Runbook — auth cutover (`AUTH_REQUIRED=false` → `true`, dev → production)

Wave 0 of this hardening pass (`api/core/config.py`'s
`_reject_unsafe_production_config`) made `ENVIRONMENT=production` combined
with `AUTH_REQUIRED=false` a **fatal boot error** — the process refuses to
start at all, not just log a warning. That is deliberate: it makes it
structurally impossible to promote this deployment to `production` while
every request is still anonymous and every row it creates has
`owner_id IS NULL`. This runbook is how an operator walks through that gate
safely, in the order that makes it safe.

**The order below is load-bearing.** Doing the steps out of sequence — in
particular, flipping `AUTH_REQUIRED` before the frontend sends tokens, or
flipping `ENVIRONMENT` and `AUTH_REQUIRED` separately instead of together —
reproduces exactly the outage this guard exists to prevent. Follow it in
order even if a later step looks skippable.

## Why this is more than "just set two env vars"

Today the box runs `ENVIRONMENT=dev` with `AUTH_REQUIRED=false`. Two
separate credential guards exist to protect a real `production` boot — the
MinIO/DB default-credential check and the auth check — but **both are inert
on `dev`**: `_reject_unsafe_production_config` only runs its checks when
`self.environment == "production"` (`api/core/config.py:283-284`). That is
fine as long as the box is not reachable from the public internet. It stops
being fine the moment it is.

## Step 1 — Cloudflare Access in front of both hostnames, before anything else is published

Put both hostnames (the app hostname and the storage subdomain) behind
Cloudflare Access (Zero Trust — the free tier covers up to 50 users) before
either is exposed to the internet at all, and before any other step below.

This is not a nice-to-have — it is the only thing standing between "box on
the internet" and "box on the internet with every guard turned off,"
because of the specific order this cutover has to happen in: the fatal
guard forces the box to stay on `ENVIRONMENT=dev` until the frontend patch
in `docs/04-frontend-integration-smart-model-tune.md` ships (see Step 5),
and while it's on `dev` **both** credential guards are inert — not just the
auth one. A publicly reachable box on `dev` (default MinIO credentials,
`slm:slm` Postgres credentials, every request anonymous) is strictly worse
than the current state of not being published at all. Cloudflare Access is
what covers that window. Do not publish either hostname without it first.

## Step 2 — get the Supabase `sub` UUID for the owning account

The backfill script needs a real Supabase auth user id (the JWT `sub`
claim) to assign as `owner_id`. Get it one of two ways:

- Decode an existing session JWT's payload (the middle base64url segment)
  and read the `sub` field — no signature verification needed for this,
  you're just reading a claim you already trust from your own session.
- Or look it up directly in the Supabase dashboard under
  Authentication → Users.

Keep this UUID handy for Step 3.

## Step 3 — run the backfill: `--dry-run` first, then `--apply`

The script is `scripts/backfill_project_owner.py`. `--dry-run` is
effectively the default — `--apply` is the only flag that writes anything,
and the two are mutually exclusive on the CLI. It talks to Postgres with
plain sync `psycopg2` and needs `DATABASE_URL` or `ALEMBIC_DATABASE_URL` set
in its environment (same DSN resolution as `alembic/env.py`).

The `api` container already has `DATABASE_URL`/`ALEMBIC_DATABASE_URL` set
(from `docker-compose.yml`'s `x-app-env` block), so running the script
inside it via `docker compose exec` needs no extra env wiring:

```bash
# Preview only — always do this first. Prints every projects row with
# owner_id IS NULL (id, created_at, name), up to --preview-limit (default 20).
docker compose exec api \
  python scripts/backfill_project_owner.py --owner-id <sub-uuid> --dry-run

# Eyeball the listed rows. If everything printed is a legacy/demo project
# that should belong to this account, apply:
docker compose exec api \
  python scripts/backfill_project_owner.py --owner-id <sub-uuid> --apply
```

Notes on the real CLI (verified against `scripts/backfill_project_owner.py`,
not assumed):

- Only `projects.owner_id` is written. `Dataset`/`TrainingJob`/
  `ModelArtifact`/`EvaluationRun` all reach ownership by FK join through
  `Project` (see `api/services/ownership.py`'s module docstring), so there
  is no second table to backfill.
- `--project-id UUID` is repeatable and restricts the run to specific rows;
  omit it to target every row with `owner_id IS NULL`.
- The `WHERE owner_id IS NULL` clause is on both the preview `SELECT` and
  the `UPDATE`, so a re-run after a successful apply touches zero rows —
  safe to run again if you're not sure whether it already ran.
- The script exits non-zero on a bad UUID, an unset DSN, or a connection
  failure, so it's safe to use as a gate in an automated deploy step, not
  just interactively.
- If you're running it outside a container with a local venv instead:
  `.venv/bin/python scripts/backfill_project_owner.py --owner-id <uuid> --dry-run`.

## Step 4 — verify zero null owners remain

```bash
docker compose exec postgres psql -U ${POSTGRES_USER:-slm} -d ${POSTGRES_DB:-slm} \
  -c "SELECT count(*) FROM projects WHERE owner_id IS NULL;"
```

Must return `0`. If it doesn't, go back to Step 3 — either the `--apply` run
didn't cover every row (check for a stale `--project-id` filter) or new
`owner_id IS NULL` rows were created after the backfill ran (a request came
in while `AUTH_REQUIRED` was still `false`). Do not proceed to Step 6 until
this is `0`.

## Step 5 — confirm the frontend actually sends `Authorization`

Do not take "the frontend patch shipped" on faith — curl the API with a
real token and diff the response against an anonymous call:

```bash
# Anonymous — works today under AUTH_REQUIRED=false
curl -s -o /dev/null -w "%{http_code}\n" https://<app-host>/api/v1/projects

# With a real Supabase session token
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer <access_token>" \
  https://<app-host>/api/v1/projects
```

Both should currently return the same success status (auth is optional
while `AUTH_REQUIRED=false`) — what you're checking is that the
`Authorization` header actually leaves the browser, not just that the API
accepts it. Use the browser network tab against the real frontend, not just
curl, to confirm requests genuinely carry the header end to end.

**This is a frontend-team deliverable, not something shippable from this
repo.** The required frontend patch — `engineApi.ts`'s `apiFetch` plus the
three hand-rolled `fetch` calls, and the `Sec-WebSocket-Protocol: bearer`
subprotocol on the training WebSocket — is fully specified in
`docs/04-frontend-integration-smart-model-tune.md` ("Required Frontend
Change — send the Supabase token"). Track its status there, not here.

## Step 6 — flip both flags together, then restart

Once Step 4 is `0` and Step 5 is confirmed, set **both** in `.env`:

```bash
ENVIRONMENT=production
AUTH_REQUIRED=true
```

`AUTH_REQUIRED=true` also requires `SUPABASE_URL` or `SUPABASE_JWT_SECRET`
to be set — the same guard rejects `AUTH_REQUIRED=true` with neither
present, because there would be no JWKS and no HS256 fallback to verify a
token against.

Then recreate (not just `restart`) the `api` container so it actually picks
up the new `.env` values — `docker compose restart` does not reread
`env_file`, only `up -d` does:

```bash
docker compose up -d api worker worker-cpu
```

**Why together, not one then the other**: this is exactly what the fatal
guard in `api/core/config.py` prevents structurally — `ENVIRONMENT=production`
with `AUTH_REQUIRED` still `false` refuses to boot, so there is no
intermediate state where the box is `production` but still anonymous. Doing
it the other way — `AUTH_REQUIRED=true` while still `ENVIRONMENT=dev` — boots
fine, but per Step 5's standing constraint in `docs/04-frontend-integration-smart-model-tune.md`
("ship the frontend changes first, confirm tokens are arriving, then flip.
Reversing that order takes the product down."), flipping `AUTH_REQUIRED`
before the frontend patch has shipped is what takes the product down: see
the "what you will see" section below for the exact failure mode.

## Rollback

Flip both back to `ENVIRONMENT=dev` and `AUTH_REQUIRED=false` in `.env`,
then `docker compose up -d api worker worker-cpu` again.

Rows that were backfilled stay owned — that's harmless to roll back over,
not something that needs undoing. `_check_owner` in
`api/services/ownership.py:88-105` no-ops entirely when `user is None`
(the very first line of the function), so with `AUTH_REQUIRED=false` every
row is visible to every anonymous caller regardless of whether
`owner_id` is null or set to a real UUID. The backfill only matters once
`user` starts being non-`None` on every request, i.e. once `AUTH_REQUIRED`
is `true` again.

## What you will see if it goes wrong

**Booting with `ENVIRONMENT=production` and `AUTH_REQUIRED=false`** — the
container fails to start at all. The actual error raised by
`_reject_unsafe_production_config` (`api/core/config.py:321-326`) is:

```
ENVIRONMENT=production but these still hold the values shipped in
.env.example: AUTH_REQUIRED=false — every request would be anonymous and
every row it creates would have owner_id NULL. Back-fill existing rows with
scripts/backfill_project_owner.py, ship the frontend Authorization header,
then set AUTH_REQUIRED=true.. Set real secrets, or use ENVIRONMENT=dev/staging
if this is not a production deployment.
```

(The double period before "Set real secrets" is not a copy-paste error in
this runbook — it's a cosmetic artifact of how `_reject_unsafe_production_config`
joins offender strings in `api/core/config.py`, reproduced here verbatim so
it doesn't look like a typo when you actually see it.)

(If MinIO/DB credentials are also still default, or `AUTH_REQUIRED=true`
is set with neither `SUPABASE_URL` nor `SUPABASE_JWT_SECRET`, those
offenders are listed in the same error — the validator collects every
offender before raising, it does not stop at the first one.)

**Flipping `AUTH_REQUIRED=true` while the frontend does not actually send
`Authorization`** — the container boots fine (this combination is legal),
but every Engine call the frontend makes now gets a `401`, while
`GET /health` keeps returning `200` because it has no auth dependency. That
means **no "Engine unreachable" banner fires** — there is no such banner
wired up at all (`docs/04-frontend-integration-smart-model-tune.md` notes
`engineHealthCheck` is defined but never called from `src/`), and even if
there were, `/health` staying green would mask the outage rather than
surface it. This was proven by wire replay on real infrastructure and is
recorded in the workspace `TASK_TRACKER.md`
("⚠️ Frontend must send the token before `AUTH_REQUIRED` can be flipped" —
confirmed by wire replay 2026-08-06: phase 1 works untouched; phase 2 →
every Engine call 401s and the socket is refused, while `GET /health` still
200s). If you flip and then see this symptom, flip back immediately (see
Rollback) — the frontend patch has not actually shipped yet, whatever the
deploy ticket said.

**A caller hits another user's project after the cutover** — they get a
`404`, not a `403`, with a message identical to a genuine missing row
(`api/services/ownership.py:33-41`, `_not_found`). This is intentional, not
a bug: a `403` would let one user enumerate the existence of another user's
projects/datasets/trainings/models/evaluations by response code alone. Do
not "fix" this into a 403.
