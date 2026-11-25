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

Backend `a3739f3` supports Supabase and Keycloak concurrently. It selects exactly
one verifier by the token's `iss`; an unknown issuer or failed verification
returns 401 without falling back to the other provider. The existing Supabase
frontend remains supported. Per the 2026-10-03 backend handoff, this support is
deployed on slmpc, but `OIDC_*` configuration awaits the permanent Keycloak issuer.
The backend team owns that configuration and network attachment; do not apply a
frontend Compose override to the shared Engine or change its authentication mode.

Keycloak access tokens must have an exact issuer match, an audience containing
`tunelab-api` (the realm's `tunelab-api-audience` mapper), an RS/ES/PS signature,
and both `exp` and `sub`. Send the fresh access token through the existing REST
adapter and WebSocket subprotocols `['bearer', token]`. API keys prefixed
`sk-slm-` are unaffected. Never disable authentication to work around a 401.

## User migration and ownership

`owner_id` is the Engine actor ID, not the Keycloak subject. Never compare it to
frontend `user.id`. Supabase users retain their existing `sub` as their actor ID.
A linked Keycloak identity uses that same actor ID and sees the original data;
an unlinked identity receives a new random actor ID and starts with no data.

Only the backend administrator links verified identities through
`scripts/link_identity.py` and `identity_links`. Matching email addresses do not
link accounts. Do not rewrite ownership rows from the frontend. Verify that the
old and new accounts belong to the same person before requesting a link.
The handoff identifies one existing owner requiring linkage:
`02fda8bd-d111-4e1d-af2e-121ce36d8559`; do not infer that person's identity.

## Deploy the frontend auth stack on slmpc

Run as `slmuser` using rootless Docker, from the deployed frontend checkout.
Keep `docker-compose.production.yml` and `docker/keycloak/tunelab-realm.json`
together. Do not edit the backend repository, `~/drive/slm-platform`, the
`slm-platform` Compose project, or backend containers. Preserve the production
`slm-frontend` on port 8082 and its ngrok tunnel throughout this rollout.

1. **Before the first Keycloak boot, obtain an approved permanent HTTPS hostname.**
   Use a named tunnel or reserved domain. If none is available, stop and ask the
   operator; do not invent one or use `*.trycloudflare.com` or another rotating URL.
   Set `FRONTEND_ORIGIN=https://<host>` and `AUTH_PUBLIC_URL=https://<host>/auth`.
   Realm import happens once. A later hostname change requires explicit realm
   redirect updates and backend review of issuer configuration and identity links.
2. Build/publish a versioned frontend image from the reviewed main revision, then
   create `~/tunelab/.env.production` privately on the server with mode `600`.
   Preserve any existing file and credentials. Start from the checked-in
   [production environment template](../.env.production.example), replace every
   placeholder, and generate new secrets on the server. Never reuse credentials
   from `Data.txt`:

   ```dotenv
   FRONTEND_IMAGE=sady567/slm-frontend:<reviewed-main-tag>
   FRONTEND_ORIGIN=https://<host>
   AUTH_PUBLIC_URL=https://<host>/auth
   AUTH_DB_PASSWORD=<new-random-secret>
   KEYCLOAK_ADMIN_PASSWORD=<new-random-secret>
   ```

   Keep secrets out of terminal output, shell history, logs and commits. Do not
   print the env file or expanded Compose configuration. Defaults are
   `ENGINE_UPSTREAM=slm-edge:80`, `ENGINE_UPSTREAM_HOST=edge`, and
   `ENGINE_DOCKER_NETWORK=slm-platform_slm-net`. Leave `AUTH_UPSTREAM` owned by
   Compose as `keycloak:8080`; pointing `/auth/` at the Engine returns HTML.
3. Start the frontend-owned Compose stack (do not start its services manually):

   ```sh
   chmod 600 ~/tunelab/.env.production
   docker compose -p tunelab-main -f docker-compose.production.yml --env-file ~/tunelab/.env.production up -d
   docker compose -p tunelab-main -f docker-compose.production.yml --env-file ~/tunelab/.env.production ps
   docker network ls --filter name=tunelab-main-auth
   ```

4. Route the approved hostname to `127.0.0.1:5175`, or the explicitly configured
   `FRONTEND_PORT`. Use a separate route without changing the old production tunnel.
5. Verify the new stack below. Only after it works may the frontend-owned
   `slm-frontend-postgres-candidate` on port 8083 be stopped. Do not stop or change
   `slm-frontend` on port 8082. Never run `docker compose down -v` or delete the
   `auth-postgres` volume.

### Discovery and handoff

Replace `<host>` with the approved hostname. These commands read public metadata:

```sh
curl -fsS -D /tmp/tunelab-discovery.headers 'https://<host>/auth/realms/tunelab/.well-known/openid-configuration' -o /tmp/tunelab-discovery.json
grep -i '^content-type:' /tmp/tunelab-discovery.headers
python3 -c "import json; d=json.load(open('/tmp/tunelab-discovery.json')); print(d['issuer']); print(d['jwks_uri'])"
docker run --rm --network tunelab-main-auth curlimages/curl -fsS -o /dev/null -w '%{http_code}\n' \
  http://tunelab-main-keycloak:8080/auth/realms/tunelab/protocol/openid-connect/certs
```

Discovery must return `application/json`, the exact public issuer
`https://<host>/auth/realms/tunelab`, and a reachable JWKS URL. The internal JWKS
probe must return 200. `tunelab-main-keycloak` is the Compose network alias;
report the actual container name from `compose ps` as well as that alias.

Register one test account through the new frontend. Inspect its access-token
payload locally and record only `iss`, `aud`, and `sub`; never paste the token
into a website, terminal output, logs or a message. Check `aud` contains
`tunelab-api`. If an administrator creates an account, require `UPDATE_PASSWORD`
and a password of at least 12 characters; reset email remains disabled without SMTP.

Prepare this handoff for the backend team without guessing missing values:

```text
1. issuer: <exact discovery issuer>
2. keycloak container / network: <actual container; alias tunelab-main-keycloak> / tunelab-main-auth
3. decoded claims only: iss=<...> aud=<...> sub=<...>
4. Supabase 02fda8bd-d111-4e1d-af2e-121ce36d8559 belongs to: <verified owner>
5. Keycloak sub for the same owner: <Users → ID>; same-person verification: <method/result>
6. Production cutover date and URL: <agreed date and URL>
7. Frontend PR: <link>
```

The backend team sets `OIDC_ISSUER`, `OIDC_JWKS_URL`, and
`OIDC_AUDIENCE=tunelab-api`, attaches the auth network, and links the verified
identities. Frontend operators must not perform those backend changes.

### Joint acceptance and rollback

Before switching traffic, validate against the real stack together:

| Case | Expected result |
| --- | --- |
| Existing Supabase token → `GET /api/v1/projects` | 200, original projects |
| Linked Keycloak token → same endpoint | 200, same projects |
| Missing or invalid token | 401 |
| Unlinked Keycloak account opens the test owner's project | 403 |
| WebSocket for own / another owner's job | Connects / rejected with 4403 by the server |
| New Keycloak account creates a project | Visible only to that owner |

Browsers may expose a rejected pre-accept WebSocket handshake as a generic
failure; use server-side evidence for the 4403 check. Login or public health
alone does not prove Engine access. Agree on the production URL and cutover time
only after these checks. Roll back traffic to the old frontend first; retain
Keycloak and PostgreSQL volumes and identity mappings.

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
API or WebSocket connection. Complete the joint dual-provider checks above.

A whole-project premium UI audit also reports pre-existing issues on unrelated
screens. Keep its report as evidence; do not claim full UI compliance from build
success or from the authentication tests alone.

## References

- [Keycloak JavaScript adapter](https://www.keycloak.org/securing-apps/javascript-adapter)
- [Keycloak PostgreSQL configuration](https://www.keycloak.org/server/db)
- [Keycloak account administration](https://www.keycloak.org/docs/26.2.4/server_admin/)
