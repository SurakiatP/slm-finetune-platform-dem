# ADR-001: Locked Tech Stack

**Status:** Accepted
**Confidence:** High
**Date:** 2026-05-07
**Supersedes:** —

## Context + Decision Drivers

`require.md` (the user-supplied spec) explicitly enumerates a tech stack and labels it "Locked — do not deviate." Drivers:

- Single-developer PoC on RTX 3060 12GB (hardware constraint)
- Frontend teammate expects a specific OpenAPI shape from FastAPI
- Local-only deployment with Docker Compose
- The user has already evaluated alternatives and locked their choice

## Decision

Use exactly the stack listed in [`docs/architecture/TECH_STACK.md`](../architecture/TECH_STACK.md). The most load-bearing locks:

- **MLflow** for all experiment tracking
- **OpenRouter** (via `openai` SDK) for SDG
- **Unsloth + QLoRA + TRL** for fine-tuning
- **Celery + Redis** for async jobs
- **PostgreSQL + SQLAlchemy 2.0** for state
- **MinIO** for object storage
- **Ollama** for inference
- **FastAPI + Pydantic v2** for API

## Alternatives Considered

- **W&B / TensorBoard** for tracking — rejected; require.md specifies MLflow.
- **OpenAI / Anthropic SDK direct** — rejected; OpenRouter centralizes billing and teacher selection (see ADR-003).
- **vLLM / Text Generation Inference** for serving — rejected; Ollama provides simpler GGUF flow on consumer GPU.
- **arq / Dramatiq / RQ** for async — rejected; require.md specifies Celery.
- **MongoDB / SQLite** — rejected; PostgreSQL is required for MLflow backend + relational queries.

## AI Instructions

**Guidance Level: STRICT**

- Do **NOT** suggest replacements for any locked tool.
- If a use case appears to need a different tool, propose the problem to the developer **first** with reasoning; only proceed with explicit approval and a new ADR.
- Adding a *new* dependency (not a replacement) is allowed if it doesn't conflict with the locked stack — call it out in the WORKING_LOG entry for that session.
- Always reference [`docs/architecture/TECH_STACK.md`](../architecture/TECH_STACK.md) for exact versions when generating `pyproject.toml` or import statements.

## Consequences

✅ Predictable, reproducible environment for the developer.
✅ Clear contract with the frontend teammate.
✅ Reduced decision fatigue mid-implementation.
⚠️ Less flexibility if a tool turns out to be a poor fit — must go through ADR process.
⚠️ Lock-in to specific versions; periodic review needed.
