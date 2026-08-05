# ADR-006 — Defer API authentication; run unauthenticated behind a private network

- **Status**: **Superseded by [ADR-009](./ADR-009-supabase-jwt-auth.md)** (2026-08-05)
- **Date**: 2026-08-04
- **Why superseded**: this record accepted "no auth" *on the condition* that the
  platform sits behind a private network. That condition was never available —
  `smart-model-tune` calls this API directly from the user's browser
  (`src/lib/engineApi.ts:2`), so the Engine is public by construction. The body
  below is left unedited as the record of what was decided and on what premise.
- **Supersedes**: the "No authentication system" row in the constraint table of
  [`../01-architecture.md`](../01-architecture.md) — that row stays true in
  practice, but its stated source no longer exists (see Context).

## Context

`BACKEND_GAP_ANALYSIS.md` (2026-07-30) lists two P0 items:

- no authentication or authorization on any HTTP endpoint
- `/ws/jobs/{job_id}` accepts any connection, using the Celery task UUID as a
  de-facto secret instead of an authorization check

Both are real. Verified in code on 2026-08-04:

- `grep -rn "owner_id|organization_id|tenant_id|X-API-Key|Authorization" --include='*.py'`
  returns **nothing** across the whole repo.
- `api/routers/websocket.py` calls `await ws.accept()` as its first statement,
  with no check of any kind.
- `api/main.py` installs only `CORSMiddleware`; there is no auth dependency
  anywhere in the router graph.

Two things complicate the decision rather than simplify it:

1. **The constraint's source document is gone.** `docs/01-architecture.md:152`
   attributes "No authentication system" to `require.md`, but that file is not
   in the repository. So the constraint cannot be re-read, re-scoped, or
   confirmed — there is no document left to appeal to.
2. **The frontend already has authentication.** `smart-model-tune` authenticates
   users through Supabase and owns its own `projects` / `trained_models` /
   `datasets` tables under Supabase RLS. Any auth we add to this backend has to
   agree with that, or users end up with two identities.

Doing auth properly therefore means: pick an identity source, add an ownership
column to `Project` and enforce it transitively across datasets, training jobs,
artifacts, evaluations and inference, gate the WebSocket, and revise a large
share of the 322 existing unit tests. That is a program of work, not a patch.

## Decision

**Defer it.** Authentication is explicitly out of scope for branch
`feat/be-fe-gap001`, which ships realtime progress recovery and job control
only. The platform continues to run unauthenticated and MUST NOT be exposed
directly to the public internet — it is reachable only via a private network,
VPN, or an authenticating reverse proxy (the `slmpc.pasaflow.com` deployment
already sits behind Cloudflare + wetty).

This is a deferral with a recorded design, not a rejection. When the work is
scheduled, the intended shape is:

- **Identity source: Supabase.** The frontend already holds a valid Supabase
  session; the backend verifies the JWT rather than minting its own. No second
  user store, no second login.
- Backend verifies `Authorization: Bearer <supabase-jwt>` against the Supabase
  project JWKS, caching keys. The `sub` claim is the user id.
- Add `owner_id` to `Project`. Every other entity reaches its owner through
  `project_id`, so ownership is enforced with a join, not five new columns.
- `/ws/jobs/{job_id}` authorizes **before** `ws.accept()`, resolving the job id
  back to its project and comparing owners. The Celery UUID stops being treated
  as a capability.
- Acceptance criterion (from `BACKEND_GAP_ANALYSIS.md`): user A gets `403` on
  every resource and every job stream belonging to user B.

## Consequences

**Accepted:**

- Anyone who can reach the API can read, launch, cancel, and delete any
  project's resources. Network isolation is the *only* control, so a
  misconfigured ingress is a total compromise. This must be stated wherever
  deployment is documented.
- The `job_id` in `websocket_url` remains security-relevant by obscurity. It is
  returned in plain HTTP responses and stored in browser `localStorage` by
  `smart-model-tune`; treat it as public.
- The new cancel endpoints added in this branch (`POST …/cancel`) are
  destructive **and unauthenticated** — the same exposure as the existing
  `DELETE /api/v1/trainings/{id}`, but a wider surface. This is a real
  widening of risk and is accepted only under the private-network condition
  above.

**Deliberately kept cheap to reverse:**

- No API shape in this branch assumes an anonymous caller. Adding a FastAPI
  dependency and an `owner_id` filter later does not require re-designing any
  endpoint added here.
- `Project.external_project_id` (migration `0005`) already links a backend
  project to its Supabase row, so the Supabase `sub` → `owner_id` mapping has a
  place to land without another schema redesign.

## Notes

The "No authentication system" row in `docs/01-architecture.md` should now cite
this ADR instead of the missing `require.md`.
