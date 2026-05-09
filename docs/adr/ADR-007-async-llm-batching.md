# ADR-007: Async LLM Batching with Custom asyncio.gather

**Status:** Accepted
**Confidence:** High
**Date:** 2026-05-09
**Supersedes:** —

## Context + Decision Drivers

Phase 9 SDG hardening introduces three new LLM-bound stages on top of the
existing Generator: Meta-Prompting (1 call/job), LLM-as-Judge
(~500 calls/loop), and Format Detection (1 call/upload). Doing these
serially against OpenRouter would push job latency from minutes to hours —
the Judge alone would multiply Generator time by 5×.

We need batched concurrent LLM calls. The constraints:

- **Hexagonal:** logic stays in `ai_engine/data_gen/`, no Celery/FastAPI imports.
- **No new heavy deps:** can't add `distilabel`, `ray`, etc.
- **Celery worker pool is `prefork`** — `asyncio.run` from inside a sync
  task body is fine; running the *whole* worker as async is not.
- **OpenRouter rate limits** are per-org — bursting 1000 concurrent calls
  trips 429s.

The OpenAI Python SDK already exposes `AsyncOpenAI`, so the underlying
client is solved. The decision is *how to orchestrate* batches.

## Decision

Add `AsyncOpenRouterClient` to `ai_engine/data_gen/openrouter_client.py`
(co-located with the existing sync `OpenRouterClient`). Its primary
primitive is:

```python
async def chat_batch(
    self,
    *,
    prompts: list[Prompt],
    model: str,
    concurrency: int = 100,
    ...
) -> list[ChatResult | Exception]
```

implemented as `asyncio.gather(*, return_exceptions=True)` over a per-call
coroutine guarded by an `asyncio.Semaphore(concurrency)`. Per-call retries
(connect / timeout / 429) are handled inside the per-call wrapper using the
same `tenacity` decorator that the sync client already uses.

`SyntheticDataGenerator.generate()` becomes `async def`. The Celery task
body stays sync and crosses the boundary with a single `asyncio.run(...)`
call — same pattern as the inference service.

## Alternatives Considered

- **`asyncio.Queue` + N worker coroutines** — equivalent throughput but
  more code (workers, sentinel termination, result-ordering). The semaphore
  + `gather` form recovers in-order results for free.
- **`distilabel` / `ray` / `dask`** — heavy deps for a workload that fits
  in 80 lines. Rejected per §3.2 of the Phase 9 spec.
- **Threads (`concurrent.futures.ThreadPoolExecutor`)** — works but
  conflicts with the rest of the async stack. The OpenAI SDK has a native
  `AsyncOpenAI`; no reason to bridge through threads.
- **One bigger Celery task per LLM call (Celery group/chord)** — pushes
  hundreds of tiny tasks through Redis per loop iteration. Throughput would
  be dominated by broker overhead, not LLM latency.

## AI Instructions

**Guidance Level: STRICT**

- All multi-call SDG stages (Generator, Judge) MUST go through
  `AsyncOpenRouterClient.chat_batch(...)`. No `for prompt in prompts:
  client.chat(prompt)` loops.
- Single-call stages (Format Detection, PDF→QA multimodal) keep using the
  sync `OpenRouterClient`. Wrap with `asyncio.to_thread(...)` if invoked
  from an async FastAPI handler.
- Default `concurrency=100`. Lower (50 / 25) only if smoke tests trip
  persistent 429s. Track that as a flag-back to the developer; do not
  silently raise the cap above 100.
- Failures inside `chat_batch` come back as exceptions in the result list,
  *not* raised. Callers MUST iterate and decide which to skip vs. abort.
- Do NOT add a third client class (e.g. `BatchOpenRouterClient`). Two —
  sync and async — is the contract.

## Consequences

✅ Generator + Judge throughput scales with `concurrency` instead of
   `num_calls × per_call_latency`. A 100-call loop iteration completes
   in roughly the slowest single call's time (~3-8s) instead of 100×.

✅ Pattern is composable — future stages (e.g. translation pass) can reuse
   `chat_batch` without any plumbing changes.

✅ `tenacity` retries continue to mask transient 429 / connect / timeout
   errors, so smoke tests don't fail on the first OpenRouter hiccup.

⚠️ At `concurrency=100` we may hit OpenRouter's per-org concurrent-request
   ceiling on cheap models. Mitigation: per-spec we track this as a known
   risk and lower `GENERATOR_BATCH_SIZE` if it surfaces.

⚠️ Errors from individual calls are silently dropped (the call returns an
   `Exception` instead of raising). This is intentional — one bad row out
   of 100 should not abort an entire SDG loop iteration — but the
   orchestrator MUST log the error count via `SDGProgress` so failures
   stay visible.

⚠️ Inside a Celery prefork worker, `asyncio.run` builds a fresh event loop
   per task. That's fine for SDG (one loop per job) but if the SDG task
   ever forks sub-tasks the loop must be shared via `asyncio.get_event_loop()`,
   not re-created. Not a concern at Phase 9 scope.
