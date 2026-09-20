"""Independent final-review regressions: no external services or GPU execution."""

import hashlib
import json
from io import BytesIO
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from ai_engine.evaluation.metrics_ner import compute_metrics
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus
from tests.unit import test_worker_zombie_cancel_eval as harness
from tests.unit.test_worker_zombie_cancel_eval import sync_sessionmaker as sync_sessionmaker


def test_ner_offsets_are_not_interchangeable_with_matching_surface_text():
    gold = json.dumps([{"text": "ไทย", "type": "LOC", "start": 0, "end": 3}])
    wrong = json.dumps([{"text": "ไทย", "type": "LOC", "start": 4, "end": 7}])
    assert compute_metrics(predicted=[wrong], expected=[gold])["f1_micro"] == 0


@pytest.mark.parametrize("tampered", [False, True])
def test_evaluation_verifies_frozen_test_content_before_inference(
    monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub, tampered
):
    module = harness._install_worker_patches(
        monkeypatch, sync_sessionmaker, fake_minio, fake_redis_pubsub
    )
    project_id, training_id, artifact_id, dataset_id, evaluation_id = [uuid4() for _ in range(5)]
    harness._seed_chain(
        sync_sessionmaker,
        project_id=project_id,
        training_id=training_id,
        artifact_id=artifact_id,
        dataset_id=dataset_id,
        evaluation_id=evaluation_id,
        eval_status=JobStatus.PENDING,
    )
    original = b'{"question":"original held-out input","answer":"[]"}\n'
    changed = b'{"question":"replacement held-out input","answer":"[]"}\n'
    with sync_sessionmaker() as session:
        job = session.get(TrainingJob, training_id)
        job.context_snapshot = {
            "template_id": "tpl-004",
            "system_prompt": "Extract entities.",
            "test_dataset_id": str(dataset_id),
            "test_sha256": hashlib.sha256(original).hexdigest(),
        }
        session.commit()
    body = changed if tampered else original
    fake_minio.put_object("datasets", "eval/ds.jsonl", BytesIO(body), len(body))
    predict = MagicMock(return_value=(["[]"], ["[]"], ["input"]))
    monkeypatch.setattr(module, "_predict_rows", predict)
    result = module.run_evaluation.apply(kwargs={"evaluation_id": str(evaluation_id)})
    if tampered:
        assert result.failed(), "Changed test bytes were scored despite a frozen test_sha256"
        predict.assert_not_called()
    else:
        assert result.successful(), result.result
        assert predict.call_args.kwargs["rows"][0]["question"] == "original held-out input"
        assert predict.call_args.kwargs["system_prompt"] == "Extract entities."
