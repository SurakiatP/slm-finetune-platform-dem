"""HuggingFace `TrainerCallback` factory that publishes per-step progress.

The callback emits two things on every `on_log`:
  1. A `TrainingProgress` `WSMessage` via the caller-supplied publish fn.
  2. Numeric metrics into the active MLflow run (best-effort; never raises).

`transformers`, `torch` are deferred imports so this module is importable
on hosts without the [training] extras.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import mlflow
from pydantic import BaseModel

from api.schemas.progress import TrainingProgress

if TYPE_CHECKING:  # pragma: no cover
    from transformers import TrainerCallback

log = logging.getLogger(__name__)

PublishFn = Callable[[BaseModel], None]


def _gpu_memory_mb() -> float | None:
    """Current allocated CUDA memory in MB, or None if no GPU / torch unavailable."""
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        return torch.cuda.memory_allocated() / (1024 * 1024)
    except Exception:  # noqa: BLE001 — telemetry is best-effort
        return None


def make_progress_callback(*, job_id: str, publish: PublishFn) -> "TrainerCallback":
    """Build a HuggingFace TrainerCallback that pushes progress + MLflow metrics.

    Args:
        job_id: Celery task id used as the WS channel suffix (`job:{job_id}`).
        publish: caller-supplied function that takes a `BaseModel` and publishes
            it to Redis (the worker provides this).
    """
    # Deferred import — `transformers` is only available in the worker container.
    from transformers import TrainerCallback

    class _ProgressCallback(TrainerCallback):  # type: ignore[misc]
        def on_log(  # type: ignore[override]
            self,
            args: Any,
            state: Any,
            control: Any,
            logs: dict[str, Any] | None = None,
            **_kwargs: Any,
        ) -> None:
            if not logs:
                return
            try:
                msg = TrainingProgress(
                    job_id=job_id,
                    epoch=float(state.epoch or 0.0),
                    epochs_total=int(args.num_train_epochs),
                    step=int(state.global_step),
                    steps_total=int(state.max_steps or state.global_step or 1),
                    train_loss=_as_float(logs.get("loss")),
                    eval_loss=_as_float(logs.get("eval_loss")),
                    learning_rate=_as_float(logs.get("learning_rate")),
                    samples_per_second=_as_float(logs.get("train_samples_per_second")),
                    gpu_memory_mb=_gpu_memory_mb(),
                )
                publish(msg)
            except Exception:  # noqa: BLE001 — telemetry must not break training
                log.exception("progress callback: failed to publish WS message")

            # Mirror numeric metrics into the active MLflow run, if any.
            for key, value in logs.items():
                if not isinstance(value, (int, float)):
                    continue
                try:
                    mlflow.log_metric(key, float(value), step=int(state.global_step))
                except Exception:  # noqa: BLE001
                    log.debug("mlflow log_metric '%s' failed (no active run?)", key, exc_info=True)

    return _ProgressCallback()


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["make_progress_callback", "PublishFn"]
