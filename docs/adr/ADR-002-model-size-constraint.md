# ADR-002: Model Size ≤3B + QLoRA 4-bit Only

**Status:** Accepted
**Confidence:** High
**Date:** 2026-05-07
**Supersedes:** —

## Context + Decision Drivers

The developer runs everything on a **single RTX 3060 with 12GB VRAM**. We must:

- Fine-tune SLMs in this VRAM budget
- Leave headroom for activations, gradients, optimizer state, and the data loader
- Allow Ollama to run inference on the same GPU (or share gracefully)

QLoRA 4-bit quantization is the established way to shrink memory footprint while preserving fine-tune quality. A 3B parameter model in 4-bit weights fits comfortably (~2GB for weights), leaving ~8–10GB for training overhead.

## Decision

- **Maximum model size: 3 billion parameters**
- **All loading uses `load_in_4bit=True`** via Unsloth / bitsandbytes
- **Default base model:** `unsloth/Llama-3.2-3B-Instruct-bnb-4bit`
- After every training/HPO run, **release GPU memory**:
  - `del model; del trainer`
  - `torch.cuda.empty_cache()`
  - `gc.collect()` (defensive)

## Alternatives Considered

- **7B+ models with QLoRA** — rejected; risk of OOM during training, doesn't leave room for Ollama
- **Full-precision LoRA (no quantization)** — rejected; OOM at 3B
- **CPU offloading via accelerate** — rejected; 10x+ slower and not in scope for PoC
- **Gradient checkpointing only** — accepted as a complementary technique, but does not replace 4-bit

## AI Instructions

**Guidance Level: STRICT**

- Refuse / flag any request to load a model >3B parameters
- Always pass `load_in_4bit=True` to Unsloth's `FastLanguageModel.from_pretrained`
- Always emit GPU cleanup at the end of every training Celery task (in a `finally` block)
- When suggesting a base model, default to `unsloth/Llama-3.2-3B-Instruct-bnb-4bit` unless the developer specified otherwise
- Validation: in `api/schemas/training.py`, validate the user-selected base model is in an allowlist of ≤3B 4-bit-ready models

## Consequences

✅ Training fits on consumer GPU
✅ Inference (Ollama) can co-exist
✅ Reproducible memory profile
⚠️ Cannot fine-tune bigger frontier-style SLMs (acceptable for PoC)
⚠️ User cannot pick arbitrary HF model — must be in our allowlist
