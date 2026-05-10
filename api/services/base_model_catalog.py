"""Mapping from a training `base_model` id (Unsloth/HF) to the equivalent
Ollama-Hub tag, plus best-effort pull helpers.

Why a separate module: the same mapping is read by two callers that should
not import each other —
  • `api.services.model_service` populates `ModelArtifactResponse.base_ollama_tag`
  • `workers.tasks.model_export` triggers `ollama pull` after registering
    a fine-tuned tag, so the playground can A/B compare base vs fine-tuned

Mapping rationale: Ollama-Hub publishes the same Meta/Qwen/Google instruct
weights as the Unsloth pre-quantized variants we use during training. The
exact bit-level numerics differ (Ollama serves its own GGUF q4_K_M; we go
BNB-4bit → merge f16 → llama-quantize q4_k_m), so this is suitable for
demo playground comparisons but not for scientific A/B benchmarks.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


# Authoritative table — six entries, mirrored 1:1 with `SUPPORTED_BASE_MODELS`
# in `api/routers/tasks_meta.py`. When a new base model is added there, add it
# here as well (or set the value to `None` if Ollama-Hub doesn't carry it).
_BASE_TO_OLLAMA_TAG: dict[str, str | None] = {
    "unsloth/Llama-3.2-1B-Instruct-bnb-4bit": "llama3.2:1b",
    "unsloth/Llama-3.2-3B-Instruct-bnb-4bit": "llama3.2:3b",
    "unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit": "qwen2.5:0.5b",
    "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit": "qwen2.5:1.5b",
    "unsloth/Qwen2.5-3B-Instruct-bnb-4bit": "qwen2.5:3b",
    "unsloth/gemma-2-2b-it-bnb-4bit": "gemma2:2b",
}


def get_ollama_base_tag(base_model: str) -> str | None:
    """Return the Ollama-Hub equivalent of an Unsloth base id, or None.

    `None` means: we have not mapped this base (frontend should hide the
    "compare with base" affordance for this artifact).
    """
    return _BASE_TO_OLLAMA_TAG.get(base_model)


def pull_ollama_base_blocking(
    base_model: str,
    *,
    ollama_base_url: str,
    timeout_s: float = 300.0,
) -> bool:
    """Best-effort `ollama pull` of the base equivalent, sync.

    Used by the worker after a successful fine-tuned-model register so the
    playground can immediately A/B compare base vs fine-tuned without the
    user having to SSH in and run `ollama pull` by hand.

    Returns True on success, False on any failure — callers must NOT raise:
    a missed pull is a soft degradation (FE will see `base_ollama_pulled=
    false` until the user pulls manually), not a training failure.
    """
    tag = get_ollama_base_tag(base_model)
    if not tag:
        log.info("ollama pull skipped: no mapping for base_model=%s", base_model)
        return False

    base = ollama_base_url.rstrip("/")
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(f"{base}/api/pull", json={"name": tag, "stream": False})
            if resp.status_code >= 400:
                log.warning(
                    "ollama pull %s failed: HTTP %d body=%s",
                    tag,
                    resp.status_code,
                    resp.text[:300],
                )
                return False
    except httpx.HTTPError as exc:
        log.warning("ollama pull %s errored: %s", tag, exc)
        return False

    log.info("ollama pull %s OK (base for fine-tune A/B compare)", tag)
    return True


__all__ = [
    "get_ollama_base_tag",
    "pull_ollama_base_blocking",
]
