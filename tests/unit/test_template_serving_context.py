"""CPU contracts for immutable template evaluation and serving context."""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from api.models.training_job import TrainingJob
from api.schemas.enums import TaskType
from api.schemas.evaluations import EvaluationCreate
from api.schemas.inference import ChatCompletionRequest, CompletionRequest
from api.services import evaluation_service, inference_service
from workers.tasks import auto_pipeline, evaluation, model_export


def test_ner_exact_spans_and_invalid_json():
    gold = json.dumps([{"text": "ไทย", "type": "LOCATION", "start": 2, "end": 5}])
    wrong = json.dumps([{"text": "ไทย", "type": "PERSON", "start": 2, "end": 5}])
    result = evaluation._compute_metrics_for_task(
        task_type=TaskType.QA,
        predicted=[gold, wrong, "oops"],
        expected=[gold, gold, gold],
        classification_labels=None,
        template_id="tpl-004",
    )
    assert result["precision_micro"] == 0.5
    assert result["recall_micro"] == pytest.approx(1 / 3)
    assert result["f1_micro"] == pytest.approx(0.4)
    assert result["invalid_json_rate"] == pytest.approx(1 / 3)
    assert "rouge1" not in result


@pytest.mark.parametrize(
    "prediction", ["{}", '[{"type":"X","start":true,"end":2,"text":"a"}]', "```json\n[]\n```"]
)
def test_ner_rejects_malformed_entity_output(prediction):
    from ai_engine.evaluation.metrics_ner import compute_metrics

    assert compute_metrics(predicted=[prediction], expected=["[]"])["invalid_json_rate"] == 1


def test_prediction_passes_saved_system_prompt(monkeypatch):
    client = MagicMock()
    client.post.return_value = httpx.Response(
        200,
        request=httpx.Request("POST", "http://test"),
        json={"choices": [{"message": {"content": "positive"}}]},
    )
    monkeypatch.setattr(
        evaluation.httpx,
        "Client",
        lambda **kw: MagicMock(__enter__=lambda s: client, __exit__=lambda *a: None),
    )
    evaluation._predict_rows(
        rows=[{"text": "ดี", "label": "positive"}],
        task_type=TaskType.CLASSIFICATION,
        tool_definitions=None,
        ollama_base_url="http://test",
        ollama_tag="local/model",
        system_prompt="saved",
    )
    assert client.post.call_args.kwargs["json"]["messages"] == [
        {"role": "system", "content": "saved"},
        {"role": "user", "content": "ดี"},
    ]


@pytest.mark.parametrize("literal", [False, True])
@pytest.mark.parametrize("text_mode", [False, True])
@pytest.mark.parametrize("override", [None, "caller", ""])
async def test_inference_prompt_snapshot(monkeypatch, literal, text_mode, override):
    artifact = SimpleNamespace(id=uuid4(), training_job_id=uuid4(), ollama_model_tag="local/saved")
    training = SimpleNamespace(context_snapshot={"system_prompt": "saved"})
    db = AsyncMock()
    db.execute.return_value = MagicMock(scalar_one_or_none=lambda: artifact)
    db.get.return_value = training
    monkeypatch.setattr(
        inference_service.ownership, "assert_model_access", AsyncMock(return_value=artifact)
    )
    monkeypatch.setattr(inference_service, "_audit_call", AsyncMock())
    post = AsyncMock(
        return_value={
            "created": 1,
            "model": artifact.ollama_model_tag,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }
    )
    monkeypatch.setattr(inference_service, "_post_json", post)
    identifier = "local/saved:latest" if literal else str(artifact.id)
    if text_mode:
        body = CompletionRequest(model=identifier, prompt=["one", "two"], system_prompt=override)
        result = await inference_service.text_completions(db, body)
        assert [choice.text for choice in result.choices] == ["ok", "ok"]
        assert result.usage.total_tokens == 6
    else:
        messages = [{"role": "system", "content": override}] if override is not None else []
        body = ChatCompletionRequest(
            model=identifier, messages=[*messages, {"role": "user", "content": "one"}]
        )
        await inference_service.chat_completions(db, body)
    for call in post.call_args_list:
        assert call.args[0] == "/v1/chat/completions"
        assert call.args[1]["messages"][0] == {
            "role": "system",
            "content": "saved" if override is None else override,
        }


async def test_base_text_completion_remains_passthrough(monkeypatch):
    db = AsyncMock()
    monkeypatch.setattr(inference_service, "_audit_call", AsyncMock())
    post = AsyncMock(
        return_value={
            "created": 1,
            "model": "qwen2.5:1.5b",
            "choices": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
        }
    )
    monkeypatch.setattr(inference_service, "_post_json", post)
    await inference_service.text_completions(
        db, CompletionRequest(model="qwen2.5:1.5b", prompt="one")
    )
    assert post.call_args.args[0] == "/v1/completions"
    assert "system_prompt" not in post.call_args.args[1]
    db.execute.assert_not_called()


def test_template_test_dataset_resolution_does_not_fall_back():
    test_id = uuid4()
    session = MagicMock()
    session.get.return_value = None
    job = SimpleNamespace(
        context_snapshot={"template_id": "tpl-004", "test_dataset_id": str(test_id)}
    )
    assert auto_pipeline._evaluation_dataset(session, job) is None
    session.get.assert_called_once_with(auto_pipeline.Dataset, test_id)
    session.execute.assert_not_called()


def test_export_retains_owner_and_saved_context(monkeypatch):
    artifact_id, training_id = uuid4(), uuid4()
    job = SimpleNamespace(
        training_name="saved",
        owner_id="owner",
        project_id=None,
        context_snapshot={"system_prompt": "saved", "chat_template": "qwen-2.5"},
    )
    session = MagicMock()
    session.get.side_effect = lambda model, identifier: (
        job if model is TrainingJob else SimpleNamespace(training_job_id=training_id)
    )

    @contextmanager
    def scope():
        yield session

    monkeypatch.setattr(model_export, "session_scope", scope)
    assert model_export._ollama_tag_context(artifact_id) == ("saved", "owner")
    assert model_export._serving_context(artifact_id) == job.context_snapshot
    ollama = MagicMock()
    model_export._register_with_ollama(
        ollama=ollama, tag="owner/saved", gguf_path="file.gguf", system_prompt="saved"
    )
    assert ollama.create_from_blob.call_args.kwargs["system"] == "saved"


async def test_manual_template_evaluation_rejects_other_datasets(monkeypatch):
    artifact = SimpleNamespace(id=uuid4(), training_job_id=uuid4(), ollama_model_tag="owner/model")
    dataset = SimpleNamespace(id=uuid4(), storage_uri="s3://test/data.jsonl", num_samples=1)
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(
        context_snapshot={
            "template_id": "tpl-004",
            "test_dataset_id": str(uuid4()),
        }
    )
    monkeypatch.setattr(
        evaluation_service.ownership, "assert_model_access", AsyncMock(return_value=artifact)
    )
    monkeypatch.setattr(
        evaluation_service.ownership, "assert_dataset_access", AsyncMock(return_value=dataset)
    )
    with pytest.raises(HTTPException, match="test dataset") as error:
        await evaluation_service.submit_evaluation_job(
            db, EvaluationCreate(model_artifact_id=artifact.id, dataset_id=dataset.id)
        )
    assert error.value.status_code == 400
    db.flush.assert_not_called()
