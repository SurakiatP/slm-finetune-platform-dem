"""T7: unit tests for `ai_engine/hpo/optuna_objective.py::HPOObjective` —
proving `_release_gpu_memory()` runs after EVERY trial, success or failure.

`HPOObjective.__call__`'s `finally` block (around the `UnslothTrainer(...)` /
`.train()` call) drops the trainer reference and then calls the module-level
`_release_gpu_memory()` before the trial's result is extracted or reported.
Study-level GPU cleanup (`workers/tasks/hpo_training.py`) only runs once,
after the *whole* study finishes, so a single trial that OOMs or errors
otherwise would leak VRAM into every trial after it without this per-trial
release — see the docstring right above `_release_gpu_memory` in that module.

Real Optuna is used for `TrialPruned` (it's a base dependency per the module's
own docstring — "Optuna is in base deps"), but the `Trial` passed to
`HPOObjective.__call__` is a hand-rolled stub exposing only the three attrs
`sample_config`/`__call__` actually touch (`number`, `report`, `should_prune`,
plus `suggest_float` for the one search-space dimension used below) — a real
`optuna.Trial` needs a live `Study`, which is unnecessary machinery for what
this file tests.

`UnslothTrainer`, `mlflow_run_scope`, `log_params_flat`, and `log_metrics_dict`
are monkeypatched directly on the `optuna_objective` module (they're plain
module-level imports there, not lazy/deferred), mirroring the coarse
trainer-boundary mocking pattern in `tests/unit/test_worker_orphan_cleanup_training.py`
(`_FakeUnslothTrainer`).
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import ai_engine.hpo.optuna_objective as optuna_objective
from ai_engine.hpo.optuna_objective import HPOObjective
from ai_engine.training.unsloth_trainer import TrainingResult
from api.schemas.enums import TaskType
from api.schemas.training import HPOConfig, HPOFloatRange, HPOSearchSpace


@contextmanager
def _fake_mlflow_run_scope(**kwargs):
    yield SimpleNamespace(run_id="fake-run-id", experiment_id="fake-exp-id")


class _FakeTrial:
    """Stub Optuna trial: only the attrs `sample_config` and
    `HPOObjective.__call__` actually touch."""

    def __init__(self, number: int = 0, *, prune: bool = False) -> None:
        self.number = number
        self._prune = prune
        self.reported: list[tuple[float, int]] = []

    def suggest_float(self, name, low, high, log=False):  # noqa: ANN001
        return (low + high) / 2

    def report(self, value, step) -> None:  # noqa: ANN001
        self.reported.append((value, step))

    def should_prune(self) -> bool:
        return self._prune


class _SucceedingTrainer:
    def __init__(self, *, base_model, config, task_type, tool_definitions, output_dir):
        pass

    def train(self, rows, callbacks=None):
        return TrainingResult(
            adapter_dir="/tmp/does-not-matter",
            final_train_loss=0.5,
            final_eval_loss=0.4,
            train_runtime_seconds=1.0,
            train_samples_per_second=10.0,
            steps_completed=1,
            metrics={"eval_loss": 0.4},
        )


class _RaisingTrainer:
    def __init__(self, *, base_model, config, task_type, tool_definitions, output_dir):
        pass

    def train(self, rows, callbacks=None):
        raise RuntimeError("trainer blew up mid-trial")


def _make_objective(monkeypatch: pytest.MonkeyPatch, *, trainer_cls) -> HPOObjective:
    monkeypatch.setattr(optuna_objective, "UnslothTrainer", trainer_cls)
    monkeypatch.setattr(optuna_objective, "mlflow_run_scope", _fake_mlflow_run_scope)
    monkeypatch.setattr(optuna_objective, "log_params_flat", lambda *a, **k: None)
    monkeypatch.setattr(optuna_objective, "log_metrics_dict", lambda *a, **k: None)

    hpo_config = HPOConfig(
        search_space=HPOSearchSpace(
            learning_rate=HPOFloatRange(low=1e-5, high=5e-4, log=True)
        ),
    )
    return HPOObjective(
        base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
        task_type=TaskType.QA,
        rows=[{"question": "q1", "answer": "a1"}],
        hpo_config=hpo_config,
        workdir_root=tempfile.mkdtemp(prefix="hpo-objective-test-"),
    )


class TestReleaseCalledOnSuccessfulTrial:
    def test_release_called_once_after_successful_trial(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release_calls: list[None] = []
        monkeypatch.setattr(
            optuna_objective, "_release_gpu_memory", lambda: release_calls.append(None)
        )
        objective = _make_objective(monkeypatch, trainer_cls=_SucceedingTrainer)
        trial = _FakeTrial(number=0, prune=False)

        value = objective(trial)

        assert value == pytest.approx(0.4)
        assert len(release_calls) == 1, "GPU memory must be released exactly once per trial"


class TestReleaseCalledWhenTrainerRaises:
    def test_release_called_once_when_trainer_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release_calls: list[None] = []
        monkeypatch.setattr(
            optuna_objective, "_release_gpu_memory", lambda: release_calls.append(None)
        )
        objective = _make_objective(monkeypatch, trainer_cls=_RaisingTrainer)
        trial = _FakeTrial(number=0, prune=False)

        with pytest.raises(RuntimeError, match="trainer blew up mid-trial"):
            objective(trial)

        assert len(release_calls) == 1, (
            "GPU memory must be released even when the trainer raises (finally path)"
        )
