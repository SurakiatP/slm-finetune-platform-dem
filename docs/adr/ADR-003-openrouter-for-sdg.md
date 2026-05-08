# ADR-003: OpenRouter for Synthetic Data Generation

**Status:** Accepted
**Confidence:** High
**Date:** 2026-05-07
**Supersedes:** —

## Context + Decision Drivers

Synthetic Data Generation (SDG) needs a strong "teacher" LLM. We could call OpenAI / Anthropic / Together / etc. directly, but:

- Each provider is a separate billing relationship and SDK surface
- We want to A/B different teachers (Claude vs GPT vs Llama-3.1-405B) without rewriting calls
- A unified `openai`-compatible interface keeps the client wrapper trivial

OpenRouter sits in front of all of these and exposes a single OpenAI-compatible endpoint at `https://openrouter.ai/api/v1`.

## Decision

- All SDG calls go through **OpenRouter**, using the `openai` Python SDK with `base_url="https://openrouter.ai/api/v1"`
- API key from env var `OPENROUTER_API_KEY`
- Default teacher: `anthropic/claude-3.5-sonnet` (overridable via `OPENROUTER_TEACHER_MODEL` env or per-request param)
- The same client is reused for the **LLM-as-judge** evaluator (see Phase 7)

## Alternatives Considered

- **Direct OpenAI SDK** — rejected; locks us to one provider, doesn't match require.md
- **Direct Anthropic SDK** — rejected; same reason; also lacks a wide model menu
- **LiteLLM proxy** — viable but adds an extra service to deploy locally; OpenRouter is hosted and free of ops
- **HuggingFace Inference API** — rejected; weaker model menu, less reliable for SDG quality

## AI Instructions

**Guidance Level: STRICT**

- Do **NOT** import `anthropic`, `cohere`, `google.generativeai`, etc. for generation — only `openai`
- Always set `base_url="https://openrouter.ai/api/v1"` when constructing the client
- Read API key from `Settings` (pydantic-settings), never hardcode
- All SDG and LLM-judge logic lives in `ai_engine/data_gen/openrouter_client.py` — do not duplicate the client construction elsewhere
- Use `tenacity` for retries on transient errors (5xx, rate-limit, timeout)
- Log every call's model, token usage, and latency at INFO level

## Consequences

✅ One billing account, one SDK, many models
✅ Easy A/B of teachers via env var
✅ Same client serves SDG and LLM-judge
⚠️ OpenRouter is a single point of failure — if it's down, SDG and judge are down
⚠️ Slightly higher per-token cost than direct providers (acceptable for PoC scale)
