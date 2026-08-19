# Supabase account security deployment

The frontend implements password changes, TOTP MFA, MFA login challenges, and account deletion. Password and MFA calls use the signed-in user's Supabase session directly. Permanent account deletion additionally requires the included database migration and Edge Function.

Target project:

```text
mqoftavekwbsgpwzjdfb
```

## Dashboard settings

The Supabase project owner must configure the following under **Authentication → Providers → Email**:

- Keep Email authentication enabled.
- Enable **Secure password change**.
- Keep TOTP enrollment and verification enabled under the MFA settings.

## Deploy migration and function

Run from the frontend repository root with a Supabase account that has access to the target project:

```bash
supabase login
supabase link --project-ref mqoftavekwbsgpwzjdfb
supabase db push --dry-run
supabase db push
supabase functions deploy delete-account
```

Hosted Edge Functions receive `SUPABASE_URL`, `SUPABASE_SECRET_KEYS`, and the legacy `SUPABASE_SERVICE_ROLE_KEY` from Supabase. Do not add any of these secret values to the frontend `.env`, Docker build arguments, source control, or browser code.

## Verification

1. Sign in with an email/password test account.
2. Change its password. If the session is older than the secure-change window, confirm the emailed nonce flow works.
3. Enroll a TOTP authenticator, sign out, and confirm the next login is held at `/mfa` until a valid code is entered.
4. Open Settings → Account → Delete Account and type the exact signed-in email.
5. Confirm the user disappears from Authentication → Users and their rows disappear from `profiles`, `projects`, `datasets`, `trained_models`, `deployed_endpoints`, `api_keys`, and `api_call_events`.

FastAPI Engine jobs and artifacts are intentionally outside this deletion flow and remain unchanged.
