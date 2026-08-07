"""Unit tests for gap-analysis item 13 ("clean up orphaned artifacts when a
job is cancelled or fails") as it applies to `workers/tasks/data_generation.py`.

`generate_synthetic_data` uploads the train (and, if requested, holdout)
JSONL to MinIO (`put_jsonl`, under `sdg/{id}.jsonl`) BEFORE the `Dataset`
row(s) that reference them are committed. A cancel (SIGTERM -> `SystemExit`,
caught by the existing `except BaseException`) or an ordinary failure
landing in that window used to leave those JSONL(s) orphaned in the
`datasets` bucket forever.

The fix tracks every uploaded key in `uploaded_keys` (appended the instant
each `put_jsonl` returns) and a `committed` flag (set the instant the
Dataset-persisting `session_scope()` block commits). The
`except BaseException` handler deletes `uploaded_keys` via `remove_object`
only when `committed` is still False.

Same `.apply()` + in-memory sqlite + `fake_minio` harness as
`tests/unit/test_worker_usage_events.py` (which already covers the
DB-status and usage-billing side of these same terminal paths); this file
adds the storage-cleanup assertions that file doesn't make.
"""

from __future__ import annotations

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


# =============================================================================
# Fixtures / shared helpers (mirrors test_worker_usage_events.py)
# =============================================================================


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


def _build_payload(project_id, *, holdout_size=0) -> dict:
    request = SDGRequestDescriptionOnly(
        project_id=project_id,
        task_type=TaskType.QA,
        task_description="Answer questions about our 30-day return policy",
        num_samples=1,
        holdout_size=holdout_size,
    )
    return request.model_dump(mode="json")


def _dataset_bucket_objects(fake_minio) -> list[str]:
    return [obj.object_name for obj in fake_minio.list_objects("datasets", recursive=True)]


def _fake_result(n=1):
    return SDGRunResult(
        valid_rows=[{"question": f"q{i}", "answer": f"a{i}"} for i in range(n)],
        rejected_count=0,
        duplicate_count=0,
        judge_rejected_count=0,
        judge_parse_failures=0,
        api_calls=1,
    )


# =============================================================================
# 1. Success path — the JSONL(s) must survive
# =============================================================================


class TestSuccessPathKeepsUpload:
    def test_success_leaves_train_object_intact(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(1)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": _build_payload(project_id), "dataset_id": str(dataset_id)}
        )
        assert result.successful(), f"task raised: {result.result!r}"

        objects = _dataset_bucket_objects(fake_minio)
        assert objects == [f"sdg/{dataset_id}.jsonl"], (
            f"the uploaded JSONL must still be present in MinIO after a successful run: {objects}"
        )

        session = sync_sessionmaker()
        try:
            ds = session.get(Dataset, dataset_id)
            assert ds.status == JobStatus.COMPLETED
            assert ds.storage_uri == f"s3://datasets/sdg/{dataset_id}.jsonl"
        finally:
            session.close()

    def test_success_with_holdout_leaves_both_objects_intact(
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
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={
                "request_payload": _build_payload(project_id, holdout_size=1),
                "dataset_id": str(dataset_id),
            }
        )
        assert result.successful(), f"task raised: {result.result!r}"

        objects = _dataset_bucket_objects(fake_minio)
        assert len(objects) == 2, f"expected train + holdout JSONL to both survive: {objects}"
        assert f"sdg/{dataset_id}.jsonl" in objects


# =============================================================================
# 2. Cancel path — the JSONL(s) must be deleted
# =============================================================================


class TestCancelPathDeletesUpload:
    def test_cancel_after_upload_deletes_orphaned_object(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(1)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        # `s3_uri` is called exactly once right after each `put_jsonl` (once
        # for train, again for holdout if requested) — a seam that lands
        # right after the upload but strictly before the DB-persisting
        # `session_scope()` block that would set `committed = True`.
        # Exploding on the first call is the SDG equivalent of
        # `training.py`'s "_persist_artifact raises" and
        # `model_export.py`'s "_persist_export_uris raises" injection
        # points. Mirrors the cancel endpoint: flips CANCELLED first, in
        # its own transaction, then raises the SIGTERM-turned-SystemExit.
        real_s3_uri = dg_module.s3_uri
        calls = {"n": 0}

        def _s3_uri_then_cancel(bucket, key):
            calls["n"] += 1
            if calls["n"] == 1:
                session = sync_sessionmaker()
                try:
                    ds = session.get(Dataset, dataset_id)
                    ds.status = JobStatus.CANCELLED
                    session.commit()
                finally:
                    session.close()
                raise SystemExit(-241)
            return real_s3_uri(bucket, key)

        monkeypatch.setattr(dg_module, "s3_uri", _s3_uri_then_cancel)

        try:
            dg_module.generate_synthetic_data.apply(
                kwargs={"request_payload": _build_payload(project_id), "dataset_id": str(dataset_id)}
            )
        except SystemExit:
            pass  # `.apply()` lets BaseException through; expected.

        objects = _dataset_bucket_objects(fake_minio)
        assert objects == [], f"a cancelled run must not leave an orphaned JSONL: {objects}"

        session = sync_sessionmaker()
        try:
            ds = session.get(Dataset, dataset_id)
            assert ds.status == JobStatus.CANCELLED
        finally:
            session.close()


# =============================================================================
# 3. Failure path — the JSONL(s) must be deleted
# =============================================================================


class TestFailurePathDeletesUpload:
    def test_failure_after_upload_deletes_orphaned_object(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(1)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        real_s3_uri = dg_module.s3_uri
        calls = {"n": 0}

        def _s3_uri_then_fail(bucket, key):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected SDG failure after upload for test")
            return real_s3_uri(bucket, key)

        monkeypatch.setattr(dg_module, "s3_uri", _s3_uri_then_fail)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": _build_payload(project_id), "dataset_id": str(dataset_id)}
        )
        assert not result.successful()

        objects = _dataset_bucket_objects(fake_minio)
        assert objects == [], f"a failed run must not leave an orphaned JSONL: {objects}"

        session = sync_sessionmaker()
        try:
            ds = session.get(Dataset, dataset_id)
            assert ds.status == JobStatus.FAILED
            assert "injected SDG failure after upload" in ds.error_message
        finally:
            session.close()


# =============================================================================
# 4. Cleanup errors must never mask the original failure
# =============================================================================


class TestCleanupErrorDoesNotMaskOriginalFailure:
    def test_minio_error_during_cleanup_does_not_change_terminal_outcome(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(1)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        real_s3_uri = dg_module.s3_uri
        calls = {"n": 0}

        def _s3_uri_then_fail(bucket, key):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected SDG failure after upload for test")
            return real_s3_uri(bucket, key)

        def _broken_remove_object(*a, **k):
            raise ConnectionError("MinIO is down during cleanup")

        monkeypatch.setattr(dg_module, "s3_uri", _s3_uri_then_fail)
        monkeypatch.setattr(dg_module, "remove_object", _broken_remove_object)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": _build_payload(project_id), "dataset_id": str(dataset_id)}
        )
        assert not result.successful()
        assert isinstance(result.result, RuntimeError)
        assert "injected SDG failure after upload" in str(result.result), (
            "a cleanup error must not replace the original exception surfaced to Celery"
        )

        session = sync_sessionmaker()
        try:
            ds = session.get(Dataset, dataset_id)
            assert ds.status == JobStatus.FAILED
            assert "injected SDG failure after upload" in ds.error_message, (
                "a cleanup error must not overwrite the original failure's error_message"
            )
        finally:
            session.close()


# =============================================================================
# 5. THE test that actually exercises the `committed` guard.
#
# Every test above raises BEFORE the DB-persisting session_scope block
# commits, so `committed` is False in all of them — a broken guard (e.g.
# "if uploaded_keys:" instead of "if not committed and uploaded_keys:")
# would still pass every one of them, because in every one of them NOT
# deleting and "deleting because not committed" look identical. This test
# raises AFTER that commit, so `committed` is True; only a correctly gated
# guard leaves the object alone.
# =============================================================================


class TestPostCommitFailureDoesNotDeleteLiveObject:
    def test_failure_after_the_commit_must_not_delete_the_now_live_object(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1000, 500)
            return _fake_result(1)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        # Explode in the success-path log line, which runs after both the
        # DB commit (`committed = True`) AND the (successful) JobCompleted
        # publish — same injection point `test_worker_usage_events.py`'s
        # `TestTerminalFrameIsTerminal` uses for the identical reason.
        real_info = dg_module.log.info

        def _explode_on_done_log(msg, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            if isinstance(msg, str) and msg.startswith("SDG done"):
                raise RuntimeError("something failed after the run was committed")
            return real_info(msg, *args, **kwargs)

        monkeypatch.setattr(dg_module.log, "info", _explode_on_done_log)

        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": _build_payload(project_id), "dataset_id": str(dataset_id)}
        )

        objects = _dataset_bucket_objects(fake_minio)
        assert objects == [f"sdg/{dataset_id}.jsonl"], (
            "a post-commit failure must not delete the now-live, DB-referenced "
            f"object: {objects}"
        )

        session = sync_sessionmaker()
        try:
            ds = session.get(Dataset, dataset_id)
            assert ds.status == JobStatus.COMPLETED, (
                "a durably-committed COMPLETED run was unwound by a post-commit failure"
            )
        finally:
            session.close()
