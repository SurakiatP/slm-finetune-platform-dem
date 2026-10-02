# TuneLab frontend

The main frontend uses **Keycloak + PostgreSQL** for accounts, profiles, passwords,
MFA and sessions. React connects to a public OIDC client; it never receives a
PostgreSQL connection string or password. Business data still uses the Engine API.

## Run locally

1. Install Docker and Node.js, then run `npm ci`.
2. For a new setup, copy `.env.example` to **`.env.auth.local`** and set unique
   `AUTH_DB_PASSWORD` and `KEYCLOAK_ADMIN_PASSWORD` values. Preserve an existing file.
   This ignored file contains server secrets; do not add them to `VITE_*` or to the
   repository's historically tracked `.env` file.
3. Run `docker compose --env-file .env.auth.local up -d --build`.
4. Open **http://localhost:5175**. Choose Create account or Sign in.
5. For Vite hot reload, run `npm run dev` and open http://localhost:5173.
   Vite forwards `/auth` to the Compose frontend on port 5175. Both origins are
   registered in the realm. The self-host frontend is a separate project.

The PostgreSQL service has no published host port. Its data persists in the
`auth-postgres` volume. Keycloak creates and migrates its own tables.

**Engine integration requires OIDC configuration.** An existing Supabase-only
backend will reject these tokens until its verifier is configured. Changing the
shared backend to OIDC also stops accepting the self-host frontend's Supabase
tokens. Use a dedicated backend or implement dual-provider identity handling
before cutting over a shared deployment. No remote backend has been changed.

See [PostgreSQL authentication and migration](docs/postgresql-auth.md) for backend
configuration, account migration, SMTP, MFA, account deletion and rollout checks.

## Verification

```sh
npm test
npx tsc --noEmit -p tsconfig.app.json
npm run lint
npm run build
```

See the [validation report](docs/postgresql-auth-validation.md) for local browser checks and remaining integration limits.

The application uses React, TypeScript, Vite, Tailwind and shared shadcn/ui
components. Login and account-security forms are hosted by Keycloak. Old
`supabase/` migrations remain only as historical migration input; no Supabase
SDK is imported by the active frontend.
