# SLM Fine-Tuning Platform — Backend PoC

Backend-only proof of concept for an automated **Small Language Model (SLM)** fine-tuning
platform. Users describe a task → the platform synthesizes training data with OpenRouter,
fine-tunes a ≤3B-parameter model with **Unsloth + QLoRA**, tracks every run in **MLflow**,
and serves the result via **Ollama** (OpenAI-compatible API).

The frontend is built separately by a teammate; this repo exposes the OpenAPI contract only.

> **Source of truth for scope:** [`require.md`](./require.md)
> **Locked tech stack:** [`docs/architecture/TECH_STACK.md`](./docs/architecture/TECH_STACK.md) (ADR-001)
> **Project rules for AI agents:** [`CLAUDE.md`](./CLAUDE.md)

---

## Table of Contents

1. [Hard constraints](#hard-constraints)
2. [Architecture at a glance](#architecture-at-a-glance)
3. [Prerequisites](#prerequisites)
4. [Quickstart](#quickstart)
5. [Service endpoints](#service-endpoints)
6. [API usage](#api-usage)
7. [Repository layout](#repository-layout)
8. [Development workflow](#development-workflow)
9. [Troubleshooting](#troubleshooting)

---

## Hard constraints

| Constraint | Source |
|------------|--------|
| Models ≤3B parameters, must fit in QLoRA 4-bit | ADR-002 |
| RTX 3060 12GB target hardware | `require.md` |
| No authentication system | `require.md` |
| No frontend code in this repo | `require.md` |
| MLflow for experiment tracking (not W&B / TensorBoard) | ADR-001 |
| OpenRouter for SDG (not direct OpenAI / Anthropic) | ADR-003 |
| Celery for async jobs (never FastAPI BackgroundTasks) | ADR-004 |
| Only 3 task types: classification, tool_calling, qa | ADR-005 |

---

## Architecture at a glance

```
                ┌────────────┐
   frontend ───►│ FastAPI    │◄──── WebSocket  ◄──┐
   (separate)   │ (api)      │                    │
                └─────┬──────┘                    │
                      │ enqueue                   │ pub/sub
                      ▼                           │
                ┌────────────┐    ┌──────────┐    │
                │ Celery     ├───►│ Redis    ├────┘
                │ worker     │    │ broker   │
                │ (GPU)      │    └──────────┘
                └─┬────────┬─┘
                  │        │
                  │        └─► OpenRouter (SDG, LLM-as-judge)
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
   ┌────────┐ ┌────────┐ ┌────────┐
   │MLflow  │ │MinIO   │ │Ollama  │
   │(track) │ │(blob)  │ │(serve) │
   └────────┘ └────────┘ └────────┘
        │
        ▼
   PostgreSQL (app state + MLflow backend store)
```

7 services in `docker-compose.yml`: `postgres`, `redis`, `minio`, `mlflow`, `api`,
`worker` (GPU), `ollama` (GPU).

---

## Prerequisites

- **OS** — Linux or Windows 11 with WSL2 (the GPU services need NVIDIA Container Toolkit
  inside WSL2, not Windows directly).
- **GPU** — NVIDIA RTX 3060 12GB or stronger; CUDA 12.1+ driver.
- **Docker Desktop** with Compose v2 (or Docker Engine + Compose plugin).
- **NVIDIA Container Toolkit** — see
  [official install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
- **Python 3.11+** for local tooling (lint, tests, alembic outside containers).
- **OpenRouter API key** — get one at <https://openrouter.ai/keys>.

Verify GPU passthrough works:

```bash
docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
```

If this prints your GPU, you're good.

---

## Quickstart

```bash
# 1. Clone and enter the repo
git clone <repo-url> slm-platform
cd slm-platform

# 2. Configure environment
cp .env.example .env
# Open .env and fill in OPENROUTER_API_KEY (everything else has dev defaults).

# 3. Start the stack (first build takes ~10-15 min — CUDA + Unsloth are heavy)
docker compose up -d --build

# 4. Watch logs until the API is healthy
docker compose logs -f api
```

Once everything is up, hit:

- API docs (Swagger UI) — <http://localhost:8000/docs>
- API docs (ReDoc) — <http://localhost:8000/redoc>
- OpenAPI JSON (give this to your frontend teammate) — <http://localhost:8000/openapi.json>

Stop the stack:

```bash
docker compose down            # keeps volumes (data persists)
docker compose down -v         # wipes postgres / minio / ollama data
```

---

## Service endpoints

| Service | URL | Notes |
|---------|-----|-------|
| API (FastAPI) | <http://localhost:8000> | `/docs`, `/redoc`, `/openapi.json` |
| WebSocket | `ws://localhost:8000/ws/jobs/{job_id}` | Per-job progress stream |
| MLflow UI | <http://localhost:5000> | Experiments, runs, registered models |
| MinIO console | <http://localhost:9001> | Login: `minioadmin` / `minioadmin` |
| MinIO S3 API | <http://localhost:9000> | Used by MLflow + the API |
| PostgreSQL | `localhost:5432` | User: `slm`, DB: `slm` |
| Redis | `localhost:6379` | DB 0: app, 1: broker, 2: results |
| Ollama | <http://localhost:11434> | OpenAI-compatible inference |

All ports are configurable in `.env`.

---

## API usage

The full lifecycle for one task type: **project → SDG → train → export → infer → evaluate**.
Two ready-to-run walkthroughs ship in [`examples/`](./examples):

- `examples/python_client.py` — async + WebSocket progress streaming
- `examples/quickstart_curl.sh` — pure-curl version (needs `jq`)

### Curl snippets

#### 1. Create a project

```bash
curl -X POST http://localhost:8000/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"name": "policy-bot", "task_type": "qa"}'
# → 201 Created, body has `id` (the project_id)
```

#### 2. Generate a synthetic dataset

> **Phase 9 contract:** with_seed mode references a previously-uploaded
> seed dataset via `seed_dataset_id` (no inline `seed_data`). Upload the
> seed first, then submit the SDG job. The server picks the LLM models —
> there is no `teacher_model` override.

##### 2a. Upload seed examples

```bash
# Build a tiny JSONL seed file
cat > /tmp/seed.jsonl <<'EOF'
{"question": "Return window?", "answer": "30 days."}
{"question": "Need a receipt?", "answer": "Yes, please keep it."}
{"question": "Sale items returnable?", "answer": "Sale items are final."}
{"question": "Refund timing?", "answer": "5-7 business days."}
{"question": "Where to ship?", "answer": "Returns Lane 123."}
EOF

curl -X POST http://localhost:8000/api/v1/datasets/upload-seed \
  -F "project_id=<project_id>" \
  -F "task_type=qa" \
  -F "name=seed-v1" \
  -F "file=@/tmp/seed.jsonl;type=application/x-ndjson"
# → 201 Created. Body: { dataset_id, format_detection, ... }
#   Stash dataset_id as <seed_dataset_id> for step 2b.
```

The response includes a `format_detection` audit (`ran`, `field_mapping`,
`rows_dropped`) — when uploaded keys don't match the canonical schema
(`text`/`label` for classification, `question`/`answer` for QA / tools)
the server runs a key-rename pass via gemini-2.5-flash-lite before
persisting.

For QA, you can also upload a **PDF** instead of JSONL — the SDG worker
extracts Q&A pairs from the document on its first iteration:

```bash
curl -X POST http://localhost:8000/api/v1/datasets/upload-seed \
  -F "project_id=<project_id>" \
  -F "task_type=qa" \
  -F "file=@./policy.pdf;type=application/pdf"
# → 201 Created. Body: { dataset_id, pdf_uri, ... }
```

PDFs are capped at 25 MiB / 100 pages.

##### 2b. Submit the SDG job

```bash
curl -X POST http://localhost:8000/api/v1/datasets/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "sdg_mode": "with_seed",
    "project_id": "<project_id>",
    "task_type": "qa",
    "task_description": "Answer questions about our return policy",
    "num_samples": 200,
    "seed_dataset_id": "<seed_dataset_id>"
  }'
# → 202 Accepted; subscribe to ws://localhost:8000/ws/jobs/{job_id} for progress
```

The progress stream emits `SDGProgress` messages with phase markers
(`format_detection`, `meta_prompting`, `generating`, `judging`, `dedup`,
`persisting`) and per-loop counters (`current_loop`, `judge_rejected`,
`judge_parse_failures`, `dedup_rejected`).

#### 3. Submit a training job

Manual mode:

```bash
curl -X POST http://localhost:8000/api/v1/trainings \
  -H 'Content-Type: application/json' \
  -d '{
    "mode": "manual",
    "project_id": "<project_id>",
    "dataset_id": "<dataset_id>",
    "manual_config": {
      "learning_rate": 2e-4,
      "num_train_epochs": 3,
      "per_device_train_batch_size": 2,
      "gradient_accumulation_steps": 4,
      "lora": {"r": 16, "alpha": 32, "dropout": 0.05}
    }
  }'
```

HPO mode:

```bash
curl -X POST http://localhost:8000/api/v1/trainings \
  -H 'Content-Type: application/json' \
  -d '{
    "mode": "hpo",
    "project_id": "<project_id>",
    "dataset_id": "<dataset_id>",
    "hpo_config": {
      "n_trials": 8,
      "objective_metric": "eval_loss",
      "direction": "minimize",
      "search_space": {
        "learning_rate": {"type": "float", "low": 1e-5, "high": 1e-3, "log": true},
        "lora_r": {"type": "categorical", "choices": [8, 16, 32]}
      }
    }
  }'
```

#### 4. Export to GGUF and serve via Ollama

```bash
curl -X POST http://localhost:8000/api/v1/models/<model_id>/export \
  -H 'Content-Type: application/json' \
  -d '{"format": "gguf", "quantization": "q4_k_m"}'
# When complete, the artifact has `ollama_model_tag` set (e.g. "slm/abcd1234").
```

#### 5. Inference (OpenAI-compatible)

```bash
curl http://localhost:8000/api/v1/inference/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "<model_id>",
    "messages": [{"role": "user", "content": "What is your return window?"}],
    "temperature": 0.0
  }'
```

`model` accepts either a `ModelArtifact` UUID (the platform looks up the Ollama tag) or a
literal Ollama tag like `llama3.2:3b`.

#### 6. Evaluate

```bash
curl -X POST http://localhost:8000/api/v1/evaluations \
  -H 'Content-Type: application/json' \
  -d '{
    "model_artifact_id": "<model_id>",
    "dataset_id": "<dataset_id>",
    "use_llm_judge": true,
    "judge_model": "anthropic/claude-3.5-sonnet"
  }'
```

### Python (httpx + websockets)

Run the bundled walkthrough:

```bash
pip install httpx websockets
python examples/python_client.py --task-type qa --num-samples 30
python examples/python_client.py --task-type classification --train   # also fine-tune
```

It mirrors the curl flow above and streams `TrainingProgress` / `SDGProgress` /
`HPOProgress` messages from the WebSocket.

### Error responses

All error bodies share one shape (`api/schemas/responses.py::ErrorResponse`):

```json
{ "detail": "Project 00000000-... not found", "code": "not_found", "extra": null }
```

Validation errors (422) include the per-field details under `extra.errors`.

---

## Repository layout

```
slm-platform/
├── api/             # FastAPI app: routers, schemas, ORM models, services
├── workers/         # Celery worker app + task definitions
├── ai_engine/       # Pure domain logic — data_gen, training, hpo, evaluation
│                    # (no FastAPI / Celery imports — keep hexagonal)
├── tests/           # unit + integration
├── examples/        # Standalone client scripts (Phase 8)
├── docs/            # Architecture, ADRs, standards, prompt templates
├── docker/          # Per-service Dockerfiles (api, worker, mlflow)
├── alembic/         # DB migrations (Phase 2)
├── scripts/         # Operational helpers
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── CLAUDE.md
├── WORKING_LOG.md
├── TASK_TRACKER.md
└── require.md       # Original spec — DO NOT EDIT
```

---

## Development workflow

### Container-first (recommended)

The `api` and `worker` services bind-mount their source — code changes reload automatically:

```bash
docker compose logs -f api worker     # tail logs
docker compose restart api            # full restart if you changed deps
docker compose exec api bash          # shell into the API container
docker compose exec worker bash       # shell into the worker (GPU) container
```

### Host-side tooling

For lint / type-check / unit tests run on the host (no GPU needed):

```bash
python -m venv .venv && source .venv/bin/activate   # Linux/macOS
# .\.venv\Scripts\Activate.ps1                       # Windows PowerShell

pip install -e ".[dev,eval]"

ruff check .
ruff format .
mypy api workers ai_engine
pytest -m "not integration and not gpu"
```

### Database migrations

```bash
docker compose exec api alembic revision --autogenerate -m "describe change"
docker compose exec api alembic upgrade head
```

---

## Troubleshooting

**`docker compose up` hangs on the worker build**
The worker image installs torch + unsloth on top of CUDA — first build is 10–15 min and
several GB. Subsequent builds use the layer cache.

**`worker` container exits with `could not select device driver "nvidia"`**
NVIDIA Container Toolkit isn't installed or Docker can't see it. Re-run the prerequisite
verification (`docker run --gpus all nvidia/cuda:... nvidia-smi`). On Windows, the toolkit
must be installed inside WSL2, not on Windows directly.

**`api` keeps restarting, logs say `connection refused` to postgres**
Postgres takes a few seconds to become healthy. The compose file already gates `api` on
`postgres: condition: service_healthy`, so this should self-resolve. If it persists,
inspect the postgres logs (`docker compose logs postgres`).

**MLflow shows no artifacts even though training "completed"**
MinIO bucket creation is handled by the `minio-init` job. If you see this, run:
```bash
docker compose up -d minio-init
```

**Out of GPU memory during fine-tuning on RTX 3060 12GB**
Drop `per_device_train_batch_size`, raise `gradient_accumulation_steps`, or shorten
`max_seq_length`. Hard cap on model size is 3B params (ADR-002).

**OpenRouter returns 401**
Double-check `OPENROUTER_API_KEY` in `.env`, then `docker compose restart api worker`.

---

## Working with this repo as an AI agent

If you are an AI assistant (Claude Code, Cursor, etc.), read these files in order before
making changes:

1. [`CLAUDE.md`](./CLAUDE.md) — session protocol, hard constraints, forbidden files
2. [`WORKING_LOG.md`](./WORKING_LOG.md) — most recent session state
3. [`TASK_TRACKER.md`](./TASK_TRACKER.md) — phase-by-phase task list
4. [`require.md`](./require.md) — original spec (source of truth)
5. [`docs/adr/`](./docs/adr/) — architectural decisions

The project follows an AI-native session protocol: **Discovery → Execution → Handover**.
Update `WORKING_LOG.md` and `TASK_TRACKER.md` at the end of every session.
