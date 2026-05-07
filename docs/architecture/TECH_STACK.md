# Tech Stack (Locked)

> Authoritative reference. Any addition or replacement requires an ADR.
> See [ADR-001](../adr/ADR-001-locked-tech-stack.md) for the rationale.

## Runtime

| Component | Version | Purpose |
|-----------|---------|---------|
| Python | 3.11+ | All services |
| Docker | latest | Container runtime |
| Docker Compose | v2+ | Service orchestration |
| NVIDIA Container Toolkit | latest | GPU passthrough |

## Infrastructure Services (Docker Compose)

| Service | Image | Port | Notes |
|---------|-------|------|-------|
| postgres | `postgres:16-alpine` | 5432 | App state |
| redis | `redis:7-alpine` | 6379 | Celery broker + pub/sub |
| minio | `minio/minio` | 9000, 9001 | S3-compatible blob store + console |
| mlflow | custom (uses postgres + minio) | 5000 | Tracking + model registry |
| api | local FastAPI | 8000 | HTTP + WebSocket |
| worker | local Celery + GPU | — | NVIDIA runtime, 1 GPU reserved |
| ollama | `ollama/ollama` | 11434 | NVIDIA runtime, 1 GPU |

GPU services declare:
```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: 1
          capabilities: [gpu]
```

## API Layer

| Library | Min Version | Purpose |
|---------|-------------|---------|
| fastapi | 0.115.0 | HTTP framework |
| uvicorn[standard] | 0.32.0 | ASGI server |
| pydantic | 2.10.0 | Validation |
| pydantic-settings | 2.7.0 | Config from env |
| websockets | 13.1 | WebSocket support |
| python-multipart | 0.0.17 | File uploads |

## Persistence

| Library | Min Version | Purpose |
|---------|-------------|---------|
| sqlalchemy | 2.0.36 | ORM (use 2.0 declarative + async) |
| alembic | 1.14.0 | Migrations |
| psycopg2-binary | 2.9.10 | Postgres driver |
| minio | 7.2.10 | S3 client |

## Async Jobs

| Library | Min Version | Purpose |
|---------|-------------|---------|
| celery | 5.4.0 | Distributed task queue |
| redis | 5.2.0 | Broker + result backend + pub/sub |
| tenacity | 9.0.0 | Retry logic |

## ML / Training

| Library | Min Version | Purpose |
|---------|-------------|---------|
| torch | 2.5.1 | Tensor / CUDA |
| unsloth | 2024.12.4 | Fast QLoRA fine-tuning |
| transformers | 4.47.0 | HF models / Trainer |
| peft | 0.14.0 | LoRA / adapter |
| trl | 0.13.0 | SFTTrainer |
| bitsandbytes | 0.45.0 | 4-bit quantization |
| accelerate | 1.2.0 | Multi-device coordination |
| datasets | 3.2.0 | HF datasets |
| optuna | 4.1.0 | HPO |
| mlflow | 2.19.0 | Experiment tracking |

## Synthetic Data Generation

| Library | Min Version | Purpose |
|---------|-------------|---------|
| openai | 1.58.1 | Used with `base_url=https://openrouter.ai/api/v1` (see ADR-003) |

## Evaluation

| Library | Min Version | Purpose |
|---------|-------------|---------|
| evaluate | 0.4.3 | HF metrics |
| rouge-score | 0.1.2 | QA metrics |
| scikit-learn | 1.6.0 | Classification metrics |
| deepeval | 2.0.9 | LLM judge utilities |

## Default Models

| Use | Default | Override |
|-----|---------|----------|
| Teacher (SDG) | `anthropic/claude-3.5-sonnet` (via OpenRouter) | env `OPENROUTER_TEACHER_MODEL` |
| Base (training) | `unsloth/Llama-3.2-3B-Instruct-bnb-4bit` | env `DEFAULT_BASE_MODEL` |
| Inference runtime | Ollama | — |

## Forbidden / Replaced

- ❌ Weights & Biases / TensorBoard → MLflow only
- ❌ Direct OpenAI / Anthropic SDK calls → OpenRouter only
- ❌ FastAPI BackgroundTasks for long jobs → Celery only
- ❌ Synchronous HF Trainer in API process → must be inside Celery worker
- ❌ vLLM / TGI → Ollama (simpler GGUF flow on RTX 3060)
