"""MLflow lifecycle helpers.

Workers wrap their training runs in `mlflow_run_scope(...)` and log params
/ metrics / artifacts through the returned context. The context exposes
the run id and a deep-link URL we surface back via the API.

This module imports `mlflow` only — no torch/transformers — so it's safe
to import in any process.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import mlflow

from api.core.config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MlflowRunHandle:
    run_id: str
    run_name: str
    experiment_id: str
    tracking_uri: str

    @property
    def run_url(self) -> str:
        """Web URL pointing at the run in the MLflow UI."""
        base = self.tracking_uri.rstrip("/")
        return f"{base}/#/experiments/{self.experiment_id}/runs/{self.run_id}"


@contextmanager
def mlflow_run_scope(
    *,
    experiment_name: str,
    run_name: str,
    tags: Mapping[str, str] | None = None,
    nested: bool = False,
) -> Iterator[MlflowRunHandle]:
    """Open one MLflow run; ends cleanly (FAILED on exception, FINISHED otherwise)."""
    settings = get_settings()
    mlflow.set_tracking_uri(str(settings.mlflow_tracking_uri))
    mlflow.set_experiment(experiment_name)
    with mlflow.start_run(run_name=run_name, tags=dict(tags or {}), nested=nested) as run:
        info = run.info
        handle = MlflowRunHandle(
            run_id=info.run_id,
            run_name=info.run_name or run_name,
            experiment_id=info.experiment_id,
            tracking_uri=str(settings.mlflow_tracking_uri),
        )
        log.info(
            "mlflow run started: experiment=%s run=%s id=%s",
            experiment_name,
            handle.run_name,
            handle.run_id,
        )
        try:
            yield handle
        except Exception:
            mlflow.set_tag("status", "failed")
            raise


def log_params_flat(params: Mapping[str, Any], *, prefix: str = "") -> None:
    """Flatten nested dicts to dotted-key MLflow params (params can't be nested)."""
    for key, value in params.items():
        full_key = f"{prefix}{key}"
        if isinstance(value, Mapping):
            log_params_flat(value, prefix=f"{full_key}.")
        elif isinstance(value, (list, tuple)):
            # MLflow doesn't accept lists as params; serialize compactly.
            mlflow.log_param(full_key, ",".join(str(v) for v in value)[:500])
        elif value is None:
            continue
        else:
            mlflow.log_param(full_key, str(value)[:500])


def log_metrics_dict(metrics: Mapping[str, float], *, step: int | None = None) -> None:
    for key, value in metrics.items():
        if isinstance(value, (int, float)):
            mlflow.log_metric(key, float(value), step=step)


__all__ = [
    "MlflowRunHandle",
    "mlflow_run_scope",
    "log_params_flat",
    "log_metrics_dict",
]
