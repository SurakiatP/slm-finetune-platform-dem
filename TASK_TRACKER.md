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

## Phase 9: Production Hardening (Sessions 12–13 discoveries)

> Bugs surfaced by the vast.ai full-lifecycle smoke test. B1–B5 are shipped + verified on `dev`. **B5 closed in Session 13** with a 4-step fix that took the QA training all the way through `completed` on the 4060 Ti VM in 49 s. **B6 + B7** are new follow-ups uncovered while attempting `§16 #6–9` (model export → inference) — the post-training chain is gated on these.

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| B1 | `DELETE /datasets/{id}` 500→409 when refs exist | ✅ | Done — pre-check + 409 envelope; regression test in `tests/integration/test_dataset_delete.py`; verified on production deploy. Commit `c117f2a` |
| B2 | `evaluation_strategy`→`eval_strategy` (HF Transformers ≥4.41) | ✅ | Done — single rename in `ai_engine/training/unsloth_trainer.py`. Commit `134da3f` |
| B3 | `SFTTrainer(tokenizer=…)`→`processing_class=…` (TRL ≥0.12, hard-removed 0.16) + migrate to `SFTConfig` | ✅ | Done — `dataset_text_field` / `max_seq_length` / `packing` moved into `SFTConfig`. Commit `4fb9fe3` |
| B4 | `SFTConfig.max_seq_length`→`max_length` (TRL ≥0.18) | ✅ | Done — single rename. Commit `1b6763b` |
| B5 | Unsloth `<EOS_TOKEN>` placeholder validator failure (TRL ≥0.20 + Unsloth-patched SFT) | ✅ | Done — 4 commits: messages-format formatters (`9b77054`), real-EOS resolver (`3106550`), import-unsloth-first + pre-render via `apply_chat_template` (`b6927cc`), worker `LD_LIBRARY_PATH` for cu13 nvJitLink (`9ca376c`). Verified `b5-retry-2` training `completed` in 49 s on 4060 Ti, mlflow_run_id `be62ce9063…`, ModelArtifact `f2086b81…` registered with 22.99 MB LoRA on MinIO. |
| **B6** | **GGUF export blocked by missing `llama.cpp` in worker image** | **⏳** | **See plan below — Unsloth's interactive `install_llama_cpp` hits EOFError under Celery; need to pre-install in worker.Dockerfile** |
| **B7** | `model.export` Celery task fails silently — no error written to `ModelArtifact` row | ⏳ | One-line fix: on the `except` branch, also `UPDATE model_artifacts SET ... export_error = str(exc)` (or add a `last_export_status` column) before re-raising. Currently the only signal is `JobFailed` on Redis pub-sub, which nobody is consuming after the WebSocket closes. |

### B5 — Resolution summary (Session 13)

The original "messages-format-only" plan from Session 12 turned out to be insufficient — landing it surfaced *three* further blockers in sequence. All four landed on `dev`:

1. **Messages format + chat-template wiring** (commit `9b77054`) — formatters now return `list[{role, content}]`; trainer calls `get_chat_template(tokenizer, chat_template=...)` before SFTConfig with a `_chat_template_for(base_model)` mapping (`llama-3.2` / `chatml` / `gemma-2`).
2. **EOS resolver** (commit `3106550`) — `_resolve_eos_token()` walks `tokenizer.eos_token` → `convert_ids_to_tokens(eos_token_id)` → per-family fallback (`<|eot_id|>`/`<|im_end|>`/`<end_of_turn>`). Unsloth's 4-bit ports do leave `tokenizer.eos_token = '<|eot_id|>'`, but TRL's vocab validator was being fed the literal `'<EOS_TOKEN>'` sentinel anyway, so we now pass a known-real EOS to `SFTConfig.eos_token` explicitly.
3. **Import order + pre-render** (commit `b6927cc`) — *the* root cause: `from trl import SFTConfig, SFTTrainer` ran *before* `from unsloth import FastLanguageModel`, so the local names bound to TRL stock classes that were never monkey-patched. Reordered imports + pre-render messages → text via `tokenizer.apply_chat_template()` and feed via `dataset_text_field="text"`, sidestepping Unsloth's `formatting_func`-required path entirely.
4. **CUDA cu13 lib path** (commit `9ca376c`) — bitsandbytes 0.49.2 on the cu130 PyTorch wheel needs `libnvJitLink.so.13` from `/opt/conda/lib/python3.11/site-packages/nvidia/cu13/lib`, which isn't on the default loader path. Set `LD_LIBRARY_PATH` on the worker service in `docker-compose.yml`.

**vast.ai smoke (5 retries, 4 distinct failures, then green):**
| Try | Failure | Fix landed |
|-----|---------|------------|
| 1 | HF API 500 (transient) | wait + retry |
| 2 | `<EOS_TOKEN>` not in vocab | (was supposed to be fixed by `9b77054` but wasn't enough) |
| 3 | `<EOS_TOKEN>` not in vocab | even after `_resolve_eos_token` the value got clobbered → diagnosed import-order bug |
| 4 | `libnvJitLink.so.13` missing | `9ca376c` |
| 5 | HF read timeout (10 s) | retry |
| 6 | ✅ `completed` in 49 s | — |

### B6 — Plan: pre-install `llama.cpp` in worker.Dockerfile

**Diagnosis:**
- `unsloth/save.py:1074` calls `unsloth_zoo.llama_cpp.check_llama_cpp(llama_cpp_folder="llama.cpp")` which expects a sibling folder containing `llama-quantize` (or `quantize`) plus a converter script.
- When the folder is missing, Unsloth falls back to `install_llama_cpp()` → `install_package(packages, sudo, …)` which prompts via `input()` for the sudo password. Celery workers have no stdin → `EOFError: EOF when reading a line`.
- Setting `LLAMA_CPP_PATH` env var doesn't help — the library reads the folder name from the function arg, not the environment.

**Fix:**
1. In `docker/worker.Dockerfile`, after the apt block, add:
   ```dockerfile
   RUN apt-get update && apt-get install -y --no-install-recommends cmake \
       && git clone --depth 1 https://github.com/ggerganov/llama.cpp.git /app/llama.cpp \
       && cd /app/llama.cpp \
       && cmake -B build -DGGML_CUDA=ON -DLLAMA_CURL=OFF \
       && cmake --build build --config Release -j --target llama-quantize \
       && cp build/bin/llama-quantize /app/llama.cpp/ \
       && pip install -r requirements.txt    # for the convert_hf_to_gguf.py script
   ```
   The convert-side dependency is `gguf` Python package — already a transitive of llama.cpp's requirements.txt. The compiled binary needs to live at `/app/llama.cpp/llama-quantize` because `check_llama_cpp("llama.cpp")` is called with cwd=`/app`.
2. Worker image rebuild adds ~5 min on the VM. Image size grows ~400 MB (llama.cpp source + cmake build cache + the CUDA quantize binary). Acceptable.
3. After rebuild, re-run `POST /api/v1/models/{id}/export` and verify `gguf_uri` populates plus `ollama_model_tag` registers.

**Workflow when ready:**
1. Pull on VM: `git pull origin dev`.
2. Rebuild worker: `docker compose build worker && docker compose up -d worker` (preserves all stack state — postgres, MLflow, MinIO; only the worker image changes).
3. Trigger export on the existing artifact `f2086b81-47a4-4638-ba0d-b2fec6e0ec40` — it already has the LoRA adapter on MinIO, no need to re-train.
4. Verify Ollama registration: `curl http://localhost:11434/api/tags` should list `slm/f2086b81`.
5. Smoke test inference via OpenAI-compatible router: `POST /api/v1/inference/chat/completions` with `model: f2086b81-47a4-4638-ba0d-b2fec6e0ec40` and a QA prompt.

### B7 — Plan: surface model.export failures on the artifact row

**Diagnosis:** Session 13 wasted ~30 min polling `gguf_uri != null` while the Celery task had crashed instantly. The crash logs `JobFailed` to Redis pub-sub, but no client is subscribed once the WebSocket closes, and the artifact row stays in its post-training state (lora_adapter_uri set, gguf_uri null, no error field). API consumers can't tell the difference between "still exporting" and "export crashed".

**Fix:** add `export_error_message: str | None` to `ModelArtifact` (Alembic migration + schema update). On the `except` branch in `workers/tasks/model_export.py`, write the error into that column inside a fresh `session_scope()`, then re-raise. UI/CLI can then surface the failure on `GET /api/v1/models/{id}`.

Out of scope for this fix: distinguishing "export queued" from "export running". A first cut keeps it boolean (error_message null → either queued, running, or success — caller checks gguf_uri/safetensors_uri to disambiguate).

---

## Out of Scope (do NOT build)

- ❌ Authentication / user management
- ❌ Frontend (teammate handles)
- ❌ Multi-tenancy
- ❌ Distributed training (single GPU only)
- ❌ Models >3B parameters
- ❌ Direct OpenAI/Anthropic calls (use OpenRouter — see ADR-003)
