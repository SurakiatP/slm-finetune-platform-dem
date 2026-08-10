"""Unit tests for the zombie-cancel fix in `workers/tasks/evaluation.py`.

The bug: a job cancelled while no worker exists yet stays queued with
`status=CANCELLED`. A worker later picks it up anyway, runs the whole
pipeline, and overwrites CANCELLED -> COMPLETED — silently undoing the
cancel and (for evaluation specifically) leaving any LLM-judge spend from
that run unbilled, since the `except BaseException` handler that normally
records usage on a terminal outcome never fires on this path (no exception
is raised).

Two guards close this, mirroring the uniform shape applied across all five
cancellable task files:

  1. START-CHECK (section 1, "load context"): if the row is already
     CANCELLED the instant it's loaded, skip the RUNNING flip and the
     artifact/dataset validation entirely, publish the cancelled terminal
     frame, and return — no usage was spent on this path.
  2. COMPLETED-GUARD (section 6, "persist + publish completion"): if the
     row was flipped to CANCELLED by the cancel endpoint *while* this task
     was mid-flight (predicting / judging), discard the terminal-success
     writes (metrics_json, judge fields, COMPLETED, ended_at, the
     `evaluation.completed` audit row) but still bill whatever the judge
     spent, exactly once, under `outcome="cancelled"`.

Harness follows `tests/unit/test_worker_orphan_cleanup_training.py` (sync
sqlite + JSONB `@compiles` shim, `_install_worker_patches`-style
monkeypatching of `session_scope`/`sync_redis_scope`/`get_minio_client`,
`.apply()` to run the Celery task synchronously) and stubs Ollama with the
`_FakeClient` pattern from `tests/unit/test_worker_progress_frames.py`.
"""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from api.models.base import Base
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.models.usage_event import UsageEvent
from api.schemas.enums import DatasetSource, JobStatus, TaskType, TrainingMode


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN003
    return "JSON"


_JUDGE_MODEL = "deepseek/deepseek-v4-flash-0731"  # priced in model_pricing's builtin map


# =============================================================================
# Fixtures / shared helpers
# =============================================================================


@pytest.fixture
def sync_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    yield maker
    engine.dispose()


class _FakeResponse:
    @staticmethod
    def raise_for_status() -> None:
        return None

    @staticmethod
    def json() -> dict:
        return {"choices": [{"message": {"content": "answer"}}]}


class _FakeClient:
    """Stand-in for `httpx.Client` — same shape as
    `test_worker_progress_frames.py`'s fixture. `run_evaluation` never gets
    far enough to call Ollama on the START-CHECK path; the COMPLETED-GUARD
    test does exercise `_predict_rows`, so this is needed there.
    """

    def __init__(self, *a, **kw) -> None:
        pass

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *a) -> None:
        return None

    def post(self, *a, **kw) -> _FakeResponse:
        return _FakeResponse()


def _install_worker_patches(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    """Same use-site patching pattern as `test_worker_orphan_cleanup_training.py`."""
    import workers.tasks.evaluation as evaluation_module

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

    monkeypatch.setattr(evaluation_module, "session_scope", _fake_session_scope)
    monkeypatch.setattr(evaluation_module, "sync_redis_scope", _fake_redis_scope)
    monkeypatch.setattr(evaluation_module, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(evaluation_module.httpx, "Client", _FakeClient)
    return evaluation_module


def _seed_chain(
    sync_sessionmaker,
    *,
    project_id,
    training_id,
    artifact_id,
    dataset_id,
    evaluation_id,
    eval_status: JobStatus,
):
    """Project -> TrainingJob -> ModelArtifact -> Dataset -> EvaluationRun."""
    session = sync_sessionmaker()
    try:
        project = Project(id=project_id, name="proj", task_type=TaskType.QA)
        session.add(project)

        dataset = Dataset(
            id=dataset_id,
            project_id=project_id,
            name="ds",
            task_type=TaskType.QA,
            source=DatasetSource.SDG,
            status=JobStatus.COMPLETED,
            num_samples=2,
            storage_uri="s3://datasets/eval/ds.jsonl",
        )
        session.add(dataset)

        job = TrainingJob(
            id=training_id,
            project_id=project_id,
            dataset_id=dataset_id,
            mode=TrainingMode.MANUAL,
            status=JobStatus.COMPLETED,
            base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
            config_json={},
        )
        session.add(job)

        artifact = ModelArtifact(
            id=artifact_id,
            training_job_id=training_id,
            name="artifact",
            base_model="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
            ollama_model_tag="slm/x",
        )
        session.add(artifact)

        run = EvaluationRun(
            id=evaluation_id,
            model_artifact_id=artifact_id,
            dataset_id=dataset_id,
            status=eval_status,
        )
        session.add(run)

        session.commit()
    finally:
        session.close()


def _seed_dataset_jsonl(fake_minio, *, bucket="datasets", key="eval/ds.jsonl"):
    from workers.storage import put_jsonl

    put_jsonl(
        fake_minio,
        bucket,
        key,
        [
            {"question": "q1", "answer": "a1"},
            {"question": "q2", "answer": "a2"},
        ],
    )


def _last_snapshot_frame(fake_redis_pubsub, job_id: str) -> dict:
    import json

    from api.core.redis_client import job_snapshot_key

    raw = fake_redis_pubsub.client.get(job_snapshot_key(job_id))
    assert raw is not None, "no snapshot frame was ever published for this job"
    return json.loads(raw)


def _usage_rows(sync_sessionmaker) -> list[UsageEvent]:
    session = sync_sessionmaker()
    try:
        return session.query(UsageEvent).all()
    finally:
        session.close()


# =============================================================================
# 1. START-CHECK — row already CANCELLED at context-load
# =============================================================================


class TestStartCheckExitsCleanlyOnAlreadyCancelled:
    def test_apply_returns_cancelled_without_running_the_row(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        evaluation_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, training_id, artifact_id, dataset_id, evaluation_id = (
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        )
        _seed_chain(
            sync_sessionmaker,
            project_id=project_id,
            training_id=training_id,
            artifact_id=artifact_id,
            dataset_id=dataset_id,
            evaluation_id=evaluation_id,
            eval_status=JobStatus.CANCELLED,
        )
        # Deliberately no dataset JSONL seeded in `fake_minio` — a cancelled
        # zombie whose dataset/artifact context is gone must still exit
        # cleanly rather than blow up trying to read it.

        result = evaluation_module.run_evaluation.apply(
            kwargs={"evaluation_id": str(evaluation_id)}
        )

        assert result.successful(), f"task raised: {result.result!r}"
        assert result.result == {
            "status": "cancelled",
            "evaluation_id": str(evaluation_id),
        }

        session = sync_sessionmaker()
        try:
            row = session.get(EvaluationRun, evaluation_id)
            assert row.status == JobStatus.CANCELLED
            assert row.started_at is None, (
                "the RUNNING flip must never happen for a row already "
                "CANCELLED at context-load"
            )
            assert row.metrics_json is None
        finally:
            session.close()

        assert _usage_rows(sync_sessionmaker) == [], (
            "nothing was spent on this path — zero usage rows expected"
        )

        frame = _last_snapshot_frame(fake_redis_pubsub, result.id)
        assert frame["type"] == "failed"
        assert frame["error_type"] == "Cancelled"


# =============================================================================
# 2. COMPLETED-GUARD — row flipped to CANCELLED mid-flight
# =============================================================================


class TestCompletedGuardDiscardsMidFlightCancel:
    def test_cancel_during_judging_bills_once_and_discards_completion(
        self, monkeypatch: pytest.MonkeyPatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    ) -> None:
        evaluation_module = _install_worker_patches(
            monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
        )
        project_id, training_id, artifact_id, dataset_id, evaluation_id = (
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
        )
        _seed_chain(
            sync_sessionmaker,
            project_id=project_id,
            training_id=training_id,
            artifact_id=artifact_id,
            dataset_id=dataset_id,
            evaluation_id=evaluation_id,
            eval_status=JobStatus.PENDING,
        )
        _seed_dataset_jsonl(fake_minio)

        # The seam: stand in for `_apply_llm_judge`. It (a) bills a priced
        # entry the way a real judge pass would, (b) flips the run to
        # CANCELLED in the DB — simulating `POST /evaluations/{id}/cancel`
        # landing while this task is mid-judge, and (c) returns
        # `(None, None)` the way the real function does when judging is
        # skipped/incomplete.
        def _fake_apply_llm_judge(
            *, use_llm_judge, task_type, judge_model, settings, questions, expected,
            predicted, metrics, usage=None,
        ):
            usage.add(_JUDGE_MODEL, "eval_judge", 100, 50)
            session = sync_sessionmaker()
            try:
                row = session.get(EvaluationRun, evaluation_id)
                row.status = JobStatus.CANCELLED
                session.commit()
            finally:
                session.close()
            return None, None

        monkeypatch.setattr(evaluation_module, "_apply_llm_judge", _fake_apply_llm_judge)

        result = evaluation_module.run_evaluation.apply(
            kwargs={
                "evaluation_id": str(evaluation_id),
                "use_llm_judge": True,
                "judge_model": _JUDGE_MODEL,
            }
        )

        assert result.successful(), f"task raised: {result.result!r}"
        assert result.result == {
            "status": "cancelled",
            "evaluation_id": str(evaluation_id),
        }

        session = sync_sessionmaker()
        try:
            row = session.get(EvaluationRun, evaluation_id)
            assert row.status == JobStatus.CANCELLED
            assert row.metrics_json is None, (
                "terminal-success writes must be discarded once the row is "
                "CANCELLED, not overwritten onto it"
            )
            assert row.llm_judge_score is None
        finally:
            session.close()

        usage_rows = _usage_rows(sync_sessionmaker)
        assert len(usage_rows) == 1, (
            f"expected exactly one usage row (billed once, not zero, not "
            f"twice), got {len(usage_rows)}"
        )
        assert usage_rows[0].outcome == "cancelled"

        published_types = [
            __import__("json").loads(msg)["type"]
            for _channel, msg in fake_redis_pubsub.published
        ]
        assert "completed" not in published_types, (
            "no JobCompleted frame must be published once the row was "
            "discovered CANCELLED at persist time"
        )
        frame = _last_snapshot_frame(fake_redis_pubsub, result.id)
        assert frame["type"] == "failed"
        assert frame["error_type"] == "Cancelled"

        from api.models.audit_event import AuditEvent

        session = sync_sessionmaker()
        try:
            audits = (
                session.query(AuditEvent)
                .filter_by(resource_type="evaluation", action="evaluation.completed")
                .all()
            )
            assert audits == [], (
                "no evaluation.completed audit row must be written for a "
                "run discarded as cancelled"
            )
        finally:
            session.close()
