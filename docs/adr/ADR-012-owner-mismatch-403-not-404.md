# ADR-012 — Owner-mismatch is `403`, not `404` (supersedes ADR-009 §Ownership)

- **Status**: Accepted
- **Date**: 2026-08-08
- **Supersedes**: the "Another user's existing resource returns `404`, not
  `403`" rule in [ADR-009](./ADR-009-supabase-jwt-auth.md)'s Ownership
  section (that ADR is otherwise unaffected and stays Accepted — see
  Decision below for why this is a narrow supersede, not a full one).

## Context

`BACKEND_GAP_ANALYSIS.md:33-34` states the P0 acceptance criterion for
authorization in one sentence: *"ผู้ใช้ A ต้องได้รับ `403` เมื่อเข้าถึง
resource หรือ job stream ของผู้ใช้ B ทุกกรณี"* — user A must get `403` when
reaching a resource or job stream belonging to user B, in every case.

ADR-006 (the original deferral record) quoted the same criterion the same
way — its own Decision section says *"user A gets `403` on every resource
and every job stream belonging to user B."* Somewhere between ADR-006 and
ADR-009 actually implementing ownership, the code shipped `404` instead,
and ADR-009's Ownership section wrote a justification for it: a `403` would
let user B confirm that a given id exists, turning every ownership-checked
endpoint into a probe for other users' project/dataset/training/model/
evaluation ids. `api/services/ownership.py`'s module docstring repeated
that argument at length, citing the *same* acceptance criterion as its
reason for doing the opposite of what the criterion says.

That argument is not wrong on its technical merits — a `403` genuinely does
leak existence, and `404`-for-both genuinely does not. But it was never
reconciled with the fact that the written acceptance criterion says `403`,
not `404`, and no one signed off on trading spec compliance for a narrower
attack surface. A document that argues at length for the opposite of the
customer's stated requirement, using that same requirement as its citation,
is not a considered trade-off — it's a bug that documented itself
persuasively enough to survive two rounds of hardening work without being
questioned.

This round's review caught it while auditing `ownership.py`'s docstrings
against `BACKEND_GAP_ANALYSIS.md` line by line.

## Decision

**Owner-mismatch on an existing resource returns `403`. A resource that
doesn't exist at all still returns `404`.** These must never collapse into
one code in either direction — see Testing below.

Concretely:

- `api/services/ownership.py`: `_check_owner` (called only after its caller
  has confirmed the row exists) now raises a new `_forbidden(label,
  resource_id)` — `HTTP_403_FORBIDDEN` — instead of `_not_found`.
  `_not_found` itself is unchanged and still fires only from the
  `db.get(...) is None` branch in each `assert_<entity>_access`, before
  `_check_owner` ever runs. This one change is sufficient for every
  `assert_project_access` / `assert_dataset_access` /
  `assert_training_access` / `assert_model_access` /
  `assert_evaluation_access` call site across the API — none of them
  needed their own edit.
- `owner_id IS NULL` (a row that predates auth, or was created during
  phase-1 with no user attached) still fails closed exactly as before —
  invisible to *everyone* once a caller is authenticated, not a special
  "visible to all" case. Only the status code changes, from 404 to 403: the
  row exists, it just belongs to nobody, and there is no third HTTP status
  for that.
- `api/routers/jobs.py` (`GET /api/v1/jobs/{job_id}/progress`) gets the
  matching split, independently: `resolve_job_owner(job_id).found is False`
  stays 404 (and so do "no frame published yet" / "TTL expired" / "corrupt
  payload" — none of those are ownership failures, they're all "there is
  nothing here" in the same sense a missing row is); `found is True` with
  an owner mismatch (including null-owner) is now 403. This mirrors
  `ownership.py`'s split exactly, for the same reason: it's the REST twin
  of the same ownership check.
- `api/routers/websocket.py` (`/ws/jobs/{job_id}`) is **explicitly out of
  scope** and unchanged: it keeps one close code, `4403`, for both "job
  unknown" and "job belongs to someone else." Close codes 4401/4403 are
  effectively unobservable to browsers today — `WebSocket.onclose` reports
  `1006` for most server-initiated closes in practice — so splitting them
  would not currently deliver the distinction to any real client, and
  fixing that observability gap is a separate, already-tracked problem.
  Revisit this exclusion once that gap closes; at that point the WS path
  should get the same 403-shaped split REST just did, for the same reason.
  `api/services/job_ownership.py`'s `resolve_job_owner` did not need to
  change to support any of this — it already returned a plain
  `JobOwnerResult(found, owner_id)` with no HTTP or close-code opinion
  baked in, so `jobs.py` and `websocket.py` were already free to diverge
  without it knowing they do.
- `scope_project_to_owner` / `scope_datasets_to_owner` / etc. (the `list_*`
  filtering helpers) are **unchanged**. A list endpoint filters rows out
  silently — there is no single id in play for a `403` to attach to, and no
  request "fails" the way a single-resource lookup does. Returning `403`
  there would be a category error, not a stricter check, regardless of
  which code the single-resource endpoints use. This asymmetry with
  `assert_*_access` is intentional (see `ownership.py`'s module docstring).

### Why this is a narrow supersede, not a rewrite of ADR-009

ADR-009's actual decision — verify Supabase JWTs, add `owner_id` to
`Project`, enforce it transitively through every FK, the two-phase
`AUTH_REQUIRED` rollout, the WebSocket subprotocol credential — is
unaffected and stays Accepted. Only the "what status code does an
ownership failure return" sub-decision inside its Ownership section is
superseded here. Per this ADR directory's own rule (see `README.md`), an
Accepted record is never edited in place; ADR-009's text is left exactly as
written, as the record of what was decided and why, with this ADR
recorded as superseding that one paragraph of it.

## Consequences

**Accepted, not eliminated: `403` is a genuine existence oracle.** Once
`_check_owner` raises `_forbidden`, any authenticated caller can
distinguish "this id exists and isn't mine" (403) from "this id doesn't
exist" (404) for every project, dataset, training job, model artifact and
evaluation run in the system, including via brute-forcing UUIDs — though
the 122 bits of entropy in a v4 UUID make blind enumeration impractical on
its own, and every affected endpoint requires a valid bearer token before
this even applies. This is the exact risk ADR-009's `404`-for-both design
existed to prevent, and it has not been mitigated here — it is being
accepted because the P0 acceptance criterion explicitly requires `403`,
and "404 for everything" is the specific thing that criterion rules out. A
future requirement for both "no enumeration" and "spec-correct status
codes" needs a new decision (rate-limiting the ownership-failure path
specifically, or a non-guessable id scheme), not a silent revert of this
one.

**No new frontend work required.** Both `smart-model-tune` and the
embedded `frontend/` on `feat/web-ui` are anonymous today (`AUTH_REQUIRED`
phase 1) — this ADR only changes what an *authenticated* ownership mismatch
returns, and phase 1 skips the ownership check entirely for every caller.
The distinction becomes observable only once `AUTH_REQUIRED=true`, which is
still blocked on the frontend patch tracked separately
(`docs/patches/smart-model-tune-auth.md`, `TASK_TRACKER.md`).

**`openapi.json`**: FastAPI only auto-documents success responses plus
`422` from request validation; these `HTTPException`s are raised in the
service layer, not declared via `responses=` on any route, so they were
never reflected in the generated spec in the first place. Regenerating
after this change is expected to be a no-op — see the working log for
confirmation on this branch.

## Testing

Every ownership-failure test in `tests/unit/` was audited (`grep -rn "404"
tests/unit/`) and, where it exercised an owner-mismatch path, updated to
expect `403`. Per the standing rule this round's plan set for exactly this
kind of change: **each affected resource type carries a pair of tests, kept
adjacent in the file** — non-owner reaching an *existing* row (`403`) next
to any caller reaching a UUID that names *no* row (`404`). A single-sided
edit (flip every `404` to `403` without also pinning the missing-row case)
would let a defect that makes *every* failure `403` — including a
genuinely missing row — pass the whole suite; the missing-row half of each
pair is what catches that specific defect, and nothing else does. Verified
by mutation in both directions: reverting `_check_owner` to raise
`_not_found` turns every `403` test red; making `_not_found` itself return
`403` (i.e. everything is `403`) turns every missing-row `404` test red.

## Rejected alternatives

- *Rate-limit or delay the ownership-failure response* to blunt
  enumeration while still returning 403 — real mitigation, but a
  cross-cutting concern (needs a shared throttling layer, applies to more
  than this one code path) and out of scope for a status-code fix. Tracked
  as a follow-up, not solved here.
- *Ask for the acceptance criterion to be re-confirmed as `404`* before
  touching anything — rejected because the criterion is unambiguous as
  written (`403`, "ทุกกรณี" / "every case"), and re-litigating a customer
  requirement mid-implementation without them having asked for it is not
  this team's call to make.
- *Leave `ownership.py`'s docstring as-is and just change the code* —
  rejected outright. A comment block that argues at length for the
  opposite of what the code now does is worse than no comment at all; the
  next person to read it would "fix" the code back to match the doc.
