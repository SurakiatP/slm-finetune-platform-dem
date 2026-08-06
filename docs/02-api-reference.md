# API Reference

**Purpose**: Endpoint-by-endpoint reference for the SLM Fine-Tuning Platform
backend API — every path, parameter, request/response shape, status code,
and gotcha you need to integrate against it without reading router code.

**Audience**: Frontend engineers wiring up a UI against this API (either
`smart-model-tune` via `VITE_ENGINE_HOST`, or this repo's own `frontend/` on
`feat/web-ui`), and backend engineers who need a contract refresher before
changing a router.

This is the human companion to [`openapi.json`](./openapi.json); regenerate
that with `scripts/export_openapi.py` when the contract changes. All routes
are mounted under `/api/v1` except `GET /health` (root-level). The spec
currently has **33 paths / 40 operations**; this doc covers all of them.

**A note on error codes**: FastAPI's auto-generated OpenAPI only documents
the success response and a generic `422` (Pydantic validation failure) for
each operation — `openapi.json` does not list the `400`/`404`/`409`/`413`/
`502` responses that the service layer actually raises via `HTTPException`.
Those are real and documented below per-endpoint, sourced from the router →
service call chain, not from the spec.

Cross-references: [`01-architecture.md`](./01-architecture.md) (data model /
status lifecycle), [`03-realtime-websocket.md`](./03-realtime-websocket.md)
(job progress over `/ws/jobs/{job_id}` — every `202 Accepted` response below
includes a `websocket_url` field for this; see also
[Job Progress](#job-progress) below for the REST snapshot of the same
frames), and
[`04-frontend-integration-smart-model-tune.md`](./04-frontend-integration-smart-model-tune.md)
(which UI screen consumes what).

---

## Duplicate submissions

The three job-submit endpoints — `POST /datasets/generate`, `POST /trainings`,
`POST /evaluations` — dedupe repeats inside a **60-second window**, so a
double-clicked Launch button enqueues one Celery task and buys one OpenRouter
bill instead of two.

The window is keyed by the caller plus a SHA-256 of the canonical request body
(keys sorted, so re-serialising a body does not change it). A repeat inside the
window returns the **original `202` response verbatim**, with:

```
X-Idempotent-Replay: true
```

Nothing is enqueued for a replay. A body that differs in any field is a
different job and proceeds normally.

Two things worth knowing:

- **It works without a token.** The caller is `user.id` when authenticated, and
  `anon:<X-Forwarded-For first hop>` otherwise. Phase-1 clients that send no
  `Authorization` header are covered.
- **Send `Idempotency-Key` if you want to control it.** When present, that
  header replaces the body hash — useful if you deliberately want to submit the
  same body twice, or to make a retry after a network timeout safe.

`POST /models/{id}/export` is **not** in this window; it uses a stricter
resource-state guard instead (see its `409` below).

Dedupe is best-effort: if Redis is unavailable the request proceeds normally
rather than failing, since losing dedupe is strictly better than losing a
submission.

---

## Authentication

Every route below **except the `Metadata` section** requires a Supabase JWT once
`AUTH_REQUIRED=true`. See [ADR-009](./adr/ADR-009-supabase-jwt-auth.md).

```
Authorization: Bearer <supabase access token>
```

Get the token client-side from `supabase.auth.getSession()`. The backend verifies
it against the project's JWKS (`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`),
checking signature, `exp`, `aud` (`authenticated`) and `iss`. There is no separate
API key and no backend login endpoint — Supabase is the only identity source.

### Two-phase rollout — what you get today

`AUTH_REQUIRED` defaults to **`false`**, and until it is flipped:

| Request | Phase 1 (`false`) | Phase 2 (`true`) |
|---|---|---|
| No `Authorization` header | **served anonymously**, no ownership filtering | `401` |
| Valid token | served, scoped to that user | served, scoped to that user |
| Invalid / expired / forged token | **`401`** | `401` |

The third row is the one to internalise: *absent* is tolerated in phase 1,
*invalid* never is. Sending a broken token is worse than sending none.

### Public routes

`/api/v1/tasks`, `/api/v1/tasks/{task_type}/example`, `/api/v1/base-models` and
`/api/v1/sdg-pipeline` are static catalogs with no DB access and no user data —
readable without a token in both phases, so a login screen can populate its
pickers. `/health`, `/docs`, `/redoc` and `/openapi.json` are also open.

### Ownership

`Project.owner_id` holds the token's `sub`. Every other resource inherits its
owner by foreign key (`Dataset`/`TrainingJob` → project; `ModelArtifact` →
training → project; `EvaluationRun` → artifact → training → project). You never
send `owner_id` — it is set server-side on create and rejected as input.

Two responses that will look wrong until you know why:

- **Another user's existing resource returns `404`, not `403`.** A `403` would
  confirm the resource exists; the message is byte-identical to a genuine
  not-found so the two cannot be told apart.
- **Resources created before authentication existed (`owner_id IS NULL`) are
  invisible to everyone** once you send a token. They fail closed. If you had
  test data before the cutover, it needs an owner assigned or it disappears.

### WebSocket

`/ws/jobs/{job_id}` cannot use a header — browsers do not allow them on
`new WebSocket()`. Pass the token as a subprotocol instead:

```js
new WebSocket(url, ["bearer", accessToken]);
```

The server echoes `bearer` back as the selected subprotocol. Close codes:

| Code | Meaning |
|---|---|
| `4401` | no credential while auth is required, **or** a credential was offered that failed verification or wasn't the two-value `["bearer", token]` shape |
| `4403` | authenticated, but the job is unknown **or** belongs to another user — deliberately the same code and reason for both, so the endpoint can't be probed for which job ids exist |

Unlike the HTTP header, a *malformed* subprotocol is rejected rather than treated
as anonymous: a header can be mangled by proxies, a subprotocol is only ever set
by your own code.

`GET /api/v1/jobs/{job_id}/progress` carries the same ownership rule as the
socket, and collapses every failure — no frame yet, TTL expired, corrupt payload,
unknown job, someone else's job — into one identical `404`.

## Projects

Top-level grouping entity — one `task_type` per project, immutable after
creation. Source: `api/routers/projects.py`, `api/schemas/projects.py`,
`api/services/projects_service.py`.

### POST /api/v1/projects

Create a project. `api/routers/projects.py:19-29`.

- **Body** (`ProjectCreate`, `extra="forbid"`):

  | Field | Type | Constraints |
  |---|---|---|
  | `name` | string | required, 1–200 chars |
  | `description` | string \| null | optional, ≤2000 chars |
  | `task_type` | enum | required: `classification` \| `tool_calling` \| `qa` |
  | `external_project_id` | string \| null | optional, ≤200 chars |

- **Success**: `201` `ProjectResponse` (adds `id`, `created_at`, `updated_at`).
- **Errors**: `409` if `external_project_id` is already mapped to another
  project (`api/services/projects_service.py:17-31`).
- **Gotcha**: `external_project_id` is the intended join key for an
  external system (e.g. a `smart-model-tune` Supabase project row) to map
  1:1 onto this Engine-side project — it must be globally unique. `task_type`
  is immutable; there's no way to change it later, only `PATCH name`/
  `description`.

```json
// Request
{
  "name": "support-ticket-router",
  "description": "Classify inbound tickets into one of three queues",
  "task_type": "classification",
  "external_project_id": "supabase-proj-abc123"
}

// 201 response
{
  "id": "6c9f1e2a-...-000000000001",
  "name": "support-ticket-router",
  "description": "Classify inbound tickets into one of three queues",
  "task_type": "classification",
  "external_project_id": "supabase-proj-abc123",
  "created_at": "2026-07-28T10:00:00Z",
  "updated_at": "2026-07-28T10:00:00Z"
}
```

### GET /api/v1/projects

List projects, paginated. `api/routers/projects.py:32-45`.

- **Query params**: `limit` (1–200, default 50), `offset` (≥0, default 0),
  `external_project_id` (optional exact-match filter —
  `api/services/projects_service.py:47-67`).
- **Success**: `200` `Page[ProjectResponse]` — `{items, total, limit, offset}`.

### GET /api/v1/projects/{project_id}

Get one project. **Errors**: `404` if not found.

### PATCH /api/v1/projects/{project_id}

Update `name` and/or `description` (both optional; omit to leave unchanged).
`task_type` is not a field on `ProjectUpdate` — sending it is rejected by
`extra="forbid"` with `422`. **Errors**: `404` if not found.

### DELETE /api/v1/projects/{project_id}

Delete a project. **Success**: `204`. **Errors**: `404` if not found.
**Gotcha**: cascades to the project's datasets / trainings at the DB level
(per router summary) — there is no confirmation step or dry-run.
The project's `audit_events` rows are **not** cascaded away: their FK is
`ON DELETE SET NULL`, so the record of who deleted what survives. See the
activity endpoint below.

### GET /api/v1/projects/{project_id}/activity

Audit trail for one project, newest first.

**Query**: `limit` (1–200, default 50), `offset` (default 0).
**Success**: `200` with `Page[AuditEventResponse]`.
**Errors**: `404` if the project doesn't exist **or belongs to someone
else** — same shape as `GET /projects/{id}`, deliberately, so this can't be
used to probe which project ids exist.

Each event carries `action` (e.g. `project.create`, `sdg.submit`,
`training.completed`, `export.cancel`, `inference.chat_completions`,
`dataset.download`, `job.orphan_reconciled`), `resource_type` /
`resource_id`, `outcome` (`success` / `failure`), `actor_id` (the Supabase
`sub`, null for anonymous phase-1 callers), `request_id` (matches the
`X-Request-ID` response header of the call that caused it, and the
`request_id` field in the server logs), `created_at`, and a free-form
`metadata` object.

Events are written in the **same database transaction** as the action they
record, so the log cannot silently miss an entry: if the audit write fails,
the action fails with it.

**Gotcha**: events whose project was later deleted are not reachable here.
The rows survive the delete (`project_id` goes null) but no longer belong to
a project anyone can query by id.

---

## Datasets & SDG

Seed upload, synthetic data generation (two modes), and dataset lifecycle
reads. Source: `api/routers/datasets.py`, `api/schemas/sdg.py`,
`api/schemas/datasets.py`, `api/services/datasets_service.py`,
`api/services/sdg_service.py`.

### POST /api/v1/datasets/upload-seed

Upload seed examples (multipart form) — JSON array, JSONL, or (QA-only) PDF.
`api/routers/datasets.py:37-56`.

- **Body**: `multipart/form-data`
  | Field | Type | Notes |
  |---|---|---|
  | `project_id` | UUID (form) | required |
  | `task_type` | enum (form) | required; must match the project's `task_type` or `400` |
  | `file` | file | required; `.json`/`.jsonl` or `.pdf` (QA only) |
  | `name` | string (form) | optional display name |

- **Success**: `201` `SeedUploadResponse`:
  `dataset_id`, `task_type`, `num_samples` (0 for PDF uploads — QA pairs
  come from SDG later), `invalid_rows` (indexes that failed schema
  validation), `format_detection` (audit trail, see below), `pdf_uri`
  (set only for QA + PDF).
- **Format Detection**: if uploaded rows aren't already in the canonical
  shape for the task type, an LLM pass (`ai_engine/data_gen/format_detector.py`)
  renames/maps fields before validation. `format_detection.ran=False` when
  the seed was already canonical or for PDF uploads. This audit trail is
  persisted on the `Dataset` row (`generation_metadata.format_detection`).
- **Caps**: JSON/JSONL ≤ 10 MiB, PDF ≤ `MAX_SEED_PDF_BYTES` (25 MiB) —
  `413` over the cap (`api/services/datasets_service.py:71-74, 400-417, 621-626`).
- **Errors**:
  - `404` project not found.
  - `400` project/upload `task_type` mismatch; empty file; unparseable
    JSON; top-level JSON not an array; PDF for non-QA task_type; corrupt PDF
    (`PdfCorruptError`); no valid rows survive Format Detection + schema
    validation.
  - `413` file exceeds size cap, or PDF fails page/byte probe
    (`PdfTooLargeError`/`PdfTooManyPagesError`).
  - `422` all rows structurally valid but fail the **semantic guard**
    (`assert_semantic_fit`) — e.g. a QA file that Format-Detected cleanly
    into classification shape but is the wrong *kind* of content. Currently
    only classification has a semantic guard.
- **Gotcha**: for QA + PDF, the returned `dataset_id` is what you later pass
  as `seed_dataset_id` to `POST /datasets/generate` in `with_seed` mode —
  the SDG worker reads the PDF and derives Q&A pairs; `num_samples` on the
  seed dataset itself stays 0 forever.

```json
// multipart fields: project_id=..., task_type=qa, file=policy.pdf

// 201 response
{
  "dataset_id": "00000000-0000-0000-0000-000000000099",
  "task_type": "qa",
  "num_samples": 0,
  "invalid_rows": [],
  "format_detection": {
    "ran": false, "model_used": null, "field_mapping": {},
    "rows_total": 0, "rows_canonicalised": 0, "rows_dropped": 0,
    "notes": "PDF upload — Format Detection not applicable"
  },
  "pdf_uri": "s3://datasets/seed-pdfs/00000000-....pdf"
}
```

### POST /api/v1/datasets/generate

Generate a synthetic dataset via OpenRouter. Body is a discriminated union
on `sdg_mode`. `api/routers/datasets.py:59-70`, `api/schemas/sdg.py`.

- **Body — shared fields** (`_SDGRequestBase`, `extra="forbid"`):
  | Field | Type | Constraints |
  |---|---|---|
  | `project_id` | UUID | required |
  | `task_type` | enum | required |
  | `task_description` | string | required, ≥10 chars |
  | `num_samples` | int | required, 1–10,000 |
  | `holdout_size` | int | default 100, 0–2,000 — extra rows persisted as a **separate child Dataset** (`parent_dataset_id`) for leak-free eval; stratified by label/tool name, random for QA |
  | `temperature` | float | default 0.9, 0.0–2.0 |
  | `dataset_name` | string \| null | optional; defaults to project + timestamp |

- **`sdg_mode: "with_seed"`** (`SDGRequestWithSeed`) — adds
  `seed_dataset_id: UUID` (required). Must reference a dataset from
  `POST /datasets/upload-seed` with `source=seed` and matching `task_type`
  and `project_id`; for QA it may instead carry a `pdf_uri`.
- **`sdg_mode: "description_only"`** (`SDGRequestDescriptionOnly`) — adds:
  - `classification_config.labels: list[str]` — **required when
    `task_type=classification`**; min 2 entries, non-empty, unique.
  - `tool_calling_config.tool_definitions: list[ToolDefinition]` —
    **required when `task_type=tool_calling`**; min 1, unique `name`s.
  - `qa` needs neither config block.
  - Sending a config block that doesn't match `task_type` (e.g.
    `classification_config` with `task_type=qa`) is a `422` from the
    model validator, not silently ignored.
- **Success**: `202` `SDGJobAcceptedResponse` — `job_id` (Celery task id),
  `dataset_id` (placeholder row, 0 rows until the job completes),
  `status=pending`, `websocket_url` (`/ws/jobs/{job_id}`).
- **Errors**:
  - `404` project not found, or (with_seed) `seed_dataset_id` not found.
  - `400` project/request `task_type` mismatch; seed dataset
    `source != seed`; seed `task_type` mismatch; seed belongs to a
    different project; PDF seed used with `task_type != qa`.
  - `409` seed dataset has neither JSONL rows nor a `pdf_uri` (nothing to
    seed from) — `api/services/sdg_service.py:130-143`.
  - `422` Pydantic validation (missing task-specific config, duplicate
    labels/tool names, `num_samples` out of range, etc).
- **Gotcha (breaking change, Phase 9)**: the old inline `seed_data: [...]`
  field is gone. `with_seed` mode requires `seed_dataset_id` referencing a
  dataset uploaded via `upload-seed` first — inline seed rows are rejected
  with `422` (`extra="forbid"` on the schema). `smart-model-tune` was found
  still sending the old shape; see the workspace hub `TASK_TRACKER.md`.

```json
// with_seed mode
{
  "sdg_mode": "with_seed",
  "project_id": "00000000-0000-0000-0000-000000000001",
  "task_type": "qa",
  "task_description": "Answer questions about our 30-day return policy",
  "num_samples": 200,
  "holdout_size": 50,
  "temperature": 0.9,
  "seed_dataset_id": "00000000-0000-0000-0000-000000000099"
}

// description_only mode
{
  "sdg_mode": "description_only",
  "project_id": "00000000-0000-0000-0000-000000000002",
  "task_type": "classification",
  "task_description": "Classify customer support tickets",
  "num_samples": 500,
  "holdout_size": 100,
  "classification_config": { "labels": ["billing", "technical", "general"] }
}

// 202 response (either mode)
{
  "job_id": "celery-task-id-here",
  "dataset_id": "11111111-...-0000000000aa",
  "status": "pending",
  "websocket_url": "ws://localhost:8000/ws/jobs/celery-task-id-here"
}
```

### GET /api/v1/datasets

List datasets, optionally filtered by `project_id`. Query: `project_id`
(optional), `limit` (1–200, default 50), `offset` (≥0, default 0).
Success: `200` `Page[DatasetResponse]`.

### GET /api/v1/datasets/{dataset_id}

Get a dataset. **Errors**: `404`.

- **Response shape** (`DatasetResponse`) — the fields worth knowing:
  `status` (`pending`/`running`/`completed`/`failed`/`cancelled` — lifecycle
  of row population, not of the whole dataset object), `error_message`
  (set when `status=failed` — the SDG worker's exception), `num_samples`,
  `storage_uri` (null until rows are persisted), `generation_metadata`
  (free-form dict — holds `format_detection`, `seed_dataset_id`,
  `celery_task_id`, `pdf_uri`, etc depending on how the dataset was made),
  `celery_task_id` (top-level field, added alongside the new job-control
  endpoints — the SDG job id for datasets generated via `POST
  /datasets/generate`, `null` for plain seed uploads; use this to reconnect
  to `/ws/jobs/{id}` or `GET /jobs/{id}/progress` after a page reload
  instead of digging into `generation_metadata`, which still mirrors the
  same value for backward compatibility), `parent_dataset_id` (set on
  holdout children — use the parent for training, the child for eval).

### GET /api/v1/datasets/{dataset_id}/preview

Preview the first N rows. Query: `limit` (1–200, default 20). Success:
`200` `DatasetPreviewResponse` (`samples: list[dict]`, `total: int`).
**Errors**: `404` not found; `409` dataset has no rows yet (still
generating) — `storage_uri` is null.

### GET /api/v1/datasets/{dataset_id}/download

Stream the raw JSONL file (`application/x-ndjson`, `Content-Disposition:
attachment`). **Errors**: `404` not found; `409` no rows yet (same check as
preview).

### DELETE /api/v1/datasets/{dataset_id}

Delete a dataset. **Success**: `204`. **Errors**: `404` not found; `409`
if any `TrainingJob` or `EvaluationRun` still references it (both FKs are
`ondelete=RESTRICT`) — delete those first, or delete the parent project to
cascade (`api/services/datasets_service.py:200-233`).

### POST /api/v1/datasets/{dataset_id}/cancel

Cancel a running SDG generation job. Revokes the underlying Celery task
(`celery_task_id`, falling back to `generation_metadata.celery_task_id` for
rows that predate the dedicated column) and flips `status=cancelled`.
`api/services/datasets_service.py:259-287`.

- **Success**: `200` — `{"dataset_id": "...", "status": "cancelled"}`.
- **Errors**: `404` if the dataset doesn't exist.
- **Idempotent**: cancelling a dataset that's already terminal
  (`completed`/`failed`/`cancelled`) returns `200` with the *existing*
  status, performs no revoke, and mutates nothing. This also covers plain
  seed uploads — they're persisted with `status=completed` synchronously,
  so cancelling one is a no-op report, not an error.
- **Gotcha**: a broker failure during the revoke call is logged and
  swallowed — the status still flips to `cancelled` either way. No
  WebSocket terminal frame is published by this endpoint; the SDG task's
  own handler publishes `JobFailed` when it notices the revoke, and a
  second terminal frame from here would double-deliver.

```json
// 200 response
{ "dataset_id": "11111111-...-0000000000aa", "status": "cancelled" }
```

### GET /api/v1/sdg-pipeline

Live metadata endpoint — returns the fixed LLM model ids the SDG pipeline
currently uses. `api/routers/tasks_meta.py:225-235`. No params. Success:
`200` `SdgPipelineModels`: `generator`, `judge`, `diversity_rules` (mirrors
`ai_engine/data_gen/models.py` module constants so the frontend doesn't
hardcode a copy that goes stale).

---

## Trainings

Start manual or HPO fine-tuning runs; read status, metrics, and MLflow
links; cancel. Source: `api/routers/trainings.py`, `api/schemas/training.py`
(request side), `api/schemas/trainings.py` (read side),
`api/services/training_service.py`, `api/services/trainings_service.py`.

### POST /api/v1/trainings

Start a training job — `mode` discriminates `manual` vs `hpo`.
`api/routers/trainings.py:35-49`.

- **`mode: "manual"`** (`ManualTrainingRequest`): `project_id`,
  `dataset_id`, optional `base_model` (defaults to
  `settings.default_base_model`; must be in `GET /base-models` — else
  `422`), optional `training_name`, `manual_config`
  (`ManualTrainingConfig` — LR, epochs, batch size, LoRA rank/alpha, etc;
  see [`01-architecture.md`](./01-architecture.md) for the tuned RTX 3060
  defaults). All fields optional with 3060-safe defaults if omitted.
- **`mode: "hpo"`** (`HPOTrainingRequest`): same base fields plus
  `hpo_config.search_space` (per-hyperparameter `float`/`int`/`categorical`
  ranges — at least one must be set), `hpo_config.n_trials` (2–20, default
  6), `hpo_config.fixed_config` (baseline for params not searched).
- **Success**: `202` `TrainingJobAcceptedResponse` — `job_id`,
  `training_id`, `status=pending`, `websocket_url`. (`mlflow_run_id`/
  `mlflow_url` are null at submission — the worker populates them once the
  run starts; poll `GET /trainings/{id}` or `GET /trainings/{id}/mlflow-url`.)
- **Errors** (same checks for both modes,
  `api/services/training_service.py:72-165, 167-283`):
  - `404` project or dataset not found.
  - `400` dataset belongs to a different project; dataset `task_type` ≠
    project `task_type`.
  - `409` dataset not ready (`storage_uri` unset or `num_samples=0` — SDG
    still running or seed never uploaded).
  - `422` `base_model` not in the supported allowlist; (HPO only)
    `n_trials` exceeds `settings.default_hpo_max_trials`; (HPO only)
    `per_device_train_batch_size` search-space choices exceed the
    RTX-3060-safe ceiling for the chosen `base_model` + `max_seq_length`
    (a defense-in-depth OOM guard — manual mode has no such check; power
    users can overshoot at their own risk).
- **Gotcha**: manual mode skips the VRAM safety check that HPO enforces —
  a single manual-mode OOM just fails that one job, whereas an unsafe HPO
  search space would poison every trial.

```json
// manual mode
{
  "mode": "manual",
  "project_id": "00000000-0000-0000-0000-000000000001",
  "dataset_id": "00000000-0000-0000-0000-000000000010",
  "base_model": "unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
  "training_name": "qa-policy-v1",
  "manual_config": {
    "learning_rate": 2e-4, "num_train_epochs": 3,
    "per_device_train_batch_size": 2, "gradient_accumulation_steps": 8,
    "optim": "adamw_8bit",
    "lora": { "r": 16, "alpha": 32, "dropout": 0.05 }
  }
}

// hpo mode
{
  "mode": "hpo",
  "project_id": "00000000-0000-0000-0000-000000000001",
  "dataset_id": "00000000-0000-0000-0000-000000000010",
  "training_name": "qa-policy-search",
  "hpo_config": {
    "n_trials": 6, "objective_metric": "eval_loss", "direction": "minimize",
    "sampler": "tpe", "pruner": "median",
    "search_space": {
      "learning_rate": { "type": "float", "low": 1e-5, "high": 5e-4, "log": true },
      "lora_r": { "type": "categorical", "choices": [8, 16, 32] }
    }
  }
}

// 202 response (either mode)
{
  "job_id": "celery-task-id",
  "training_id": "22222222-...-0000000000bb",
  "mlflow_run_id": null, "mlflow_url": null,
  "status": "pending",
  "websocket_url": "/ws/jobs/celery-task-id"
}
```

### GET /api/v1/trainings

List training jobs. Query: `project_id`, `status` (query alias for
`status_filter`; one of `JobStatus`), `limit` (1–200, default 50), `offset`
(≥0, default 0). Success: `200` `Page[TrainingResponse]`.

### GET /api/v1/trainings/{training_id}

Get a training job. **Errors**: `404`. `TrainingResponse` includes
`config_json` (the full submitted config, manual or HPO),
`best_metric_value`/`best_params_json` (HPO winners, null for manual mode),
`error_message`, `started_at`/`ended_at`.

### DELETE /api/v1/trainings/{training_id}

Cancel a running/pending training job. **Note the status code — 202, not
204** (unlike Projects/Datasets deletes): the revoke is a request to the
Celery broker, not a synchronous guarantee. Idempotent — cancelling an
already-terminal job (`completed`/`failed`/`cancelled`) returns the
existing status rather than erroring
(`api/services/trainings_service.py:71-104`). **Errors**: `404` not found.
Response body: `{"training_id": "...", "status": "cancelled"}`.

### POST /api/v1/trainings/{training_id}/cancel

**Alias for `DELETE /api/v1/trainings/{training_id}` above** — delegates
to the exact same service function (`trainings_service.cancel_training`),
so the two verbs can never disagree on behaviour. Added for clients that
prefer a `POST .../cancel` shape consistent with the new dataset/
export/evaluation cancel endpoints, without deprecating the existing
`DELETE`. **Note the status code differs from its siblings**: `200`, not
`202` — unlike `DELETE`, which returns `202` because historically it was
documented as "a request to the broker, not a synchronous guarantee."
Both verbs perform the identical revoke-then-flip-status work; only the
documented status code differs, since `DELETE` predates this alias and
its `202` contract is left unchanged. Same idempotency semantics as
`DELETE`. **Errors**: `404` not found.

```json
// 200 response
{ "training_id": "22222222-...-0000000000bb", "status": "cancelled" }
```

### GET /api/v1/trainings/{training_id}/mlflow-url

Resolve the MLflow run URL. **Errors**: `404` training not found. Success:
`200` `MlflowUrlResponse` — `mlflow_url` is `null` if the run hasn't started
yet (no `mlflow_run_id`/`mlflow_experiment_id` on the row); no error is
raised for that case.

### GET /api/v1/trainings/{training_id}/metrics

Full metric history (every key MLflow logged) plus, for HPO runs, a child
trial summary. `api/routers/trainings.py:109-118`. **Errors**: `404`
training not found; `502` if MLflow is unreachable
(`api/services/trainings_service.py:214-222`). If the run hasn't started
(`mlflow_run_id` still null), returns `200` with `metrics={}` and
`hpo_children=null` — **not a 404**. `hpo_children` is `null` for manual
mode, a list (possibly empty) for HPO mode.

### GET /api/v1/trainings/{training_id}/loss-history

Lightweight `train_loss` + `eval_loss` series only, for chart components.
**Errors**: `404` training not found; `502` if MLflow is unreachable.
**Gotcha**: if the run hasn't started yet (no `mlflow_run_id`) this returns
`200` with **empty arrays**, not a 404 — same pattern as `/metrics`. Points
are sorted by step and de-duplicated by `(step, value)` server-side
(`api/services/trainings_service.py:138-158`) so the frontend can plot
directly without its own dedupe pass.

---

## Models & Export

Trained-model artifacts (one per successful `TrainingJob`) — list, export
to GGUF/SafeTensors, download. Source: `api/routers/models.py`,
`api/schemas/artifacts.py`, `api/services/model_service.py`.

Note: the REST resource is `models` but the ORM package is `api.models` —
the router module is named `models.py` for REST clarity and doesn't
collide (`api/routers/models.py:1-5`).

### GET /api/v1/models

List model artifacts. Query: `project_id`, `training_job_id`, `limit`
(1–200, default 50), `offset` (≥0, default 0). Success: `200`
`Page[ModelArtifactResponse]`.

### GET /api/v1/models/{model_id}

Get one artifact. **Errors**: `404`. Response includes `lora_adapter_uri`
(set once training completes), `gguf_uri`/`safetensors_uri` (set only
after a successful export of that format), `ollama_model_tag` (set once
GGUF export registers with Ollama — required for inference and for
evaluations with an LLM judge), `export_error_message`, `export_status`
(`JobStatus | None` — `null` means no export was ever requested for this
artifact, distinct from `pending`/`running`/`failed`/`cancelled`; added
alongside the export-cancel endpoint below), `export_celery_task_id`
(the export job id — reconnect to `/ws/jobs/{id}` or `GET
/jobs/{id}/progress` with it).
**Gotcha**: `ModelArtifact` still has **no generic `status` field**
covering the whole row (unlike Dataset/TrainingJob/EvaluationRun) —
`export_status` only covers the export sub-lifecycle, added *purely
additively* alongside the URI/error-message fields
(`workers/tasks/model_export.py:542-561`), which remain the completion
contract callers already relied on before this field existed. In
practice `export_status=completed` and `gguf_uri`/`safetensors_uri` being
set are written in the same transaction, so either signal works — but
don't drop the URI check if you're supporting clients built before
`export_status` existed.

### POST /api/v1/models/{model_id}/export

Enqueue a GGUF or SafeTensors export. `api/routers/models.py:66-77`.

- **Body** (`ModelExportRequest`): `format` (`gguf` | `safetensors`;
  `lora` is a valid enum value at the schema level but the export worker
  raises on it — see gotcha below), `quantization` (string, GGUF only,
  e.g. `q4_k_m` (default) / `q5_k_m` / `f16` — passed straight through to
  `llama-quantize`, not validated against a closed list at the API layer;
  ignored for `safetensors`).
- **Success**: `202` `ModelExportResponse` — `artifact_id`, `format`,
  `job_id`, `status=pending`, `websocket_url`.
- **Errors**: `404` model not found; `409` artifact has no
  `lora_adapter_uri` on file (training likely never completed —
  `api/services/model_service.py:88-101`); `409` **an export is already in
  flight** for this artifact (`export_status` is `pending` or `running`).
  The detail names the in-flight `job_id` so you can cancel it via
  `POST /models/{id}/export/cancel` first. A *terminal* `export_status`
  (`completed` / `failed` / `cancelled`) does not block a fresh export —
  re-exporting at a different quantization is a normal thing to do.
  This guard replaces the idempotency window used by the other submit
  endpoints: one artifact can only have one export at a time, so a
  resource-state `409` is both stricter and more informative than a
  replayed `202` would be.
- **Gotcha**: `format=lora` passes request validation (it's a legal
  `ArtifactFormat` value) but the Celery worker
  (`workers/tasks/model_export.py:171`) raises `ValueError("unsupported
  export format")` for anything other than `gguf`/`safetensors` — that
  failure surfaces asynchronously (via `export_error_message` on the
  artifact / the WebSocket `failed` message), not as an HTTP error on this
  call. In practice only request `gguf` or `safetensors`.

```json
// Request
{ "format": "gguf", "quantization": "q4_k_m" }

// 202 response
{
  "artifact_id": "33333333-...-0000000000cc",
  "format": "gguf",
  "job_id": "celery-task-id",
  "status": "pending",
  "websocket_url": "/ws/jobs/celery-task-id"
}
```

### POST /api/v1/models/{model_id}/export/cancel

Cancel an in-progress export. Revokes `export_celery_task_id` and flips
`export_status=cancelled`. `api/services/model_service.py:132-162`.

- **Success**: `200` — `{"artifact_id": "...", "status": "cancelled"}`.
- **Errors**:
  - `404` model artifact not found.
  - **`409 Conflict`** when `export_status is None` — no export was ever
    requested for this artifact. This is the one cancel endpoint in this
    group that can 409: unlike datasets/trainings/evaluations (which
    always have *something* to be terminal about), a freshly-trained
    artifact with no export request yet has nothing to cancel, and
    reporting a fake `cancelled` transition for a job that was never
    enqueued would be actively misleading. Call `POST
    /models/{id}/export` first.
- **Idempotent** once `export_status` is already terminal
  (`completed`/`failed`/`cancelled`): returns `200` with the current
  status, no revoke performed. The `409` case above is distinct from this
  — it's "nothing to cancel, ever," not "already cancelled."
- **Gotcha**: same broker-failure-swallowed and no-terminal-WS-frame
  behavior as the other cancel endpoints — see the dataset-cancel gotcha
  above.

```json
// 200 response
{ "artifact_id": "33333333-...-0000000000cc", "status": "cancelled" }

// 409 response (no export ever requested)
{
  "detail": "Model 33333333-...-0000000000cc has no export in progress — POST /api/v1/models/33333333-...-0000000000cc/export first.",
  "code": "conflict",
  "extra": null
}
```

### GET /api/v1/models/{model_id}/download

Stream a previously-exported artifact. Query: `format` (alias `fmt`,
default `gguf`). **Errors**: `404` model not found; `409` requested format
was never exported for this model, or (GGUF specifically) no `.gguf` blob
was found under the export prefix; `400` unsupported `format` value, or
format is `safetensors`/`lora` — those are multi-file directories not
zipped server-side, so this endpoint returns `400` telling the caller to
fetch objects via the MinIO API directly (`api/services/model_service.py:
127-194`). **Only GGUF actually streams a file through this endpoint.**

---

## Evaluations

Task-specific metrics + optional LLM-judge scoring against a held-out
dataset; compare runs side by side. Source: `api/routers/evaluations.py`,
`api/schemas/evaluations.py`, `api/services/evaluation_service.py`.

### POST /api/v1/evaluations

Start an evaluation run. `api/routers/evaluations.py:26-36`.

- **Body** (`EvaluationCreate`): `model_artifact_id` (required),
  `dataset_id` (required), `use_llm_judge` (bool, default `false`),
  `judge_model` (optional string override for `LLM_JUDGE_MODEL` when
  `use_llm_judge=true`).
- **Success**: `202` `EvaluationAcceptedResponse` — `evaluation_id`,
  `job_id`, `status=pending`, `websocket_url`.
- **Errors** (`api/services/evaluation_service.py:28-73`):
  - `404` model artifact not found, or dataset not found.
  - `409` model artifact has no `ollama_model_tag` (not yet exported+
    registered — `POST /models/{id}/export` with `format=gguf` first);
    dataset has no rows persisted (`storage_uri` unset or
    `num_samples=0`).
  - `400` dataset `task_type` doesn't match the artifact's owning
    project's `task_type` (checked only when the training job/project can
    still be resolved).
- **Gotcha**: evaluation requires a **GGUF-exported, Ollama-registered**
  model — you cannot evaluate a raw LoRA adapter. Run `POST
  /models/{id}/export {"format":"gguf"}` first and wait for it to complete.

```json
// Request
{
  "model_artifact_id": "33333333-...-0000000000cc",
  "dataset_id": "44444444-...-0000000000dd",
  "use_llm_judge": true,
  "judge_model": "anthropic/claude-3.5-sonnet"
}

// 202 response
{
  "evaluation_id": "55555555-...-0000000000ee",
  "job_id": "celery-task-id",
  "status": "pending",
  "websocket_url": "/ws/jobs/celery-task-id"
}
```

### GET /api/v1/evaluations

List evaluation runs. Query: `model_artifact_id`, `dataset_id`, `status`
(alias for `status_filter`), `limit` (1–200, default 50), `offset` (≥0,
default 0). Success: `200` `Page[EvaluationResponse]`.

### GET /api/v1/evaluations/{evaluation_id}

Get one run. **Errors**: `404`. `EvaluationResponse` includes
`metrics_json` (task-specific metrics, null until complete),
`llm_judge_score`/`llm_judge_model`, `error_message`.

### POST /api/v1/evaluations/{evaluation_id}/cancel

Cancel a running evaluation. Revokes `celery_task_id`, flips
`status=cancelled`, and stamps `ended_at`. `api/services/
evaluation_service.py:151-171`.

- **Success**: `200` — `{"evaluation_id": "...", "status": "cancelled"}`.
- **Errors**: `404` if the evaluation run doesn't exist.
- **Idempotent**: cancelling an already-terminal run
  (`completed`/`failed`/`cancelled`) returns `200` with the existing
  status and does nothing else.
- **Gotcha**: same broker-failure-swallowed and no-terminal-WS-frame
  behavior as the other cancel endpoints — see the dataset-cancel gotcha
  under [Datasets & SDG](#datasets--sdg).

```json
// 200 response
{ "evaluation_id": "55555555-...-0000000000ee", "status": "cancelled" }
```

### POST /api/v1/evaluations/compare

Pivot metrics across multiple runs into a chart-ready grid.
`api/routers/evaluations.py:74-83`.

- **Body** (`EvaluationCompareRequest`): `evaluation_ids: list[UUID]` —
  **min 2, max 10** entries.
- **Success**: `200` `EvaluationCompareResponse` —
  `metrics: {metric_name: {evaluation_id: value_or_null}}` (union of metric
  names across all runs; a run missing a given metric gets `null`, not a
  dropped key, so the frontend can render a complete grid),
  `judge_scores: {evaluation_id: score_or_null}`.
- **Errors**: `404` if any id in `evaluation_ids` doesn't resolve to an
  existing `EvaluationRun` — the error lists *all* missing ids at once
  (`api/services/evaluation_service.py:159-171`), not just the first.

```json
// Request
{ "evaluation_ids": ["55555555-...-ee", "66666666-...-ff"] }

// 200 response
{
  "evaluation_ids": ["55555555-...-ee", "66666666-...-ff"],
  "metrics": { "accuracy": { "55555555-...-ee": 0.91, "66666666-...-ff": 0.88 } },
  "judge_scores": { "55555555-...-ee": 4.2, "66666666-...-ff": null }
}
```

---

## Job Progress

REST snapshot of the last WebSocket frame published for any job (SDG,
training, HPO, export, evaluation) — for clients that don't want to hold a
socket open, or a WS client's own first paint. Source:
`api/routers/jobs.py`. See
[`03-realtime-websocket.md`](./03-realtime-websocket.md) for the full
`WSMessage` payload shapes and the snapshot mechanism (ADR-008).

### GET /api/v1/jobs/{job_id}/progress

Return the last-published progress frame for a job, validated against the
`WSMessage` discriminated union (`sdg_progress` / `training_progress` /
`hpo_progress` / `export_progress` / `evaluation_progress` / `completed` /
`failed`). `api/routers/jobs.py:26-51`.

- **Success**: `200` — one `WSMessage` variant, keyed by its `type`
  discriminator. Same JSON shape a WebSocket client would receive over
  `/ws/jobs/{job_id}`.
- **Errors**: `404` + the standard `ErrorResponse` (`{"detail": "..."}`)
  when no frame exists for `job_id` — either the job never published one,
  or the snapshot's 24h Redis TTL expired. **A corrupt or legacy stored
  payload that fails `WSMessage` validation is also reported as `404`,
  never `500`** — a snapshot that can't be parsed is treated as
  equivalent to no snapshot (`api/routers/jobs.py:44-51`).
- **Gotcha**: the snapshot is a UX accelerator, not a source of truth —
  it lives in Redis with a 24h TTL and is lost on a Redis flush.
  Authoritative job state remains each resource's own `status` column
  (`Dataset.status` / `TrainingJob.status` / `EvaluationRun.status` /
  `ModelArtifact.export_status`); fall back to the relevant `GET` on a
  `404` here rather than treating it as "job doesn't exist."

```json
// 200 response (training_progress example)
{
  "type": "training_progress",
  "job_id": "celery-task-id",
  "timestamp": "2026-08-04T10:00:00Z",
  "epoch": 1.5,
  "epochs_total": 3,
  "step": 120,
  "steps_total": 240,
  "train_loss": 0.42,
  "eval_loss": 0.51,
  "learning_rate": 0.0002,
  "samples_per_second": 3.1,
  "gpu_memory_mb": 8192
}

// 404 response — standard ErrorResponse shape (api/core/exceptions.py)
{ "detail": "No progress frame for job celery-task-id", "code": "not_found", "extra": null }
```

---

## Inference

OpenAI-compatible passthrough to the local Ollama daemon, for playground /
A-B comparisons. Source: `api/routers/inference.py`,
`api/schemas/inference.py`, `api/services/inference_service.py`.

### POST /api/v1/inference/chat/completions

OpenAI-compatible chat completions. `api/routers/inference.py:23-32`.

- **Body** (`ChatCompletionRequest`, `extra="forbid"`): `model` (string —
  either a `ModelArtifact` UUID **or** a literal Ollama tag, e.g.
  `llama3.2:3b`, for comparing against an un-fine-tuned base), `messages`
  (min 1, roles `system`/`user`/`assistant`/`tool`), `temperature` (0–2,
  default 0.7), `top_p` (0–1, default 1.0), `max_tokens` (1–8192,
  optional), `stream` (default `false`), `stop`, `seed`.
- **Success**: `200` `ChatCompletionResponse` (OpenAI shape:
  `id`/`object`/`created`/`model`/`choices`/`usage`).
- **Errors**:
  - `400` **`stream=true` is rejected** — `"streaming is not supported on
    /api/v1/inference (set stream=false)"` (`api/services/
    inference_service.py:41-45`). This API is non-streaming only.
  - `404` `model` is a UUID but no matching `ModelArtifact` exists; or
    Ollama itself 404s the resolved tag.
  - `409` `model` resolves to a `ModelArtifact` with no
    `ollama_model_tag` set — export it with `format=gguf` first.
  - `502` Ollama is unreachable, or returns any other `4xx`/`5xx`.
- **Gotcha**: passing a `ModelArtifact` UUID as `model` only works after a
  successful GGUF export (same registration requirement as evaluations
  with an LLM judge). Passing a raw Ollama tag bypasses that check
  entirely — useful for comparing against stock base models.

```json
// Request
{
  "model": "33333333-...-0000000000cc",
  "messages": [{"role": "user", "content": "What's your return policy?"}],
  "temperature": 0.7,
  "stream": false
}

// 200 response (shape; values illustrative)
{
  "id": "chatcmpl-...", "object": "chat.completion", "created": 1234567890,
  "model": "slm-platform/qa-policy-v1:latest",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "You can return items within 30 days..."},
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 12, "completion_tokens": 18, "total_tokens": 30}
}
```

### POST /api/v1/inference/completions

OpenAI-compatible legacy text completions. Same `model` resolution,
`stream=true` rejection (`400`), and error modes as chat completions above.
Body (`CompletionRequest`): `model`, `prompt` (string or list of strings),
`temperature`, `top_p`, `max_tokens` (default 256, 1–8192), `stream`,
`stop`, `seed`.

### GET /api/v1/inference/models

List models the local Ollama daemon currently has loaded (OpenAI `/models`
shape). No params. Success: `200` `ModelDescriptorList` —
`{object: "list", data: [{id, object, created, owned_by, metadata}]}`.

**Owner-scoped.** Entries in our own namespace (`slm/…`, one per exported
fine-tune) are filtered to the caller's own artifacts. Base models the
daemon has pulled in (`llama3.2:1b`, …) carry no ownership information and
stay listed for everyone — hiding them would only make a model picker lie
about what the daemon can serve. An anonymous caller (no `Authorization`
header, phase 1) gets the unfiltered list, identical to pre-auth behaviour.

**Tag shape.** `id` for our models is the canonical `slm/<first-8-of-uuid>`,
not the `slm/<8hex>:latest` the daemon reports — Ollama appends an implicit
version on create that the DB never stores. The id in this response is
exactly the string `model` accepts on `/chat/completions` and
`/completions`, so a picker's value round-trips. The suffixed form is also
accepted on those endpoints for callers that copied it out of `ollama list`.

---

## Metadata

Static/near-static endpoints that power dynamic frontend forms — no
DB/Celery involved except `/sdg-pipeline` which reads live module
constants. Source: `api/routers/tasks_meta.py`, `api/main.py`.

### GET /api/v1/tasks

List the 3 supported task types. No params. Success: `200`
`list[TaskTypeInfo]` — each entry has `task_type`, `display_name`,
`description`, `sample_schema` (JSON Schema of one training row for that
task type), `example` (one canonical example row),
`sdg_modes_supported` (`["with_seed", "description_only"]` for all three —
both modes are supported for every task type).

### GET /api/v1/tasks/{task_type}/example

Get the canonical example row for one task type. Path param `task_type`
(enum — an invalid value is rejected by Pydantic path validation with
`422` before the handler even runs; the handler's own `404` branch is
effectively unreachable, per its `pragma: no cover` comment at
`api/routers/tasks_meta.py:85-89`). Success: `200` — a raw `dict` (not a
wrapped schema), e.g. `{"text": "I can't log into my account", "label":
"technical"}` for classification.

### GET /api/v1/base-models

List the Unsloth 4-bit base models supported for training (ADR-002
allowlist — this is also the source of truth `POST /trainings` validates
`base_model` against). No params. Success: `200` `list[BaseModelInfo]` —
`id` (HF model id, pass this as `base_model` in training requests),
`display_name`, `family`, `params_billions` (≤3.5 by schema constraint),
`context_length`, `recommended_max_seq_length`, `quantization`
(`"bnb-4bit"`), `license`, `notes`, `ollama_tag` (nullable — the
Ollama-Hub equivalent of the same instruct weights, for A/B-comparing a
fine-tuned artifact against its un-tuned base via `/inference/chat/completions`).
Currently 10 models across Llama 3.2, Qwen2.5, Qwen3, Gemma 2, SmolLM2, and
TinyLlama families (`api/routers/tasks_meta.py:100-208`).

### GET /api/v1/sdg-pipeline

Documented above under [Datasets & SDG](#datasets--sdg) — listed here too
since it's tagged `metadata` in the OpenAPI spec.

### GET /health

Liveness probe. No params, no DB check — just returns `{"status": "ok"}`.
Not under `/api/v1`, and public (no token). Use this for container
healthchecks. Deliberately dependency-free: it decides whether a supervisor
restarts the container, so it must never fail because something *else* is
down. `GET /ready` is the endpoint that asks about dependencies.

### GET /ready

Readiness probe. Probes PostgreSQL, Redis, MinIO and the Celery worker
concurrently, each bounded by a 3-second timeout. Not under `/api/v1`, and
public — orchestrators and load balancers have no token to send.

Success: `200` with a per-dependency breakdown.

```json
{
  "status": "degraded",
  "checks": {"postgres": "ok", "redis": "ok", "minio": "ok", "worker": "unavailable"}
}
```

`status` is `ok` (everything up), `degraded` (a non-fatal dependency is
down), or `unready`.

**`503` only when PostgreSQL or Redis is unreachable** — the two the API
cannot answer a single request without. **MinIO and the worker report
`unavailable` on a `200`**: with MinIO down, uploads and artifact downloads
fail but every read still works; with no worker, submits still enqueue and
`api/services/job_reconcile.py` ends orphaned jobs rather than leaving clients
spinning. Returning `503` for either would pull the whole API out of rotation
and take the UI offline to report a partial outage. See
`api/services/readiness.py` for the reasoning and
`tests/unit/test_readiness.py` for the guard.

---

## Verification notes

All 33 paths / 40 operations in `openapi.json` are covered above — the
enumeration was cross-checked against `python3 -c "import json;
json.load(open('openapi.json'))['paths']"` before writing this file
(33 paths, 40 GET/POST/PATCH/DELETE operations). This count includes the
5 job-control endpoints added alongside ADR-006/ADR-008 (`GET
/jobs/{job_id}/progress`, and one `POST .../cancel` each for datasets,
model export, evaluations, and trainings — the last being an alias for
the pre-existing `DELETE /trainings/{id}`).

Discrepancies found while writing this doc (not code changes — flagged for
awareness):

1. **`openapi.json` under-documents error responses.** Every operation's
   spec only lists its success code(s) plus a generic `422`. The `400`/
   `404`/`409`/`413`/`502` paths shown above are real (`HTTPException`
   raises in the service layer) but don't appear in the spec at all,
   because none of the routers pass an explicit `responses=` to their
   FastAPI decorators. If a typed client is code-genned from
   `openapi.json`, it will not know these status codes are possible.
2. **`api/services/training_service.py`'s module docstring is stale.** It
   says "HPO mode goes through `submit_hpo_training_job` (Phase 6 —
   currently 501)" — but `submit_hpo_training_job` is fully implemented
   (validates project/dataset/base_model/n_trials/VRAM-safety and enqueues
   `train_hpo`); there is no `501` anywhere in that file or
   `api/routers/trainings.py`. HPO training works end-to-end; the comment
   just wasn't updated after Phase 6 shipped.
3. **`/trainings/{id}/loss-history` and `/trainings/{id}/metrics` do not
   404 on "missing" data**, despite that being an intuitive assumption —
   they 404 only if `training_id` itself doesn't exist. If the training
   hasn't started logging to MLflow yet (`mlflow_run_id` still null), both
   return `200` with empty series, so the frontend can render an empty
   chart without special-casing a 404.
