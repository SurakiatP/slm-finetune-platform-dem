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

## Phase 9: Production Hardening (Session 12 discovery)

> Bugs surfaced by the vast.ai full-lifecycle smoke test. Bugs #1–4 already shipped on `dev`. Bug #5 is the only remaining blocker for the §16 §6–9 chain (training → models → export → inference).

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| B1 | `DELETE /datasets/{id}` 500→409 when refs exist | ✅ | Done — pre-check + 409 envelope; regression test in `tests/integration/test_dataset_delete.py`; verified on production deploy. Commit `c117f2a` |
| B2 | `evaluation_strategy`→`eval_strategy` (HF Transformers ≥4.41) | ✅ | Done — single rename in `ai_engine/training/unsloth_trainer.py`. Commit `134da3f` |
| B3 | `SFTTrainer(tokenizer=…)`→`processing_class=…` (TRL ≥0.12, hard-removed 0.16) + migrate to `SFTConfig` | ✅ | Done — `dataset_text_field` / `max_seq_length` / `packing` moved into `SFTConfig`. Commit `4fb9fe3` |
| B4 | `SFTConfig.max_seq_length`→`max_length` (TRL ≥0.18) | ✅ | Done — single rename. Commit `1b6763b` |
| **B5** | **Unsloth `<EOS_TOKEN>` placeholder validator failure (TRL ≥0.20 + Unsloth-patched SFT)** | **🔧** | **See plan below — needs `data_formatters` rework, not a one-line fix** |

### B5 — Resume plan (for next session)

**Diagnosis (already verified):**
- `SFTTrainer.__init__()` line 662 raises `ValueError: The specified eos_token ('<EOS_TOKEN>') is not found in the vocabulary` on every train attempt, even with `eos_token=tokenizer.eos_token` passed explicitly to `SFTConfig`.
- Root cause: `unsloth_zoo` monkey-patches `trl.SFTConfig` and `trl.SFTTrainer` after `import unsloth`. The patched classes inject `<EOS_TOKEN>` as a chat-template substitution sentinel that's *expected* to be replaced by the actual EOS via Unsloth's `get_chat_template()` flow. We bypass that flow by feeding pre-rendered text via `dataset_text_field="text"`, so the substitution never happens and the validator catches the placeholder.
- Underlying tokenizer is fine: `tok.eos_token == '<|eot_id|>'` (verified inside the running worker container). The break is *only* in the SFT-time chat-template handshake.

**Recommended fix — Option A (path matches Unsloth's intended driver pattern):**
1. **`ai_engine/training/data_formatters.py`** — switch the per-task formatters to emit messages format instead of plain text:
   - `format_qa(row)` → `[{"role":"user","content":row["question"]}, {"role":"assistant","content":row["answer"]}]`
   - `format_classification(row)` → similar `(user_text, assistant_label)` two-message turn
   - `format_tool_calling(row)` → `(user_question, assistant_tool_call_json)` two-message turn; tool_definitions still go in the system prompt or as a separate tools field
   - Keep the dispatcher signature `get_formatter(task_type, tool_definitions=...) -> Callable[[dict], list[dict]]` (return type changes from `str` to `list[dict]`).
2. **`ai_engine/training/unsloth_trainer.py`** — wire the chat template before SFTConfig:
   - After `FastLanguageModel.from_pretrained()` and *before* `get_peft_model()`: call `tokenizer = get_chat_template(tokenizer, chat_template="llama-3.2")` (or `"chatml"` / `"qwen-2.5"` etc. depending on `self.base_model`).
   - Build `Dataset.from_list([{"messages": formatter(row)} for row in rows])` (field name `messages`, not `text`).
   - In SFTConfig drop `dataset_text_field="text"` and the explicit `eos_token=` we added in `d79da39`. With chat-templated messages, TRL+Unsloth handle the EOS substitution itself.
   - Remove the manual `eos_token=tokenizer.eos_token` line from `d79da39` since it's no longer needed (and was a partial fix).
3. **Base-model → chat-template mapping** — the Unsloth list of supported templates is in `unsloth.chat_templates.CHAT_TEMPLATES`. Likely picks: `"llama-3.2"` for the Llama 3.2 1B/3B, `"chatml"` for Qwen2.5, `"gemma-2"` for Gemma 2. Worth a small lookup dict keyed by base_model prefix; default to `"chatml"` if unknown.
4. **Tests** — `test_full_flow.py::test_qa_full_flow` already gates on `INTEGRATION_HAS_GPU=1`. Once the fix lands and the smoke test trains successfully on vast.ai, that test becomes a true regression guard. No new unit-level test is meaningfully possible without a GPU.

**vast.ai workflow when ready (VM is stopped, not destroyed):**
1. Restart instance from cloud.vast.ai dashboard (instant — preserves disk + hf-cache + pulled models).
2. SSH in, `cd /root/slm-platform && git pull origin dev`.
3. `docker compose restart worker` (api needs no restart for trainer-only changes; worker because the deferred imports re-evaluate on fresh task).
4. POST a manual training with the same minimal config from Session 12 (`Llama-3.2-1B-Instruct-bnb-4bit`, 1 epoch, 1 sample/batch, 5-row QA seed). Project + dataset from Session 12 already exist on the VM (preserved across stop/start).
5. Poll `/api/v1/trainings/{id}` until terminal. Expect `completed` with `best_metric_value` set + `mlflow_run_id` populated. If it fails again, capture `error_message` + `docker compose logs worker --tail 60` and iterate.
6. On success, continue §16 #6–9: model export (GGUF q4_k_m) → inference chat completion → optional evaluation. Each step has known shape per SWAGGER_GUIDE.md.

**Non-goal — DO NOT do in B5:**
- Don't pin Unsloth backwards. Floor pins (`trl>=0.18.2`, `transformers>=4.51.3`) make this not viable, and downgrading Unsloth itself drags `bitsandbytes` / `peft` / sm_89 support backwards too.
- Don't bypass Unsloth's monkeypatches by reordering imports. That's a stability landmine — Unsloth's perf wins come *from* the patches.
- Don't add backwards-compat shims that emit both `text` and `messages` fields. Either flow works; pick messages and move forward.

---

## Out of Scope (do NOT build)

- ❌ Authentication / user management
- ❌ Frontend (teammate handles)
- ❌ Multi-tenancy
- ❌ Distributed training (single GPU only)
- ❌ Models >3B parameters
- ❌ Direct OpenAI/Anthropic calls (use OpenRouter — see ADR-003)
