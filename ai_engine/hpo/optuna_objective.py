"""Optuna objective: one trial = one nested MLflow run = one fine-tune.

Pure domain code — Optuna and MLflow are deferred imports inside the callable
so this module is importable on hosts without `[training]` extras.

The objective wraps `UnslothTrainer` from Phase 5 verbatim; HPO is just an
outer loop over `ManualTrainingConfig` variations.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ai_engine.hpo.search_spaces import sample_config
from ai_engine.training.callbacks import make_progress_callback
from ai_engine.training.mlflow_logger import (
    log_metrics_dict,
    log_params_flat,
    mlflow_run_scope,
)
from ai_engine.training.unsloth_trainer import TrainingResult, UnslothTrainer
from api.schemas.data_formats import ToolDefinition
from api.schemas.enums import TaskType
from api.schemas.training import HPOConfig, ManualTrainingConfig

if TYPE_CHECKING:  # pragma: no cover
    from optuna import Trial

log = logging.getLogger(__name__)


# ---- Trial-progress event --------------------------------------------------


@dataclass(frozen=True)
class TrialOutcome:
    """Per-trial summary handed to the worker via `on_trial_done`.

    Translated by the Celery task into `api.schemas.progress.HPOProgress` with
    `job_id` + `timestamp` attached, then published to Redis.
    """

    trial_number: int
    trials_total: int
    params: dict[str, Any]
    value: float | None
    pruned: bool
    failed: bool = False
    best_value: float | None = None
    best_params: dict[str, Any] | None = None


TrialCallback = Callable[[TrialOutcome], None]


# ---- Objective -------------------------------------------------------------


@dataclass
class HPOObjective:
    """Callable passed to `optuna.Study.optimize`.

    Each `__call__(trial)` runs one full UnslothTrainer.train() and returns
    the configured objective metric. A nested MLflow run is opened for every
    trial; the parent run is opened by the caller.
    """

    base_model: str
    task_type: TaskType
    rows: list[dict[str, Any]]
    hpo_config: HPOConfig
    workdir_root: str
    tool_definitions: list[ToolDefinition] | None = None
    on_trial_done: TrialCallback | None = None
    inner_progress_publish: Any = None  # Optional[PublishFn] from callbacks.py
    job_id: str = ""

    # Internal state — updated in __call__.
    _best_value: float | None = field(default=None, init=False)
    _best_params: dict[str, Any] | None = field(default=None, init=False)
    _direction: str = field(default="minimize", init=False)

    def __post_init__(self) -> None:
        self._direction = self.hpo_config.direction
        os.makedirs(self.workdir_root, exist_ok=True)

    # -- main entrypoint -------------------------------------------------------

    def __call__(self, trial: Trial) -> float:
        from optuna.exceptions import TrialPruned

        sampled = sample_config(trial, self.hpo_config.search_space, self.hpo_config.fixed_config)
        run_name = f"trial-{trial.number:03d}"

        result: TrainingResult | None = None
        value: float | None = None
        pruned = False
        failed = False

        try:
            with mlflow_run_scope(
                experiment_name=f"slm/{self.task_type.value}",
                run_name=run_name,
                tags={
                    "trial_number": str(trial.number),
                    "celery_job_id": self.job_id,
                    "task_type": self.task_type.value,
                    "base_model": self.base_model,
                    "mode": "hpo-trial",
                },
                nested=True,
            ):
                log_params_flat(sampled.params)
                trial_workdir = os.path.join(self.workdir_root, f"trial-{trial.number}")
                os.makedirs(trial_workdir, exist_ok=True)

                try:
                    trainer = UnslothTrainer(
                        base_model=self.base_model,
                        config=sampled.config,
                        task_type=self.task_type,
                        tool_definitions=self.tool_definitions,
                        output_dir=trial_workdir,
                    )
                    callbacks = []
                    if self.inner_progress_publish is not None and self.job_id:
                        callbacks.append(
                            make_progress_callback(
                                job_id=self.job_id,
                                publish=self.inner_progress_publish,
                            )
                        )
                    result = trainer.train(self.rows, callbacks=callbacks)
                    log_metrics_dict({k: v for k, v in result.metrics.items()})
                finally:
                    # Trial adapters are throwaway — only the final-best run keeps its weights.
                    shutil.rmtree(trial_workdir, ignore_errors=True)

                value = _extract_metric(result.metrics, self.hpo_config.objective_metric)
                if value is None:
                    raise RuntimeError(
                        f"trial {trial.number}: objective metric "
                        f"'{self.hpo_config.objective_metric}' not found in trainer metrics"
                    )

                # Optuna pruning hook: report once at the end (the trainer doesn't
                # surface mid-training metrics here for the median pruner to use,
                # so this acts mostly as a stop-now signal between trials).
                trial.report(value, step=result.steps_completed or 1)
                if trial.should_prune():
                    raise TrialPruned()

            # Track best value for outcome callbacks (Optuna also stores this in
            # study.best_value, but we want it on every progress event).
            self._update_best(value, sampled.params)

        except TrialPruned:
            pruned = True
            log.info("trial %d pruned at value=%s", trial.number, value)
            self._emit_done(trial.number, sampled.params, value, pruned=True)
            raise
        except Exception as exc:
            failed = True
            log.exception("trial %d failed: %s", trial.number, exc)
            self._emit_done(trial.number, sampled.params, None, failed=True)
            raise
        else:
            self._emit_done(trial.number, sampled.params, value, pruned=False)
            return float(value)

    # -- helpers ---------------------------------------------------------------

    def _update_best(self, value: float, params: dict[str, Any]) -> None:
        if self._best_value is None:
            self._best_value = value
            self._best_params = dict(params)
            return
        is_better = (
            value < self._best_value if self._direction == "minimize" else value > self._best_value
        )
        if is_better:
            self._best_value = value
            self._best_params = dict(params)

    def _emit_done(
        self,
        trial_number: int,
        params: dict[str, Any],
        value: float | None,
        *,
        pruned: bool = False,
        failed: bool = False,
    ) -> None:
        if self.on_trial_done is None:
            return
        try:
            self.on_trial_done(
                TrialOutcome(
                    trial_number=trial_number,
                    trials_total=self.hpo_config.n_trials,
                    params=params,
                    value=value,
                    pruned=pruned,
                    failed=failed,
                    best_value=self._best_value,
                    best_params=dict(self._best_params) if self._best_params else None,
                )
            )
        except Exception:  # noqa: BLE001 — telemetry must not break optimization
            log.warning("on_trial_done callback raised", exc_info=True)


# ---- helpers ---------------------------------------------------------------


def _extract_metric(metrics: dict[str, float], wanted: str) -> float | None:
    """Pick `wanted` from metrics, falling back to common HF Trainer aliases.

    HF reports `eval_loss` after `evaluate()` and `train_loss` after `.train()`.
    """
    for key in (wanted, f"eval_{wanted}", f"train_{wanted}"):
        if key in metrics and isinstance(metrics[key], (int, float)):
            return float(metrics[key])
    return None


def make_optuna_workdir(prefix: str) -> str:
    """Create the per-job tempdir that holds trial sub-dirs."""
    return tempfile.mkdtemp(prefix=prefix)


__all__ = [
    "HPOObjective",
    "TrialOutcome",
    "TrialCallback",
    "make_optuna_workdir",
]
