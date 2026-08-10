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
from api.core.redis_client import job_snapshot_key
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.models.usage_event import UsageEvent
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


def _usage_rows(sync_sessionmaker) -> list[UsageEvent]:
    session = sync_sessionmaker()
    try:
        return list(session.execute(select(UsageEvent)).scalars().all())
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


# =============================================================================
# 6. Zombie-cancel — a job cancelled while no worker exists must stay
#    cancelled, not be resurrected into COMPLETED by a worker that later
#    picks the queued task up.
#
#    Two guards:
#      • START-CHECK: the row is already CANCELLED by the time this worker
#        even loads it — no RUNNING flip, no generation work, just a
#        `_cancelled_frame` announcement and a clean `.apply()` success.
#      • COMPLETED-GUARD (discard): the row flips to CANCELLED mid-flight,
#        after the start-check already passed but before the success commit
#        — the success commit must discard every write instead of resurrecting
#        CANCELLED back to COMPLETED, still bill usage once, and clean up the
#        now-orphaned upload(s).
# =============================================================================


class TestZombieCancelStartCheck:
    def test_already_cancelled_at_start_short_circuits_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )

        # A worker must never even reach `_run_generator` for a job that was
        # already cancelled before it started — if it does, the start-check
        # didn't short-circuit.
        async def _fail_if_called(**kwargs):
            raise AssertionError(
                "_run_generator must not run for a job cancelled before start"
            )

        monkeypatch.setattr(dg_module, "_run_generator", _fail_if_called)

        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(
            sync_sessionmaker,
            project_id=project_id,
            dataset_id=dataset_id,
            status=JobStatus.CANCELLED,
        )

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": _build_payload(project_id), "dataset_id": str(dataset_id)}
        )
        assert result.successful(), f"task raised: {result.result!r}"
        assert result.result == {"status": "cancelled", "dataset_id": str(dataset_id)}

        session = sync_sessionmaker()
        try:
            ds = session.get(Dataset, dataset_id)
            assert ds.status == JobStatus.CANCELLED
            assert ds.storage_uri is None
        finally:
            session.close()

        objects = _dataset_bucket_objects(fake_minio)
        assert objects == [], f"nothing should ever be uploaded for this job: {objects}"

        # A published `JobFailed(error_type="Cancelled")` frame, and the
        # `job:{id}:last` snapshot key holding the same terminal frame for a
        # late/reconnecting subscriber.
        assert fake_redis_pubsub.published, "no frame was published at all"
        channel, payload = fake_redis_pubsub.published[-1]
        frame = json.loads(payload)
        assert frame["type"] == "failed"
        assert frame["error_type"] == "Cancelled"

        job_id = result.id
        snapshot = fake_redis_pubsub.client.get(job_snapshot_key(job_id))
        assert snapshot is not None
        snapshot_frame = json.loads(snapshot)
        assert snapshot_frame["type"] == "failed"
        assert snapshot_frame["error_type"] == "Cancelled"


class TestZombieCancelDiscardsMidFlightCancellation:
    def test_cancelled_between_upload_and_commit_discards_the_completed_write(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1800, 900)
            return _fake_result(1)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        # Same seam as `TestCancelPathDeletesUpload` above (`s3_uri` called
        # right after `put_jsonl`, strictly before the DB-persisting
        # `session_scope()` block) but WITHOUT the SystemExit: this models
        # the cancel endpoint's status flip landing in that same window,
        # except the SIGTERM itself never arrives (or arrives too late to
        # matter) — the task's own success path is the one that must notice
        # CANCELLED and discard, not the `except BaseException` handler.
        real_s3_uri = dg_module.s3_uri
        calls = {"n": 0}

        def _s3_uri_then_cancel_no_raise(bucket, key):
            calls["n"] += 1
            if calls["n"] == 1:
                session = sync_sessionmaker()
                try:
                    ds = session.get(Dataset, dataset_id)
                    ds.status = JobStatus.CANCELLED
                    session.commit()
                finally:
                    session.close()
            return real_s3_uri(bucket, key)

        monkeypatch.setattr(dg_module, "s3_uri", _s3_uri_then_cancel_no_raise)

        result = dg_module.generate_synthetic_data.apply(
            kwargs={"request_payload": _build_payload(project_id), "dataset_id": str(dataset_id)}
        )
        assert result.successful(), f"task raised: {result.result!r}"
        assert result.result == {"status": "cancelled", "dataset_id": str(dataset_id)}

        objects = _dataset_bucket_objects(fake_minio)
        assert objects == [], f"the orphaned upload must be deleted: {objects}"

        session = sync_sessionmaker()
        try:
            ds = session.get(Dataset, dataset_id)
            assert ds.status == JobStatus.CANCELLED
            assert ds.storage_uri is None, "the discard path must not write storage_uri"
            assert ds.num_samples == 0, "the discard path must not write num_samples"

            children = list(
                session.execute(
                    select(Dataset).where(Dataset.parent_dataset_id == dataset_id)
                ).scalars()
            )
            assert children == [], "no holdout Dataset row must be created on discard"
        finally:
            session.close()

        rows = _usage_rows(sync_sessionmaker)
        assert len(rows) == 1, f"usage must be billed exactly once: {[r.outcome for r in rows]}"
        assert rows[0].outcome == "cancelled"
        assert rows[0].prompt_tokens == 1800
        assert rows[0].completion_tokens == 900

        types = [json.loads(msg)["type"] for _channel, msg in fake_redis_pubsub.published]
        assert "completed" not in types, f"no JobCompleted frame must ever be published: {types}"
        assert types[-1] == "failed", f"the terminal frame must be the cancelled one: {types}"

        last_frame = json.loads(fake_redis_pubsub.published[-1][1])
        assert last_frame["error_type"] == "Cancelled"


# =============================================================================
# 7. Zombie-cancel — the discard branch's `committed = True` must actually
#    hold, i.e. a crash landing AFTER the discard commit must not re-bill or
#    re-write the terminal frame.
#
# The discard branch (section 6 above) sets `committed = True` the moment its
# CANCELLED-discovering commit lands, then does best-effort cleanup and
# publishes the cancelled frame. That flag is the ONLY thing standing between
# a crash in that tail and the `except BaseException` handler redoing the
# whole terminal sequence: the handler's four blocks (status write, usage
# record, orphan cleanup, `JobFailed` publish) are each gated on `not
# committed`.
#
# Nothing exercised that flag behaviorally — deleting `committed = True` from
# the discard branch left the entire suite green, which is precisely the
# "assertion that cannot fail" shape this repo hunts. This test closes it.
#
# The crash is injected at the one place in that tail that can realistically
# raise: the terminal publish. Note the publish is guarded by `except
# Exception`, and `SystemExit` is a `BaseException` — so a cancel's SIGTERM
# arriving in this exact window (the real-world scenario: the cancel endpoint
# writes CANCELLED, the task's success path notices and discards, and only
# THEN does billiard's SIGTERM handler fire) propagates straight past the
# guard into `except BaseException`. That is the entry the flag has to
# defend, and reaching it is asserted here (`pytest.raises(SystemExit)`)
# rather than assumed.
# =============================================================================


class TestDiscardBranchIsIdempotentAgainstALateCrash:
    def test_systemexit_after_discard_does_not_rebill_or_overwrite_the_frame(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        dg_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, dataset_id = uuid4(), uuid4()
        _seed_project_and_dataset(sync_sessionmaker, project_id=project_id, dataset_id=dataset_id)

        async def _fake_run_generator(**kwargs):
            kwargs["usage"].add(dg_module.sdg_models.GENERATOR, STAGE_GENERATE, 1800, 900)
            return _fake_result(1)

        monkeypatch.setattr(dg_module, "_run_generator", _fake_run_generator)

        # Same seam as section 6: flip the row to CANCELLED in the
        # post-upload/pre-commit window, without raising, so the success
        # path's own guard is what discovers it and takes the discard branch.
        real_s3_uri = dg_module.s3_uri
        calls = {"n": 0}

        def _s3_uri_then_cancel_no_raise(bucket, key):
            calls["n"] += 1
            if calls["n"] == 1:
                session = sync_sessionmaker()
                try:
                    ds = session.get(Dataset, dataset_id)
                    ds.status = JobStatus.CANCELLED
                    session.commit()
                finally:
                    session.close()
            return real_s3_uri(bucket, key)

        monkeypatch.setattr(dg_module, "s3_uri", _s3_uri_then_cancel_no_raise)

        # The crash. `publish_ws_message` writes the `job:{id}:last` snapshot
        # BEFORE it publishes, so raising from `publish` models a SIGTERM
        # landing with the cancelled frame already durably snapshotted — the
        # exact state a reconnecting watcher would read. Raising on the FIRST
        # terminal (`failed`) frame means: if the handler re-publishes its own
        # `JobFailed(error_type="SystemExit")`, that second publish overwrites
        # the snapshot before dying again — which is what the assertions below
        # detect.
        spy_publish = fake_redis_pubsub.client.publish

        def _publish_then_sigterm(channel, message):
            returned = spy_publish(channel, message)
            payload = message.decode("utf-8") if isinstance(message, bytes) else message
            if json.loads(payload)["type"] == "failed":
                raise SystemExit(-241)
            return returned

        monkeypatch.setattr(fake_redis_pubsub.client, "publish", _publish_then_sigterm)

        with pytest.raises(SystemExit):
            dg_module.generate_synthetic_data.apply(
                kwargs={"request_payload": _build_payload(project_id), "dataset_id": str(dataset_id)}
            )

        # 1. Billing: exactly one row. Without `committed = True` the handler
        #    re-enters `usage_service.record_run` and the same 1800/900 tokens
        #    are charged to this actor twice.
        rows = _usage_rows(sync_sessionmaker)
        assert len(rows) == 1, (
            f"the discard already billed this run; a crash after it must not "
            f"bill again — got {[(r.outcome, r.prompt_tokens) for r in rows]}"
        )
        assert rows[0].outcome == "cancelled"
        assert rows[0].prompt_tokens == 1800
        assert rows[0].completion_tokens == 900

        # 2. The terminal frame: exactly one, and it is still the cancelled
        #    one. Without the flag the handler publishes a second terminal
        #    frame carrying `error_type="SystemExit"` — the watcher's last
        #    word on a cancelled job then reads as a crash.
        terminal = [
            json.loads(message)
            for _channel, message in fake_redis_pubsub.published
            if json.loads(message)["type"] == "failed"
        ]
        assert len(terminal) == 1, (
            f"a terminal frame is terminal — exactly one `failed` frame "
            f"expected, got {[f['error_type'] for f in terminal]}"
        )
        assert terminal[0]["error_type"] == "Cancelled"

        # 3. `job:{id}:last` — the key that heals hanging watchers — must
        #    still hold the cancelled frame, not the SystemExit that followed.
        job_id = fake_redis_pubsub.published[0][0].split(":", 1)[1]
        snapshot = fake_redis_pubsub.client.get(job_snapshot_key(job_id))
        assert snapshot is not None
        snap = json.loads(snapshot)
        assert snap["type"] == "failed"
        assert snap["error_type"] == "Cancelled", (
            "the snapshot a reconnecting client reads must say the job was "
            "cancelled, not that it crashed"
        )

        # 4. The row itself: the handler's status/error write is gated on the
        #    same flag, so a discarded run must not acquire an error_message.
        session = sync_sessionmaker()
        try:
            ds = session.get(Dataset, dataset_id)
            assert ds.status == JobStatus.CANCELLED
            assert ds.error_message is None, (
                "the discard already finalised this row; the late crash must "
                "not stamp an error message onto it"
            )
        finally:
            session.close()
