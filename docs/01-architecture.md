# Architecture

## Purpose

This document explains what the SLM Fine-Tuning Platform backend does, how its
code is organized (hexagonal layering), where GPU work happens, what
infrastructure services it depends on, the hard constraints that shape design
decisions, and the shape of its core data model. It is meant to let a new
contributor understand the system's structure without reading every file.

## Audience

Backend/full-stack developers picking up this repository cold — including
anyone integrating a frontend against it. For the HTTP contract itself see
[API reference](./02-api-reference.md) and `openapi.json` in this directory;
for the live-progress channel (not in OpenAPI) see
[realtime WebSocket](./03-realtime-websocket.md); for frontend-specific
integration notes see
[frontend integration](./04-frontend-integration-smart-model-tune.md); for
the recorded decisions behind specific constraints or design changes see
[`docs/adr/`](./adr/README.md) (currently [ADR-006](./adr/ADR-006-defer-authentication.md)
and [ADR-008](./adr/ADR-008-ws-progress-snapshot.md); ADR-001–005 are
recorded only as the constraint table in §5 below).

---

## 1. What the system does

One-line pipeline: **describe a task → synthesize training data → fine-tune a
small model → export it → serve it → evaluate it.**

```
 [1] Project          POST /api/v1/projects
       │               (name + task_type: classification | tool_calling | qa)
       ▼
 [2] SDG (data_gen)    POST /api/v1/datasets/upload-seed   (seed JSONL or PDF)
       │               POST /api/v1/datasets/generate      (async, Celery)
       │               generate → validate → judge → dedup loop, + holdout split
       ▼
 [3] Fine-tune         POST /api/v1/trainings (mode: manual | hpo)
       │               Unsloth + QLoRA training on GPU (workers/tasks/training.py,
       │               workers/tasks/hpo_training.py); every run tracked in MLflow
       ▼
 [4] Export            POST /api/v1/models/{id}/export
       │               LoRA adapter → merged HF checkpoint → GGUF (llama.cpp),
       │               registered as an Ollama model tag
       ▼
 [5] Serve             POST /api/v1/inference/chat/completions
       │               OpenAI-compatible proxy to Ollama
       ▼
 [6] Evaluate          POST /api/v1/evaluations
                        task metrics + optional LLM-as-judge (qa/tool_calling only)
```

This mirrors the flow documented in `README.md:167` ("project → SDG → train →
export → infer → evaluate") and the ASCII diagram at `README.md:46-76`, both
verified against the router/service/worker code below. Progress for the
async stages (SDG, training, HPO) streams live over a WebSocket — see
[realtime WebSocket](./03-realtime-websocket.md).

---

## 2. Hexagonal layers

Three layers, one dependency direction: **`api/` → `ai_engine/` ← `workers/`**
(both `api` and `workers` may depend on `ai_engine`; `ai_engine` depends on
neither).

| Layer | Role | Representative files |
|---|---|---|
| `api/` | FastAPI HTTP surface: routers → services → schemas/models. Validation + orchestration only, no heavy compute. | `api/main.py:19-118` (router wiring), `api/routers/datasets.py`, `api/services/sdg_service.py:30-100` (submits the Celery task, doesn't run it) |
| `ai_engine/` | Pure domain logic: SDG generation, training, HPO, evaluation math. No FastAPI or Celery imports, ever. | `ai_engine/data_gen/generator.py`, `ai_engine/training/unsloth_trainer.py`, `ai_engine/hpo/optuna_objective.py`, `ai_engine/evaluation/metrics_qa.py` |
| `workers/` | Celery tasks: orchestrate `ai_engine` + sync DB session + MinIO + Redis pub/sub. This is where domain logic meets infrastructure. | `workers/tasks/data_generation.py`, `workers/tasks/training.py`, `workers/tasks/hpo_training.py`, `workers/tasks/model_export.py`, `workers/tasks/evaluation.py` |

**Verified boundary**: `grep -rn "^from fastapi\|^from celery" ai_engine/`
returns nothing — confirmed no FastAPI/Celery imports anywhere under
`ai_engine/`. Note the boundary is not zero-coupling, though: several
`ai_engine/` modules (`ai_engine/training/callbacks.py:20`,
`ai_engine/data_gen/generator.py:43-45`, `ai_engine/hpo/optuna_objective.py`)
import Pydantic types from `api/schemas/*` (e.g. `TrainingProgress`,
`TaskType`, `SDGRequestWithSeed`) — these are plain data-shape definitions
with no FastAPI/Celery dependency of their own, so they don't violate the "no
web framework in the domain layer" rule, but they do mean `ai_engine/` is not
a fully standalone package importable without `api/` on the path.

Call pattern example (SDG): `api/routers/datasets.py` → `api/services/sdg_service.py:81-87`
(`generate_synthetic_data.apply_async(...)`, an async Celery enqueue — the
HTTP request returns immediately) → Celery picks up
`workers/tasks/data_generation.py:48` (`generate_synthetic_data` task) →
which calls into pure `ai_engine/data_gen/generator.py`'s
`SyntheticDataGenerator.generate(...)` (`workers/tasks/data_generation.py:319-325`)
→ progress callback publishes to Redis (`workers/tasks/data_generation.py:74-91`).

---

## 3. Where GPU runs vs CPU-only

GPU (needs the `worker` container's CUDA runtime; gated by `deploy: *gpu-deploy`
in `docker-compose.yml:262-264`):

| File | What it does |
|---|---|
| `ai_engine/training/unsloth_trainer.py:126` (`UnslothTrainer`), `:390-394` (CUDA capability check) | Unsloth + QLoRA fine-tuning |
| `ai_engine/training/callbacks.py:30-39` (`_gpu_memory_mb`) | Reads live CUDA memory for `TrainingProgress.gpu_memory_mb` |
| `workers/tasks/training.py` | Manual-mode training task — invokes `UnslothTrainer` |
| `workers/tasks/hpo_training.py:78,396-406` | Optuna trials (each trains a model) + final retrain; `_release_gpu_memory()` cleanup |
| `workers/tasks/model_export.py:355-361` (`_release_gpu_memory`), plus the HF→GGUF conversion/quantization step (`_quantize_merged_to_gguf`, `:489-546`) driving `llama.cpp`'s `convert_hf_to_gguf.py` / `llama-quantize` | GGUF export — the merge/convert step needs the base model loaded |

CPU-only (everything else):

- All of `api/` (routers, services, schemas) — pure HTTP + validation + DB/MinIO I/O.
- SDG generation itself is CPU-bound network I/O, not local GPU compute — it
  calls OpenRouter over HTTPS (`ai_engine/data_gen/openrouter_client.py`),
  confirmed by no `torch`/`cuda` references anywhere under `ai_engine/data_gen/`.
- `workers/tasks/evaluation.py` — confirmed no `torch`/`cuda` imports; model
  inference for evaluation goes through Ollama's HTTP API (the GPU work
  already happened at export/serve time), so the evaluation task itself is
  CPU-only orchestration + metric computation (`ai_engine/evaluation/*`).
- `workers/tasks/data_generation.py` — CPU-only orchestration around the
  async OpenRouter calls.

---

## 4. Infrastructure services

From `docker-compose.yml` (9 service blocks; `minio-init` is a one-shot init
job, not a long-running service — README's "7 services" count
(`README.md:74-75`) is stale, it omits `minio-init` and `frontend`):

| Service | Purpose | Port (host) |
|---|---|---|
| `postgres` (`docker-compose.yml:41`) | App state (projects/datasets/training_jobs/model_artifacts/evaluation_runs) + separate `mlflow` DB | `${POSTGRES_PORT:-5432}` |
| `redis` (`:63`) | Celery broker (db1) + result backend (db2) + WS pub/sub channel (db0) | `${REDIS_PORT:-6379}` |
| `minio` (`:78`) | S3-compatible blob store for datasets/model artifacts + MLflow artifact root | `${MINIO_PORT:-9000}` (S3 API), `${MINIO_CONSOLE_PORT:-9001}` (console) |
| `minio-init` (`:98`) | One-shot `mc mb` job that creates the `mlflow`/`datasets`/`models` buckets on first boot | n/a |
| `mlflow` (`:124`) | Experiment tracking server (Postgres backend store + MinIO artifact store) | `${MLFLOW_PORT:-5000}` |
| `api` (`:166`) | FastAPI app (`uvicorn api.main:app --reload`) — the HTTP + WS surface | `${API_PORT:-8000}` (default 8000) |
| `frontend` (`:202`) | nginx serving the built React SPA (`frontend/`), reverse-proxying `/api` and `/ws` to `api` so the browser only ever talks to one origin (`docker/frontend.Dockerfile:1-9`, `docker/frontend.nginx.conf`) | `${FRONTEND_PORT:-8082}` |
| `worker` (`:218`) | Celery worker (GPU) — runs SDG/training/HPO/export/evaluation tasks | n/a (no exposed port) |
| `ollama` (`:265`) | OpenAI-compatible inference server (GPU) for the exported GGUF models | `${OLLAMA_PORT:-11434}` |

---

## 5. Hard constraints / ADRs

From the constraints table in `README.md:33-42` (this repo does not currently
contain the `require.md`, `CLAUDE.md`, or `docs/architecture/TECH_STACK.md`
files that `README.md` cross-references at lines 11-13 and 493-497 — those
apparently live only at the workspace level, outside this repo. `docs/adr/`
is the one exception: it now exists in this repo, see below):

| Constraint | Source (as cited by README) |
|---|---|
| Models ≤3B params, must fit in QLoRA 4-bit | ADR-002 |
| RTX 3060 12GB target hardware | `require.md` |
| No authentication system | [ADR-006](./adr/ADR-006-defer-authentication.md) |
| Web UI lives only in `frontend/` (never mixed into `api`/`workers`/`ai_engine`) | Session 10 scope change |
| MLflow for experiment tracking (not W&B / TensorBoard) | ADR-001 |
| OpenRouter for SDG (not direct OpenAI / Anthropic) | ADR-003 |
| Celery for async jobs (never FastAPI BackgroundTasks) | ADR-004 |
| Only 3 task types: classification, tool_calling, qa | ADR-005 |

The "No authentication system" row used to cite `require.md`, a file that no
longer exists in this repo (see the discrepancy note below). It now cites
[ADR-006](./adr/ADR-006-defer-authentication.md), which re-verifies the
constraint against current code, records it as a deliberate deferral rather
than an unsourced assumption, and lays out the migration path (Supabase JWT
verification, `owner_id` on `Project`) for when auth is scheduled.

Verified in code:
- 3 task types enforced by `api/schemas/enums.py:13-18` (`TaskType`: `classification`, `tool_calling`, `qa`), also referenced from `ai_engine/data_gen/generator.py:44`.
- Celery-only async: no `BackgroundTasks` import anywhere under `api/`; all long-running work is a `@celery_app.task` in `workers/tasks/*.py`.
- No auth: no auth middleware/dependency in `api/main.py`; CORS is wide open on methods/headers with `allow_credentials=False` (`api/main.py:92-99`). This is deliberate and deferred, not an oversight — see [ADR-006](./adr/ADR-006-defer-authentication.md) for the full accepted-risk writeup and intended future shape.
- MLflow: every training run opens an `mlflow_run_scope` (`ai_engine/training/mlflow_logger.py`, used from `workers/tasks/training.py` and `workers/tasks/hpo_training.py:128-138`).
- OpenRouter-only SDG: `ai_engine/data_gen/openrouter_client.py` is the only LLM client used by `ai_engine/data_gen/*`.

**Discrepancy note for reconciliation**: `README.md` (lines 11-13, 493-497)
points to `require.md`, `docs/adr/`, `CLAUDE.md`, and
`docs/architecture/TECH_STACK.md` as living inside this repo. That's now
only partially stale: `require.md`, `CLAUDE.md`, and
`docs/architecture/TECH_STACK.md` still don't exist here — they apparently
live only at the workspace level, outside this repo, per the workspace-level
`CLAUDE.md`'s "hub docs consolidated up one directory" note. `docs/adr/`,
however, **does now exist in this repo** (`docs/adr/README.md`,
`docs/adr/ADR-006-defer-authentication.md`,
`docs/adr/ADR-008-ws-progress-snapshot.md`) — added alongside the realtime
job-control work this branch ships. The in-repo `README.md` hasn't been
updated to reflect that `docs/adr/` is real now, so a reader following its
links from inside this repo alone will still hit dead references for the
other three paths, but not for `docs/adr/` anymore.

---

## 6. Data model

Five core SQLAlchemy entities under `api/models/`. All use a UUID primary key
(`api/models/base.py:50-52`, `uuid_pk()`) and a `created_at`/`updated_at`
`TimestampMixin` (`api/models/base.py:34-47`). Enums are persisted as native
Postgres enum types via `pg_enum()` (`api/models/base.py:55-67`), which
stores the enum's `.value` string, not its Python member name.

```
Project 1──* Dataset (self-referential: parent_dataset_id → holdout child)
Project 1──* TrainingJob *──1 Dataset
TrainingJob 1──1 ModelArtifact
ModelArtifact 1──* EvaluationRun *──1 Dataset
```

### Project — `api/models/project.py:19`
| Column | Notes |
|---|---|
| `id`, `name`, `description` | — |
| `task_type` | `TaskType` enum, fixed at creation |
| `external_project_id` | optional unique opaque ID an external frontend (e.g. a Supabase row) can use to map 1:1 to this Project, avoiding client-only mapping tables (`:30-42`) |

Relationships: `datasets` (1:N, cascade delete-orphan), `training_jobs` (1:N,
cascade delete-orphan).

### Dataset — `api/models/dataset.py:21`
| Column | Notes |
|---|---|
| `project_id` | FK → `projects.id`, `ondelete=CASCADE` |
| `task_type`, `source` (`DatasetSource`: `seed`\|`sdg`\|`merged`) | — |
| `status` | `JobStatus`, default `PENDING` — see lifecycle below |
| `error_message` | populated when `status=FAILED` |
| `num_samples`, `storage_uri` (s3://…), `size_bytes` | null/0 until generation completes |
| `generation_metadata` | JSONB — SDG context; also where `celery_task_id` is stashed (`generation_metadata.celery_task_id`, see [realtime WebSocket](./03-realtime-websocket.md)) |
| `parent_dataset_id` | FK → `datasets.id` (self-referential), set when this row is a holdout child of an SDG over-generation run; `generation_metadata.role` carries `"train"`/`"holdout"` (`:68-76`) |

Relationships: `project`, `training_jobs` (1:N), `evaluation_runs` (1:N),
`parent`/`holdout_children` (self-referential 1:N for the holdout split).

### TrainingJob — `api/models/training_job.py:22`
| Column | Notes |
|---|---|
| `project_id`, `dataset_id` | FKs; `dataset_id` is `ondelete=RESTRICT` (can't delete a dataset a training run still references) |
| `mode` | `TrainingMode`: `manual` \| `hpo` |
| `status` | `JobStatus`, default `PENDING` |
| `celery_task_id` | unique, doubles as the public `job_id` / WS channel suffix (`:46-51`) |
| `base_model`, `training_name` | — |
| `mlflow_experiment_id`, `mlflow_run_id` | — |
| `config_json` | JSONB — full `ManualTrainingConfig` or `HPOConfig` payload |
| `best_metric_value`, `best_params_json` | HPO-only; null for manual runs |
| `error_message`, `started_at`, `ended_at` | — |

Relationships: `project`, `dataset`, `model_artifact` (1:1, cascade
delete-orphan).

### ModelArtifact — `api/models/model_artifact.py:18`
| Column | Notes |
|---|---|
| `training_job_id` | FK, **unique** → 1:1 with `TrainingJob` |
| `name`, `base_model`, `mlflow_run_id` | — |
| `lora_adapter_uri`, `gguf_uri`, `safetensors_uri` | populated lazily as each export stage completes |
| `size_mb` | — |
| `ollama_model_tag` | unique; set once the GGUF is registered with Ollama |
| `export_error_message` | last export failure; cleared on successful re-export |

**No `status` column.** Export completion is inferred: `gguf_uri` set ⇒
GGUF export succeeded; `export_error_message` set (with `gguf_uri` still
null) ⇒ export failed; neither set ⇒ no export attempted yet or one is
currently running. Callers must check these two fields rather than a status
enum (`api/models/model_artifact.py:43-46`).

Relationships: `training_job` (1:1), `evaluation_runs` (1:N).

### EvaluationRun — `api/models/evaluation_run.py:21`
| Column | Notes |
|---|---|
| `model_artifact_id`, `dataset_id` | FKs; `dataset_id` is `ondelete=RESTRICT` |
| `celery_task_id` | unique — public `job_id` |
| `status` | `JobStatus`, default `PENDING` |
| `metrics_json` | JSONB — per-task metrics (accuracy/f1/rougeL/json_validity/…) |
| `llm_judge_score`, `llm_judge_model` | optional, mean judge score across rows |
| `error_message`, `started_at`, `ended_at` | — |

Relationships: `model_artifact`, `dataset`.

### Status lifecycle

`JobStatus` (`api/schemas/enums.py:35-42`): `pending → running → completed |
failed | cancelled`.

| Entity | Has `status: JobStatus`? | Who sets it |
|---|---|---|
| `Dataset` | Yes (default `PENDING`) | Seed uploads go straight to `COMPLETED` (synchronous); SDG-generated datasets start `PENDING` and are flipped `RUNNING`/`COMPLETED`/`FAILED` by `workers/tasks/data_generation.py:70,169,269` |
| `TrainingJob` | Yes (default `PENDING`) | `RUNNING`/`COMPLETED`/`FAILED` set by `workers/tasks/training.py` and `workers/tasks/hpo_training.py:114-115,291,328`. `CANCELLED` is the only entity where this value is actually used in code today, set by `api/services/trainings_service.py:101` (a cancel endpoint) — confirmed via `grep -rn "JobStatus.CANCELLED"`, no other entity sets it |
| `EvaluationRun` | Yes (default `PENDING`) | Set by `workers/tasks/evaluation.py` |
| `ModelArtifact` | **No status field** | Completion inferred from `gguf_uri`/`export_error_message` (see above) — this is the one entity that deliberately doesn't follow the shared lifecycle pattern |

---

## 7. SDG pipeline internals (high level)

Orchestrator: `ai_engine/data_gen/generator.py` (`SyntheticDataGenerator`,
docstring at `:1-31` lays out the full pipeline). Conceptually:

1. **Setup (once per job)** — resolve seed rows into label/tool examples,
   compute a 90/10 quota with a sentinel ("unknown"/reject) class for
   classification and tool-calling, and issue one meta-prompting call to
   generate "diversity rules" that steer later generation
   (`ai_engine/data_gen/meta_prompter.py`). For QA + PDF seeds, the PDF is
   converted to an initial pool of Q&A pairs in a one-shot multimodal call.
2. **Generate → validate → judge → dedup loop**, up to `MAX_LOOPS=20`
   (`ai_engine/data_gen/constants.py:12`):
   - Generate a batch (up to `GENERATOR_BATCH_SIZE=100` concurrent OpenRouter
     calls, `CANDIDATES_PER_GEN_CALL=5` candidates each — `constants.py:37-44`).
   - Schema + business validation (`ai_engine/data_gen/validators.py`).
   - LLM-as-judge batch scores each candidate; rows below `JUDGE_THRESHOLD`
     are dropped (`ai_engine/data_gen/judge.py`).
   - MinHash LSH near-duplicate filter (`ai_engine/data_gen/minhash_dedup.py`).
   - Quota-respecting collection into the accepted set, with an
     EMA-smoothed adaptive over-generation multiplier so later loops request
     roughly the right amount of raw output to hit target.
   - Loop ends early on success (quota met), continues to `MAX_LOOPS`
     (partial success), or aborts after `MAX_CONSECUTIVE_FAILURES`
     zero-yield loops (`SDGAbortedError`).
3. **Holdout split** — `ai_engine/data_gen/holdout_split.py`, invoked from
   `workers/tasks/data_generation.py:119-129` after generation. Splits
   `valid_rows` into a train set (persisted on the original `Dataset` row)
   and, if `holdout_size > 0`, a second `Dataset` row
   (`source=DatasetSource.SDG`, `parent_dataset_id` set,
   `generation_metadata.role="holdout"`) — see the leak-free evaluation note
   in `README.md:358-360`.

Models used (`ai_engine/data_gen/models.py`, hardcoded per Phase 9 decision —
no per-request override):

| Constant | Model | Used for |
|---|---|---|
| `FORMAT_DETECTION` (`:11`) | `google/gemini-2.5-flash-lite` | One-off key-rename pass on seed upload |
| `PDF_QA` (`:16`) | `google/gemini-2.5-flash-lite` | First-iteration PDF → Q&A extraction (multimodal) |
| `DIVERSITY_RULES` (`:19`) | `deepseek/deepseek-v4-flash` | Meta-prompting call (once per job) |
| `GENERATOR` (`:22`) | `deepseek/deepseek-v4-flash` | Main synthetic-row generation, up to ~100 concurrent calls/loop |
| `JUDGE` (`:26`) | `deepseek/deepseek-v4-flash` | LLM-as-judge scoring, up to ~500 concurrent calls/loop (5 candidates × 100 calls) |

Note all three generation-time roles (`DIVERSITY_RULES`/`GENERATOR`/`JUDGE`)
now point at the same `deepseek/deepseek-v4-flash` SKU; only the
upload-time/PDF helpers (`FORMAT_DETECTION`/`PDF_QA`) use a different,
cheaper multimodal model. See [realtime WebSocket](./03-realtime-websocket.md)
for how this loop's progress is streamed live.
