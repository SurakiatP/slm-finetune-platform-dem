# ADR-010 — CPU/GPU queue split, concurrency quotas, and OpenRouter cost controls

- **Status**: Accepted
- **Date**: 2026-08-07

## Context

Two separate problems showed up once the platform had real concurrent usage
to reason about, both traced back to the same root cause: nothing in the
system counted or capped anything.

**Queue starvation.** There were no Celery `task_routes` at all — one
`worker` service at `--concurrency=1` drained a single default queue, and
all five task types (`sdg.generate`, `train.manual`, `train.hpo`,
`model.export`, `evaluation.run`) serialized into that one slot. SDG
generation is CPU-bound network I/O against OpenRouter — no GPU involved,
see `01-architecture.md` §3 — but a long (20+ minute) SDG run shared the
GPU worker's single slot, so it blocked GPU training for the whole platform
even though the two never actually contend for a real resource.

**No cost or concurrency ceiling.** Nothing stopped an actor from submitting
an unbounded number of concurrent SDG or GPU jobs, and nothing recorded what
each SDG run actually cost against the OpenRouter bill until this round —
`usage_events` (migration `0009`) is the first table that persists
token/cost data at all. A single RTX 3060 (ADR-002/`require.md`) can run
exactly one training/export/evaluation job at a time; SDG has no such
hardware ceiling but does have a real dollar one every submission burns
against.

This ADR covers four decisions made to close those gaps: the queue split,
per-bucket concurrency quotas, a monthly budget gate, and an OpenRouter
circuit breaker — plus the accounting (`usage_events`, `cost_usd`) all three
of the latter read from.

## Decision

### CPU/GPU queue split

`sdg.generate` now routes to a `cpu` queue (`task_routes={"sdg.*": {"queue":
"cpu"}}` in `workers/celery_app.py`), served by a new `worker-cpu` Compose
service (`-Q cpu --concurrency=2`). Every other task falls through to
`task_default_queue="gpu"`, served by the existing `worker` service
unchanged (`-Q gpu --concurrency=1`). `worker-cpu` reuses the `api` Docker
image rather than a new CUDA build — it needs `celery`/`openai`/`tenacity`/
`redis`/`sqlalchemy`, all of which the API image already ships, and never
imports `torch`. See `01-architecture.md` §8 for the full mechanics
(including the `task_routes` name-vs-module-path trap already hit once).

### Concurrency quota: the DB is the counter, not Redis

`api/services/quota.py` counts in-flight jobs with a live `SELECT COUNT(*)`
against `Dataset.status` / `TrainingJob.status` / `EvaluationRun.status` /
`ModelArtifact.export_status`, not a Redis increment/decrement counter.

### 429, not 503, for a quota rejection

Quota rejections return `429` with a `Retry-After` header, not `503`.

### Calendar-month budget window

The monthly budget (`BUDGET_MONTHLY_USD_PER_ACTOR` /
`BUDGET_MONTHLY_USD_GLOBAL`, both `None`/unlimited by default) resets on the
**UTC calendar month boundary**, not a rolling 30-day window.

### Breaker state lives in Redis, not in-process

The OpenRouter circuit breaker (`api/services/circuit_breaker.py`) keeps its
failure count, `opened_at` timestamp, and half-open probe lock in Redis.

### Budget is enforced mid-run, not just at submit

`POST /datasets/generate` checks the budget once at submit
(`usage_service.assert_within_budget`), and the worker checks it again after
every OpenRouter response for the duration of the run
(`UsageAccumulator.check_budget()` in `ai_engine/data_gen/usage.py`, called
from `ai_engine/data_gen/generator.py`).

### Anonymous callers get the global cap only

Quota and budget both have a per-actor cap and a global cap. For an
anonymous caller (`AUTH_REQUIRED=false`, no token), only the global cap
applies — the per-actor cap is simply not evaluated for that request.

### `cost_usd` is `NULL` for an unpriced model, never `0`

`api/services/model_pricing.py:cost_usd()` returns `None`, not
`Decimal("0")`, when a model has no entry in the pricing map (built-in table
+ `MODEL_PRICING_JSON` override). `usage_events.cost_usd` is a nullable
`Numeric(12, 6)` column for exactly this reason, and every `SUM(cost_usd)`
this codebase runs (the budget gate, the `/usage` rollup) relies on SQL's
NULL-skipping aggregate behavior to make the result a floor, not a lie.

### Format Detection: breaker yes, budget no

Format Detection is the one counted OpenRouter surface that runs in the API
process rather than a worker. It feeds and honours the circuit breaker, but
is **not** gated by the monthly budget.

Rejected alternative — gating it too. Consistency says every dollar-spending
call should sit behind the same cap. But this call is a fraction of a cent,
happens during a seed *upload* rather than as a job, and precedes any job
existing at all. Returning 402 on an upload because a different feature (SDG)
exhausted the month's budget takes the whole product offline to enforce a cap
the upload barely moves. It still *counts toward* the budget — its usage row
is written like every other — so the money is never invisible; it simply is
not a gate.

### `actor_id=None` means two different things, deliberately

`usage_service._monthly_spend_stmt(actor_id=None)` means "no actor filter",
i.e. the platform-wide total the global budget cap reads.
`summary_for_actor(actor_id=None)` means the *anonymous bucket*
(`actor_id IS NULL`) — one unauthenticated caller's own rows.

Rejected alternative — unifying them so `GET /usage` always shows the number
the budget gate enforces on. That promise can only hold for an anonymous
caller by handing them the platform-wide total, which leaks every other
user's spend to anyone without a token. The asymmetry is kept and documented
at both call sites instead: an anonymous caller can be 402'd by the global
cap using a number `/usage` never showed them. Once `AUTH_REQUIRED` flips,
every caller is authenticated and the discrepancy disappears on its own.

## Consequences

**Accepted:**

- Two Celery workers to operate and monitor instead of one; a stuck
  `worker-cpu` process now fails independently of GPU training rather than
  taking it down with it (an improvement) but is also a second thing that
  can be down without the other noticing on its own.
- The extra `SELECT COUNT(*)` per submission (quota) and `SUM(cost_usd)`
  query per submission + per OpenRouter response (budget) are accepted
  costs — job volumes on this platform are tiny (a handful of concurrent
  jobs at most, per the caps above), so this is not a hot path.
- Per-actor quota and per-actor budget are both inert under today's
  `AUTH_REQUIRED=false` — every caller looks anonymous, so only the global
  caps do any work. This stays true until `AUTH_REQUIRED` flips.
- A rotated/failed Redis instance degrades every one of these three
  subsystems to its most-permissive state (fail open) rather than blocking
  the platform — see `api/services/circuit_breaker.py`'s module docstring
  for the explicit accepted-cost reasoning (a stuck-open breaker or a
  falsely-believed-open one is worse than letting a few extra calls
  through).

**Prices: verified 2026-08-07, and which tier was chosen.**

Both rows in `api/services/model_pricing.py` were originally written as
unverified placeholders. They have since been checked against OpenRouter's
`/api/v1/models/{id}/endpoints` and both happened to be correct — but the
check surfaced something the placeholders had no way to express: **neither
model has a single price.**

| model | cheapest | **standard (used)** | dearest |
|---|---|---|---|
| `google/gemini-2.5-flash-lite` | $0.05 / $0.20 (AI Studio Flex) | **$0.10 / $0.40** (Vertex, Vertex EU, AI Studio) | $0.18 / $0.72 (AI Studio Priority) |
| `deepseek/deepseek-v4-flash-0731` | $0.09 / $0.18 (DeepInfra) | **$0.14 / $0.28** (DeepSeek first-party + 15 others) | $0.20 / $0.40 (Phala) |

USD per 1M prompt / completion tokens. DeepSeek is served by 25 providers
spanning more than 2x.

We record the **standard tier**, not the floor and not the ceiling.
OpenRouter selects the provider per request and the response identifies only
the model, never the endpoint that served it — so no per-call price is
recoverable after the fact and any single number is an approximation. Taking
the floor would make a budget cap fire late, which is precisely the failure
this feature exists to prevent; note that the floor is also what
OpenRouter's *top-level* `pricing` field reports, so reading the obvious
field would have silently under-billed. Taking the ceiling would overstate a
typical run by more than 2x and 402 users nowhere near their limit. The
model owner's own list rate is the defensible middle, and a deployment that
pins its routing can correct it via `MODEL_PRICING_JSON` without a deploy.

Also not modelled: DeepSeek quotes a separate cached-input rate ($0.018 per
1M). All prompt tokens are billed at the full rate, so a cache-heavy run is
over-billed rather than under-billed — the safe direction.

**Still true regardless:** a recorded `cost_usd` is an estimate, not an
invoice. Token counts are the ground truth (straight from OpenRouter's
response `usage`), cost is derived, and every figure can be recomputed
retroactively from stored token counts if a rate turns out to be wrong or
changes. Reconcile against the OpenRouter bill before anyone treats these
numbers as authoritative.

**Rejected alternatives:**

- *A Redis counter for concurrency quota, incremented on submit and
  decremented on completion* — rejected because it has no self-healing
  equivalent to the DB's. The DB is already the source of truth for job
  state, and it self-heals: if a worker is SIGKILLed mid-job, the row is
  left `pending`/`running`, but `api/services/job_reconcile.py` sweeps every
  5 minutes and flips orphaned jobs to `failed`, so a DB-derived in-flight
  count drops back down on its own within one sweep interval. A Redis
  counter has no equivalent — it leaks exactly on the SIGKILL-before-
  decrement case commit `b93091e` already had to patch up for a different
  counter, and a leaked counter never recovers by itself: it 429s every
  future submission for that actor/bucket forever, silently, until someone
  notices and manually resets it. That is worse than the problem it would
  solve.
- *`503` instead of `429` for a quota rejection* — rejected because `503`
  is the status most HTTP client libraries and reverse proxies treat as
  safe to auto-retry, possibly with backoff. That is exactly the wrong
  incentive when the rejection reason is an already-saturated queue: a
  `503`-triggered retry storm would make the saturation worse, not better.
  `429` paired with an explicit `Retry-After` tells the caller precisely
  how long to back off without implying the server itself is unhealthy.
- *A rolling 30-day budget window* — rejected in favor of the calendar
  month because OpenRouter's own billing period is a calendar month. A
  rolling window would never line up with the invoice a human is actually
  reconciling this feature against.
- *In-process circuit breaker state* — rejected because
  `worker_max_tasks_per_child=1` (see `workers/celery_app.py`) recycles the
  Celery worker's process after every single task. An in-memory failure
  counter or `opened_at` timestamp would be wiped clean before the next
  task even started, so an in-process breaker would never trip no matter
  how many times OpenRouter failed. Redis is the only state store both the
  long-lived API process and every short-lived worker process can agree on.
- *Submit-time-only budget enforcement* — rejected because a run that
  starts at $0 and burns $50 over a three-hour SDG loop would never be
  stopped by a check that only runs once, before the first API call.
  Enforcing it again after every OpenRouter response
  (`UsageAccumulator.check_budget()`) is what actually caps spend inside a
  long-running job. This stays hexagonal-clean: `ai_engine` never learns
  about `api.core.config` or Postgres — the worker resolves prices as plain
  floats and hands them to `UsageAccumulator`, which does its own
  arithmetic and raises `SDGBudgetExceededError` without ever importing
  outside `ai_engine`.
- *A Redis-per-IP side channel to give anonymous callers their own
  per-actor-equivalent quota* — rejected. Ownership lives on
  `Project.owner_id`, a Supabase `sub` claim, and there is no IP address
  anywhere in the data model to bucket an anonymous request by. Building
  one would mean adding a second, parallel identity system for the sole
  purpose of working around `AUTH_REQUIRED=false` — solving a problem that
  goes away entirely once auth is turned on, at the cost of a leaky
  side-channel with the same self-healing problems as the rejected Redis
  quota counter above. Anonymous callers being covered by the global cap
  only is recorded here as a known, accepted limitation of phase 1
  (ADR-009), not an oversight.
- *`cost_usd = 0` for an unpriced model* — rejected because `0` is a lie
  that actively defeats the feature it would appear in: a run against a
  model missing from the pricing map would look free and sail straight
  past `usage_service.assert_within_budget` forever, no matter how much
  real money it spent. `NULL` plus `has_unpriced_usage` on the summary
  response is more code at every read site, but it is the only shape that
  can't be silently wrong. Token counts are the ground truth in either
  case; cost is a derived, correctable-after-the-fact number, not
  something to guess at with a placeholder zero.

## Related

- [ADR-002](../01-architecture.md#5-hard-constraints--adrs) — RTX 3060
  12GB / QLoRA 4-bit constraint, the reason the GPU bucket's global cap is
  as low as it is.
- [ADR-009](./ADR-009-supabase-jwt-auth.md) — `Project.owner_id`, the
  phase-1/phase-2 `AUTH_REQUIRED` split, and the anonymous-caller behavior
  this ADR's per-actor caps inherit.
- [`01-architecture.md` §8](../01-architecture.md#8-cpugpu-queue-topology) —
  the queue-split mechanics in full.
- [`02-api-reference.md`](../02-api-reference.md#submit-gates-concurrency-quotas-budget-and-the-circuit-breaker) —
  the `429`/`402`/`503` response contract this ADR's gates produce.
