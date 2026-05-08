# Task Tracker

> All tasks at-a-glance, organized by phase. Update at end of every session.
> Source of truth for scope: [`require.md`](./require.md).

## Status Legend
- ⏳ Not Started · 🔧 In Progress · ✅ Done · ⛔ Blocked · ❌ Cancelled

---

## Phase 1: Foundation

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| F1 | Project structure (all dirs from require.md) | ✅ | Done — all packages have `__init__.py`; `.gitignore` written |
| F2 | `docker-compose.yml` (7 services) | ✅ | Done — postgres, redis, minio (+ minio-init), mlflow, api, worker (GPU), ollama (GPU); 3 Dockerfiles in `docker/` |
| F3 | `.env.example` | ✅ | Done — all DB/Redis/MinIO/OpenRouter/MLflow vars documented |
| F4 | `pyproject.toml` | ✅ | Done — base + `[training]` + `[eval]` + `[dev]`; `asyncpg` added (see Session 1 note) |
| F5 | `README.md` | ✅ | Done — overview, prereqs, quickstart, service URLs, dev workflow, troubleshooting |

## Phase 2: Schemas & Models

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| S1 | Enums (`TaskType`, `TrainingMode`, `SDGMode`, `JobStatus`) | ✅ | Done — also added `DatasetSource`, `ArtifactFormat`, `WSMessageType` |
| S2 | Per-task data format schemas | ✅ | Done — `ClassificationSample`, `ToolCallingSample` (validates JSON answer), `QASample`, `ToolDefinition`, `parse_samples()` |
| S3 | SDG request/response schemas | ✅ | Done — discriminated union by `sdg_mode`; per-task config validators |
| S4 | Training request/response schemas | ✅ | Done — `ManualTrainingRequest` / `HPOTrainingRequest` discriminated by `mode`; `HPOSearchSpace` enforces ≥1 param |
| S5 | WebSocket message schemas | ✅ | Done — discriminated union of 5 message types in `progress.py` |
| S6 | SQLAlchemy ORM models | ✅ | Done — 5 tables with FKs, naming convention, native PG enums via `pg_enum()` helper |
| S7 | Alembic init + first migration | ✅ | Done — `alembic.ini` + `env.py` (asyncpg→psycopg2 auto-rewrite) + handwritten `0001_initial`; verified via `alembic upgrade head --sql` |

## Phase 3: API Skeleton

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| A1 | FastAPI app + CORS + OpenAPI metadata | ✅ | Done — `api/main.py` + `api/core/{config,database,redis_client}.py`; lifespan disposes engine on shutdown |
| A2 | Routers mounted (return 501 for logic) | ✅ | Done — 25 HTTP paths across 7 routers; full request/response validation works pre-501 |
| A3 | WebSocket `/ws/jobs/{job_id}` + Redis pub/sub | ✅ | Done — async pubsub with `asyncio.wait(FIRST_COMPLETED)` to detect client disconnect cleanly |
| A4 | `/api/v1/tasks` static metadata | ✅ | Done — returns 3 task types with Pydantic-derived JSON schemas + canonical examples |
| A5 | `/api/v1/base-models` static metadata | ✅ | Done — 6 Unsloth 4-bit models (Llama 3.2 1B/3B, Qwen2.5 0.5B/1.5B/3B, Gemma 2 2B) |

## Phase 4: SDG Pipeline

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| D1 | OpenRouter client wrapper | ✅ | Done — sync `OpenRouterClient` w/ tenacity retries on connection/timeout/429 |
| D2 | Prompt templates (3 tasks × 2 modes = 6) | ✅ | Done — `build_prompt()` dispatcher + `Prompt` dataclass; uses `response_format: json_object` |
| D3 | Pydantic validators per task | ✅ | Done — `validate_generated_rows()` returns `(accepted, failures)`; closed-label + tool-name + param-type checks |
| D4 | Deduplication | ✅ | Done — `Deduplicator` (case + whitespace normalized SHA-1 of text/question); seed-aware |
| D5 | Celery task `generate_synthetic_data` | ✅ | Done — `SyntheticDataGenerator` orchestrator + `workers/{celery_app,sync_db,storage,progress}.py` + `workers/tasks/data_generation.py`; publishes `SDGProgress`/`JobCompleted`/`JobFailed` to `job:{id}`; persists JSONL to MinIO + updates Dataset row |
| D6 | Wire to `POST /datasets/generate` | ✅ | Done — `api/services/sdg_service.py` (project-exists + task-type-match guards); endpoint enqueues task and returns 202 |

## Phase 5: Training Pipeline (Manual)

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| T1 | Unsloth wrapper (load_in_4bit + LoRA) | ✅ | Done — `UnslothTrainer.train()` wraps Unsloth + TRL `SFTTrainer`; bf16/fp16 auto-select; eval_split=0.1 (skipped <4 rows) |
| T2 | Data formatters per task | ✅ | Done — `format_classification` (text/label), `format_qa` (Alpaca), `format_tool_calling` (ChatML w/ optional tool defs); dispatcher `get_formatter()` |
| T3 | WebSocketProgressCallback | ✅ | Done — `make_progress_callback(job_id, publish)` emits `TrainingProgress` to Redis on every `on_log` + mirrors numeric metrics into MLflow |
| T4 | MLflow logger (params, metrics, model) | ✅ | Done — `mlflow_run_scope()` context + `log_params_flat()` (handles nested dicts, lists) + `log_metrics_dict()`; `MlflowRunHandle.run_url` deep-link |
| T5 | Celery task `train.manual` | ✅ | Done — registered in `celery_app.include`; persists `mlflow_run_id`, uploads adapter via `put_directory()`, inserts `ModelArtifact`, flips status COMPLETED/FAILED, GPU cleanup in `finally` (ADR-002 STRICT) |
| T6 | Wire to `POST /trainings` mode=manual | ✅ | Done — `api/services/training_service.py` validates project/dataset/base-model-allowlist; router dispatches manual→service, hpo→501 stub |

## Phase 6: HPO Pipeline

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| H1 | Optuna search space builder | ✅ | Done — `sample_config(trial, search_space, fixed)` + `best_params_to_config()`; LoRA-side knobs merge into a fresh `LoRAConfig` |
| H2 | Objective function w/ nested MLflow | ✅ | Done — `HPOObjective` callable; one nested run per trial; pruning via `trial.report` + `should_prune`; `on_trial_done(TrialOutcome)` callback |
| H3 | Celery task `train.hpo` | ✅ | Done — registered in `celery_app.include`; parent MLflow run + nested trials + `best` retrain run; persists `best_metric_value` + `best_params_json`; uploads final adapter to MinIO |
| H4 | Wire to `POST /trainings` mode=hpo | ✅ | Done — `submit_hpo_training_job` replaces 501; same validation chain as manual + `n_trials <= settings.default_hpo_max_trials` |

## Phase 7: Inference & Evaluation

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| I1 | Ollama integration (LoRA → GGUF) | ✅ | Done — `workers/ollama_client.py` + GGUF export task; auto-registers `slm/{id8}` with daemon (best-effort) |
| I2 | OpenAI-compatible endpoints | ✅ | Done — async httpx proxy to Ollama `/v1/...`; UUID-or-tag identifier resolver; streaming rejected with 400 |
| I3 | Classification metrics | ✅ | Done — `metrics_classification.py`: accuracy, macro F1, per-label F1, confusion matrix, out-of-set count |
| I4 | Tool-calling metrics | ✅ | Done — `metrics_tool_calling.py` (stdlib only): json_validity, name_accuracy, arg_accuracy (conditional on name match), exact_match |
| I5 | QA metrics | ✅ | Done — `metrics_qa.py`: EM (case+ws normalised), ROUGE-1/2/L (rouge_score), corpus BLEU (sacrebleu, normalised to 0-1) |
| I6 | LLM-as-judge via OpenRouter | ✅ | Done — `llm_judge.py`: 1-5 rubric, JSON-mode response, mean across successful rows, skipped tracking |
| I7 | Model export (GGUF, SafeTensors) | ✅ | Done — Celery task `model.export`; uploads to MinIO + Ollama registration; wired `POST /models/{id}/export` returning 202 |

## Phase 8: Polish

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| W1 | Wire-501: projects + datasets + trainings CRUD | ✅ | Done — 3 services + 3 router updates; 0 remaining 501s; cancel revokes Celery + flips status |
| P1 | Comprehensive error handling | ✅ | Done — `api/core/exceptions.py` registers handlers for `HTTPException` (Starlette base — catches both routed + routing-layer), `RequestValidationError`, generic `Exception`; uniform `ErrorResponse(detail, code, extra)` |
| P2 | OpenAPI request/response examples | ✅ | Done — `json_schema_extra` examples on `ProjectCreate` (3), `SDGRequest{WithSeed,DescriptionOnly}`, `{Manual,HPO}TrainingRequest`, `EvaluationCreate`, `ModelExportRequest` |
| P3 | End-to-end integration test | ✅ | Done — `tests/integration/test_full_flow.py` (3 tests: QA full flow, classification create-only, 404 ErrorResponse shape); `pytest -m integration` |
| P4 | Example client scripts | ✅ | Done — `examples/python_client.py` (argparse + WS streaming) + `examples/quickstart_curl.sh` (jq-driven) |
| P5 | README API usage examples | ✅ | Done — full curl snippets for 6-step lifecycle + Python walkthrough pointer + ErrorResponse doc |

---

## Out of Scope (do NOT build)

- ❌ Authentication / user management
- ❌ Frontend (teammate handles)
- ❌ Multi-tenancy
- ❌ Distributed training (single GPU only)
- ❌ Models >3B parameters
- ❌ Direct OpenAI/Anthropic calls (use OpenRouter — see ADR-003)
