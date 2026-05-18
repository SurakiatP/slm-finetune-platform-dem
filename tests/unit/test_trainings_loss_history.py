"""Unit tests for `get_training_loss_history` — covers MLflow metric-key mapping.

Regression coverage for the Session 26 bug: endpoint was querying MLflow for
metric key ``train_loss`` (which only exists as a single epoch-aggregate point
logged by ``log_metrics_dict`` at default step=0), causing the frontend loss
curve to render flat. The HuggingFace Trainer emits per-step training loss
under the key ``loss`` (see ``ai_engine/training/callbacks.py`` line 82-86),
which is the data the chart actually needs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from api.services import trainings_service
from api.services.mlflow_metrics import MetricPointDC


@pytest.mark.asyncio
async def test_loss_history_queries_loss_not_train_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    """Endpoint must query MLflow for 'loss' (per-step), not 'train_loss' (epoch-aggregate)."""
    training_id = uuid4()
    fake_job = MagicMock()
    fake_job.id = training_id
    fake_job.mlflow_run_id = "mlflow-run-abc"

    fake_db = MagicMock()
    fake_db.get = AsyncMock(return_value=fake_job)

    captured_keys: list[str] = []

    async def fake_get_metric_history(
        run_id: str, metric_key: str, *, client: object | None = None
    ) -> list[MetricPointDC]:
        captured_keys.append(metric_key)
        return [
            MetricPointDC(step=1, value=4.5, timestamp_ms=1000),
            MetricPointDC(step=2, value=3.0, timestamp_ms=2000),
        ]

    monkeypatch.setattr(
        trainings_service.mlflow_metrics, "get_metric_history", fake_get_metric_history
    )

    resp = await trainings_service.get_training_loss_history(fake_db, training_id)

    assert "loss" in captured_keys, (
        f"endpoint must query MLflow metric 'loss' (HF Trainer's per-step key), "
        f"got queries: {captured_keys}"
    )
    assert "train_loss" not in captured_keys, (
        f"endpoint must NOT query 'train_loss' (it only contains a single epoch-aggregate "
        f"point logged at default step=0 by workers/tasks/training.py:163), "
        f"got queries: {captured_keys}"
    )
    assert "eval_loss" in captured_keys

    assert len(resp.train_loss) == 2
    assert resp.train_loss[0].step == 1
    assert resp.train_loss[0].value == 4.5
    assert len(resp.eval_loss) == 2


@pytest.mark.asyncio
async def test_loss_history_empty_when_no_mlflow_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If TrainingJob has no mlflow_run_id, skip MLflow calls and return empty series."""
    training_id = uuid4()
    fake_job = MagicMock()
    fake_job.id = training_id
    fake_job.mlflow_run_id = None

    fake_db = MagicMock()
    fake_db.get = AsyncMock(return_value=fake_job)

    called = False

    async def fake_get_metric_history(*_args: object, **_kwargs: object) -> list[MetricPointDC]:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(
        trainings_service.mlflow_metrics, "get_metric_history", fake_get_metric_history
    )

    resp = await trainings_service.get_training_loss_history(fake_db, training_id)

    assert called is False, "should not call MLflow when mlflow_run_id is None"
    assert resp.train_loss == []
    assert resp.eval_loss == []
    assert resp.mlflow_run_id is None


@pytest.mark.asyncio
async def test_loss_history_sorts_points_by_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """MLflow may return points out-of-order; endpoint must sort by step ascending."""
    training_id = uuid4()
    fake_job = MagicMock()
    fake_job.id = training_id
    fake_job.mlflow_run_id = "mlflow-run-xyz"

    fake_db = MagicMock()
    fake_db.get = AsyncMock(return_value=fake_job)

    async def fake_get_metric_history(
        run_id: str, metric_key: str, *, client: object | None = None
    ) -> list[MetricPointDC]:
        return [
            MetricPointDC(step=3, value=2.5, timestamp_ms=3000),
            MetricPointDC(step=1, value=4.5, timestamp_ms=1000),
            MetricPointDC(step=2, value=3.5, timestamp_ms=2000),
        ]

    monkeypatch.setattr(
        trainings_service.mlflow_metrics, "get_metric_history", fake_get_metric_history
    )

    resp = await trainings_service.get_training_loss_history(fake_db, training_id)

    assert [p.step for p in resp.train_loss] == [1, 2, 3]
    assert [p.value for p in resp.train_loss] == [4.5, 3.5, 2.5]
