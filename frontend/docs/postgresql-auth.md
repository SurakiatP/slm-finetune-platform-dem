# PostgreSQL account migration — main frontend

## Architecture

Browser → `/auth` proxy → Keycloak → PostgreSQL (`tunelab_auth`).
Browser → `/api/v1` and `/ws` proxy → Engine (its existing business database).

Only `smart-model-tune-main` is changed. `tunelab-frontend-selfhosted`, archived
nested copies, and the remote Engine remain unchanged. Supabase SDK, credential
forms, profile-table requests and Edge Function account deletion are removed
from the active frontend. Historical SQL remains in `supabase/` for reference.

`src/auth/keycloak.ts` owns Authorization Code + PKCE S256, in-memory tokens,
refresh and logout. It rejects external return destinations. Auth initialization
finishes before React Router mounts, so OIDC callback parameters are consumed
once. Refresh failure clears identity; identity changes clear the query cache.
REST, streaming POST and WebSocket all obtain a refreshed bearer token from this
same adapter. Enrolled OTP is checked by Keycloak's standard browser flow before
a token is issued. Frontend route checks do not replace Engine authorization.

## Local stack and secrets

Use the README commands with `--env-file .env.auth.local`. Port 5175 avoids the
existing self-host stack. The local file is ignored and must stay private. The
historically tracked `.env` contains frontend configuration only; `.gitignore`
does not retroactively untrack files. Never put server secrets there.

Keycloak bootstrap administrator: `KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD`
from `.env.auth.local`, at `http://localhost:5175/auth/admin/`. These are not
application-user credentials. Create application accounts using registration.

Keycloak's database password and the bootstrap password are required; there is
no fallback password. PostgreSQL is accessible only on the Docker network.
Do not use `docker compose down -v` on a stack whose accounts must be retained.
Back up the PostgreSQL volume/database before upgrades or account migration.

Realm import runs only when the realm does not exist. Editing JSON or changing
redirect-related environment variables does not update an existing realm. Apply
such updates using the admin console, then restart/rebuild as necessary.

## Engine integration

Existing `api/core/auth.py` supports generic OIDC, including issuer, JWKS,
audience and asymmetric signature verification. It chooses OIDC **instead of**
Supabase when `OIDC_ISSUER` is set. This migration does not change that policy.

For a **dedicated local Engine** on the same Docker host, merge
`docker-compose.backend-oidc.yml` from this frontend into the backend Compose
project (use the absolute path to the override):

```sh
# Run in slm-finetune-platform-dem; main auth Compose stack must already exist.
docker compose -f docker-compose.yml \
  -f ../smart-model-tune-main/docker-compose.backend-oidc.yml up -d api
```

The override adds the `tunelab-main-auth` network and configures:

```dotenv
OIDC_ISSUER=http://localhost:5175/auth/realms/tunelab
OIDC_JWKS_URL=http://tunelab-main-keycloak:8080/auth/realms/tunelab/protocol/openid-connect/certs
OIDC_AUDIENCE=tunelab-api
AUTH_REQUIRED=true
```

For a remote Engine, do not use the Docker hostname or localhost issuer above.
Set `AUTH_PUBLIC_URL` to the reachable HTTPS Keycloak base URL ending `/auth`,
configure the exact frontend origins in the realm, and set `OIDC_ISSUER` to
`<AUTH_PUBLIC_URL>/realms/tunelab`. Set JWKS to a backend-reachable endpoint
`<issuer>/protocol/openid-connect/certs`. Keep audience `tunelab-api` and
`AUTH_REQUIRED=true`. Never disable auth to work around 401 responses.

Do not apply this override to a shared Engine while any frontend still sends
Supabase tokens. Use separate Engine deployments/databases, or implement and test
dual-provider identity mapping first. Merely changing frontend URL or creating
another account does not resolve an issuer mismatch.

## User migration and ownership

No live accounts, passwords, MFA secrets or Engine rows were copied or deleted.
New Keycloak registrations have new subject IDs. A matching email does **not**
grant access to an old Supabase user's projects, datasets or models.

Before production cutover:

1. Back up Supabase account/profile data and the Engine database.
2. Inventory subjects and every owner reference (projects, datasets, usage,
   API keys, deployments and audit records), and account for retention rules.
3. Import verified accounts/profiles with a reviewed old-to-new identity map.
   Transfer ownership transactionally under that map; do not link by an
   unverified email or by username alone.
4. Arrange password resets and MFA re-enrollment unless a separately validated
   credential migration is available. Old Supabase reset links do not work here.
5. Test that owner A can still access mapped records and owner B cannot, for
   REST and authenticated WebSocket. Preserve rollback backups and the identity
   map before switching frontend traffic.

## Account features

- Registration, login, password change, profiles, active sessions and TOTP
  management use the provider's built-in pages.
- Self-service deletion is enabled through the `delete_account` required action
  and the default `self-service-account` group with only the `account/delete-account`
  role. The provider reauthenticates and confirms deletion. Deletion removes the
  Keycloak account, not Engine projects/datasets; the app states this explicitly.
- Password reset is **disabled until SMTP is configured**. In Realm settings →
  Email configure and test SMTP, then enable Forgot password in Login. Configure
  email verification before a public deployment. Recovery routes explain the
  dependency and link to provider login; they never claim to have sent an email.
- Google sign-in requires an identity-provider configuration in Keycloak. No
  Google credentials were provided or migrated; its login button is not shown
  unless that provider is configured.
- Application copy remains English/Thai. Keycloak's built-in theme uses its
  installed language bundles; a Thai Keycloak theme is separate work.

## Acceptance checks

Build, typecheck, lint and component/unit tests must pass. In a running stack,
check discovery and JWKS, registration/login/logout, profile persistence across
restart, OTP challenge after enrollment, password change/recovery, and account
console deletion. Never treat public Engine `/health` as proof of an authenticated
API or WebSocket connection. Test both with a dedicated compatible Engine.

A whole-project premium UI audit also reports pre-existing issues on unrelated
screens. Keep its report as evidence; do not claim full UI compliance from build
success or from the authentication tests alone.

## References

- [Keycloak JavaScript adapter](https://www.keycloak.org/securing-apps/javascript-adapter)
- [Keycloak PostgreSQL configuration](https://www.keycloak.org/server/db)
- [Keycloak account administration](https://www.keycloak.org/docs/26.2.4/server_admin/)
