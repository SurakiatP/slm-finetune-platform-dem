# Realtime WebSocket

## Purpose

This document covers the live job-progress channel — a system entirely
separate from the REST API and **not present in `openapi.json`** (OpenAPI
covers HTTP routes only; WebSockets aren't representable in OpenAPI 3.x).
It explains the transport chain, the `job_id` concept, message payload
shapes, which job types emit intermediate progress vs terminal-only frames,
and known limitations for anyone building a live dashboard against it.

## Audience

Backend/full-stack devs picking this up cold — especially anyone wiring a
frontend progress bar/log view against `/ws/jobs/{job_id}`.

See also: [architecture](./01-architecture.md),
[API reference](./02-api-reference.md),
[frontend integration](./04-frontend-integration-smart-model-tune.md).

---

## 1. Transport chain

```
Celery task (workers/tasks/*.py)
   │  publish_ws_message(redis, job_id, msg)      workers/progress.py:34-36
   ▼
Redis Pub/Sub channel  "job:{job_id}"              api/core/redis_client.py:21-23
   │  PUBLISH (sync redis-py client, decode_responses=True)
   ▼
FastAPI WebSocket endpoint  GET /ws/jobs/{job_id}   api/routers/websocket.py:28
   │  SUBSCRIBE + relay loop (async redis-py, forwards text verbatim)
   ▼
Browser (or any WS client)
```

- **Publish side** (Celery/sync): `workers/progress.py:34-36` —
  `publish_ws_message(client, job_id, message)` calls
  `client.publish(job_channel(job_id), message.model_dump_json())`. The sync
  Redis client comes from `workers/progress.py:19-21` (`get_sync_redis`),
  opened per-task via the `sync_redis_scope()` context manager
  (`workers/progress.py:24-31`).
- **Channel naming**: `api/core/redis_client.py:21-23` —
  `job_channel(job_id) -> f"job:{job_id}"`. Both the sync (worker) and async
  (API) sides import this same helper, so the two ends can never drift.
- **Subscribe + relay side** (FastAPI, async): `api/routers/websocket.py:28-86`.
  On connect it opens its own async Redis client (`get_redis_client()`,
  `api/core/redis_client.py:17-19`), subscribes to `job:{job_id}`
  (`:59`), and runs two concurrent tasks — `_relay_redis_to_ws()` (forwards
  every Pub/Sub message's raw `data` string to the WS via `ws.send_text`,
  `:36-47`) and `_watch_client_close()` (blocks on `ws.receive_text()` purely
  to detect `WebSocketDisconnect`, `:49-56`) — via `asyncio.wait(...,
  return_when=FIRST_COMPLETED)` (`:70-76`), tearing both down (unsubscribe,
  close both Redis clients, close the WS) whichever finishes first.

**No gRPC, no SSE, no long-poll.** This is the only live-progress transport
in the system.

---

## 2. The `job_id`

`job_id` **is** the Celery task id (`self.request.id` inside the task body,
e.g. `workers/tasks/data_generation.py:60`). It is:

1. Returned to the HTTP caller at job-submission time. For SDG:
   `api/services/sdg_service.py:87` (`async_result.id`), surfaced in the
   response as `SDGJobAcceptedResponse.job_id` and `websocket_url:
   f"/ws/jobs/{job_id}"` (`:95-100`).
2. Persisted for later recovery. For SDG, stashed into the dataset row's
   JSONB metadata: `api/services/sdg_service.py:90-93` —
   `metadata["celery_task_id"] = job_id; dataset.generation_metadata =
   metadata`. So a client that lost the WS connection (or reloaded the page)
   can recover `job_id` via `GET /api/v1/datasets/{id}` and read
   `generation_metadata.celery_task_id`, then reconnect to
   `/ws/jobs/{that_id}`.
3. For `TrainingJob` and `EvaluationRun`, the same id is additionally a
   first-class column — `celery_task_id` on `api/models/training_job.py:46-51`
   and `api/models/evaluation_run.py:35-39` (both unique + indexed) — so
   those two don't need to dig into a metadata blob to recover it.

---

## 3. No replay

`api/routers/websocket.py:9` states it directly: *"No replay: clients only
receive messages published after they connect. For backfill on reconnect,
query the DB for the job's current state."* There is no Redis Streams /
last-value cache backing this — it's a bare Pub/Sub channel, so any frame
published while nobody is subscribed is gone forever.

**Consequence**: a freshly opened or reloaded page shows *nothing* until the
next frame is published — which, mid-SDG-loop, can be tens of seconds away
(see §6). There is no queryable "latest progress" endpoint beyond each
resource's coarse `status` column (`pending`/`running`/`completed`/`failed`,
see [architecture §6](./01-architecture.md#6-data-model)).

**Always pair the WS with REST polling as backfill** — e.g. poll
`GET /api/v1/datasets/{id}` (or the training/evaluation equivalent) on
mount/reconnect to get the current coarse `status`, then let the WS carry
fine-grained updates from that point forward.

---

## 4. Frame payload models

All defined in `api/schemas/progress.py`. Every message shares `job_id` and
`timestamp` from `_WSMessageBase` (`:24-31`) plus a `type` discriminator
(`WSMessageType`, `api/schemas/enums.py:61-69`). The `WSMessage` union
(`api/schemas/progress.py:127-131`) is what anything published to
`job:{job_id}` must validate against — and since the WS endpoint forwards
Redis payloads verbatim, these Pydantic models **are** the wire format.

### `SDGProgress` (`:37-76`)
| Field | Notes |
|---|---|
| `phase` | one of `generating`, `validating`, `deduplicating`, `persisting` (Phase 4, legacy) or `format_detection`, `meta_prompting`, `judging`, `dedup` (Phase 9) |
| `samples_generated`, `samples_target`, `samples_valid`, `samples_rejected`, `duplicates_removed` | running counters |
| `current_loop` | 0-indexed SDG loop iteration; `None` outside the loop body |
| `judge_rejected`, `judge_parse_failures` | rows dropped by the LLM judge / unparseable judge responses |
| `dedup_rejected` | rows dropped by the MinHash LSH filter |

### `TrainingProgress` (`:79-89`)
| Field | Notes |
|---|---|
| `epoch` (float), `epochs_total` | fractional epoch, e.g. `1.5` |
| `step`, `steps_total` | — |
| `train_loss`, `eval_loss`, `learning_rate`, `samples_per_second` | nullable — mirror whatever the HF `Trainer` logged that step |
| `gpu_memory_mb` | nullable — current allocated CUDA memory, best-effort |

### `HPOProgress` (`:92-102`)
| Field | Notes |
|---|---|
| `trial_number` (0-indexed), `trials_total` | — |
| `current_params`, `best_value`, `best_params` | — |
| `last_trial_value`, `last_trial_pruned` | outcome of the just-finished trial |
| `inner_progress` | optional nested `TrainingProgress` for the current trial's steps — **currently always `None`** in practice, see §5 |

### `JobCompleted` (`:105-111`)
| Field | Notes |
|---|---|
| `result` | loose `dict[str, Any]` — shape differs per job type (SDG/training/HPO/eval/export) |
| `mlflow_run_id`, `dataset_id`, `model_artifact_id` | nullable, populated when relevant |

### `JobFailed` (`:114-124`)
| Field | Notes |
|---|---|
| `error` | human-readable message |
| `error_type` | exception class name, e.g. `OutOfMemoryError` |
| `traceback` | nullable, only emitted at `LOG_LEVEL=DEBUG` |

---

## 5. Per-job-type coverage

| Job type | Task file | Intermediate frames? | Terminal frames |
|---|---|---|---|
| SDG (data generation) | `workers/tasks/data_generation.py` | Yes — `SDGProgress`, per loop iteration (`emit_progress`, `:74-91`, wired to the generator's `progress_cb`) | `JobCompleted` (`:211-233`) / `JobFailed` (`:275-286`) |
| Training (manual mode) | `workers/tasks/training.py` | Yes — `TrainingProgress`, per HF `Trainer` logging step, via `make_progress_callback` (`ai_engine/training/callbacks.py:42-90`, registered at `workers/tasks/training.py:142`) | `JobCompleted` (`:178-179`) / `JobFailed` (`:226-227`) |
| Training (HPO mode) | `workers/tasks/hpo_training.py` | Yes — `HPOProgress`, per completed trial (`on_trial` callback, `:161-174`, wired to `HPOObjective.on_trial_done`). Per-step inner training progress during trials is explicitly suppressed (`inner_progress_publish=None`, `:184-186`, comment: *"the WS firehose would be too chatty across N trials"*) — the one exception is the final best-params retrain, which **does** get a live `TrainingProgress` stream via its own `make_progress_callback` (`:246-247`) | `JobCompleted` (`:297-311`) / `JobFailed` (`:333-340`) |
| Evaluation | `workers/tasks/evaluation.py` | **No** — confirmed no intermediate `publish(...)` calls between task start and the terminal publish (only 2 `publish(...)` call sites in the whole file, both terminal) | `JobCompleted` (`:144-145`) / `JobFailed` (`:180-181`) |
| Model export (GGUF/SafeTensors) | `workers/tasks/model_export.py` | **No** — same pattern, only 2 `publish(...)` call sites, both terminal, despite the multi-minute HF→GGUF conversion + quantization pipeline in between | `JobCompleted` (`:249-250`) / `JobFailed` (`:287-288`) |

So a client watching an evaluation or export job over the WS will see
silence for the entire run and then a single terminal frame — REST status
polling is the *only* way to show liveness for those two job types today.

---

## 6. SDG emit cadence

From `ai_engine/data_gen/generator.py`:

- **Setup**: one `emit("meta_prompting")` call right after the progress
  emitter is constructed (`:174-182`), before any generation happens.
- **Per loop iteration** (up to `MAX_LOOPS=20`, `ai_engine/data_gen/constants.py:12`):
  up to three `emit(...)` calls depending on how far the loop gets —
  `emit("generating", current_loop=...)` at batch dispatch or on a
  generator-call/parse failure (`:372`, `:423-428`), `emit("judging",
  current_loop=...)` right before the judge batch runs (`:442-447`), and a
  final `emit("generating", current_loop=..., samples_generated=...,
  samples_valid=...)` after quota-respecting collection (`:489-494`). In the
  common case (no failures) that's **2 frames per loop**: one at
  `"judging"`, one at the end-of-loop `"generating"`.
- Each loop dispatches up to `GENERATOR_BATCH_SIZE=100` concurrent
  OpenRouter calls (`ai_engine/data_gen/constants.py:41`) for the generator
  step, then up to the same order of magnitude for the judge batch
  (`JUDGE_BATCH_SIZE=100`, `:44` — model comment at
  `ai_engine/data_gen/models.py:24-25` notes up to ~500 concurrent judge
  calls per loop, 5 candidates × 100 generator calls). **Gaps of tens of
  seconds between WS frames during SDG are normal** — they correspond to
  those concurrent-call batches actually completing against OpenRouter, not
  a stalled connection.
- `"format_detection"` is a valid `phase` value in the `SDGProgress` schema
  (`api/schemas/progress.py:46`) and in `GenerationPhase`
  (`ai_engine/data_gen/generator.py:81`), but **is never actually emitted**
  by `generator.py` — `grep -rn "format_detection" ai_engine/ workers/` only
  matches the type literal, no `emit("format_detection")` call site exists.
  Format detection runs synchronously inside the seed-upload HTTP request
  (`POST /datasets/upload-seed`), not inside the async SDG job, so it never
  goes through this WS channel at all. Treat `format_detection` as a
  reserved/vestigial phase value, not something to build UI around.

---

## 7. Known sharp edges + future improvement

- **No replay** (§3) means every reconnect is a blank slate until the next
  frame — combined with the tens-of-seconds SDG cadence (§6) and the fully
  silent evaluation/export tasks (§5), a dashboard that *only* watches the
  WS can look broken/frozen even when the job is healthy.
- **No snapshot-on-connect**: unlike some pub/sub-backed progress systems,
  there's no "give me the last frame" fetch on subscribe.

A future improvement, without changing the transport model, would be:

1. **Snapshot-on-connect** — store the last-published frame per `job_id` in
   Redis (either a plain `SET job:{id}:last <payload>` alongside the
   `PUBLISH`, or switch to Redis Streams with `XADD`/`XRANGE` for real
   replay) and have the WS endpoint send it immediately after `subscribe()`
   succeeds, before relaying new messages.
2. **A REST "current progress" endpoint** (e.g.
   `GET /api/v1/jobs/{job_id}/progress`) backed by the same last-frame store,
   so non-WS clients (or a WS client's first paint) get an instant snapshot
   instead of waiting on both a DB status poll and the next live frame.
3. **Intermediate frames for evaluation and export** — both already have
   natural checkpoints to hook (per-row evaluation progress in
   `workers/tasks/evaluation.py`'s scoring loop; per-stage progress in
   `workers/tasks/model_export.py`'s download → convert → quantize →
   register pipeline) but currently only publish at the very end.
