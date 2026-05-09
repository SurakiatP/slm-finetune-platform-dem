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

## Phase 9: Production Hardening (Sessions 12–14 discoveries)

> Bugs surfaced by the vast.ai full-lifecycle smoke test. **All B1–B8 closed.** Session 14 finished the chain on a fresh RTX 5000 Ada VM: training (55 s) → LoRA on MinIO → 770 MB q4_k_m GGUF on MinIO → Ollama register → `POST /api/v1/inference/chat/completions` returns `"Paris."`. `§16 #1–9` of `SWAGGER_GUIDE.md` is green for the first time. Phase 9 is empty.

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| B1 | `DELETE /datasets/{id}` 500→409 when refs exist | ✅ | Done — pre-check + 409 envelope; regression test in `tests/integration/test_dataset_delete.py`; verified on production deploy. Commit `c117f2a` |
| B2 | `evaluation_strategy`→`eval_strategy` (HF Transformers ≥4.41) | ✅ | Done — single rename in `ai_engine/training/unsloth_trainer.py`. Commit `134da3f` |
| B3 | `SFTTrainer(tokenizer=…)`→`processing_class=…` (TRL ≥0.12, hard-removed 0.16) + migrate to `SFTConfig` | ✅ | Done — `dataset_text_field` / `max_seq_length` / `packing` moved into `SFTConfig`. Commit `4fb9fe3` |
| B4 | `SFTConfig.max_seq_length`→`max_length` (TRL ≥0.18) | ✅ | Done — single rename. Commit `1b6763b` |
| B5 | Unsloth `<EOS_TOKEN>` placeholder validator failure (TRL ≥0.20 + Unsloth-patched SFT) | ✅ | Done — 4 commits: messages-format formatters (`9b77054`), real-EOS resolver (`3106550`), import-unsloth-first + pre-render via `apply_chat_template` (`b6927cc`), worker `LD_LIBRARY_PATH` for cu13 nvJitLink (`9ca376c`). Verified `b5-retry-2` training `completed` in 49 s on 4060 Ti, mlflow_run_id `be62ce9063…`, ModelArtifact `f2086b81…` registered with 22.99 MB LoRA on MinIO. |
| B6 | GGUF export pipeline (LoRA → merged HF → f16 GGUF → q4_k_m) | ✅ | Done — 6 commits over Session 14, see resolution summary below. Verified on RTX 5000 Ada VM: artifact `65a05a2b…` exported 770 MB `model.q4_k_m.gguf` to `s3://models/exports/65a05a2b…/gguf` in ~90 s; `export_error_message` cleared on success. Final commit `b581fdb`. |
| B7 | `model.export` Celery task fails silently — no error written to `ModelArtifact` row | ✅ | Done — added `export_error_message` column (migration `0002_export_error`), worker writes `(str(exc) or repr(exc))[:4000]` on except, clears on success. Commit `2e3498f` + revision-id shortening fix `61b72f2`. Functioned perfectly during B6 iteration: 6 distinct error messages surfaced instantly via `GET /api/v1/models/{id}.export_error_message`, no log diving required. |
| B8 | Ollama `/api/create` schema migration (`modelfile` → `from`/`files`) | ✅ | Done — `OllamaClient.upload_blob()` + `create_from_blob()`; `_register_with_ollama` rewritten; legacy `build_modelfile`/`create_from_modelfile` deleted. Commit `137adec`. Plus follow-up `3e8730b` to relax response-side Pydantic `extra="forbid"` so Ollama's `system_fingerprint` field doesn't 500 inference. Verified: `POST /api/v1/inference/chat/completions` returns `"Paris."` end-to-end on artifact `65a05a2b…`. |

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

### B6 — Resolution summary (Session 14)

The "pre-install llama.cpp in worker.Dockerfile" plan from Session 13 unblocked the *first* failure mode. Each fix surfaced a deeper one — six distinct issues over the iteration, every one of which was visible in real time via `export_error_message` (B7) without log diving. The 1B QA artifact `65a05a2b…` re-exported successfully at the end as 770 MB `model.q4_k_m.gguf` on MinIO.

| Part | Symptom in `export_error_message` | Root cause | Fix | Commit |
|------|-----------------------------------|------------|-----|--------|
| 1 | `EOF when reading a line` | Unsloth's `install_llama_cpp` falls through to interactive `input()` because no llama.cpp installed | Pre-build `llama.cpp` in worker image with `BUILD_SHARED_LIBS=OFF` so `llama-quantize` is statically linked + add ldd self-check | `fc15096` + `e0e2808` |
| 2 | `config.json does not exist inside .../gguf` | `save_pretrained_gguf` no longer auto-merges in Unsloth 2025.11 | Call `save_pretrained_merged` ourselves before the GGUF step | `765f2cd` |
| 3 | (same as part 2 but on `.../stage`) | `model = PeftModel.from_pretrained(base, adapter_dir)` doesn't tag the model the way Unsloth's saver expects → "Skipping Merge" warning, merge no-ops | Load via `FastLanguageModel.from_pretrained(model_name=adapter_dir)` instead — Unsloth reads `adapter_config.json`, fetches the base, and returns a model the saver recognises | `8496ea8` |
| 4 | `unsloth_convert_hf_to_gguf.py … exit 1` | Unsloth ships a *patched* convert script that calls an old `AutoTokenizer` signature, incompatible with transformers 4.51+ | Bypass Unsloth's GGUF wrapper. Drive the *original* `/app/llama.cpp/convert_hf_to_gguf.py` + our `llama-quantize` directly | `b5456dc` |
| 5 | `'dict' object has no attribute 'model_type'` | **transformers 4.57.2 bug** at `tokenization_utils_base.py:2419` — `_config.model_type` accessed on a `json.load`'d dict | Bump `transformers_version` in saved `config.json` from `4.57.2` → `4.58.0` after merge so the buggy version-gated branch doesn't fire. *This was the load-bearing one — see [memory note](../../../../Users/parks/.claude/projects/C--ai-engineer-nectec2-slm-final-dem/memory/transformers_4_57_2_bug.md)* | `1aef622` |
| 6 | `ollama create failed: {"error":"neither 'from' or 'files' was specified"}` | Ollama API breaking change; B6 main goal already met (GGUF on MinIO) | Wrap Ollama call in try/except — registration is now best-effort, `gguf_uri` persists even if Ollama rejects. Real Ollama migration tracked as B8 | `b581fdb` |

**vast.ai smoke (Session 14, on a 5000 Ada VM after `vast.ai copy` discarded the original disk):**

| # | `export_error_message` value | Notes |
|---|------------------------------|-------|
| 1 | `EOF when reading a line` | llama.cpp not in image; pre-build added |
| 2 | `config.json does not exist inside .../gguf` | merge wasn't running; added explicit `save_pretrained_merged` |
| 3 | `config.json does not exist inside .../stage` | merge ran but said "no LoRA detected"; switched adapter loader |
| 4 | `unsloth_convert_hf_to_gguf.py … exit 1` | Unsloth's patched script broken; bypassed it |
| 5 | `Failed to convert model to GGUF: 'dict' object has no attribute 'model_type'` | transformers bug; bumped version field |
| 6 | `ollama create failed: …'from' or 'files'…` | Ollama API change; made registration best-effort |
| 7 | (success) `gguf_uri=s3://models/exports/65a05a2b…/gguf`, `ollama_model_tag=null` | B6 closed; B8 opened |

### B8 — Resolution summary (Session 14, same day)

After B6 closed, exercising `POST /api/v1/models/{id}/export` against the new flow surfaced two more issues in sequence — both fixed within the same session:

1. **Ollama legacy schema gone** (`137adec`) — replaced `build_modelfile` + `create_from_modelfile` with `upload_blob` (sha256 streamed in 64 KB chunks to `POST /api/blobs/sha256:<HEX>`) + `create_from_blob` (`POST /api/create` with `{model, files: {"model.gguf": digest}, parameters: {...}}`). The blob upload also frees us from needing a shared volume between worker and ollama containers — Ollama stores the blob in its own filesystem.

2. **Inference 500 on `extra="forbid"`** (`3e8730b`) — first real `POST /api/v1/inference/chat/completions` call hit a `ValidationError: system_fingerprint Extra inputs are not permitted` because Ollama 0.5+ matches OpenAI's added `system_fingerprint: 'fp_ollama'` field, and our pass-through response schemas were strict. Flipped response-side schemas (`ChatCompletionResponse`, `…Choice`, `…Usage`, `ChatMessage`, `CompletionResponse`, `ModelDescriptor*`) to `extra="ignore"`; request schemas keep `extra="forbid"` so caller mistakes still surface.

**Smoke confirmation:** `model: <UUID>`, `messages: [{role:user, content:"What is the capital of France? Answer in one word."}]` → `"Paris."` with `finish_reason=stop`, 22 / 3 / 25 tokens. The trained QA adapter is being applied — the seed had five capital-city Q/A pairs including France→Paris.

---

## Phase 9 — SDG Hardening (Sessions 15+)

> Branch: `feature/sdg-improvements` (from `dev@59c12e2`). Spec:
> [`PHASE9_SDG_HARDENING_SPEC.md`](./PHASE9_SDG_HARDENING_SPEC.md). All
> three sub-phases shipped as 14 commits in Session 15; awaiting live
> Swagger-UI smoke from parks before merging back to `dev`.

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| H9.1.1 | datasketch + pypdf added to base deps | ✅ | Commit `0b1fc63` |
| H9.1.2 | ADR-007 (async LLM batching) accepted + indexed | ✅ | Commit `0b1fc63` |
| H9.1.3 | `models.py` — 5 hardcoded LLM identifiers (Q6.1) | ✅ | Commit `be355eb` |
| H9.1.4 | `constants.py` — every Phase 9 tunable | ✅ | Commit `be355eb` |
| H9.1.5 | `AsyncOpenRouterClient` + `chat_raw` for multimodal | ✅ | Commit `7b32747`; 5 mock-server tests green |
| H9.2.1 | MinHashLSH dedup + short-text guard | ✅ | Commit `36187b6`; 7 unit tests |
| H9.2.2 | Coverage pool helper | ✅ | Commit `36187b6`; 6 unit tests |
| H9.2.3 | LLM-as-Judge (weighted 0.4/0.3/0.3) | ✅ | Commit `68ecfd7`; 7 unit tests |
| H9.2.4 | Meta-prompter + hardcoded fallback | ✅ | Commit `68ecfd7`; 7 unit tests |
| H9.2.5 | Format Detection (schema mismatch + key renamer) | ✅ | Commit `5b46bf8`; 8 unit tests |
| H9.2.6 | PDF loader (probe + base64) | ✅ | Commit `5b46bf8`; 7 unit tests |
| H9.2.7 | `canonical_field_names` + `FormatDetectionReport` | ✅ | Commit `acfa4a3` |
| H9.2.8 | Prompts rewrite — RTC-FO 5 families | ✅ | Commit `acfa4a3`; 16 unit tests |
| H9.3.1 | `SDGProgress` widened (judge/dedup/loop fields) | ✅ | Commit `8743903` |
| H9.3.2 | `seed_dataset_id` replaces `seed_data`; drop `teacher_model` | ✅ | Commit `8743903` (breaking) |
| H9.3.3 | Upload-seed accepts PDF for QA + runs Format Detection | ✅ | Commit `b0fc6e6`; PDF cleanup on delete |
| H9.3.4 | Async SDG generator (quota + sentinel + adaptive) | ✅ | Commit `f5fe435` |
| H9.3.5 | Worker `asyncio.run` boundary + `seed_dataset_id` validation | ✅ | Commit `0f5c834` |
| H9.3.6 | Integration tests rewritten + examples + README | ✅ | This commit |
| H9.3.7 | **Live Swagger smoke (parks)** | ⏳ | Run `docker compose up -d`, hit `/docs`, exercise upload-seed → generate. Then PR → `dev`. |

---

## Out of Scope (do NOT build)

- ❌ Authentication / user management
- ❌ Frontend (teammate handles)
- ❌ Multi-tenancy
- ❌ Distributed training (single GPU only)
- ❌ Models >3B parameters
- ❌ Direct OpenAI/Anthropic calls (use OpenRouter — see ADR-003)
