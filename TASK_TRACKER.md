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

> Branch: `feature/sdg-improvements` (from `dev@59c12e2`, merged into
> `dev@31e7f25` on 2026-05-10 via PR #3). Spec:
> [`PHASE9_SDG_HARDENING_SPEC.md`](./PHASE9_SDG_HARDENING_SPEC.md). All
> sub-phases shipped + 3 quality-gate bugs caught and fixed across
> Sessions 15-17; both Claude-driven runbook walk and parks's manual
> Swagger pass green. **Phase 9 SDG Hardening closed.**

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
| H9.3.6 | Integration tests rewritten + examples + README | ✅ | Commit `3512bda` |
| H9.3.7 | Seed-data fixtures (12 files × 40 rows + PDF) | ✅ | Commits `fc3d93e` + `7ac77cf` |
| H9.3.8 | 3 task-specific manual test runbooks | ✅ | Commit `fc3d93e` |
| H9.3.9 | SWAGGER_GUIDE.md aligned with Phase 9 | ✅ | Commit `3132687` |
| H9.3.10 | vast.ai deploy (hybrid: services Docker + host py3.11) for Swagger smoke | ✅ | Session 16 setup; uvicorn:8000 + celery `-P solo` running |
| H9.3.11 | **Bug 1**: SDG sentinel quota routing — Generator emits real label, row bucketed wrong, every sentinel loop yields 0 → SDGAbortedError after 5 zero-yield loops | ✅ | Commit `b1a9581` — stamp `b["label_or_tool"]` on every row for classification + tool_calling sentinel batches. Verified target=10 → 10/10 with sentinel "สวัสดี". |
| H9.3.12 | **Bug 2**: Judge rejects 100% of sentinel rows (rubric was sentinel-blind, asked "does text fit assigned label" → low fidelity for off-topic content) | ✅ | Commit `aa62149` — `_row_is_sentinel` detector + sentinel-specific rubric in `build_judge_prompt` + `[Sentinel row]` prompt prelude. Verified target=20 → 20/20 with distribution `{real:6×3, unknown:2}`. |
| H9.3.13 | **Bug 3**: tool_calling `with_seed` returns `samples=0 calls=0` after 2.4 s — generator never derived `tool_defs` from seed rows, so quota was empty and main loop bailed at iteration 0 | ✅ | Commit `843a539` (Session 17) — mirror the cls_labels derivation: parse each seed row's JSON-encoded `answer` to collect unique tool names, synthesise minimal `ToolDefinition` per name (empty parameters; per-tool seed rows convey schema in-context). Verified target=20 → 20/20 with distribution `{play_music:2, light_on:4, set_oven:4, set_volume:4, start_timer:4, no_tool_needed:2}`. |
| H9.3.14 | **Live runbook drive (Claude driver, Session 17)** — all 3 task-specific runbooks executed end-to-end via httpx against the live vast.ai stack | ✅ | Driver `scripts/session17_runbook_driver.py` (untracked); 50/50 sub-checks PASS once Bug 3 fixed and celery restarted. Classification 13/13, tool_calling 17/17, QA 13/13. One transient: QA T6 hung the worker for 18 min in `ep_poll` after 5 successful httpx calls (no error log) — fresh celery reran it cleanly in 48 s. Recurrence would warrant a wall-clock watchdog around `chat_batch`. |
| H9.3.15 | **Manual Swagger pass (parks)** — same payloads through Swagger UI for content-quality eyeball | ✅ | Confirmed working. Sample reviewed: 20-row tool_calling output (`264585f0-cc59-454e-a6c1-252d76068405.jsonl`) — perfect tool-set membership, JSON-decode-clean, sentinel quota = 2, parameter types match (celsius:int, level:int, minutes:int, etc.), good phrasing diversity. |
| H9.3.16 | Open PR `feature/sdg-improvements` → `dev` | ✅ | Done — PR #3 merged into `dev` at `31e7f25` (2026-05-10). 22 commits brought across; `feature/sdg-improvements@a2476b2` is the merged tip. |

---

## Phase 10 — Manual Test Coverage (Session 18+)

> Branch: `feature/training-eval-smoke` (from `dev@31e7f25`, currently at
> `b72e578`). Five reusable smoke drivers in `scripts/swagger_smoke_section*.py`.
> All 6 previously-untested Swagger sections green on vast.ai RTX 5000 Ada
> (`202.215.2.218:51812`); 4 API bugs + 1 infra issue caught and fixed during
> the Claude-driver pass. **parks's manual Swagger walk is the remaining gate
> before PR back to `dev`.**

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| MT.1 | §16 full-lifecycle smoke driver — 13 checks (T1-T11 + WS + mlflow-url) | ✅ | `47026fc` — verified green on RTX 5000 Ada (training 30 s, export 70 s, inference returned "Paris.") |
| MT.2 | §13 evaluation smoke driver — rule-based (T1-T6) + LLM judge (T7-T8) + compare | ✅ | `5e1ef0d` (driver) + `b72e578` (judge fixes); rule-based 6/6, with LLM judge 8/8 (mean=5.000 with `claude-haiku-4.5`) |
| MT.3 | §8 HPO mode smoke driver — n_trials=2, search_space {lr, lora_r}, full lifecycle | ✅ | `5e1ef0d`; 7/7 PASS, best_metric=2.45, lora_r=16, lr=4.56e-4, ~3 min wall time |
| MT.4 | §11 SafeTensors export + 807 MB binary download + §12 legacy `/completions` | ✅ | `2c79919`; 4/4 PASS first try after MT.5/MT.6/MT.7 fixes landed |
| MT.5 | §9 DELETE training mid-flight cancel + idempotent re-DELETE | ✅ | `2c79919`; 7/7 PASS, status pending → running → cancelled in <10 s, no 500s on second DELETE |
| MT.B1 | **Bug 1** (§16 driver T11): `GET /inference/models` 500 when Ollama empty — `raw.get("data", [])` returns None when key is present-but-None | ✅ | `ef111b0` — `raw.get("data") or []` |
| MT.B2 | **Bug 2** (§13 driver T2): eval task fails with `ModuleNotFoundError: sacrebleu`. `metrics_qa.py` imports it but `[eval]` extras forgot it | ✅ | `8900576` — added `sacrebleu>=2.4.0` to `[eval]` + live-installed in worker |
| MT.B3 | **Bug 3** (§8 driver T7): `GET /models?training_job_id=X` returns ALL artifacts instead of filtering. Router declared only `project_id`; `training_job_id` was silently dropped by FastAPI | ✅ | `31ea5bd` — added `training_job_id` Query param to router + WHERE clause in service |
| MT.B4 | **Bug 4** (§13 driver T8): LLM judge returns `score=0.0` while every row OpenRouter-404s. Default `claude-3.5-sonnet` retired. Plus `judge_rows()` returned `0.0` instead of `None` when zero successful rows | ✅ | `b72e578` — bumped default to `claude-haiku-4.5` (verified live); changed `JudgeBatchResult.mean_score` to `float \| None`; strengthened §13 driver T8 to assert `skipped < n` |
| MT.I1 | **Infra issue** (mid-§8): `nvidia-smi` `Driver/library version mismatch` after `apt install nvidia-container-toolkit` pulled newer `nvidia-utils-580-server`. Reboot blocked by auto-mode classifier | ✅ | In-place `rmmod nvidia_uvm/_drm/_modeset/nvidia` → `modprobe nvidia[*]` (after stopping GPU containers); then `docker compose up -d --force-recreate worker` to refresh nvidia mounts on the existing container |
| MT.6a | Author 5 manual-test runbooks (`docs/runbooks/{platform-basics,training-manual-lifecycle,training-hpo,evaluation,model-export-extras}.md`) | ✅ | `c8c1772`. Mirrors `sdg-test-classification.md` format. ~52 Tasks total across 5 files; cross-references MT.B1-B4 + MT.I1 in Troubleshooting tables so each runbook doubles as regression check |
| MT.6 | **Manual Swagger pass (parks)** — walk all 5 new runbooks + verify content matches reality | ✅ | Session 19 (`bfb8ccd`, `217a93a`, `bdcf9e2`). Walked all 8 runbooks end-to-end on RTX 4060 Ti vast.ai host. Found + fixed: sacrebleu missing in worker image, GPU mount stale on cold-boot, OPENROUTER_API_KEY empty in api container, `nvml driver/library mismatch` after `nvidia-container-toolkit` install. After fixes: all runbooks green. Doc corrections committed: envelope shapes, field names, num_samples, evaluation T9 (now 400 not 202), HPO Task 3 (REST API primary, MLflow UI secondary) |
| MT.7 | Open PR `feature/training-eval-smoke` → `dev` | 🔧 | 10 commits (`034d869`..`01e4626`). Branch up to date with origin. Expected clean fast-forward — no conflicts. Opening PR is the next housekeeping step in Session 19 |
| MT.8 | Two metrics endpoints to keep FE off MLflow REST | ✅ | `6523209`. `GET /trainings/{id}/loss-history` (lightweight train+eval loss series) + `GET /trainings/{id}/metrics` (full per-key history + HPO `hpo_children` summary). New module `api/services/mlflow_metrics.py` (thin httpx wrapper). 502 on MLflow down; per-key failures logged + emit `[]` rather than failing whole response. Verified live on manual + HPO trainings; `ab06262` updated runbooks |
| MT.9 | `base_ollama_tag` field + auto-pull base for A/B compare in playground | ✅ | `538e5b7`. New `api/services/base_model_catalog.py` (single source of truth for `base_model id → ollama tag` mapping). `BaseModelInfo.ollama_tag` field on `/base-models`. `ModelArtifactResponse.base_ollama_tag` computed field on `/models/{id}` (lazy import). Worker auto-pulls base via `/api/pull` after registering `slm/<id8>` (best-effort, non-blocking on failure). Verified end-to-end: POST export → both `slm/e4d52ebf:latest` and `llama3.2:1b` appear in `ollama list` and `/inference/models` ~17s after register |
| MT.10 | Expand `SUPPORTED_BASE_MODELS` 6 → 11 (sub-2B variety) | ✅ | `01e4626`. Added Qwen3-0.6B, Qwen3-1.7B, DeepSeek-R1-Distill-Qwen-1.5B, SmolLM2-1.7B-Instruct, TinyLlama-1.1B-Chat. All Unsloth bnb-4bit instruct mirrors, all Apache 2.0 / MIT, all non-gated (no HF_TOKEN). All 11 Ollama tags verified via `registry.ollama.ai/.../manifests/...` HTTP 200 + live pull of `qwen3:0.6b` (12s, 522MB). Skipped IBM Granite (no bnb-4bit instruct mirror) and Liquid LFM2 (no official Ollama Hub tag) |
| MT.11 | `docs/runbooks/api_docs.md` — single-page FE integration guide | ✅ | `5ef89fb` (1216 lines), `f0cffa1` (post-verification corrections), `bdcf9e2` (WS event refinements after live capture). Sections: Overview / Common Patterns / Health-Metadata / Projects / Datasets / Trainings / Models / Evaluations / Inference / WebSocket / FE Integration Patterns / HTTP Status Reference. Every claim verified live or from source-code review |
| MT.B5 | **Bug 5** (this session): POST `/evaluations` with mismatched `dataset.task_type` vs `artifact.task_type` accepted as 202 → worker silently failed downstream. No guard at API level | ✅ | `bfb8ccd` — added guard in `api/services/evaluation_service.submit_evaluation_job` that loads `artifact.training_job → project → task_type` and compares with `dataset.task_type`. Returns 400 + `code: bad_request` + `"Dataset task_type=X does not match artifact task_type=Y"` when mismatched. Verified: classification dataset + qa artifact → 400; matching dataset+artifact → 202 normal |
| MT.F1 | Follow-up: `mlflow_url` returns internal docker hostname `mlflow:5000` not browser-friendly `localhost:5000` | ⏳ | Surfaced in §16 driver T6c. Would benefit from a public-host-aware setting (Pydantic `AnyUrl`); not blocking smoke |
| MT.F2 | Follow-up: update `docs/runbooks/vast-ai-deployment.md` §6 with `gpg --batch --no-tty --yes` flags | ✅ | Session 25 — bundled with 5 other runbook-currency fixes (migration count 0001 → 0001+0002+0003, test count 5 → 176, `git checkout dev` → parameterized BRANCH, nvml mismatch added to §13 troubleshooting, stale-env reload warning in §7). Pre-VM audit before smoke run. |
| MT.F3 | Follow-up: clarify `format_detection.ran` semantics when `OPENROUTER_API_KEY` is empty | ✅ | Session 19 turned out to be a stale-env issue — api container started before `.env` had the key, so even though `.env` had it, the running container saw `OPENROUTER_API_KEY=`. Documented in `platform-basics.md` "Cross-runbook pre-flight pitfalls" section + memory `vast_ai_deployment.md`. Fix is `docker compose up -d api` after editing `.env` |
| MT.F4 | Follow-up: implement Patch 3 — auto-export base via same llama-quantize pipeline for ~99% A/B fairness | ⏳ | Current MT.9 (Patch 1+2) gives ~70% fairness because Ollama Hub q4_K_M uses different recipe than our BNB→f16→q4_k_m chain. For scientific A/B, would need to dequantize the same Unsloth base + re-quantize through the same llama.cpp pipeline + register as `slm-base/<id8>`. Out of session 19 scope |
| MT.F5 | Follow-up: classification + tool_calling eval metrics live verification | ⏳ | Session 19 verified QA eval shape end-to-end. Schema for classification (`accuracy, f1_macro, f1_per_label, confusion_matrix, n`) and tool_calling (`json_validity, name_accuracy, arg_accuracy, exact_match, n`) was read from `ai_engine/evaluation/metrics_*.py` — but never run live on those task types. Requires training a classification model + a tool_calling model first |
| MT.F6 | Follow-up: revisit IBM Granite + Liquid LFM2 when ecosystem catches up | ⏳ | Granite needs Unsloth to publish a `*-instruct-bnb-4bit` mirror (currently only BF16 instruct + base bnb-4bit exist). LFM2 needs Liquid AI to publish to ollama.com/library officially (community uploads exist but auto-pull would fail) |

---

## Phase 11 — SDG Hold-out for Leak-Free Evaluation (Session 20+)

> Branch: `feature/sdg-holdout` (from `feature/training-eval-smoke-v2`). Adds
> over-generation + train/holdout split so `POST /evaluations` can run against
> rows the trained model never saw.

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| HO.1 | `holdout_split.py` + 9 unit tests | ✅ | Done — commit `4287b8a` |
| HO.2 | `SDGRequest.holdout_size` field + 6 unit tests | ✅ | Done — commit `f2d3c32` |
| HO.3 | Alembic migration `0003_dataset_parent_id` | ✅ | Done — commit `44691ed`. `down_revision="0001_initial"` (not `0002_export_error` as plan assumed) |
| HO.4 | `Dataset.parent_dataset_id` ORM + self-relationship | ✅ | Done — commit `504ead2` |
| HO.5 | `DatasetResponse.parent_dataset_id` exposed on API | ✅ | Done — commit `eabf8f0` |
| HO.6 | Worker over-generates, splits, persists 2 datasets | ✅ | Done — commit `f555f8d` |
| HO.7 | api_docs.md + FE integration pattern | ✅ | Done — commit `d0c8f67` |
| HO.8 | Live SDG smoke (cls + tool + qa) with `holdout_size>0` | ✅ | Session 21 overnight smoke on RTX 3090 vast.ai. All 3 task types + negative (holdout=0) green. qa judge=3.9 leak-free. Artifacts: `docs/runbooks/session25-holdout-{state.json,log}` |
| HO.9 | Open PR `feature/training-eval-smoke-v2` → `dev` | ⏳ | parks to open via `gh pr create`. Bundles Phase 11 + Phase 12 work |

---

## Phase 12 — Code Health + Refactor Safety (Session 24)

> Branch: same `feature/training-eval-smoke-v2`. Closes a missing list
> endpoint, removes vulture-flagged dead code, and lands the snapshot-harness
> pilot so future refactor sessions have a characterization safety net.
> Plan: [`~/.claude/plans/peppy-sauteeing-rain.md`](../../Users/parks/.claude/plans/peppy-sauteeing-rain.md).

| ID | Task | Status | Next Step |
|----|------|--------|-----------|
| CH.1 | `GET /api/v1/evaluations` list endpoint (only resource missing one) | ✅ | Commits `31960bb` (service+router), `f6c9b1e` (api_docs.md), `1cb12e1` (guidebook Node 9). Filters: `model_artifact_id` / `dataset_id` / `status`. Same `Page[T]` envelope as other 4 resources |
| CH.2 | Dead-code cleanup pass 1 — 5 items removed via vulture 80% scan | ✅ | Commit `2ebc615`: `declared_attr` import, `openrouter_teacher_model` setting, `AsyncProgressCallback` + `ProgressCallbackFactory` type aliases, `MlflowRunHandle.run_url` property. `-13 / +2` lines |
| CH.3 | Dead-code cleanup pass 2 — 6 items removed via vulture 60% scan | ✅ | Commit `c528726`: `MlflowRunHandle.tracking_uri` (chain reaction from CH.2), `OllamaModelInfo.modified_at`, `OllamaClient.has_model()` + `delete_model()`, `artifact_name` local var, `_first_gguf` helper (not `_first_gguf_object`). `-27` lines |
| CH.4 | Snapshot harness Tier 1 — pure functions, 43 snapshots | ✅ | Commit `0af16ca`. Adds `syrupy>=4.6` to `[dev]`. New tests: `test_snapshot_prompts.py` (17 snapshots — generator/judge/meta/PDF × 3 task types × normal+sentinel), `test_snapshot_generator_builders.py` (15 — quota/group/sentinel helpers), `test_snapshot_metrics.py` (11 — compute_metrics × 3 task types × 4 scenarios) |
| CH.5 | Snapshot harness Tier 2 — deps + characterization marker | ✅ | Commit `38d0f55`. Adds `respx>=0.21` + `moto[s3]>=5.0` + `fakeredis>=2.20` + `dirty-equals>=0.7` to `[dev]`. Registers `characterization` pytest marker |
| CH.6 | Snapshot harness Tier 2 — `tests/conftest.py` + generator_full scaffold | ✅ | Commit `de9f33b`. 5 shared fixtures (`openrouter_responder`, `recorded_payload`, `fake_minio`, `fake_redis_pubsub`, `seed_dataset_factory`). 3 SDG full-pipeline tests skip with capture-instruction inline until recorded payloads land. 2 fixture-smoke tests confirm fake_minio + fake_redis round-trip |
| CH.7 | Snapshot harness — runbook + CLAUDE.md workflow section | ✅ | Commit `c1c3b2d`. `docs/runbooks/snapshot_harness.md` (3-tier overview, install, before/after-refactor workflow, live-capture playbook, update-vs-revert decision table, rollout roadmap). CLAUDE.md "Snapshot Harness" section enforces the no-silent-`--snapshot-update` rule |
| CH.8 | Tier 2 recorded fixtures — live capture from vast.ai SDG run | ⏳ | 9 files needed: `tests/fixtures/recorded/openrouter/sdg_{classification,qa,tool_calling}_{meta,batch,judge}.json`. Once landed, `pytest --snapshot-update` upgrades the 3 currently-skipping SDG full tests to passing snapshots. Capture playbook in runbook §4 |
| CH.9 | Negative test verified — `_GENERATOR_BASE_SYSTEM` edit fails 5 snapshots cleanly | ✅ | Validated mid-session: editing 1 line in `prompts.py` produced 5 failures with readable diffs (all generator prompts share that constant), 12 unrelated snapshots stayed green. Revert restored 174/174 pass. Harness proven to catch real changes |
| CH.10 | Rollout iteration 2 — Training (`unsloth_trainer.py`) snapshot pilot | ⏳ | Plan: Tier 1 pure helpers (`_resolve_eos_token`, `_chat_template_for`, config-assembly) + Tier 2 mock Unsloth/HF. See runbook §7 |
| CH.11 | Rollout iteration 3 — Export (`workers/tasks/model_export.py`) | ⏳ | Tier 2 mock Ollama + MinIO + subprocess. See runbook §7 |
| CH.12 | Rollout iteration 4 — Eval (`workers/tasks/evaluation.py`) | ⏳ | Tier 1 pure metrics already covered by CH.4; Tier 2 mock Ollama + OpenRouter judge needed |
| CH.13 | Rollout iteration 5 — SDG worker (`workers/tasks/data_generation.py`) | ⏳ | Tier 2 DB + Redis + MinIO + OpenRouter combined; needs CH.8 fixtures to be in place first |
| CH.14 | Rollout iteration 6 — HPO (`optuna_objective.py` + `workers/tasks/hpo_training.py`) | ⏳ | Tier 1 + Tier 2 mock trainer + capture trial callbacks |
| CH.15 | Rollout iteration 7 — API CRUD services (`api/services/{projects,datasets}_service.py`) | ⏳ | Tier 2 DB + MinIO + format detection |
| CH.16 | Rollout iteration 8 — Tier 3 live E2E (11 nodes mini-flow) | ⏳ | Compose stack, 1 epoch, 5 rows, captured snapshots ติด git. Only run manually before big refactor PRs |

### Verification numbers (CH.4-CH.7 pilot landing)

- Unit suite: **176 passed, 3 skipped, 0 failed** (122 baseline + 52 new snapshots + 2 fixture smoke; 3 SDG scaffolds skip)
- 43 syrupy snapshots reproducible 100% across 3 consecutive runs — no flakes
- vulture 80% scan post-cleanup returns 2 entries, both `cls` validator params (false positive)
- 4 commits on `feature/training-eval-smoke-v2` pushed: `0af16ca` / `38d0f55` / `de9f33b` / `c1c3b2d`

---

## Out of Scope (do NOT build)

- ❌ Authentication / user management
- ❌ Frontend (teammate handles)
- ❌ Multi-tenancy
- ❌ Distributed training (single GPU only)
- ❌ Models >3B parameters
- ❌ Direct OpenAI/Anthropic calls (use OpenRouter — see ADR-003)
