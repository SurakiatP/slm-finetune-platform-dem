"""W2-T7 cross-cutting hardening: `holdout_name` propagation edges beyond
what `tests/unit/test_sdg_holdout_name.py` already covers.

That file exercises the default-fallback / explicit-value / empty-string
cases, but only ever through `SDGRequestDescriptionOnly`. Not covered
there, and covered here instead:

  1. `holdout_size=0` — `ai_engine/data_gen/holdout_split.split_rows`
     returns an empty `holdout_rows` list whenever `holdout_size <= 0`
     (see `holdout_split.py` line ~44: `if holdout_size <= 0 or not
     rows: return list(rows), []`), so `workers/tasks/data_generation.py`'s
     `if holdout_uuid is not None:` block (the ONLY place that reads
     `request.holdout_name`) never executes. A caller-supplied
     `holdout_name` is silently accepted and silently unused — no error,
     no warning, no child Dataset row at all. Documented here as current
     behavior.
  2. `holdout_name` propagates identically under `SDGRequestWithSeed` as
     it does under `SDGRequestDescriptionOnly` — the code that reads
     `request.holdout_name` (`workers/tasks/data_generation.py`, the
     `if holdout_uuid is not None:` block) sits after the mode-specific
     branch in `_load_seed_payload` and does not care which mode produced
     the rows. `_load_seed_payload` is monkeypatched directly (rather
     than seeding a real `source=SEED` Dataset + MinIO object) since the
     with_seed data-fetch path itself is not what's under test here —
     `test_snapshot_node_3b4.py` already covers that at the
     `SyntheticDataGenerator` layer.

Same `.apply()` + in-memory sqlite + `fake_minio` harness as
`test_sdg_holdout_name.py` / `test_worker_orphan_cleanup_sdg.py`.
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
from api.schemas.sdg import SDGRequestDescriptionOnly, SDGRequestWithSeed


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


def _fake_result(n=1):
    return SDGRunResult(
        valid_rows=[{"question": f"q{i}", "answer": f"a{i}"} for i in range(n)],
        rejected_count=0,
        duplicate_count=0,
        judge_rejected_count=0,
        judge_parse_failures=0,
        api_calls=1,
    )


def _holdout_datasets(sync_sessionmaker, parent_id):
    session = sync_sessionmaker()
    try:
        return (
            session.execute(select(Dataset).where(Dataset.parent_dataset_id == parent_id))
            .scalars()
            .all()
        )
    finally:
        session.close()


def _parent_dataset(sync_sessionmaker, dataset_id):
    session = sync_sessionmaker()
    try:
        return session.get(Dataset, dataset_id)
    finally:
        session.close()


class TestHoldoutSizeZeroLeavesHoldoutNameUnused:
    def test_holdout_size_zero_creates_no_child_row_even_with_holdout_name_set(
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

        request = SDGRequestDescriptionOnly(
            project_id=project_id,
            task_type=TaskType.QA,
            task_description="Answer questions about our 30-day return policy",
            num_samples=4,
            holdout_size=0,
            holdout_name="never-used-holdout-name",
        )
        result = dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": request.model_dump(mode="json"),
                "dataset_id": str(dataset_id),
            }
        )
        assert result.successful(), f"task raised: {result.result!r}"

        children = _holdout_datasets(sync_sessionmaker, dataset_id)
        assert children == [], (
            "holdout_size=0 must produce zero child Dataset rows regardless "
            "of holdout_name being set — split_rows() returns an empty "
            "holdout list, so the `if holdout_uuid is not None:` block that "
            "reads request.holdout_name never runs."
        )

        parent = _parent_dataset(sync_sessionmaker, dataset_id)
        assert parent is not None
        assert parent.num_samples == 4
        # No dangling reference to a holdout that was never created.
        assert (parent.generation_metadata or {}).get("holdout_dataset_id") is None


class TestHoldoutNamePropagationParityAcrossModes:
    def test_with_seed_mode_honors_explicit_holdout_name_same_as_description_only(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        """`_load_seed_payload` is patched directly to skip the with_seed
        MinIO/seed-dataset fetch machinery (out of scope here — see the
        module docstring) so this test isolates exactly the thing under
        test: does `request.holdout_name` reach the child Dataset row the
        same way it does for description_only."""
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(4)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)
        monkeypatch.setattr(dg_module, "_load_seed_payload", lambda request, settings: ([], None))

        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id, parent_name="sdg-ds"
        )

        request = SDGRequestWithSeed(
            project_id=project_id,
            task_type=TaskType.QA,
            task_description="Answer questions about our 30-day return policy",
            num_samples=1,
            holdout_size=1,
            holdout_name="with-seed-holdout-eval",
            seed_dataset_id=uuid4(),
        )
        result = dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": request.model_dump(mode="json"),
                "dataset_id": str(dataset_id),
            }
        )
        assert result.successful(), f"task raised: {result.result!r}"

        children = _holdout_datasets(sync_sessionmaker, dataset_id)
        assert len(children) == 1
        assert children[0].name == "with-seed-holdout-eval"

    def test_with_seed_mode_omitted_holdout_name_falls_back_same_as_description_only(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(4)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)
        monkeypatch.setattr(dg_module, "_load_seed_payload", lambda request, settings: ([], None))

        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(
            sync_sessionmaker, project_id=project_id, dataset_id=dataset_id, parent_name="with-seed-ds"
        )

        request = SDGRequestWithSeed(
            project_id=project_id,
            task_type=TaskType.QA,
            task_description="Answer questions about our 30-day return policy",
            num_samples=1,
            holdout_size=1,
            seed_dataset_id=uuid4(),
        )
        result = dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": request.model_dump(mode="json"),
                "dataset_id": str(dataset_id),
            }
        )
        assert result.successful(), f"task raised: {result.result!r}"

        children = _holdout_datasets(sync_sessionmaker, dataset_id)
        assert len(children) == 1
        assert children[0].name == "with-seed-ds-hold-out"
