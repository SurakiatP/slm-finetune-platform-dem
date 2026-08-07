# Runbook — Secret rotation on the pasaflow VM

Everything this platform treats as a secret today lives in one place: a
`chmod 600 .env` on `scripts/deploy_pasaflow_vm.sh`'s bind-mounted disk
(`~/drive/slm-platform/.env` on `pporkaew-3090`). There is no secret
manager, no KMS, no rotation automation — that deferral is deliberate and
is recorded in ADR-011, not an oversight to "eventually fix" quietly. This
runbook is what a human does instead, by hand, when a credential needs to
change.

Why these three and nothing else: `POSTGRES_PASSWORD` / `MINIO_ROOT_PASSWORD`
/ `OPENROUTER_API_KEY` are the only credentials `scripts/deploy_pasaflow_vm.sh`
and `api/core/config.py`'s production guard both know about. Everything else
in `.env` (Supabase URL, Cloudflare tunnel ID, bucket names, quotas) is
configuration, not a secret, and rotates by just editing `.env` and
recreating the containers that read it.

## Before you start

- You need wetty access to the box (see the `deploy-slm-vm` skill for how).
- **Never** `cat`/`echo`/log the contents of `.env`. Edit it with `sed -i`
  or an editor over wetty, not by printing it to a terminal that might get
  pasted into chat.
- Confirm which containers are actually up first: `docker compose ps`.

## Postgres — `POSTGRES_PASSWORD`

**The database, not `.env`, is the source of truth for this one.** Postgres
only reads `POSTGRES_PASSWORD` from the environment when it bootstraps a
brand-new (empty) data directory — see the comment block in
`scripts/deploy_pasaflow_vm.sh`'s Phase 4 for why the deploy script itself
refuses to touch this variable once `~/drive/slm-data/postgres` is non-empty.
Editing `.env` alone does **not** change the database's actual password; it
only changes what the app *thinks* the password is, and every service that
talks to Postgres starts failing to connect with the old rows still
requiring the old password.

Order matters — **`ALTER USER` first, `.env` second**:

```bash
# 1. Change the password inside Postgres itself, while it's still running
#    with the OLD password (so this connection still authenticates).
docker compose exec -T postgres psql -U "${POSTGRES_USER:-slm}" -d "${POSTGRES_DB:-slm}" \
  -c "ALTER USER ${POSTGRES_USER:-slm} WITH PASSWORD '<new-password>';"

# 2. Only now edit .env's POSTGRES_PASSWORD to the same value. Use sed or an
#    editor over wetty — do not echo the value to the terminal.
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=<new-password>|" .env
chmod 600 .env

# 3. Recreate — NOT restart — every service that read the old DSN at
#    startup. `docker compose restart` reuses the container's existing
#    environment snapshot; `up -d` re-reads .env and replaces the
#    container, which is what actually applies the new password.
docker compose up -d --force-recreate api worker worker-cpu mlflow
```

**Name `mlflow` explicitly — do not forget it.** It is easy to remember
`api`/`worker`/`worker-cpu` and drop `mlflow` because it isn't in the
`x-app-env` anchor block by name. But `docker-compose.yml`'s `mlflow`
service builds its own DSN from `MLFLOW_DB_USER`/`MLFLOW_DB_PASSWORD` and
interpolates it into the `command:` list (`--backend-store-uri
"$$MLFLOW_BACKEND_STORE_URI"` via the `sh -c` wrapper — see the comment
above that service block for why it's `command:`, not `environment:`,
that does the interpolation). A `docker compose restart mlflow` re-execs
the *same* command string that was rendered at container-creation time, old
password baked in, so the process keeps talking to Postgres with
yesterday's credential until the container is actually recreated.

If `mlflow` uses its own role (`MLFLOW_DB_USER`, separate from
`POSTGRES_USER`), rotate it the same three-step way, substituting the
`mlflow` role name and `MLFLOW_DB_PASSWORD` in `.env`.

Verify:

```bash
docker compose exec -T api python -c "import os; print('DATABASE_URL set:', bool(os.environ.get('DATABASE_URL')))"
docker compose logs --tail=50 api worker worker-cpu mlflow | grep -i "password\|auth"
```

(that last `grep` is checking for connection-refused/auth-failure noise,
not printing the password — nothing in `.env` should ever appear in a log
line, so any match there is itself a bug to fix, not the secret leaking as
designed)

## MinIO — `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`

**The easy case.** Unlike Postgres, MinIO reads its root credentials from
the environment on every boot, not just on first init — there is no
persisted-catalog mismatch to worry about. Rotate freely:

```bash
sed -i "s|^MINIO_ROOT_PASSWORD=.*|MINIO_ROOT_PASSWORD=<new-password>|" .env
chmod 600 .env
docker compose up -d --force-recreate minio minio-init api worker worker-cpu mlflow
```

`minio-init` is included because it re-runs `mc alias set` against the new
credentials to confirm the buckets are still reachable; it exits again
after that (it's a one-shot job, not a long-running service — see
`tests/unit/test_compose_port_exposure.py`'s `MUST_HAVE_NO_PORTS`). Every
other service that authenticates to MinIO (`api`, `worker`, `worker-cpu`,
`mlflow`) needs the same recreate as the Postgres case, for the same
reason: they read `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` at process start,
not per-request.

`scripts/deploy_pasaflow_vm.sh` generates a real `MINIO_ROOT_PASSWORD`
(via `openssl rand -hex 24`) the first time it creates `.env`, precisely
because this one has no first-init-only trap — there was no reason to ever
leave it at the `minioadmin` default even on day one.

## OpenRouter — `OPENROUTER_API_KEY`

This is the flow `scripts/deploy_pasaflow_vm.sh` already implements
end-to-end (Phase 4): re-run the script, and it prompts (hidden input,
`read -rsp`, 3 attempts, format-validated against `^sk-or-v1-...$`) only
when `.env` doesn't already have a key matching that pattern. To force a
rotation without re-running the whole script:

```bash
read -rsp "New OPENROUTER_API_KEY: " NEW_KEY; echo
sed -i "s|^OPENROUTER_API_KEY=.*|OPENROUTER_API_KEY=${NEW_KEY}|" .env
unset NEW_KEY
chmod 600 .env
docker compose up -d --force-recreate api worker-cpu
```

Only `api` and `worker-cpu` consume it (SDG generation runs on the `cpu`
queue — see `workers/celery_app.py` task routing); the GPU `worker` and
`mlflow` never read this variable, so they don't need recreating.

## The VM login password itself

The plaintext password for the pasaflow VM login (not anything in this
repo's `.env`) lives in `.claude/skills/deploy-slm-vm/SKILL.md` — a
workspace file, outside this repo, in the parent `slm-platform-space/`
directory. It is already tracked as an open action in the hub's
`TASK_TRACKER.md` ("Rotate VM password (was shared in chat + stored in
SKILL.md)"). That rotation is a **human step** — logging into the box (or
its provider's console) and changing the account password, then updating
the skill file by hand — not something this script or repo can do, and
it's out of scope for anything under `slm-finetune-platform-dem/`.

## What this is not

This is not a secret manager. There is no Vault, no KMS, no automatic
rotation schedule, and no audit trail beyond `docker compose logs` and
whoever was on wetty at the time. Secrets are a `chmod 600 .env` file on a
bind-mounted disk that any process running as the same Linux user can
read. That gap is real and was deferred on purpose — see ADR-011 for the
reasoning and what would need to change (a real secret store, MinIO STS
instead of static root credentials, etc.) before this stops being true.
Nothing in this runbook closes that gap; it only makes rotating what's
here less error-prone than editing `.env` by hand and hoping every
consumer picks it up.
