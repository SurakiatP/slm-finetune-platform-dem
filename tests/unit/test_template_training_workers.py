"""Exercise manual/HPO workers, HPO trials, and final retrain without GPU imports."""

import hashlib
import json
from uuid import uuid4

import pytest

from api.models.dataset import Dataset
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from tests.unit import test_hpo_trial_gpu_release as objective_helpers
from tests.unit import test_worker_orphan_cleanup_training as manual_helpers
from tests.unit import test_worker_zombie_cancel_hpo as hpo_helpers
from tests.unit.test_worker_orphan_cleanup_training import sync_sessionmaker as sync_sessionmaker
from workers.storage import put_jsonl


@pytest.mark.parametrize("mode", ["manual", "hpo"])
def test_workers_pass_frozen_prompt_validation_and_only_train_rows(
    mode, monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
):
    helpers = manual_helpers if mode == "manual" else hpo_helpers
    module = helpers._install_worker_patches(
        monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    )
    monkeypatch.setattr(module, "preflight_gpu_vram", lambda **kwargs: None)
    monkeypatch.setattr(module, "_release_gpu_memory", lambda: None)
    import workers.tasks.auto_pipeline as auto_pipeline

    monkeypatch.setattr(auto_pipeline, "enqueue_auto_pipeline", lambda **kwargs: None)
    project_id, dataset_id, training_id = uuid4(), uuid4(), uuid4()
    kwargs = dict(project_id=project_id, dataset_id=dataset_id, training_id=training_id)
    if mode == "hpo":
        kwargs["status"] = JobStatus.PENDING
    helpers._seed_project_dataset_job(sync_sessionmaker, **kwargs)
    train = [{"question": str(i), "answer": "train-only"} for i in range(6)]
    validation = [{"question": "validation", "answer": "validation-only"}]
    test = [{"question": "test", "answer": "never-train"}]
    context = dict(
        template_id="tpl-005",
        template_version="1",
        system_prompt="Frozen prompt",
        train_sample_count=4,
        sampling_seed=11,
    )
    with sync_sessionmaker() as session:
        job = session.get(TrainingJob, training_id)
        job.owner_id = "owner"
        for role, values in (("train", train), ("validation", validation), ("test", test)):
            if role == "train":
                dataset = session.get(Dataset, dataset_id)
            else:
                dataset = Dataset(
                    id=uuid4(),
                    name=role,
                    task_type=TaskType.QA,
                    source=DatasetSource.UPLOADED,
                    status=JobStatus.COMPLETED,
                )
                session.add(dataset)
            dataset.owner_id = "owner"
            dataset.num_samples = len(values)
            dataset.storage_uri = f"s3://datasets/{role}.jsonl"
            body = "\n".join(json.dumps(row, ensure_ascii=False) for row in values).encode()
            sha = hashlib.sha256(body).hexdigest()
            dataset.generation_metadata = dict(
                template_id="tpl-005", template_version="1", role=role, sha256=sha
            )
            context[f"{role}_dataset_id"] = str(dataset.id)
            context[f"{role}_sha256"] = sha
            put_jsonl(fake_minio, "datasets", f"{role}.jsonl", values)
        job.context_snapshot = context
        session.commit()
    calls = []

    class Trainer(helpers._FakeUnslothTrainer):
        def __init__(self, *, system_prompt, **kwargs):
            assert system_prompt == "Frozen prompt"
            super().__init__(**kwargs)

        def train(self, rows, *, callbacks, validation_rows):
            calls.append((rows, validation_rows))
            return super().train(rows, callbacks=callbacks)

    monkeypatch.setattr(module, "UnslothTrainer", Trainer)
    objective_calls = []
    if mode == "hpo":
        monkeypatch.setattr(module, "HPOObjective", lambda **kwargs: objective_calls.append(kwargs))
    task = module.train_manual if mode == "manual" else module.train_hpo
    result = task.apply(kwargs={"training_id": str(training_id)}, throw=True).get()
    assert result["status"] == "completed"
    assert len(calls) == 1
    assert len(calls[0][0]) == 4
    assert all(row in train for row in calls[0][0])
    assert calls[0][1] == validation
    if mode == "hpo":
        assert objective_calls[0]["rows"] == calls[0][0]
        assert objective_calls[0]["validation_rows"] == validation
        assert objective_calls[0]["system_prompt"] == "Frozen prompt"
    with sync_sessionmaker() as session:
        assert session.get(TrainingJob, training_id).context_snapshot == context


def test_hpo_trial_forwards_custom_prompt_and_external_validation(monkeypatch):
    import ai_engine.hpo.optuna_objective as module

    calls = []

    class Trainer(objective_helpers._SucceedingTrainer):
        def __init__(self, *, system_prompt, **kwargs):
            assert system_prompt == "Frozen trial prompt"
            super().__init__(**kwargs)

        def train(self, rows, *, callbacks, validation_rows):
            calls.append((rows, validation_rows))
            return super().train(rows, callbacks=callbacks)

    objective = objective_helpers._make_objective(monkeypatch, trainer_cls=Trainer)
    monkeypatch.setattr(module, "_release_gpu_memory", lambda: None)
    objective.system_prompt = "Frozen trial prompt"
    objective.validation_rows = [{"question": "V", "answer": "external"}]
    assert objective(objective_helpers._FakeTrial()) == 0.4
    assert calls == [(objective.rows, objective.validation_rows)]
