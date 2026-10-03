# Runbook: Supabase → Keycloak migration (dual auth + identity links)

The new frontend logs in through Keycloak (realm `tunelab`, client
`tunelab-web`, audience mapper `tunelab-api`). The old frontend still uses
Supabase. During the migration both frontends share one `slm-api`, so the
backend trusts both providers at once and maps each Keycloak login to an
Engine actor id.

## How the backend decides who you are

1. **Choose the provider.** `api/core/auth.py` reads the token's `iss` without
   verifying it. That value is only used to pick a provider from the
   configured allowlist:
   - `OIDC_ISSUER` (Keycloak)
   - `{SUPABASE_URL}/auth/v1` (Supabase)

   If `iss` matches neither, the request gets a 401. If verification against
   the chosen provider fails, it is a 401 and the token is not retried
   against the other provider.
2. **Verify against that provider only.** The chosen provider's JWKS (one
   cached client per URL) checks the signature, `iss`, `aud`, `exp`, `sub`,
   and `nbf` when present. Keycloak tokens must also use an RS/ES/PS
   algorithm.
3. **Resolve the actor id** (`api/services/identity_links.py`). This is the
   value that goes into `CurrentUser.id`, every `owner_id`, and every
   ownership check:
   - **Supabase:** the actor id is the `sub` itself. All pre-migration data is
     owned by these ids.
   - **Keycloak:** look up `identity_links(issuer, subject)`. If a row exists,
     use its `actor_id`. Otherwise create a new random actor id and store it.
     That gives a new user an empty account. It also means a Keycloak `sub`
     cannot match an existing owner id by coincidence and inherit that data.

REST (`current_user_optional`) and WebSocket (`_authorize`) both go through
`authenticate()`, so they always agree on the caller. `sk-slm-` API keys
carry their own `owner_id` and do not use any of this.

**Nothing is linked by email.** The only way to point a Keycloak identity at
existing data is the operator script below.

## Pre-flight (frontend team owns these)

- [ ] Keycloak is running from `docker-compose.production.yml` (project
      `tunelab-main`).
- [ ] `AUTH_PUBLIC_URL` is a **stable** public HTTPS URL. Do not use a
      `trycloudflare.com` quick tunnel: its URL changes on every restart, and
      `issuer` changes with it.
- [ ] The frontend runs with `AUTH_UPSTREAM=keycloak:8080`, not
      `slm-edge:80`.
- [ ] This returns `Content-Type: application/json`:
      `curl -sD- https://<FRONTEND_HOST>/auth/realms/tunelab/.well-known/openid-configuration`.
      Record its `issuer` exactly.
- [ ] A decoded real access token has `aud` containing `tunelab-api`, and its
      `iss` equals the recorded `issuer` byte for byte.

## Deploy the backend

1. Take a backup with `scripts/backup.sh`, and note the current `slm-api`
   image tag for rollback.
2. Deploy an image that contains `237ff01` (dual provider) and the
   identity-links commit. Run `alembic upgrade head`, which brings the schema
   to `0016_identity_links`.
3. Add these to the `slm-api` env. Keep the Supabase vars as they are.

   ```dotenv
   OIDC_ISSUER=<issuer from discovery, exact>
   OIDC_JWKS_URL=http://tunelab-main-keycloak:8080/auth/realms/tunelab/protocol/openid-connect/certs
   OIDC_AUDIENCE=tunelab-api
   AUTH_REQUIRED=true
   ```

4. Attach `slm-api` to the `tunelab-main-auth` Docker network so it can
   reach the JWKS URL. **Do not** merge the frontend repo's
   `docker-compose.backend-oidc.yml` as is: it defaults `OIDC_ISSUER` to
   `http://localhost:5175/...`.
5. Check the old frontend first. Supabase login should still work, and
   projects, models, trainings, and tasks should all load.

## Link a migrated user

Do this only after confirming **outside the system** (in person or in the
team chat) that one person holds both the Supabase account and the Keycloak
account.

1. Find their old actor id: the Supabase `sub`, which appears as `owner_id`
   in `projects`.
2. They register in Keycloak. Copy their Keycloak `sub` from Keycloak admin →
   Users → ID.
3. Preview the change, then apply it:

   ```bash
   docker exec slm-api python scripts/link_identity.py \
     --issuer "<OIDC_ISSUER>" --subject <keycloak-sub> --actor-id <supabase-sub>
   # read the output, then
   docker exec slm-api python scripts/link_identity.py \
     --issuer "<OIDC_ISSUER>" --subject <keycloak-sub> --actor-id <supabase-sub> --apply
   ```

   If the Keycloak account already logged in before it was linked, it was
   given an empty auto-created actor. The script re-points that link. It
   refuses (exit 1) if that auto actor already owns data, because re-pointing
   would orphan it. In that case move or delete the data first, or pass
   `--force`.

4. Have them reload. Their next request resolves to the old actor id.

`--issuer` must equal `OIDC_ISSUER` exactly. If the public hostname changes,
every link has to be re-created under the new issuer. That is another reason
the hostname has to be stable.

## Acceptance (from the frontend team's migration doc)

| Case | Expected |
|---|---|
| Supabase token → `GET /api/v1/projects` | 200, old projects |
| Linked Keycloak token → same | 200, the same projects |
| No token or invalid token | 401 |
| Another user opens a project id of the test account | 403 |
| WebSocket for your own job / someone else's job | connects / close 4403 |

All five are covered offline by `tests/unit/test_identity_links.py`. Re-run
them by hand on the real stack, and never log or commit the tokens you use.

## Switch and rollback

- Point production traffic (currently the ngrok `:8082` frontend) at the
  Keycloak frontend only after every acceptance row passes on the real stack.
- Keep Supabase configured until no Supabase traffic remains. Remove
  `SUPABASE_URL` in a separate release.
- Rollback order: point traffic back at the old frontend first. Then, if
  needed, roll `slm-api` back to the noted tag. Migration `0016` is additive,
  so the old image ignores the table.
- Never delete the Keycloak or Postgres volumes during a rollback.
