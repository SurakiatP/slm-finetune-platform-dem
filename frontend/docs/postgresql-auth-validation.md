# Main frontend PostgreSQL authentication validation

Date: 2026-10-02 (Asia/Bangkok)

## Verified

| Check | Result |
| --- | --- |
| `npm test -- --reporter=dot` | 56 tests passed, 17 files |
| `npx tsc --noEmit -p tsconfig.app.json` | Passed |
| `npm run lint` | Passed |
| `npm run build` including strict i18n | Passed; 440 EN/TH keys, no missing/unused keys |
| Docker Compose configuration and frontend image build | Passed |
| PostgreSQL and Keycloak startup | Passed, imported `tunelab` realm |
| OIDC discovery | Correct issuer `http://localhost:5175/auth/realms/tunelab` |
| Browser registration and profile | Created disposable user; profile displayed in main frontend |
| Browser login/session reload/account management | Passed in headless Google Chrome |
| Access token | RSA signature verified against live JWKS; issuer and `tunelab-api` audience matched |
| Token storage | Access token absent from browser localStorage |
| PostgreSQL storage | Application account present in `user_entity` |
| Container restart | PostgreSQL + Keycloak restarted; discovery recovered and account persisted |
| MFA | Enrolled TOTP; invalid OTP rejected; valid fresh OTP completed login |
| Logout | Returned to public page; protected settings required login again |
| Account deletion | Warning, password + OTP reauthentication, final confirmation and successful deletion verified; PostgreSQL application-user count returned to zero |
| Responsive UI | English 1280×800 light and Thai 390×844 dark login checked; keyboard focus and no horizontal overflow |
| Git whitespace check | Passed with `core.whitespace=cr-at-eol` for existing CRLF files |

The browser checks used a newly created local test account, not an existing
Supabase account. The disposable account was deleted after testing. No remote users or Engine data were changed.

## Remaining limits

- Engine authenticated REST/WebSocket integration has **not** been verified.
  The existing shared backend still needs a deliberate OIDC/dual-provider rollout;
  public health, a signed JWT and successful login do not prove Engine access.
- Existing Supabase users and ownership records have not been migrated.
- SMTP password recovery/email verification and Google federation need operator
  configuration. They were not tested with external services.
- Provider-hosted pages use installed Keycloak language bundles; only the
  application's account entry is verified in Thai.
- Whole-project premium audit still fails on **43 pre-existing findings**, down
  from **52** on the untouched Git HEAD baseline. Comparing `(file, ruleId)` found
  no new issues. See `../premium-audit.json`; unrelated screens were not redesigned.
- Vite reports its existing large-bundle and old Browserslist-data warnings.

Local stack is available at http://localhost:5175. Use the private ignored
`.env.auth.local` for restart commands and administrator credentials. See
[configuration and migration guide](postgresql-auth.md).
