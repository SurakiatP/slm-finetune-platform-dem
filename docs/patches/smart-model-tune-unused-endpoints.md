# Backend surface `smart-model-tune` has not wired yet

**For:** the `smart-model-tune` frontend team
**From:** the Engine (backend) side, 2026-08-09
**Backend commit this describes:** `dev @ 11c2ae0`
**Source of truth:** `openapi.json` in this repo (40 paths). Every endpoint,
field name and default below was read out of that file on the date above —
not from memory or an older doc.

## Why this document exists

The Engine currently exposes **40 paths**. `src/lib/engineApi.ts` calls
**13 operations** of them. The other 27 are built, tested, deployed and
running on the box right now — several of them were built *specifically*
because this frontend needed them, and then never got called.

This is not a list of work we want you to do. It is a list of **things you
may be building by hand, or showing as simulated, that already exist as a
single HTTP call.** Each section says what UI it unlocks and gives the exact
request/response shape.

Nothing here requires authentication today (`AUTH_REQUIRED=false`). When
auth is switched on, everything here follows the same rule as the calls you
already make — see `docs/patches/smart-model-tune-auth.md`.

---

## What you already call (for contrast — no action needed)

`POST /projects` · `GET /projects/{id}` · `GET /projects?external_project_id=`
· `POST /datasets/upload-seed` · `POST /datasets/generate` ·
`GET /datasets/{id}` · `POST /trainings` · `GET /trainings/{id}` ·
`GET /trainings/{id}/loss-history` · `DELETE /trainings/{id}` ·
`GET /models` · `GET /models/{id}` · `POST /models/{id}/export` ·
`GET /inference/models` · `POST /inference/chat/completions` · `GET /health`
· `WS /ws/jobs/{job_id}`

All correct, all current. The `external_project_id` mapping in particular is
wired exactly the way it was designed.

---

## 1. Evaluation — the screen is simulated; the backend is real

**Today:** `src/lib/qualityCalculator.ts` computes scores client-side. There
is no `fetch` in it and nothing in `src/` calls `/api/v1/evaluations`.

**Available:** a full evaluation pipeline — rule-based metrics plus an
optional LLM judge — that runs on the worker and streams progress over the
same WebSocket you already use for training.

```
POST /api/v1/evaluations                       → 202 EvaluationAcceptedResponse
{
  "model_artifact_id": "<uuid>",   // required — Engine ModelArtifact.id
  "dataset_id":        "<uuid>",   // required — a held-out dataset
  "use_llm_judge":     false,      // optional, default false
  "judge_model":       null        // optional; server default when null
}
→ { evaluation_id, job_id, status, websocket_url }
```

```
GET  /api/v1/evaluations?project_id=<uuid>     → Page<EvaluationResponse>
GET  /api/v1/evaluations/{evaluation_id}       → EvaluationResponse
POST /api/v1/evaluations/{evaluation_id}/cancel
POST /api/v1/evaluations/compare               → side-by-side of several runs
```

`EvaluationResponse` fields: `id`, `model_artifact_id`, `dataset_id`,
`celery_task_id`, `status`, `metrics_json`, `llm_judge_score`,
`llm_judge_model`, `error_message`, `started_at`, `ended_at`, `created_at`,
`updated_at`.

Connect `websocket_url` exactly like training: you will receive
`evaluation_progress` frames (throttled to ~2s) and a terminal
`job_completed` / `job_failed`.

> **Cost note:** `use_llm_judge: true` spends real OpenRouter money. It is
> metered per row and stops mid-pass if the budget cap is hit (you would get
> a failed job with a budget message, not a silent overrun).

---

## 2. HPO / Tuning — same story, one field away

**Today:** `src/lib/tuningGenerator.ts` fabricates the tuning report.

**Available:** `POST /api/v1/trainings` is a **discriminated union on
`mode`**. You already send `mode: "manual"`. The other arm exists:

```
POST /api/v1/trainings   { "mode": "hpo", ... }   → same accepted shape
```

HPO runs emit `hpo_progress` frames on the WebSocket (trial number, best
score so far) and write per-trial metrics you can read with:

```
GET /api/v1/trainings/{training_id}/metrics      → step/value/timestamp series
```

Check the `HPOTrainingRequest` schema in `openapi.json` for the trial-budget
and search-space fields — they have sensible server defaults, so a minimal
body is short.

---

## 3. Cost and token usage — Analytics currently has no data source

**Today:** the Analytics screen reads Supabase, which nothing writes.

**Available:** every OpenRouter call the platform makes — SDG generation,
SDG judge, PDF QA, format detection, and the evaluation LLM judge — is
recorded as a row with tokens and cost.

```
GET /api/v1/usage                        → UsageSummaryResponse
  { period_start, period_end, prompt_tokens, completion_tokens,
    cost_usd, items[], has_unpriced_usage }

GET /api/v1/projects/{project_id}/usage  → Page<UsageEventResponse>
  each: { id, created_at, actor_id, project_id, job_id, provider, model,
          stage, prompt_tokens, completion_tokens, cost_usd, outcome }
```

Two honesty notes so you render it correctly:

- **`cost_usd` is a string or null**, not a number — it is a decimal, and
  `null` means we have no verified price for that model. Show unpriced usage
  as "tokens only", never as `$0`. `has_unpriced_usage` on the summary tells
  you whether any row in the period is in that state.
- Cost is an **estimate from published rates**, not an invoice. Tokens are
  ground truth.

---

## 4. Downloads — presigned URLs exist; a direct link will not work

**Today:** the download buttons have no working Engine call.

**Important:** the objects live in MinIO on an internal network. A browser
cannot reach the storage URI in `dataset.storage_uri` /
`model_artifact.gguf_uri`. You must ask for a time-boxed presigned URL:

```
GET /api/v1/datasets/{dataset_id}/download-url
→ { url, filename, content_type, expires_at, expires_in }

GET /api/v1/models/{model_id}/download-url
→ { format, files[], expires_at, expires_in, truncated }
```

Put `url` straight into `window.location` or an `<a download>`. It expires —
fetch it at click time, never cache it in state.

(`GET .../download` also exists and streams through the API. Prefer
`download-url`: a multi-GB GGUF through the API process is exactly the
request that will time out behind Cloudflare.)

> **The mistake to avoid:** `{model_id}` here is the **Engine
> `ModelArtifact.id`**, not the Supabase `trained_models` row id. Passing the
> Supabase id 404s. You already hold the Engine id via `engineStore` /
> `external_project_id`.

---

## 5. Reconnecting to an in-flight SDG job after a page reload

**Today:** `useTrainingWebSocket` recovers training runs. SDG does not
recover — a refresh mid-generation shows nothing until the next frame.

Two pieces you are missing:

- **`DatasetResponse.celery_task_id`** is a first-class field (alongside
  `status` and `error_message`). Your `EngineDataset` interface does not
  declare it. That id is the `job_id` for the WebSocket, exactly like
  `training.celery_task_id`, which you already use.
- **`GET /api/v1/jobs/{job_id}/progress`** returns the last frame for any
  job — SDG, training, HPO, export or evaluation — as the same union you get
  over the socket (`SDGProgress` | `TrainingProgress` | `HPOProgress` |
  `ExportProgress` | `EvaluationProgress` | `JobCompleted` | `JobFailed`),
  or **404 when there is no frame yet**. Frames are kept 24h.

Recommended pattern for every job type: call the REST snapshot first, paint
immediately, then open the socket. The socket also pushes the snapshot on
connect, but the REST call removes the blank moment during the handshake.

---

## 6. Cancel — you have one of four

You call `DELETE /trainings/{id}`. These three exist and are not wired:

```
POST /api/v1/datasets/{dataset_id}/cancel        # cancel a running SDG job
POST /api/v1/models/{model_id}/export/cancel     # cancel a GGUF export
POST /api/v1/evaluations/{evaluation_id}/cancel
```

(`POST /api/v1/trainings/{id}/cancel` also exists as an alias if a POST is
easier than a DELETE in your client.)

All four clean up partial artifacts and emit a terminal WebSocket frame, so
your UI gets a definite end state rather than a job that just stops talking.

---

## 7. Smaller things that remove hardcoded data

| You currently | Use instead |
|---|---|
| Hardcode 6 base models in `src/types/index.ts` and map them in `engineMappings.ts` (`phi-3-mini` maps to `null` and dead-ends a template) | `GET /api/v1/base-models` → `{ id, display_name, family, params_billions, context_length, recommended_max_seq_length, quantization, license, ollama_tag, notes }`. The list is what the backend can actually train, so an unsupported model cannot appear in the UI. |
| Hardcode the task-type list (6 values, 3 of which map to `null`) | `GET /api/v1/tasks` → the 3 supported types; `GET /api/v1/tasks/{task_type}/example` → a ready-made example payload for your form |
| — | `GET /api/v1/datasets/{id}/preview` → sample rows, for a "look at the generated data" panel |
| Fetch `mlflow_url` but never render it | `GET /api/v1/trainings/{id}/mlflow-url` — the run link, if you want an "open in MLflow" affordance |
| — | `GET /api/v1/projects/{id}/activity` → the audit trail (`action`, `resource_type`, `outcome`, `actor_id`, `created_at`), for a project activity feed |
| — | `GET /api/v1/sdg-pipeline` → which OpenRouter models the SDG pipeline is configured with right now |

---

## 8. Not an endpoint — three status codes you currently treat as one

`apiFetch` throws the same way for every non-2xx. Three of them are
actionable and the user cannot recover without being told which:

| Code | Meaning | What to show |
|---|---|---|
| **429** | Concurrency quota — you already have a job running | "You already have a job running. Try again in N seconds." Read the **`Retry-After`** response header for N. |
| **402** | Monthly OpenRouter budget cap reached (SDG) | A budget message — retrying will not help |
| **503** | Circuit breaker open — the provider is failing | "The AI provider is temporarily unavailable" + retry later |

A generic "request failed" for a 429 is the difference between a user
waiting 30 seconds and a user thinking the product is broken.

---

## Suggested order, if you want one

1. **§8 error codes** — smallest change, biggest reduction in confusing
   failures.
2. **§4 downloads** — a visible button that currently does nothing.
3. **§5 SDG reconnect** — one interface field plus one call.
4. **§1 evaluation** — replaces a simulated screen with real results.
5. **§3 usage / §2 HPO** — the two remaining simulated screens.

## Two things this document does *not* cover

- **Auth.** Separate doc: `docs/patches/smart-model-tune-auth.md`. Nothing
  above needs it today.
- **Deployment / API Keys / Analytics-as-a-product.** Those screens read
  Supabase and have **no Engine backend at all** — there is one shared
  inference endpoint, no per-model deployment, no API-key issuance, no
  per-key metering. That is an open product decision, not something you can
  wire up. (§3 gives you real usage data; it does not give you per-API-key
  billing.)

## Questions

Anything that does not match what you see, tell us and we will fix the doc —
the field lists above are generated from `openapi.json`, so a mismatch means
either your build is against an older Engine or we made an error worth
correcting.
