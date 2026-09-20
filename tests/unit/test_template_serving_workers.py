"""Drive real Celery orchestration with local DB and fake runtimes/storage."""

import hashlib
import json
from io import BytesIO
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus
from tests.unit import test_worker_orphan_cleanup_export as export_harness
from tests.unit import test_worker_zombie_cancel_eval as eval_harness
from tests.unit.test_worker_zombie_cancel_eval import sync_sessionmaker as sync_sessionmaker


@pytest.mark.parametrize("wrong_dataset", [False, True])
def test_evaluation_worker_uses_snapshot_prompt_and_ner_metric(
    monkeypatch,
    sync_sessionmaker,
    fake_minio,
    fake_redis_pubsub,
    wrong_dataset,
):
    module = eval_harness._install_worker_patches(
        monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    )
    project_id, training_id, artifact_id, dataset_id, evaluation_id = [uuid4() for _ in range(5)]
    eval_harness._seed_chain(
        sync_sessionmaker,
        project_id=project_id,
        training_id=training_id,
        artifact_id=artifact_id,
        dataset_id=dataset_id,
        evaluation_id=evaluation_id,
        eval_status=JobStatus.PENDING,
    )
    gold = json.dumps([{"text": "ไทย", "type": "LOCATION", "start": 0, "end": 3}])
    payload = json.dumps({"question": "ไทย", "answer": gold}).encode() + b"\n"
    fake_minio.put_object("datasets", "eval/ds.jsonl", BytesIO(payload), len(payload))
    with sync_sessionmaker() as session:
        job = session.get(TrainingJob, training_id)
        job.project_id = None
        job.context_snapshot = {
            "template_id": "tpl-004",
            "system_prompt": "frozen",
            "test_dataset_id": str(uuid4() if wrong_dataset else dataset_id),
            "test_sha256": hashlib.sha256(payload).hexdigest(),
        }
        session.commit()
    predict = MagicMock(return_value=([gold], [gold], ["ไทย"]))
    monkeypatch.setattr(module, "_predict_rows", predict)
    judge = MagicMock(side_effect=AssertionError("NER must not call paid judge"))
    monkeypatch.setattr(module, "_build_judge_client", judge)
    result = module.run_evaluation.apply(
        kwargs={"evaluation_id": str(evaluation_id), "use_llm_judge": True}
    )
    if wrong_dataset:
        assert result.failed()
        assert "test dataset" in str(result.result)
        predict.assert_not_called()
    else:
        assert result.successful(), result.result
        assert result.result["metrics"]["f1_micro"] == 1
        assert predict.call_args.kwargs["system_prompt"] == "frozen"
    judge.assert_not_called()


@pytest.mark.parametrize("format", ["gguf", "safetensors"])
def test_export_ships_saved_context_with_both_formats(
    monkeypatch,
    sync_sessionmaker,
    fake_minio,
    fake_redis_pubsub,
    format,
):
    calls = {}
    module = export_harness._install_worker_patches(
        monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, calls
    )
    project_id, training_id, artifact_id = [uuid4() for _ in range(3)]
    export_harness._seed_project_job_artifact(
        sync_sessionmaker, project_id=project_id, training_id=training_id, artifact_id=artifact_id
    )
    export_harness._seed_adapter_files(fake_minio, training_id=training_id)
    context = {"template_id": "tpl-004", "system_prompt": "frozen", "chat_template": "qwen-2.5"}
    with sync_sessionmaker() as session:
        job = session.get(TrainingJob, training_id)
        job.owner_id, job.project_id, job.training_name = "owner", None, "kept"
        job.context_snapshot = context
        session.commit()
    ollama = MagicMock()
    monkeypatch.setattr(module, "OllamaClient", lambda *a: ollama)
    result = module.export_model.apply(kwargs={"artifact_id": str(artifact_id), "format": format})
    assert result.successful(), result.result
    response = fake_minio.get_object(
        "models", f"exports/{artifact_id}/{format}/serving_context.json"
    )
    assert json.loads(response.read()) == context
    if format == "gguf":
        assert ollama.create_from_blob.call_args.kwargs["system"] == "frozen"
        assert ollama.create_from_blob.call_args.kwargs["tag"] == "owner/kept"
        assert "template" not in ollama.create_from_blob.call_args.kwargs
