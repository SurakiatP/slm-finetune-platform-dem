# ADR-007 — Last-frame snapshot for job progress, and two new `WSMessageType` values

- **Status**: Accepted
- **Date**: 2026-08-04
- **Relates to**: [`../03-realtime-websocket.md`](../03-realtime-websocket.md) §3, §5, §7

## Context

`/ws/jobs/{job_id}` is a bare Redis Pub/Sub relay. Any frame published while
nobody is subscribed is gone forever, so a reloaded or newly-opened page shows
nothing until the *next* publish. That wait is not small:

- SDG emits roughly 2 frames per loop iteration, and each iteration dispatches
  up to 100 concurrent OpenRouter generator calls plus a judge batch — **gaps of
  tens of seconds between frames are normal** (`docs/03-realtime-websocket.md` §6).
- Evaluation and model export published **nothing at all** between task start
  and the single terminal frame, despite export running a multi-minute
  merge → convert → quantize → upload → register pipeline.

The consuming frontend, `smart-model-tune`, worked around this by polling REST
every 10 seconds and showing a coarse status — documented as a known limitation
in `FRONTEND_CHANGES_2026-07-30.md`. Its WS hook (`useTrainingWebSocket.ts`)
already keeps only the **latest** frame per kind (`latestProgress`,
`latestSdgProgress` — singular, not arrays).

## Decision

### 1. Store the last frame, don't replay a stream

`workers/progress.py::publish_ws_message` now writes the serialized frame to
`job:{job_id}:last` with a **24h TTL** immediately before `PUBLISH`.

Chosen over Redis Streams (`XADD`/`XRANGE`, the other option floated in
`docs/03-realtime-websocket.md` §7) because no consumer wants history: the
frontend renders only the latest value, and historical training loss already has
a purpose-built source in `GET /api/v1/trainings/{id}/loss-history` backed by
MLflow. Streams would add retention, trimming, and consumer-group concerns to
buy something nobody asked for.

Every frame type is stored, **including terminal `JobCompleted` / `JobFailed`**,
so a page opened after a job finished immediately shows the final state.

Ordering is `SET` **then** `PUBLISH`, deliberately: a client that subscribes
between the two operations reads a populated key rather than an empty one. A
failed `SET` is logged and swallowed — a lost snapshot must never cost a live
frame.

### 2. Deliver it two ways

- **WebSocket, on connect**: after `subscribe()` succeeds and before the relay
  task starts, the endpoint sends the snapshot, so the client's first paint is
  immediate. Existing consumers get this with **zero client-side changes**.
- **REST**: `GET /api/v1/jobs/{job_id}/progress` returns the same frame,
  validated against the `WSMessage` union, for clients that don't hold a socket
  open. `404` + the standard `ErrorResponse` when no frame exists (job never
  published, or TTL expired). A stored payload that fails validation is logged
  and also returned as `404` — a corrupt or legacy frame is equivalent to no
  frame, and must not surface as a `500`.

### 3. Two new `WSMessageType` values

`export_progress` and `evaluation_progress`, with `ExportProgress` and
`EvaluationProgress` payloads added to the `WSMessage` union.

`api/schemas/enums.py` states its own rule: *"These values are part of the
public API contract … Treat additions as breaking changes (write an ADR and a
migration)."* This ADR is that ADR. **No migration is required**, and that is a
deliberate finding, not an oversight: `grep -rn "WSMessageType" --include='*.py'
api workers alembic` returns hits only in `api/schemas/enums.py` and
`api/schemas/progress.py` — the enum is a wire-format discriminator and is never
persisted to Postgres. (Contrast `JobStatus`, which *is* a native `job_status`
Postgres enum type shared by four tables; changing that one would need a
migration.)

## Consequences

**Accepted:**

- **Duplicate frame on connect.** A frame published between `subscribe()` and
  the snapshot `GET` is delivered twice. Harmless by construction: every frame
  is a full state snapshot, not a delta, and consumers keep only the latest.
  Recorded here so it is not "fixed" with locking that would cost more than it
  saves.
- **The snapshot is not durable state.** It lives in Redis with a 24h TTL and
  is lost on a Redis flush. It is a UX accelerator, not a source of truth —
  authoritative state remains the `status` column on `Dataset` / `TrainingJob` /
  `EvaluationRun` / `ModelArtifact.export_status`. Clients must still fall back
  to REST status on `404`.
- **One extra Redis write per frame.** Negligible at the emit cadences involved
  (SDG ~2 frames/loop, training per logging step, evaluation throttled to one
  frame per 2s).
- **Unknown message types reach existing clients.** Both new types flow to any
  connected consumer. This is safe for the two known frontends, which switch on
  `type` and ignore unrecognised values, but it is the reason this counts as a
  contract change at all.

**Correction after live verification (2026-08-05):**

The cancel endpoints publish no terminal frame themselves, on the grounds that
the revoked task's own handler already does. Running this against a real Celery
worker showed that reasoning was only true for model export. `revoke(terminate=
True, signal="SIGTERM")` reaches the worker child as a **`SystemExit`** (billiard
turns the signal into `sys.exit(-(256 - 15))`, observable as
`error_message == "-241"`), and `SystemExit` is a `BaseException` — so the
`except Exception` handlers in `data_generation.py` and `evaluation.py` never
ran, and cancelling SDG or an evaluation emitted **no terminal frame at all**.
A WebSocket-only client waited forever.

All three task bodies now catch `BaseException`, each with a
`!= JobStatus.CANCELLED` guard so the worker's cleanup cannot overwrite the
status the API already set — without that guard, widening the handler turns
every cancel into `failed`. Unit tests cannot cover this: it only appears when
a real signal reaches a real worker child, so it is pinned by source-level
regression assertions plus the GPU-box runbook check (G3).

**Rejected alternatives:**

- *Redis Streams with replay* — see above; solves a problem no consumer has.
- *Persisting progress frames to Postgres* — durable, but adds write load on the
  hot path and a retention policy, to serve data whose value expires in seconds.
- *Leaving REST polling as the only backfill* — what exists today; the 10s poll
  only ever recovers coarse status, never the `80/200` sample counter that makes
  SDG look alive.
