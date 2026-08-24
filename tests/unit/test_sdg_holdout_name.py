"""Unit tests for W1-T2: the SDG holdout Dataset's `name` honors the
request's optional `holdout_name` field, falling back to the existing
`f"{parent.name}-hold-out"` default when omitted.

Same `.apply()` + in-memory sqlite + `fake_minio` harness as
`tests/unit/test_worker_orphan_cleanup_sdg.py` — trimmed down to just the
success path with a holdout, since that's the only branch that constructs
the holdout `Dataset` row (see `workers/tasks/data_generation.py`, the
`if holdout_uuid is not None:` block).
"""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
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


def _build_payload(project_id, *, holdout_size=1, holdout_name=None) -> dict:
    request = SDGRequestDescriptionOnly(
        project_id=project_id,
        task_type=TaskType.QA,
        task_description="Answer questions about our 30-day return policy",
        num_samples=1,
        holdout_size=holdout_size,
        holdout_name=holdout_name,
    )
    return request.model_dump(mode="json")


def _fake_result(n=1):
    return SDGRunResult(
        valid_rows=[{"question": f"q{i}", "answer": f"a{i}"} for i in range(n)],
        rejected_count=0,
        duplicate_count=0,
        judge_rejected_count=0,
        judge_parse_failures=0,
        api_calls=1,
    )


def _holdout_dataset(sync_sessionmaker, parent_id):
    session = sync_sessionmaker()
    try:
        return session.execute(
            select(Dataset).where(Dataset.parent_dataset_id == parent_id)
        ).scalar_one()
    finally:
        session.close()


class TestHoldoutNameDefault:
    def test_holdout_name_omitted_falls_back_to_parent_name_suffix(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(4)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id, parent_name="sdg-ds"
        )

        result = dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": _build_payload(project_id, holdout_size=1),
                "dataset_id": str(dataset_id),
            }
        )
        assert result.successful(), f"task raised: {result.result!r}"

        holdout = _holdout_dataset(sync_sessionmaker, dataset_id)
        assert holdout.name == "sdg-ds-hold-out"


class TestHoldoutNameExplicit:
    def test_holdout_name_explicit_value_is_used_verbatim(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(4)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id, parent_name="sdg-ds"
        )

        result = dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": _build_payload(
                    project_id, holdout_size=1, holdout_name="return-policy-eval"
                ),
                "dataset_id": str(dataset_id),
            }
        )
        assert result.successful(), f"task raised: {result.result!r}"

        holdout = _holdout_dataset(sync_sessionmaker, dataset_id)
        assert holdout.name == "return-policy-eval"


class TestHoldoutNameEmptyStringFallsBackToDefault:
    def test_empty_string_holdout_name_is_falsy_and_falls_back(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        """`request.holdout_name or f"{parent.name}-hold-out"` treats an empty
        string the same as omitted (falsy), matching `dataset_name`'s
        existing `request.dataset_name or <default>` convention.
        """
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(4)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id, parent_name="sdg-ds"
        )

        result = dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": _build_payload(project_id, holdout_size=1, holdout_name=""),
                "dataset_id": str(dataset_id),
            }
        )
        assert result.successful(), f"task raised: {result.result!r}"

        holdout = _holdout_dataset(sync_sessionmaker, dataset_id)
        assert holdout.name == "sdg-ds-hold-out"
