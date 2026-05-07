# Project: Automated SLM Fine-Tuning Platform (Backend-Only PoC)

You are helping me build a backend-only PoC for an automated Small Language Model (SLM) fine-tuning platform. I'm running this on **localhost with RTX 3060 12GB VRAM**, using OpenRouter for synthetic data generation, and MLflow for experiment tracking. There's NO authentication and NO frontend - I'm building API contracts only since my teammate handles the frontend separately.

---

## 🎯 Project Goals

Build a backend platform that:
1. Accepts user input (task description + optional seed data) via REST API
2. Generates synthetic training data using OpenRouter LLMs (teacher models)
3. Fine-tunes small language models (≤3B params) using Unsloth + QLoRA
4. Tracks experiments with MLflow
5. Serves trained models via OpenAI-compatible API
6. Streams real-time progress to frontend via WebSocket

---

## 🚫 Hard Constraints

- **NO authentication system** - all endpoints are open
- **NO frontend code** - backend only, but expose clean OpenAPI spec for my teammate
- **GPU constraint**: RTX 3060 12GB - models must fit in QLoRA 4-bit
- **Model size limit**: ≤3B parameters only
- **Use MLflow** for ALL experiment tracking (not W&B, not TensorBoard)
- **Use OpenRouter** for synthetic data generation (not direct OpenAI/Anthropic)

---

## 📋 Supported Tasks (ONLY these 3)

### 1. Classification
```json
{"text": "I can't log into my account", "label": "technical"}
```

### 2. Tool Calling
```json
{
  "question": "Margherita pizza recipe says oven needs 250°C, currently at 100°C",
  "answer": "{\"name\":\"wait\",\"parameters\":{\"seconds\":150}}"
}
```
Note: `answer` is a JSON string with `name` and `parameters` keys.

### 3. QA
```json
{"question": "What is the return policy?", "answer": "You can return items within 30 days of purchase."}
```

---

## 🤖 SDG (Synthetic Data Generation) - 2 Modes

### Mode 1: `with_seed`
User provides:
- `seed_data`: 10-50 examples
- `task_description`: text
- `num_samples`: how many to generate

### Mode 2: `description_only`
User provides:
- `task_description`: text
- `num_samples`: how many to generate
- For classification: must also provide `labels` list
- For tool_calling: must also provide `tool_definitions`

Both modes call OpenRouter API with task-specific prompts.

---

## 🎓 Training - 2 Modes

### Mode 1: `manual`
User provides explicit hyperparameters (lr, batch_size, lora_r, etc.)

### Mode 2: `hpo`
Use Optuna to search hyperparameters:
- User defines search space ranges
- System runs N trials
- MLflow tracks every trial as nested run
- Final model trained with best params

---

## 🏗️ Tech Stack (Locked - do not deviate)

| Layer | Technology |
|-------|------------|
| API Framework | FastAPI |
| Validation | Pydantic v2 |
| Job Queue | Celery + Redis |
| Database | PostgreSQL (with SQLAlchemy 2.0 ORM) |
| Object Storage | MinIO (S3-compatible) |
| Experiment Tracking | MLflow |
| Synthetic Data | OpenRouter API (via openai SDK) |
| Fine-tuning | Unsloth + QLoRA + TRL |
| HPO | Optuna |
| Inference | Ollama (OpenAI-compatible) |
| Real-time | WebSocket + Redis Pub/Sub |
| Containerization | Docker Compose |

---

## 📁 Required Project Structure

```
slm-platform/
├── docker-compose.yml
├── .env.example
├── README.md
├── pyproject.toml
├── api/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── database.py
│   │   └── celery_client.py
│   ├── routers/
│   │   ├── projects.py
│   │   ├── datasets.py
│   │   ├── trainings.py
│   │   ├── models.py
│   │   ├── inference.py
│   │   ├── evaluations.py
│   │   ├── tasks_meta.py
│   │   └── websocket.py
│   ├── schemas/
│   │   ├── enums.py
│   │   ├── data_formats.py
│   │   ├── sdg.py
│   │   ├── training.py
│   │   ├── progress.py
│   │   └── responses.py
│   ├── models/
│   └── services/
├── workers/
│   ├── celery_app.py
│   └── tasks/
│       ├── data_generation.py
│       ├── training.py
│       ├── hpo_training.py
│       └── evaluation.py
├── ai_engine/
│   ├── data_gen/
│   │   ├── openrouter_client.py
│   │   ├── prompts.py
│   │   ├── validators.py
│   │   └── deduplicator.py
│   ├── training/
│   │   ├── unsloth_trainer.py
│   │   ├── data_formatters.py
│   │   ├── callbacks.py
│   │   └── mlflow_logger.py
│   ├── hpo/
│   │   ├── optuna_objective.py
│   │   └── search_spaces.py
│   └── evaluation/
│       ├── metrics_classification.py
│       ├── metrics_tool_calling.py
│       ├── metrics_qa.py
│       └── llm_judge.py
├── tests/
└── examples/
```

---

## 🛣️ Required API Endpoints

```
# Projects
POST   /api/v1/projects
GET    /api/v1/projects
GET    /api/v1/projects/{id}
PATCH  /api/v1/projects/{id}
DELETE /api/v1/projects/{id}

# Datasets
POST   /api/v1/datasets/upload-seed       # multipart/form-data
POST   /api/v1/datasets/generate          # SDG (both modes)
GET    /api/v1/datasets/{id}
GET    /api/v1/datasets/{id}/preview?limit=20
GET    /api/v1/datasets/{id}/download
DELETE /api/v1/datasets/{id}

# Trainings
POST   /api/v1/trainings                  # both manual + hpo modes
GET    /api/v1/trainings
GET    /api/v1/trainings/{id}
DELETE /api/v1/trainings/{id}             # cancel
GET    /api/v1/trainings/{id}/mlflow-url

# Models
GET    /api/v1/models
GET    /api/v1/models/{id}
POST   /api/v1/models/{id}/export         # gguf, safetensors
GET    /api/v1/models/{id}/download

# Inference (OpenAI-compatible)
POST   /api/v1/inference/chat/completions
POST   /api/v1/inference/completions
GET    /api/v1/inference/models

# Evaluation
POST   /api/v1/evaluations
GET    /api/v1/evaluations/{id}
POST   /api/v1/evaluations/compare

# Metadata (for frontend dynamic forms)
GET    /api/v1/tasks                      # returns supported task schemas
GET    /api/v1/tasks/{task_type}/example
GET    /api/v1/base-models                # supported base models

# WebSocket
WS     /ws/jobs/{job_id}                  # real-time progress
```

---

## 📐 Critical Pydantic Schemas (these are the API contracts)

### Enums
```python
class TaskType(str, Enum):
    CLASSIFICATION = "classification"
    TOOL_CALLING = "tool_calling"
    QA = "qa"

class TrainingMode(str, Enum):
    MANUAL = "manual"
    HPO = "hpo"

class SDGMode(str, Enum):
    WITH_SEED = "with_seed"
    DESCRIPTION_ONLY = "description_only"

class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

### SDGRequest must validate:
- `with_seed` mode → seed_data required (≥5 samples)
- `description_only` + classification → classification_config.labels required
- `description_only` + tool_calling → tool_definitions required
- `description_only` + qa → no extra config required

### TrainingRequest must validate:
- `manual` mode → manual_config required
- `hpo` mode → hpo_config required (with search space)

### WebSocket messages must follow this format:
```python
{
  "type": "sdg_progress" | "training_progress" | "hpo_progress" | "completed" | "failed",
  "job_id": "...",
  ...task-specific fields
}
```

---

## 🐳 Docker Compose Services Required

- `postgres:16-alpine` (port 5432)
- `redis:7-alpine` (port 6379)
- `minio/minio` (ports 9000, 9001)
- `mlflow` (port 5000) - using PostgreSQL backend + MinIO artifacts
- `api` - FastAPI service (port 8000)
- `worker` - Celery worker WITH GPU access (NVIDIA runtime)
- `ollama/ollama` (port 11434) WITH GPU access

GPU services must include:
```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: 1
          capabilities: [gpu]
```

---

## ✅ CORS Configuration (Critical for Frontend)

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 🧪 Implementation Phases (Build in this order)

### Phase 1: Foundation
1. Create complete project structure (all directories)
2. Write `docker-compose.yml` with all services
3. Write `.env.example` with all required env vars
4. Write `pyproject.toml` with all dependencies
5. Write comprehensive `README.md` with setup instructions

### Phase 2: Schemas & Models (Most Important - These are API contracts)
1. ALL Pydantic schemas in `api/schemas/` - these must be complete and correct
2. SQLAlchemy ORM models for: Project, Dataset, TrainingJob, ModelArtifact, EvaluationRun
3. Database migrations setup (Alembic)

### Phase 3: API Endpoints (Skeleton)
1. FastAPI app with CORS, OpenAPI metadata, routers mounted
2. All endpoints exist and validate inputs (return 501 Not Implemented for logic)
3. WebSocket endpoint working with Redis pub/sub
4. `/api/v1/tasks` and `/api/v1/base-models` fully working (static data)

### Phase 4: SDG Pipeline
1. OpenRouter client wrapper
2. Prompt templates for 3 tasks × 2 modes (6 total templates)
3. Pydantic validators per task type
4. Deduplication logic
5. Celery task `generate_synthetic_data` with progress publishing
6. Wire to `POST /datasets/generate`

### Phase 5: Training Pipeline (Manual)
1. Unsloth wrapper for loading + LoRA setup
2. Data formatters (3 task types → chat format)
3. Custom WebSocketProgressCallback for HuggingFace Trainer
4. MLflow integration (log params, metrics, model)
5. Celery task `train_manual`
6. Wire to `POST /trainings` with mode=manual

### Phase 6: HPO Pipeline
1. Optuna search space builder
2. Objective function with MLflow nested runs
3. Celery task `train_hpo`
4. Wire to `POST /trainings` with mode=hpo

### Phase 7: Inference & Evaluation
1. Ollama integration (convert + load LoRA → GGUF)
2. OpenAI-compatible inference endpoints
3. Evaluation metrics per task type
4. LLM-as-judge using OpenRouter
5. Model export endpoints (GGUF, SafeTensors)

### Phase 8: Polish
1. Comprehensive error handling
2. Request/response examples in OpenAPI docs
3. End-to-end integration test
4. Example client scripts in `examples/`
5. Update README with API usage examples

---

## 🎨 Code Quality Requirements

- **Python 3.11+**
- **Type hints everywhere** (use `from __future__ import annotations`)
- **Pydantic v2 syntax** (`model_validator`, `field_validator`, `model_dump`)
- **SQLAlchemy 2.0 syntax** (declarative, async where possible)
- **Async FastAPI handlers** where I/O bound
- **Docstrings** on all public functions (Google style)
- **Logging** with `logging` module (not print)
- **Error handling** with proper HTTP status codes
- **Environment variables** via `pydantic-settings`
- **NO hardcoded values** - all config via env or settings

---

## 📦 Required Python Dependencies

```toml
[project]
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "pydantic>=2.10.0",
    "pydantic-settings>=2.7.0",
    "sqlalchemy>=2.0.36",
    "alembic>=1.14.0",
    "psycopg2-binary>=2.9.10",
    "celery>=5.4.0",
    "redis>=5.2.0",
    "minio>=7.2.10",
    "python-multipart>=0.0.17",
    "websockets>=13.1",
    "openai>=1.58.1",
    "mlflow>=2.19.0",
    "optuna>=4.1.0",
    "tenacity>=9.0.0",
]

[project.optional-dependencies]
training = [
    "torch>=2.5.1",
    "unsloth>=2024.12.4",
    "transformers>=4.47.0",
    "peft>=0.14.0",
    "trl>=0.13.0",
    "bitsandbytes>=0.45.0",
    "accelerate>=1.2.0",
    "datasets>=3.2.0",
]
eval = [
    "evaluate>=0.4.3",
    "rouge-score>=0.1.2",
    "scikit-learn>=1.6.0",
    "deepeval>=2.0.9",
]
```

---

## 🚨 Important Implementation Notes

1. **OpenRouter calls**: Use `openai` SDK with `base_url="https://openrouter.ai/api/v1"`. Default teacher model: `anthropic/claude-3.5-sonnet`.

2. **Unsloth model loading**: Always use `load_in_4bit=True` for QLoRA. Default base model: `unsloth/Llama-3.2-3B-Instruct-bnb-4bit`.

3. **Data format conversion**: Each task type needs different prompt formatting:
   - Classification → `### Text: {text}\n### Label: {label}`
   - Tool Calling → ChatML format with system prompt listing available tools
   - QA → Alpaca-style instruction format

4. **WebSocket progress**: Workers publish to Redis channel `job:{job_id}`. WebSocket endpoint subscribes to this channel.

5. **MLflow tracking URI**: Read from env `MLFLOW_TRACKING_URI`. Default: `http://localhost:5000`.

6. **Job IDs**: Use Celery task IDs as job IDs - don't generate separate ones.

7. **HPO trials**: Each trial = nested MLflow run. Parent run logs final best params + final metric.

8. **Validation errors**: Return 422 with Pydantic error details (FastAPI default).

9. **Background tasks**: Don't use FastAPI BackgroundTasks - use Celery for everything async.

10. **GPU memory**: Always cleanup after training (`torch.cuda.empty_cache()`, delete model).

---

## 📝 Deliverables Checklist

After all phases complete, I should be able to:
- [ ] Run `docker compose up` and have all services healthy
- [ ] Visit `http://localhost:8000/docs` and see complete Swagger UI
- [ ] Visit `http://localhost:5000` and see MLflow UI
- [ ] Visit `http://localhost:9001` and see MinIO Console
- [ ] Run example scripts in `examples/` end-to-end
- [ ] Hand `http://localhost:8000/openapi.json` to my frontend teammate
- [ ] Generate synthetic data for all 3 task types in both modes
- [ ] Train models in both manual and HPO modes
- [ ] See real-time progress via WebSocket
- [ ] Export trained models to GGUF and load in Ollama
- [ ] Call inference endpoint OpenAI-style

---

## 🎯 Let's Start

**Step 1**: Read this entire spec carefully and confirm understanding.

**Step 2**: Ask me clarifying questions IF needed (but try to make sensible defaults first).

**Step 3**: Start with **Phase 1: Foundation**. Build the complete project structure, Docker setup, and dependencies. Don't move to Phase 2 until I approve Phase 1.

**Step 4**: After each phase, summarize what was done and wait for my approval before moving to the next phase.

Important: When in doubt about a design decision, prefer:
- Simplicity over cleverness
- Explicit over implicit
- Well-known patterns over custom solutions
- Strong typing over flexibility

Now begin Phase 1.