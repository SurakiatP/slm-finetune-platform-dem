"""Unit tests for the SDG end-game progress phases.

`workers/tasks/data_generation.py` now publishes three extra `SDGProgress`
frames after the generation loop finishes and around the deterministic
train/hold-out split, so the UI can say "splitting train/hold-out" and
"saving training data / saving hold-out data" instead of going silent
between the last `judging`/`dedup` frame and the terminal `JobCompleted`:

    ... (generation loop frames) ...
    -> "splitting_holdout"   (skipped when holdout_size == 0)
    -> "persisting_train"
    -> "persisting_holdout"  (skipped when there is no holdout)
    -> JobCompleted

Same `.apply()` + in-memory sqlite + `fake_minio` + `fake_redis_pubsub`
harness as `tests/unit/test_sdg_holdout_name.py`, extended to inspect the
frames actually published on the fake Redis channel.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from ai_engine.data_gen.generator import SDGRunResult
from ai_engine.data_gen.usage import STAGE_GENERATE
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.schemas.sdg import SDGRequestDescriptionOnly


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


@pytest.fixture
def sync_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


def _install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    import workers.tasks.data_generation as dg_module

    @contextmanager
    def _fake_session_scope():
        session = sync_sessionmaker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def _fake_redis_scope():
        yield fake_redis_pubsub.client

    monkeypatch.setattr(dg_module, "session_scope", _fake_session_scope)
    monkeypatch.setattr(dg_module, "sync_redis_scope", _fake_redis_scope)
    monkeypatch.setattr(dg_module, "get_minio_client", lambda: fake_minio)
    return dg_module


def _seed_project_and_dataset(sync_sessionmaker, *, project_id, dataset_id, parent_name="sdg-ds"):
    session = sync_sessionmaker()
    try:
        project = Project(id=project_id, name="proj", task_type=TaskType.QA)
        session.add(project)
        dataset = Dataset(
            id=dataset_id,
            project_id=project_id,
            name=parent_name,
            task_type=TaskType.QA,
            source=DatasetSource.SDG,
            status=JobStatus.PENDING,
            num_samples=0,
        )
        session.add(dataset)
        session.commit()
    finally:
        session.close()


def _build_payload(project_id, *, holdout_size) -> dict:
    request = SDGRequestDescriptionOnly(
        project_id=project_id,
        task_type=TaskType.QA,
        task_description="Answer questions about our 30-day return policy",
        num_samples=4,
        holdout_size=holdout_size,
    )
    return request.model_dump(mode="json")


def _fake_result(n=5):
    return SDGRunResult(
        valid_rows=[{"question": f"q{i}", "answer": f"a{i}"} for i in range(n)],
        rejected_count=0,
        duplicate_count=0,
        judge_rejected_count=0,
        judge_parse_failures=0,
        api_calls=1,
    )


def _sdg_progress_phases(fake_redis_pubsub) -> list[str]:
    """Ordered list of `phase` values from every published `sdg_progress` frame."""
    phases = []
    for _channel, raw in fake_redis_pubsub.published:
        payload = json.loads(raw)
        if payload.get("type") == "sdg_progress":
            phases.append(payload["phase"])
    return phases


def _run_task(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, *, holdout_size):
    dg_module = _install_worker_patches(
        monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    )

    async def _fake_run_generator(**kwargs):
        kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
        return _fake_result(5)

    monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

    project_id, dataset_id = uuid4(), uuid4()
    _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

    result = dg_module.generate_synthetic_data.apply(
        kwargs={
            "request_payload": _build_payload(project_id, holdout_size=holdout_size),
            "dataset_id": str(dataset_id),
        }
    )
    assert result.successful(), f"task raised: {result.result!r}"
    return result


class TestEndgamePhasesWithHoldout:
    def test_new_phases_are_emitted_in_order(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        _run_task(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, holdout_size=1
        )

        phases = _sdg_progress_phases(fake_redis_pubsub)

        # All three new phases fired, and in the required relative order.
        assert "splitting_holdout" in phases
        assert "persisting_train" in phases
        assert "persisting_holdout" in phases

        split_idx = phases.index("splitting_holdout")
        train_idx = phases.index("persisting_train")
        holdout_idx = phases.index("persisting_holdout")
        assert split_idx < train_idx < holdout_idx

    def test_endgame_frames_report_final_generation_counts(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        """samples_generated/samples_target must reflect the numbers the
        generation loop actually finished with (5 valid rows here, target
        computed as num_samples + holdout_size = 4 + 1 = 5), consistently
        across all three new frames."""
        _run_task(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, holdout_size=1
        )

        by_phase = {}
        for _channel, raw in fake_redis_pubsub.published:
            payload = json.loads(raw)
            if payload.get("type") == "sdg_progress" and payload["phase"] in (
                "splitting_holdout",
                "persisting_train",
                "persisting_holdout",
            ):
                by_phase[payload["phase"]] = payload

        assert set(by_phase) == {"splitting_holdout", "persisting_train", "persisting_holdout"}
        for phase, payload in by_phase.items():
            assert payload["samples_generated"] == 5, phase
            assert payload["samples_target"] == 5, phase
            assert payload["samples_valid"] == 5, phase


class TestEndgamePhasesWithoutHoldout:
    def test_holdout_phases_are_absent_when_holdout_size_is_zero(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        _run_task(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, holdout_size=0
        )

        phases = _sdg_progress_phases(fake_redis_pubsub)

        assert "splitting_holdout" not in phases
        assert "persisting_holdout" not in phases
        # persisting_train still fires — there is always a train file to save.
        assert "persisting_train" in phases


class TestEndgamePublishFailuresAreNonFatal:
    """A Redis hiccup while *announcing* an end-game phase must never fail a
    run whose generation + persistence work already succeeded — the frames
    are cosmetic, the dataset is not."""

    def test_run_still_succeeds_and_completes_when_endgame_publish_raises(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        real_publish = dg_module.publish_ws_message
        endgame = {"splitting_holdout", "persisting_train", "persisting_holdout"}

        def _flaky_publish(redis, job_id, message):  # noqa: ANN001, ANN202
            if getattr(message, "phase", None) in endgame:
                raise RuntimeError("redis is down")
            return real_publish(redis, job_id, message)

        monkeypatch.setattr(dg_module, "publish_ws_message", _flaky_publish)

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(5)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id
        )

        result = dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": _build_payload(project_id, holdout_size=1),
                "dataset_id": str(dataset_id),
            }
        )

        assert result.successful(), f"task raised: {result.result!r}"

        session = sync_sessionmaker()
        try:
            parent = session.get(Dataset, dataset_id)
            assert parent is not None
            assert parent.status is JobStatus.COMPLETED
            # The training rows were still persisted despite the failed frames.
            assert parent.num_samples == 4
        finally:
            session.close()
