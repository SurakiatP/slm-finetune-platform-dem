# API Guide — SLM Fine-Tuning Platform

Reference for every HTTP and WebSocket endpoint exposed by the platform.
For setup / quickstart see [`README.md`](./README.md). For the live OpenAPI
schema (with full request/response shapes), hit
<http://localhost:8000/docs> after `docker compose up -d`.

---

## Conventions

- **Base URL**: `http://localhost:8000` (configurable via env)
- **Versioning**: every resource lives under `/api/v1/`
- **Auth**: none — all endpoints are open (per project scope)
- **Content type**: `application/json` for everything except `upload-seed`
  (multipart) and `download` (binary stream)
- **Async jobs** (SDG, training, export, evaluation) return **`202 Accepted`**
  with a `job_id`; subscribe to `ws://.../ws/jobs/{job_id}` for progress
- **IDs**: every resource id is a UUID; `job_id` is the underlying Celery
  task id (also a UUID-shaped string)

### Error response shape

All non-2xx responses share one body:

```json
{
  "detail": "Project 00000000-... not found",
  "code": "not_found",
  "extra": null
}
```

| `code` | HTTP | When |
|--------|------|------|
| `validation_error` | 422 | Pydantic schema rejected the body; per-field details under `extra.errors` |
| `not_found` | 404 | Resource id doesn't exist |
| `conflict` | 409 | Resource exists but isn't ready (dataset still generating, model not exported, …) |
| `bad_request` | 400 | Semantic error not catchable by the schema (mismatched task_type, `stream=true`, …) |
| `payload_too_large` | 413 | Seed upload over 10 MiB |
| `bad_gateway` | 502 | Ollama daemon unreachable / returned 5xx |
| `internal_error` | 500 | Unhandled server error; `extra.correlation_id` quotes a server log line |

---

## Lifecycle at a glance

```
┌─────────┐   POST /projects   ┌─────────┐
│ create  ├───────────────────►│ project │
└─────────┘                    └────┬────┘
                                    │
            POST /datasets/upload-seed   (optional, for with_seed mode)
                                    │
            POST /datasets/generate ─┤   (SDG via OpenRouter — 202)
                                    │
                       WS job_id ───┤───► sdg_progress* → completed
                                    │
              POST /trainings ──────┤   (manual or HPO — 202)
                                    │
                       WS job_id ───┤───► training_progress* | hpo_progress*
                                    │     → completed (artifact created)
                                    │
        POST /models/{id}/export ───┤   (LoRA → GGUF + register w/ Ollama — 202)
                                    │
                       WS job_id ───┤───► completed (ollama_model_tag set)
                                    │
   POST /inference/chat/completions ┤   (forwarded to Ollama, OpenAI-compat)
                                    │
        POST /evaluations ──────────┤   (predict + metrics + optional LLM judge — 202)
                                    │
                       WS job_id ───┘───► completed (metrics_json populated)
```

---

## Resources

### Projects (`/api/v1/projects`)

A project is the top-level grouping; it pins a `task_type` and owns datasets
and trainings.

| Method | Path | What it does |
|--------|------|--------------|
| `POST` | `/api/v1/projects` | **Create a project.** Body: `{name, description?, task_type}`. `task_type` is one of `classification`, `tool_calling`, `qa` and is **immutable** after creation. Returns `201` with the new project. |
| `GET` | `/api/v1/projects` | **List projects** (paginated). Query: `limit` (1–200, default 50), `offset` (default 0). |
| `GET` | `/api/v1/projects/{id}` | **Get a project** by UUID. `404` if not found. |
| `PATCH` | `/api/v1/projects/{id}` | **Update name and/or description.** Both fields are optional in the body; `task_type` cannot be changed (create a new project instead). |
| `DELETE` | `/api/v1/projects/{id}` | **Delete a project.** Cascades to its datasets and trainings (FKs `ON DELETE CASCADE`). Returns `204`. |

### Datasets (`/api/v1/datasets`)

A dataset is a JSONL row collection in MinIO. Sources:
- `seed` — uploaded by the user
- `sdg` — generated via OpenRouter
- `merged` — seed + sdg (future)

| Method | Path | What it does |
|--------|------|--------------|
| `POST` | `/api/v1/datasets/upload-seed` | **Upload seed examples** (multipart). Form fields: `project_id`, `task_type`, `file` (JSON array OR JSONL), optional `name`. Each row is validated against the task's Pydantic schema; invalid rows are reported in `invalid_rows`. Cap: 10 MiB per upload. |
| `POST` | `/api/v1/datasets/generate` | **Run synthetic data generation** via OpenRouter. Body is a discriminated union on `sdg_mode`: `with_seed` (≥5 seed rows required) or `description_only` (per-task `classification_config` / `tool_calling_config`). Returns `202` + `{job_id, dataset_id, websocket_url}`. The dataset row is created immediately with `num_samples=0`; it fills in once the worker completes. |
| `GET` | `/api/v1/datasets` | **List datasets**, optionally filtered by `project_id`. Paginated. |
| `GET` | `/api/v1/datasets/{id}` | **Get dataset metadata** (no rows). `storage_uri` is `null` until generation completes. |
| `GET` | `/api/v1/datasets/{id}/preview?limit=20` | **Preview the first N rows.** Streams JSONL line-by-line from MinIO and stops at `limit` (1–200). `409` if the dataset has no rows yet. |
| `GET` | `/api/v1/datasets/{id}/download` | **Download the raw JSONL** as a streaming response (`application/x-ndjson`). |
| `DELETE` | `/api/v1/datasets/{id}` | **Delete a dataset.** Best-effort MinIO object cleanup; the row is removed even if the storage delete fails. `204`. |

### Trainings (`/api/v1/trainings`)

Each training run produces one `ModelArtifact` (LoRA adapter) on success.

| Method | Path | What it does |
|--------|------|--------------|
| `POST` | `/api/v1/trainings` | **Start a training job.** Discriminated on `mode`: `manual` (user supplies `manual_config` — lr, epochs, LoRA…) or `hpo` (Optuna search over `hpo_config.search_space`, then a final retrain on the best params). Returns `202`. The base model must be in `/api/v1/base-models` (ADR-002). |
| `GET` | `/api/v1/trainings` | **List training jobs.** Filters: `project_id`, `status` (`pending`/`running`/`completed`/`failed`/`cancelled`). Paginated. |
| `GET` | `/api/v1/trainings/{id}` | **Get a training job** with full config + status + `mlflow_run_id` once available. |
| `DELETE` | `/api/v1/trainings/{id}` | **Cancel** a pending or running job. Revokes the Celery task (`SIGTERM`) and flips the row to `cancelled`. Idempotent on terminal-status jobs. |
| `GET` | `/api/v1/trainings/{id}/mlflow-url` | **Resolve the MLflow run URL** for this training (deep link into the MLflow UI). Returns `null` for `mlflow_url` if the run hasn't started yet. |

### Models (`/api/v1/models`)

`models` here means **trained-model artifacts** (one per successful training).
Their LoRA adapters and exported formats (GGUF, SafeTensors) live in MinIO.

| Method | Path | What it does |
|--------|------|--------------|
| `GET` | `/api/v1/models` | **List artifacts.** Filter by `project_id` (joins through TrainingJob). Paginated. |
| `GET` | `/api/v1/models/{id}` | **Get an artifact** with all URIs (`lora_adapter_uri`, `gguf_uri`, `safetensors_uri`, `ollama_model_tag`). |
| `POST` | `/api/v1/models/{id}/export` | **Export to GGUF or SafeTensors.** Body: `{format: "gguf" \| "safetensors", quantization?: "q4_k_m" \| …}`. GGUF additionally registers the model with the local Ollama daemon (best-effort; missed daemon → upload still happens, `ollama_model_tag` stays `null`). Returns `202`. |
| `GET` | `/api/v1/models/{id}/download?format=gguf` | **Stream a previously-exported file.** Only `gguf` is implemented (single blob). `safetensors` is a multi-file directory and currently returns `400` with the `s3://` URI for direct MinIO access. |

### Inference (`/api/v1/inference`) — OpenAI-compatible

Thin proxy in front of the local Ollama daemon. Fields the platform actively
needs are typed; extras are forbidden.

| Method | Path | What it does |
|--------|------|--------------|
| `POST` | `/api/v1/inference/chat/completions` | **OpenAI-compatible chat completions.** `model` accepts either a `ModelArtifact` UUID (the platform looks up `ollama_model_tag`) or a literal Ollama tag (`llama3.2:3b`). `stream: true` is rejected with `400` — the PoC doesn't proxy SSE. |
| `POST` | `/api/v1/inference/completions` | **Legacy text completions** (same identifier rules as above). |
| `GET` | `/api/v1/inference/models` | **List models** known to the local Ollama daemon (OpenAI shape). |

### Evaluations (`/api/v1/evaluations`)

Run an evaluation by submitting one model + one dataset; the worker predicts
each row via Ollama, computes per-task metrics, and optionally asks an LLM
judge to score qualitative outputs.

| Method | Path | What it does |
|--------|------|--------------|
| `POST` | `/api/v1/evaluations` | **Start an evaluation.** Body: `{model_artifact_id, dataset_id, use_llm_judge?, judge_model?}`. The artifact must have an `ollama_model_tag` (i.e. you exported it to GGUF first). Returns `202`. |
| `GET` | `/api/v1/evaluations/{id}` | **Get evaluation results** including `metrics_json` (per-task numbers) and `llm_judge_score` (mean across rows, when judge was on). |
| `POST` | `/api/v1/evaluations/compare` | **Compare 2–10 evaluation runs.** Body: `{evaluation_ids: [UUID, ...]}`. Returns a pivot: `{metric_name: {evaluation_id: value \| null}, judge_scores: {evaluation_id: value \| null}}`. |

#### Per-task metrics

| Task | Metrics emitted |
|------|----------------|
| Classification | `accuracy`, `f1_macro`, `f1_per_label` (dict), `confusion_matrix`, `out_of_set_predictions`, `n` |
| Tool calling | `json_validity`, `name_accuracy`, `arg_accuracy` (conditional on name match), `exact_match`, `n` |
| QA | `exact_match`, `rouge1`, `rouge2`, `rougeL`, `bleu`, `n` |

All ratios are in `[0, 1]`.

### Metadata (`/api/v1/tasks`, `/api/v1/base-models`)

Static catalogs that power the frontend's dynamic forms — no DB or Celery.

| Method | Path | What it does |
|--------|------|--------------|
| `GET` | `/api/v1/tasks` | List the **3 supported task types** with their JSON Schema and a canonical example row (powers the seed-upload form). |
| `GET` | `/api/v1/tasks/{task_type}/example` | Return the **example row** for one task type only. |
| `GET` | `/api/v1/base-models` | List the **6 supported base models** (Llama 3.2 1B/3B, Qwen2.5 0.5B/1.5B/3B, Gemma 2 2B — all Unsloth 4-bit). Llama 3.2 3B is the default per ADR-002. |

### System

| Method | Path | What it does |
|--------|------|--------------|
| `GET` | `/health` | **Liveness probe.** Always `200 {"status": "ok"}` if the API process is up. Doesn't probe Postgres / Redis. |
| `GET` | `/` | Tiny landing JSON pointing at `/docs`. |

---

## WebSocket — `/ws/jobs/{job_id}`

Subscribe with `job_id` from any 202-Accepted response. The server forwards
messages from Redis channel `job:{job_id}` verbatim, so the wire schema is
exactly `api.schemas.progress.WSMessage`.

```
GET /ws/jobs/c4f8…  →  WS upgrade → forwards messages until done
```

### Message types (the `type` field is the discriminator)

| `type` | When emitted | Key fields |
|--------|--------------|------------|
| `sdg_progress` | After every batch during SDG | `phase` (`generating`/`validating`/`deduplicating`/`persisting`), `samples_generated`, `samples_target`, `samples_valid`, `samples_rejected`, `duplicates_removed` |
| `training_progress` | On every Trainer `on_log` (per-step) | `epoch`, `epochs_total`, `step`, `steps_total`, `train_loss`, `eval_loss`, `learning_rate`, `samples_per_second`, `gpu_memory_mb` |
| `hpo_progress` | At the end of each Optuna trial | `trial_number`, `trials_total`, `current_params`, `best_value`, `best_params`, `last_trial_value`, `last_trial_pruned`, `inner_progress` (nullable nested `training_progress`) |
| `completed` | Terminal — job succeeded | `result` (free-form per task), optional `mlflow_run_id`, `dataset_id`, `model_artifact_id` |
| `failed` | Terminal — job raised | `error`, `error_type` |

Clients should treat `completed` / `failed` as the close signal and disconnect.

> **No replay on reconnect.** If a client misses messages, refresh state by
> hitting the read endpoint (`GET /datasets/{id}` etc.); only messages
> published *after* connect are forwarded.

---

## Common flows

### A — Classification (description-only SDG, manual training)

```
1. POST /projects                {task_type: "classification"}
2. POST /datasets/generate       {sdg_mode: "description_only",
                                  classification_config.labels: [...] }
   → 202 + ws://.../ws/jobs/<job_id>  → wait for completed
3. POST /trainings               {mode: "manual", manual_config: {...}}
   → 202 + ws://.../ws/jobs/<job_id>  → wait for completed
4. POST /models/{id}/export      {format: "gguf"}
   → 202 → wait for completed (ollama_model_tag now set)
5. POST /evaluations             {model_artifact_id, dataset_id}
   → 202 → wait for completed → GET /evaluations/{id} → metrics_json
6. POST /inference/chat/completions  {model: <model_artifact_id>, messages: [...]}
```

### B — QA (with-seed SDG, HPO training, LLM judge)

```
1. POST /projects                {task_type: "qa"}
2. POST /datasets/upload-seed    (multipart JSONL, ≥5 rows)
3. POST /datasets/generate       {sdg_mode: "with_seed",
                                  seed_data: <inline> }   ← OR reuse the seed dataset directly
4. POST /trainings               {mode: "hpo",
                                  hpo_config: {n_trials, search_space, ...}}
   → ws emits hpo_progress per trial; completed result.best_metric_value
5. POST /models/{id}/export      {format: "gguf", quantization: "q4_k_m"}
6. POST /evaluations             {model_artifact_id, dataset_id,
                                  use_llm_judge: true}
   → llm_judge_score populated alongside metrics_json
```

### C — Tool calling (description-only with tools)

```
1. POST /projects                {task_type: "tool_calling"}
2. POST /datasets/generate       {sdg_mode: "description_only",
                                  tool_calling_config.tool_definitions: [
                                    {name, description, parameters}, ...
                                  ]}
3. POST /trainings               {mode: "manual"}
4. POST /models/{id}/export      {format: "gguf"}
5. POST /inference/chat/completions   ← tools list is baked into the SYSTEM
                                       prompt of the registered Ollama model
6. POST /evaluations
   → metrics_json includes json_validity, name_accuracy, arg_accuracy
```

---

## Pagination

All list endpoints share the same envelope:

```json
{
  "items": [ ... ],
  "total": 137,
  "limit": 50,
  "offset": 0
}
```

`limit` is capped at 200; `offset` is unbounded but stable only as long as the
underlying ordering (creation time, descending) is stable.

---

## Status codes — what to expect

| Operation | Happy path | Common failures |
|-----------|------------|-----------------|
| Create resource | `201` | `422` (validation), `404` (parent missing) |
| Get resource | `200` | `404` |
| List | `200` | — |
| Update (PATCH) | `200` | `404`, `422` |
| Delete | `204` | `404` |
| Submit async job | `202` | `404` (parent missing), `409` (dependency not ready), `422` (validation), `502` (Ollama down — for inference) |
| Cancel job | `202` | `404` (also returns the existing terminal status idempotently) |
| Download | `200` (stream) | `404`, `409` (not yet exported), `400` (multi-file format) |

---

## OpenAPI

Live machine-readable schema: `GET /openapi.json` — give this URL to your
frontend. Swagger UI: `/docs`. ReDoc: `/redoc`. The `openapi_tags` group
endpoints into the same buckets used in this guide.
