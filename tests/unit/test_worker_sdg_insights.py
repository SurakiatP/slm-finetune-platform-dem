"""Unit tests proving `generate_synthetic_data` persists an `insights`
aggregate on `Dataset.generation_metadata` at SDG completion (T9).

Runs the Celery task synchronously via `.apply(...)` against an in-memory
sync sqlite engine, mirroring the established pattern in
`tests/unit/test_worker_usage_events.py` (same fixtures, same
`_install_worker_patches`-style monkeypatching of `session_scope` /
`sync_redis_scope` / `get_minio_client` at the worker module's own
use-sites, same JSONB->JSON sqlite compile shim).

`_run_generator` is monkeypatched per test to control exactly what
`SDGRunResult` the worker sees — the real async generator loop is covered
elsewhere; this file is about what the worker does with the result once
`_run_generator` hands it back.
"""

from __future__ import annotations

import json
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

# ---- KNOWN GOTCHA shim: JSONB is Postgres-only, sqlite needs a stand-in ----
# (same shim as tests/unit/test_dataset_status.py / test_worker_usage_events.py
# — Dataset/Project use sqlalchemy.dialects.postgresql.JSONB, which sqlite's
# DDL compiler doesn't understand without this.)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


# =============================================================================
# Fixtures / shared helpers (mirrors test_worker_usage_events.py)
# =============================================================================


@pytest.fixture
def sync_sessionmaker():
    """In-memory sync sqlite engine + sessionmaker with the full ORM schema."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


def _install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    """Same use-site patching as test_worker_usage_events.py's
    `_install_worker_patches`: patch `session_scope` / `sync_redis_scope` /
    `get_minio_client` on `workers.tasks.data_generation` itself, not on the
    modules they were imported from — the module did `from X import Y`,
    which binds a local alias that patching `X.Y` afterwards doesn't reach.
    """
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


def _seed_project_and_dataset(sync_sessionmaker, *, project_id, dataset_id, status=JobStatus.PENDING):
    session = sync_sessionmaker()
    try:
        project = Project(id=project_id, name="proj", task_type=TaskType.QA)
        session.add(project)
        dataset = Dataset(
            id=dataset_id,
            project_id=project_id,
            name="sdg-ds",
            task_type=TaskType.QA,
            source=DatasetSource.SDG,
            status=status,
            num_samples=0,
        )
        session.add(dataset)
        session.commit()
    finally:
        session.close()


def _build_description_only_payload(project_id, *, num_samples=1, holdout_size=0) -> dict:
    request = SDGRequestDescriptionOnly(
        project_id=project_id,
        task_type=TaskType.QA,
        task_description="Answer questions about our 30-day return policy",
        num_samples=num_samples,
        holdout_size=holdout_size,
    )
    return request.model_dump(mode="json")


def _get_dataset(sync_sessionmaker, dataset_id) -> Dataset:
    session = sync_sessionmaker()
    try:
        ds = session.get(Dataset, dataset_id)
        assert ds is not None
        return ds
    finally:
        session.close()


def _all_datasets(sync_sessionmaker) -> list[Dataset]:
    session = sync_sessionmaker()
    try:
        return list(session.execute(select(Dataset)).scalars().all())
    finally:
        session.close()


_JUDGE_SUMMARY = {
    "mean_score": 0.87,
    "count_scored": 3,
    "distribution": {"1": 0, "2": 0, "3": 1, "4": 1, "5": 1},
}


def _make_run_generator(*, judge_scores_summary, valid_rows, rejected_count=0,
                         duplicate_count=0, semantic_duplicate_count=0,
                         judge_rejected_count=0,
                         judge_parse_failures=0, api_calls=1):
    async def _fake_run_generator(**kwargs):
        usage = kwargs["usage"]
        import workers.tasks.data_generation as dg_module

        usage.add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
        return SDGRunResult(
            valid_rows=valid_rows,
            rejected_count=rejected_count,
            duplicate_count=duplicate_count,
            semantic_duplicate_count=semantic_duplicate_count,
            judge_rejected_count=judge_rejected_count,
            judge_parse_failures=judge_parse_failures,
            api_calls=api_calls,
            judge_scores_summary=judge_scores_summary,
        )

    return _fake_run_generator


# =============================================================================
# 1. Pinned shape — judge summary present
# =============================================================================


class TestInsightsPinnedShape:
    def test_insights_persisted_with_pinned_shape(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        valid_rows = [{"question": "q1", "answer": "a1"}, {"question": "q2", "answer": "a2"}]
        monkeypatch.setattr(
            dg_module,
            "_run_generator",
            _make_run_generator(
                judge_scores_summary=_JUDGE_SUMMARY,
                valid_rows=valid_rows,
                rejected_count=2,
                duplicate_count=1,
                semantic_duplicate_count=3,
                judge_rejected_count=1,
                judge_parse_failures=0,
                api_calls=5,
            ),
        )

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)
        payload = _build_description_only_payload(project_id, num_samples=2, holdout_size=0)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": payload, "dataset_id": str(dataset_id)}
        )
        assert result.successful(), f"task raised: {result.result!r}"

        ds = _get_dataset(sync_sessionmaker, dataset_id)
        meta = ds.generation_metadata
        assert meta is not None
        assert "insights" in meta
        insights = meta["insights"]

        assert insights["schema_version"] == 1
        assert insights["judge"] == _JUDGE_SUMMARY
        assert insights["counts"] == {
            "generated": len(valid_rows),
            "target": 2,
            "train_rows": len(valid_rows),
            "holdout_rows": 0,
            "schema_rejected": 2,
            "duplicates_removed": 1,
            "semantic_duplicates_removed": 3,
            "judge_rejected": 1,
            "judge_parse_failures": 0,
        }
        assert isinstance(insights["computed_at"], str) and insights["computed_at"]

        # Blob must survive a plain json.dumps — the JSONB column has no
        # bespoke encoder, so anything non-JSON-native here would blow up
        # for real at commit time in Postgres.
        json.dumps(meta)

        # Pre-existing keys in generation_metadata must be untouched.
        for key in (
            "completed_at",
            "rejected_count",
            "duplicate_count",
            "judge_rejected_count",
            "judge_parse_failures",
            "api_calls",
            "holdout_size_requested",
            "holdout_size_actual",
            "role",
            "holdout_dataset_id",
        ):
            assert key in meta, f"pre-existing key {key!r} missing from generation_metadata"
        assert meta["rejected_count"] == 2
        assert meta["duplicate_count"] == 1
        assert meta["judge_rejected_count"] == 1
        assert meta["judge_parse_failures"] == 0
        assert meta["api_calls"] == 5
        assert meta["role"] == "train"


# =============================================================================
# 2. judge is None when SDGRunResult.judge_scores_summary is None
# =============================================================================


class TestInsightsJudgeNone:
    def test_judge_is_none_when_no_scores_parsed(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        valid_rows = [{"question": "q", "answer": "a"}]
        monkeypatch.setattr(
            dg_module,
            "_run_generator",
            _make_run_generator(judge_scores_summary=None, valid_rows=valid_rows),
        )

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)
        payload = _build_description_only_payload(project_id, num_samples=1, holdout_size=0)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": payload, "dataset_id": str(dataset_id)}
        )
        assert result.successful(), f"task raised: {result.result!r}"

        ds = _get_dataset(sync_sessionmaker, dataset_id)
        insights = ds.generation_metadata["insights"]
        assert insights["judge"] is None
        assert insights["schema_version"] == 1
        json.dumps(ds.generation_metadata)


# =============================================================================
# 3. Holdout child dataset never gets an "insights" key
# =============================================================================


class TestInsightsHoldoutChildUntouched:
    def test_holdout_child_has_no_insights_key(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        # 10 valid rows + holdout_size=3 so split_rows has enough to work with.
        valid_rows = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(10)]
        monkeypatch.setattr(
            dg_module,
            "_run_generator",
            _make_run_generator(judge_scores_summary=_JUDGE_SUMMARY, valid_rows=valid_rows),
        )

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)
        payload = _build_description_only_payload(project_id, num_samples=7, holdout_size=3)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": payload, "dataset_id": str(dataset_id)}
        )
        assert result.successful(), f"task raised: {result.result!r}"

        parent = _get_dataset(sync_sessionmaker, dataset_id)
        assert "insights" in parent.generation_metadata

        all_ds = _all_datasets(sync_sessionmaker)
        children = [d for d in all_ds if d.id != dataset_id]
        assert len(children) == 1, "expected exactly one holdout child dataset"
        child = children[0]
        assert child.generation_metadata is not None
        assert child.generation_metadata.get("role") == "holdout"
        assert "insights" not in child.generation_metadata


# =============================================================================
# 4. A malformed aggregate must never fail an otherwise-completed run
# =============================================================================


class TestInsightsDefensiveFallback:
    def test_exploding_aggregate_falls_back_to_none_without_failing_run(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        """Force the insights-dict assembly itself to raise (by making the
        module's `datetime.now(...)` blow up on its first call — the first
        thing the try block does when building `computed_at`) and assert
        the run still completes normally with `"insights": None` rather
        than the whole SDG run failing. The second `datetime.now(...)` call
        (for the pre-existing `completed_at` key, made *after* the
        try/except) must still succeed normally.
        """
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        import datetime as _datetime_module

        class _FakeDatetime:
            _call_count = 0

            @classmethod
            def now(cls, tz=None):  # noqa: ANN001
                cls._call_count += 1
                if cls._call_count == 1:
                    raise RuntimeError("boom while computing insights.computed_at")
                return _datetime_module.datetime.now(tz)

        monkeypatch.setattr(dg_module, "datetime", _FakeDatetime)

        valid_rows = [{"question": "q", "answer": "a"}]
        monkeypatch.setattr(
            dg_module,
            "_run_generator",
            _make_run_generator(judge_scores_summary=_JUDGE_SUMMARY, valid_rows=valid_rows),
        )

        project_id = uuid4()
        dataset_id = uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)
        payload = _build_description_only_payload(project_id, num_samples=1, holdout_size=0)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": payload, "dataset_id": str(dataset_id)}
        )
        assert result.successful(), f"task raised: {result.result!r}"

        ds = _get_dataset(sync_sessionmaker, dataset_id)
        assert ds.status == JobStatus.COMPLETED
        assert ds.generation_metadata is not None
        assert ds.generation_metadata["insights"] is None
        # Pre-existing `completed_at` (computed after the try/except, on the
        # second `now()` call) must still be present and unaffected.
        assert ds.generation_metadata.get("completed_at")
        json.dumps(ds.generation_metadata)
