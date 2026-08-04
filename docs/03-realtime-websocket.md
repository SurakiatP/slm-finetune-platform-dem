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
[frontend integration](./04-frontend-integration-smart-model-tune.md),
[ADR-007](./adr/ADR-007-ws-progress-snapshot.md) (the design record for §3).

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
in the system. (§3 adds a last-frame snapshot on top of this same Pub/Sub
transport — it does not change the transport model itself.)

---

## 2. The `job_id`

`job_id` **is** the Celery task id (`self.request.id` inside the task body,
e.g. `workers/tasks/data_generation.py:60`). It is:

1. Returned to the HTTP caller at job-submission time. For SDG:
   `api/services/sdg_service.py:87` (`async_result.id`), surfaced in the
   response as `SDGJobAcceptedResponse.job_id` and `websocket_url:
   f"/ws/jobs/{job_id}"` (`:95-100`).
2. Persisted for later recovery. For SDG, `api/services/sdg_service.py:89-95`
   writes the id to **both** places: `dataset.celery_task_id` (a first-class,
   indexed — but deliberately *not* unique — column,
   `api/models/dataset.py:77-89`) and the legacy
   `generation_metadata["celery_task_id"]` JSONB key, kept in sync for any
   existing reader that still looks there. So a client that lost the WS
   connection (or reloaded the page) can recover `job_id` via
   `GET /api/v1/datasets/{id}` and read either `celery_task_id` directly or
   `generation_metadata.celery_task_id`, then reconnect to
   `/ws/jobs/{that_id}`.
3. `TrainingJob` and `EvaluationRun` also carry the id as a first-class
   column — `celery_task_id` on `api/models/training_job.py:46` and
   `api/models/evaluation_run.py:35` — both **unique** + indexed, unlike
   `Dataset.celery_task_id`. `Dataset`'s column is non-unique on purpose: it
   was backfilled from pre-existing `generation_metadata` values whose
   uniqueness can't be guaranteed, so a unique constraint would risk
   breaking on migration. The three-way asymmetry this section used to
   describe (SDG's id living only in a JSONB blob vs. training/eval having a
   first-class column) is gone — all three entities now expose a
   `celery_task_id` column; only the uniqueness constraint still differs
   between `Dataset` and the other two.

---

## 3. Snapshot-on-connect (last frame only, not a replay)

Every publish also writes a **snapshot**. `workers/progress.py:40-57`
(`publish_ws_message`) does a Redis `SET job:{job_id}:last <payload>
EX=86400` (`JOB_SNAPSHOT_TTL_SECONDS`, `api/core/redis_client.py:20` — 24h)
*before* the `PUBLISH` (`:52-57`), deliberately in that order: a client that
subscribes between the two operations then reads a populated snapshot key
instead of an empty one. A failed `SET` is logged and swallowed (`:53-56`)
— a lost snapshot must never cost the live frame. Every frame type is
stored, including terminal `JobCompleted`/`JobFailed`, so a page opened
after a job finished sees the final state immediately.

The WS endpoint reads that key right after `subscribe()` succeeds and sends
it before the relay loop starts (`api/routers/websocket.py:78-85`) — so a
freshly opened or reloaded connection gets the job's most recent known state
right away instead of waiting on the next publish. The same snapshot is also
available over REST, for clients that don't hold a socket open at all:
`GET /api/v1/jobs/{job_id}/progress` (`api/routers/jobs.py:26-51`) validates
the stored payload against the `WSMessage` union and returns it, or `404` +
the standard `ErrorResponse` when there is no frame — job never published,
or the 24h TTL expired (`:38-42`). A stored payload that fails validation
(corrupt, or from a schema version predating a field) is logged and also
returned as `404`, never a `500` (`:44-51`) — a bad frame is treated the
same as no frame.

**Accepted race**: a frame published between `subscribe()` and the snapshot
read is delivered twice — once via the snapshot, once via the live relay a
moment later. This is harmless by construction: every frame is a full state
snapshot, not a delta, and both known consumers keep only the latest frame
they've seen per kind, so a duplicate is redundant, not incorrect. See
[ADR-007](./adr/ADR-007-ws-progress-snapshot.md) for why this is accepted
rather than fixed with locking.

**What is still true, despite the above:**
- **This is one frame, not a history.** There is no Redis Streams / replay
  log backing this — only the single latest payload per `job_id`. If you
  need the full trajectory of a run (e.g. training loss across every step),
  that's a different, purpose-built source: `GET
  /api/v1/trainings/{id}/loss-history`, backed by MLflow, not this channel.
- **The snapshot is not durable state.** It lives in Redis with a 24h TTL
  and is lost on a Redis flush or eviction. It is a UX accelerator, not a
  system of record — authoritative state remains each resource's own status
  field (`Dataset.status` / `TrainingJob.status` / `EvaluationRun.status` /
  `ModelArtifact.export_status`, see
  [architecture §6](./01-architecture.md#6-data-model)).
- **Clients must still fall back to REST resource status on a `404`.** A
  `404` from `GET /api/v1/jobs/{job_id}/progress` means "no frame right
  now," not "job doesn't exist." Pair the WS/snapshot with a poll of the
  owning resource (`GET /api/v1/datasets/{id}`, `/trainings/{id}`, etc.) on
  mount/reconnect for the coarse status, same discipline as before this
  feature existed.

---

## 4. Frame payload models

All defined in `api/schemas/progress.py`. Every message shares `job_id` and
`timestamp` from `_WSMessageBase` (`:24-31`) plus a `type` discriminator
(`WSMessageType`, `api/schemas/enums.py:61-70`). The `WSMessage` union
(`api/schemas/progress.py:152-162`) is what anything published to
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

### `ExportProgress` (`:105-118`)
| Field | Notes |
|---|---|
| `stage` | one of `downloading`, `merging`, `converting`, `quantizing`, `uploading`, `registering` — steps of the GGUF/SafeTensors export pipeline, in emission order |
| `detail` | nullable free-text sub-status (e.g. the quantization level on `quantizing`); display-only, no fixed vocabulary |

### `EvaluationProgress` (`:121-127`)
| Field | Notes |
|---|---|
| `phase` | one of `predicting`, `scoring`, `judging`. **`scoring` is a valid schema value that is deliberately never emitted** — `_compute_metrics_for_task` (`workers/tasks/evaluation.py`) is a pure in-memory computation over already-collected predictions, not I/O, so it isn't a meaningful checkpoint. Treat it the way §6 treats `format_detection`: a reserved/vestigial phase value, not something to build UI around. |
| `rows_done`, `rows_total` | rows processed / total for the *current* phase — resets between `predicting` and `judging`, not a running total across both |

### `JobCompleted` (`:130-136`)
| Field | Notes |
|---|---|
| `result` | loose `dict[str, Any]` — shape differs per job type (SDG/training/HPO/eval/export) |
| `mlflow_run_id`, `dataset_id`, `model_artifact_id` | nullable, populated when relevant |

### `JobFailed` (`:139-149`)
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
| Evaluation | `workers/tasks/evaluation.py` | **Yes** — `EvaluationProgress`. `predicting`: one frame per row via `on_predict_progress` (`:115-136`), throttled to at most one per `_PREDICT_PROGRESS_THROTTLE_SECONDS = 2.0`s (`:49`), except the final row, which always publishes regardless of the throttle window (`:118-119`) so the UI reliably lands on `rows_done == rows_total`; wired into `_predict_rows` via `progress_cb` (`:144`). `judging`: exactly one frame (`:159-174`), emitted only when `use_llm_judge` is set and `task_type` is `qa`/`tool_calling` (`:159`) — classification never gets a judging frame (the LLM judge doesn't apply there). `scoring` is a valid schema value, never emitted (see §4). | `JobCompleted` (`:198-208`) / `JobFailed` (`:234-240`) |
| Model export (GGUF/SafeTensors) | `workers/tasks/model_export.py` | **Yes** — `ExportProgress`, via the `publish_stage` helper (`:81-93`). GGUF exports emit all six schema stages: `downloading` (`:103`) → `merging` (`:153`, common to both formats) → `converting` (`:616-617`, inside `_quantize_merged_to_gguf`) → `quantizing` (`:625-626`, `detail` carries the quant level) → `uploading` (`:197`) → `registering` (`:223`, best-effort Ollama registration, published even though registration failure doesn't fail the export). SafeTensors exports only go through `downloading` / `merging` / `uploading` — `converting`/`quantizing`/`registering` are GGUF-only steps. | `JobCompleted` (`:274-286`) / `JobFailed` (`:334-340`) |

Evaluation and model export no longer go silent for the whole run — both now
stream intermediate frames end-to-end, same as SDG/training/HPO. SDG remains
the one job type with genuine multi-second-to-tens-of-second gaps between
frames (§6); for evaluation and export, REST status polling is still a
reasonable client-side fallback (and is the only recovery path before the
job's first frame lands, or after the snapshot's 24h TTL expires — §3), but
it is no longer the *only* way to show liveness for those two job types.

---

## 6. Emit cadence

### SDG

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

### Evaluation

`predicting` frames are throttled to at most one per
`_PREDICT_PROGRESS_THROTTLE_SECONDS = 2.0` seconds
(`workers/tasks/evaluation.py:49`), independent of how many rows complete in
that window — publishing once per row would flood the WS the same way HPO
suppresses per-step inner training progress across trials (§5). The final
row is always published even if it falls inside the throttle window
(`is_last` check, `:118-119`), so the UI is guaranteed to eventually see
`rows_done == rows_total` rather than getting stuck one row short. Exactly
one `judging` frame is published per run, only when the LLM judge actually
runs (`:159-174`) — being a single frame rather than a loop, there's no
throttling concern there.

### Model export

One `ExportProgress` frame per pipeline stage — not throttled, since each
stage runs exactly once per export and stages are minutes, not
sub-seconds, apart. `converting` and `quantizing` are emitted from inside
`_quantize_merged_to_gguf` immediately before each subprocess
(`llama.cpp`'s `convert_hf_to_gguf.py`, then `llama-quantize`) starts
(`workers/tasks/model_export.py:616-617`, `:625-626`), so a client watching
`stage` sees the frame *before* the (potentially long-running) subprocess
runs, not after it finishes. `merging` is emitted once before the
GGUF/SafeTensors format branch (`:153`) so both export formats get it, not
just GGUF.

---

## 7. Known sharp edges

- **Last frame only, not a replay.** §3's snapshot is a single cached
  payload per `job_id`, not a history. A client that needs the full
  trajectory of a run (every HPO trial, every training step) still can't
  get it from this channel — WS consumers only ever see "the current
  state," same as before ADR-007; it's just delivered immediately on
  connect now instead of only on the next publish.
- **The snapshot is not durable.** 24h TTL in Redis, lost on a flush or
  eviction. It's a UX accelerator layered on top of the authoritative
  `status` fields, not a replacement for them (§3).
- **REST fallback is still required, not optional.** `GET
  /api/v1/jobs/{job_id}/progress` 404s whenever there's no frame — job
  never started, TTL expired, or (rare) a corrupt stored payload — and a
  client that reads that 404 as "job doesn't exist" rather than "check the
  resource's own status" will show a false negative. Pair the WS/snapshot
  with a poll of the owning resource (`GET /api/v1/datasets/{id}`,
  `/trainings/{id}`, etc.) on mount, same discipline §3 already calls out.
- **SDG cadence gaps remain tens of seconds long** (§6) — the snapshot
  fixes cold-start/reconnect, it doesn't make the live stream itself
  denser.
- **`format_detection` (SDG) and `scoring` (evaluation) are reserved,
  never-emitted phase values** — see §4/§6. Don't build UI expecting either.

See [ADR-007](./adr/ADR-007-ws-progress-snapshot.md) for the full design
record behind §3 — including the rejected alternatives (Redis Streams with
real replay, persisting frames to Postgres) and why the duplicate-frame race
is accepted rather than fixed with locking.
