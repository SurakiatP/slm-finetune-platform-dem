# ADR-009 — Verify Supabase JWTs and enforce per-user ownership

- **Status**: Accepted
- **Date**: 2026-08-05
- **Supersedes**: [ADR-006](./ADR-006-defer-authentication.md)

## Context

ADR-006 deferred authentication, and it was explicit about the condition that made
deferral acceptable: the platform "MUST NOT be exposed directly to the public internet —
it is reachable only via a private network, VPN, or an authenticating reverse proxy."

**That condition cannot hold.** Reading `smart-model-tune` settles it:

- `src/lib/engineApi.ts:2` — `ENGINE_HOST = import.meta.env.VITE_ENGINE_HOST`; every call
  is a `fetch` **from the user's browser** straight to this API.
- `src/hooks/useTrainingWebSocket.ts:60` — same for the WebSocket URL.

There is no server-side proxy in between. For the frontend to work at all, the Engine
must be reachable from every user's browser, which is the opposite of a private network.
ADR-006's mitigation was never available.

The exposure that follows is not theoretical. Supabase enforces per-user isolation on its
own tables (RLS, `user_id`, `auth.uid()` — 60+ policy references across
`supabase/migrations/`), and `src/pages/Signup.tsx` lets **anyone self-register** with no
invite gate. So the product is multi-user by construction, while the Engine has no concept
of a user at all. Any logged-in person can open devtools and:

- `GET /api/v1/projects` — read every other user's projects
- `POST /api/v1/trainings` — spend GPU time against someone else's dataset
- `POST /api/v1/datasets/{id}/cancel`, `DELETE /api/v1/datasets/{id}` — destroy another
  user's work; ADR-006 itself flagged that the cancel endpoints widened this surface
- `GET /ws/jobs/{job_id}` — stream anyone's job, using the Celery UUID as a de-facto secret

This is a **correctness defect in the integration**, not deferred hardening. The
acceptance criterion is the one `BACKEND_GAP_ANALYSIS.md` already wrote: *user A must be
refused on every resource and every job stream belonging to user B.*

## Decision

Verify Supabase JWTs in the backend and enforce ownership per project.

### Identity — Supabase, not a second user store

The frontend already holds a valid Supabase session; the Engine verifies that token rather
than minting its own. `VITE_SUPABASE_PUBLISHABLE_KEY` is Supabase's **new** key format,
which pairs with **asymmetric** signing, so verification is against the project's JWKS at
`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`, checking signature, `exp`, `aud`
(`authenticated`) and `iss`. An HS256 shared-secret path is kept for projects still on
legacy symmetric keys, used only when no `SUPABASE_URL` is configured. There is no local
users table and there will not be one — `Project.owner_id` stores the `sub` claim as an
opaque string, not a foreign key.

### Rollout — two phases, because we cannot edit the frontend

`smart-model-tune` is Lovable-managed and out of this workspace's edit scope, and today
`apiFetch` (`engineApi.ts:147-152`) sends no `Authorization` header at all. Turning auth on
in one step would 401 every request the moment it deployed.

So `AUTH_REQUIRED` (default `false`) splits it:

1. **Phase 1** — a token is verified when present, but a request without one is still
   served anonymously and every ownership check is a no-op. Deploying changes nothing.
2. The frontend team ships two call-site changes (documented in
   `docs/04-frontend-integration-smart-model-tune.md`).
3. **Phase 2** — flip `AUTH_REQUIRED=true`.

**In both phases, a token that is present but fails verification is a `401`.** "Optional"
tolerates absence, not garbage. That distinction is the whole security value of phase 1;
without it, phase 1 would accept forged tokens.

The proof that phase 1 is genuinely compatible is mechanical: the pre-existing test suite
passes **unmodified**. If any existing test had needed editing, the default would be wrong.

### WebSocket credential — `Sec-WebSocket-Protocol`

Browsers cannot set headers on `new WebSocket()`. The token travels as the handshake
subprotocol, `new WebSocket(url, ["bearer", "<jwt>"])`, and the server echoes
`subprotocol="bearer"` on accept — omit that echo and the browser closes the connection
immediately. A `?token=` query parameter was rejected: it would put credentials in nginx
access logs and browser history.

Authorization happens **before** `ws.accept()`, closing the un-accepted socket rather than
accepting and hanging up. Unlike the HTTP side, a subprotocol that is offered but
malformed is rejected rather than treated as absent — an `Authorization` header can be
mangled by proxies we do not control, but a WebSocket subprotocol is only ever set by our
own client code, so anything unexpected there is a caller error. Folding the two cases
together would let a client bypass phase-2 auth by offering deliberate garbage.

### Ownership — one column, joined through

`owner_id` lives on `Project` only. Every other entity reaches it by foreign key:
`Dataset`/`TrainingJob` → `Project`; `ModelArtifact` → `TrainingJob` → `Project`;
`EvaluationRun` → `ModelArtifact` → `TrainingJob` → `Project`. One column, one migration,
and no way for a child row's owner to drift out of sync with its parent's — at the cost of
a three-hop join on the evaluation path, which is not on any hot path.

Two rules that look like bugs and are not:

- **Another user's existing resource returns `404`, not `403`.** A `403` confirms the
  resource exists, turning every endpoint into a probe for other people's ids. The detail
  string matches the genuine not-found case exactly.
- **`owner_id IS NULL` is visible to nobody** once a caller is authenticated. Rows created
  before this migration have no owner and there is nothing to infer one from, so it fails
  closed. Null does not mean public.

Resolving `job_id` → owner for the WebSocket is only possible because the previous branch
added `celery_task_id` to `Dataset` and `export_celery_task_id` to `ModelArtifact`
(`TrainingJob` and `EvaluationRun` already had theirs). Without those four columns this
endpoint could not be authorized at all.

## Consequences

**Accepted:**

- **This is not a backend-only change.** Every previous branch here has been
  zero-frontend-change; this one is not. Both `smart-model-tune` *and* the embedded
  `frontend/` on `feat/web-ui` need the two call-site changes, and the branches stay
  broken-by-default until they land — which is exactly what phase 1 buys time for.
- **A window of real exposure remains** for as long as phase 1 runs. Anonymous access is
  still full access. Phase 1 is a deployment convenience, not a security state, and the
  flip should be scheduled, not left open-ended.
- **Null-owner rows disappear at the flip.** Assign or delete them immediately beforehand.
- One in-process JWKS cache (10 min TTL, refetch on unknown `kid`) means a rotated Supabase
  signing key can be rejected for up to one refetch. Verification is offloaded with
  `asyncio.to_thread` because `PyJWKClient` uses blocking `urllib` and would otherwise stall
  the event loop on a cold cache.

**Explicitly still open** (not this ADR's scope): audit logging, closing the internal
service ports still published by `docker-compose.yml`, a startup guard against default
credentials, secret management, TLS/rate-limiting at the ingress, and per-user quotas.
Authentication is the precondition for several of those, not a substitute.

**Rejected alternatives:**

- *Validating the JWT at nginx with `auth_request`* — cheap, and it does keep strangers
  out, but nginx cannot answer "does this user own row X". It would satisfy neither the
  acceptance criterion nor the `404`-not-`403` rule, so the ownership layer would still
  have to exist in FastAPI. Two places to get wrong instead of one.
- *A shared API key for the whole deployment* — stops the public internet, does nothing
  about user A reading user B, which is the actual defect.
- *`organization_id` instead of `owner_id`* — Supabase models identity strictly per user
  (`auth.uid()`), with no organization concept anywhere in the frontend. Adding tenancy
  here would not line up with the other half of the system.
