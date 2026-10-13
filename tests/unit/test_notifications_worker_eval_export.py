"""Evaluation / export tasks record one notification per terminal outcome."""

from unittest.mock import MagicMock
from uuid import uuid4

from api.models.model_artifact import ModelArtifact
from api.models.notification import Notification
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus
from tests.unit import test_worker_orphan_cleanup_export as export_harness
from tests.unit import test_worker_zombie_cancel_eval as eval_harness
from tests.unit.test_worker_zombie_cancel_eval import sync_sessionmaker as sync_sessionmaker


def _set_owner(maker, training_id, owner="owner-1"):
    with maker() as session:
        session.get(TrainingJob, training_id).owner_id = owner
        session.commit()


def _notes(maker):
    with maker() as session:
        return session.query(Notification).all()


def _eval_setup(monkeypatch, maker, fake_minio, fake_redis_pubsub):
    module = eval_harness._install_worker_patches(monkeypatch, maker, fake_minio, fake_redis_pubsub)
    ids = [uuid4() for _ in range(5)]
    project_id, training_id, artifact_id, dataset_id, evaluation_id = ids
    eval_harness._seed_chain(
        maker,
        project_id=project_id,
        training_id=training_id,
        artifact_id=artifact_id,
        dataset_id=dataset_id,
        evaluation_id=evaluation_id,
        eval_status=JobStatus.PENDING,
    )
    eval_harness._seed_dataset_jsonl(fake_minio)
    _set_owner(maker, training_id)
    return module, project_id, evaluation_id


def test_evaluation_completed_notifies_owner(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    module, project_id, evaluation_id = _eval_setup(
        monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    )
    monkeypatch.setattr(module, "_predict_rows", MagicMock(return_value=(["a1"], ["a1"], ["q1"])))
    result = module.run_evaluation.apply(kwargs={"evaluation_id": str(evaluation_id)})
    assert result.successful(), result.result
    (note,) = _notes(sync_sessionmaker)
    assert (note.owner_id, note.event_type, note.resource_type) == (
        "owner-1",
        "evaluation.completed",
        "evaluation",
    )
    assert note.resource_id == str(evaluation_id)
    assert note.payload == {"project_id": str(project_id), "name": "artifact"}


def test_evaluation_failed_notifies_owner_with_message(
    monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
):
    module, _, evaluation_id = _eval_setup(
        monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    )
    monkeypatch.setattr(module, "_predict_rows", MagicMock(side_effect=RuntimeError("boom")))
    assert module.run_evaluation.apply(kwargs={"evaluation_id": str(evaluation_id)}).failed()
    (note,) = _notes(sync_sessionmaker)
    assert note.event_type == "evaluation.failed"
    assert note.payload["message"] == "boom"


def _export_setup(monkeypatch, maker, fake_minio, fake_redis_pubsub):
    module = export_harness._install_worker_patches(
        monkeypatch, maker, fake_minio, fake_redis_pubsub, {}
    )
    project_id, training_id, artifact_id = uuid4(), uuid4(), uuid4()
    export_harness._seed_project_job_artifact(
        maker, project_id=project_id, training_id=training_id, artifact_id=artifact_id
    )
    export_harness._seed_adapter_files(fake_minio, training_id=training_id)
    _set_owner(maker, training_id)
    return module, project_id, artifact_id


def test_export_completed_notifies_owner(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    module, project_id, artifact_id = _export_setup(
        monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    )
    result = module.export_model.apply(kwargs={"artifact_id": str(artifact_id), "format": "gguf"})
    assert result.successful(), result.result
    (note,) = _notes(sync_sessionmaker)
    assert (note.owner_id, note.event_type, note.resource_type) == ("owner-1", "export.completed", "model")
    assert note.resource_id == str(artifact_id)
    assert note.payload == {"project_id": str(project_id), "name": "artifact"}


def test_export_failed_notifies_owner_with_message(
    monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
):
    module, _, artifact_id = _export_setup(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub)

    def _boom(**kwargs):
        raise RuntimeError("persist exploded")

    monkeypatch.setattr(module, "_persist_export_uris", _boom)
    module.export_model.apply(kwargs={"artifact_id": str(artifact_id), "format": "gguf"})
    (note,) = _notes(sync_sessionmaker)
    assert note.event_type == "export.failed"
    assert "persist exploded" in note.payload["message"]
    with sync_sessionmaker() as session:
        assert session.get(ModelArtifact, artifact_id).export_status == JobStatus.FAILED


def test_evaluation_cancel_does_not_notify(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    from api.models.evaluation_run import EvaluationRun

    module, _, evaluation_id = _eval_setup(
        monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    )
    with sync_sessionmaker() as session:
        session.get(EvaluationRun, evaluation_id).status = JobStatus.CANCELLED
        session.commit()
    result = module.run_evaluation.apply(kwargs={"evaluation_id": str(evaluation_id)})
    assert result.result["status"] == "cancelled"
    assert _notes(sync_sessionmaker) == []


def test_export_cancel_does_not_notify(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub):
    module, _, artifact_id = _export_setup(monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub)
    with sync_sessionmaker() as session:
        session.get(ModelArtifact, artifact_id).export_status = JobStatus.CANCELLED
        session.commit()
    result = module.export_model.apply(kwargs={"artifact_id": str(artifact_id), "format": "gguf"})
    assert result.result["status"] == "cancelled"
    assert _notes(sync_sessionmaker) == []
