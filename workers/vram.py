"""GPU VRAM preflight + friendly-OOM-message helpers, shared across the
three GPU-bound Celery tasks (`training.py`, `hpo_training.py`,
`model_export.py`).

Why this exists: ADR-002 pins the target hardware to a single RTX 3060
12GB — there is no second GPU to fail over to. If a task starts GPU work
while the card is still pinned by a previous job, it doesn't queue
politely; it OOMs deep inside a CUDA kernel, minutes into the run, after
the dataset/adapter has already been downloaded and the (heavy) training
stack imported. `preflight_gpu_vram()` checks free VRAM *before* any of
that happens, so a busy card fails fast with an actionable message.
`friendly_oom_message()` covers the case where an OOM slips past the
preflight anyway (another process grabs VRAM in the gap, a single job's
own peak allocation exceeds what was free at preflight time, etc.) and
turns whatever CUDA/PyTorch raised into the same actionable wording.

IMPORTANT: `torch` is NOT installed in the unit-test environment (see
`tests/unit/test_hexagonal_boundaries.py` / the worker-image split this
codebase already has to reason about elsewhere). Every `torch` reference
in this module MUST stay behind a lazy, in-function `import torch` inside
a `try/except`, exactly like `workers/tasks/training.py`'s
`_release_gpu_memory` — importing this module itself must never require
torch to be present, or `pytest tests/unit/` breaks in CI.
"""

from __future__ import annotations

from api.core.config import get_settings

# workers/ may import api.core.config — the hexagonal boundary
# (ai_engine/ -> never api.core.config, enforced by
# tests/unit/test_hexagonal_boundaries.py) only restricts ai_engine/, not
# workers/, which already reads settings this way throughout
# workers/tasks/*.py.


def _cuda_free_total_gb() -> tuple[float, float] | None:
    """Return `(free_gib, total_gib)` for the current CUDA device, or
    `None` when there is no usable CUDA (torch not installed, or no GPU
    visible to this process).

    Fail-soft by design, same shape as `workers/tasks/training.py`'s
    `_release_gpu_memory`: a worker image without torch, or a CPU-only
    dev box, must be able to import this module and call this function
    without blowing up — the caller (`preflight_gpu_vram`) treats `None`
    as "nothing to check" rather than an error.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free_bytes, total_bytes = torch.cuda.mem_get_info()
    except Exception:  # noqa: BLE001 — torch missing, driver hiccup, etc.
        return None

    gib = 1024**3
    return free_bytes / gib, total_bytes / gib


def preflight_gpu_vram(*, job_kind: str) -> None:
    """Raise `RuntimeError` if there isn't enough free VRAM to safely start
    a GPU-bound job. No-op (never raises) when the check is disabled, or
    when there's nothing to check (no CUDA visible to this process — e.g.
    a CPU-only dev box, which must still be able to run the task for
    local iteration).

    Call this AFTER a task's zombie-cancel start-guard (so a job cancelled
    while still queued exits on that path first, not this one) and BEFORE
    any dataset/adapter download or heavy import (unsloth/transformers) —
    the whole point is failing fast, before the expensive part of the task
    has done any work.

    Contract with the error-classification layer: the message below MUST
    contain the literal substring "out of memory" —
    `api/services/metrics_sources.py`'s `classify_error` buckets any
    `error_message` containing that substring (case-insensitively) into
    the `"oom"` error type for the ops dashboard, and
    `tests/unit/test_metrics_sources.py:534,581` assert exactly that
    substring match. This function raising outside of an actual CUDA OOM
    is a *preflight* rejection, not a real one — but it gets the same
    "oom" classification on purpose, since it's the same underlying
    condition (not enough VRAM) caught one step earlier.

    The comparison + raise are intentionally OUTSIDE of any try/except in
    this function: a preflight rejection must propagate to the caller's
    own `except BaseException` handler (which marks the job FAILED with
    this message) and must never be silently swallowed here.

    Args:
        job_kind: short label for the calling task (e.g. "training",
            "hpo", "export") — folded into the error message so a FAILED
            job's error text says what kind of job it was, without
            needing to cross-reference the Celery task name.
    """
    settings = get_settings()
    if not settings.gpu_preflight_enforce:
        return

    mem_info = _cuda_free_total_gb()
    if mem_info is None:
        # No CUDA visible to this process (torch missing, or no GPU) —
        # nothing to preflight. A CPU-only dev/test box must still be able
        # to run the task; the training code itself is what fails (or
        # doesn't, for a mocked trainer) further down.
        return

    free_gb, total_gb = mem_info
    threshold_gb = settings.gpu_preflight_min_free_gb
    if free_gb < threshold_gb:
        raise RuntimeError(
            f"GPU preflight check failed for {job_kind} job: out of memory "
            f"headroom — {free_gb:.2f} GiB free of {total_gb:.2f} GiB total "
            f"VRAM, below the required minimum of {threshold_gb:.2f} GiB. "
            "Wait for the currently running GPU job to finish, or lower "
            "per_device_train_batch_size / max_seq_length before retrying."
        )


def friendly_oom_message(exc: BaseException) -> str | None:
    """Return a user-facing message iff `exc` looks like a CUDA OOM,
    else `None`.

    Detection can't use `isinstance(exc, torch.cuda.OutOfMemoryError)` —
    this module must import cleanly without torch installed (see module
    docstring) — so it matches on shape instead: either the exception's
    *class name* is `OutOfMemoryError` (covers
    `torch.cuda.OutOfMemoryError` without importing torch to check), or
    its stringified text contains "out of memory" (covers CUDA's own
    driver-level RuntimeError, which torch doesn't wrap in a dedicated
    class on every version).

    Same substring contract as `preflight_gpu_vram` above: the returned
    message keeps the literal substring "out of memory" so
    `classify_error` still buckets it as `"oom"` once it's written to
    `error_message` / `export_error_message`.
    """
    exc_text = str(exc) or repr(exc)
    is_oom = (
        "OutOfMemoryError" in type(exc).__name__
        or "out of memory" in exc_text.lower()
    )
    if not is_oom:
        return None

    return (
        "GPU ran out of memory during this job. Wait for the currently "
        "running GPU job to finish, or lower per_device_train_batch_size / "
        f"max_seq_length before retrying. Original error: {exc_text[:1000]}"
    )


__all__ = ["preflight_gpu_vram", "friendly_oom_message"]
