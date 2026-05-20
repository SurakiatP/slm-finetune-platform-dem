"""Celery task: run an evaluation against a trained ModelArtifact.

Flow:
  1. Load `EvaluationRun` + `ModelArtifact` + `Dataset`.
  2. Validate the model has been registered with Ollama (ollama_model_tag set).
  3. Pull dataset rows from MinIO.
  4. For each row: build the inference prompt → POST /v1/chat/completions
     to Ollama → collect `(predicted, expected)`.
  5. Compute per-task metrics (classification / tool_calling / qa).
  6. (Optional) run the LLM-as-judge on top.
  7. Persist `metrics_json` + `llm_judge_score` + flip COMPLETED.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from celery.utils.log import get_task_logger

from ai_engine.evaluation import (
    metrics_classification,
    metrics_qa,
    metrics_tool_calling,
)
from api.core.config import get_settings
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.schemas.enums import JobStatus, TaskType
from api.schemas.progress import JobCompleted, JobFailed
from workers.celery_app import celery_app
from workers.progress import publish_ws_message, sync_redis_scope
from workers.storage import get_jsonl, get_minio_client, parse_s3_uri
from workers.sync_db import session_scope

log = get_task_logger(__name__)


@celery_app.task(bind=True, name="evaluation.run", max_retries=0)
def run_evaluation(
    self,
    *,
    evaluation_id: str,
    use_llm_judge: bool = False,
    judge_model: str | None = None,
) -> dict[str, Any]:
    job_id: str = self.request.id
    settings = get_settings()
    eval_uuid = UUID(evaluation_id)

    with sync_redis_scope() as redis:

        def publish(msg: Any) -> None:
            publish_ws_message(redis, job_id, msg)

        try:
            # ---- 1. Load context --------------------------------------------
            with session_scope() as session:
                ev = session.get(EvaluationRun, eval_uuid)
                if ev is None:
                    raise RuntimeError(f"EvaluationRun {evaluation_id} not found")
                artifact = session.get(ModelArtifact, ev.model_artifact_id)
                dataset = session.get(Dataset, ev.dataset_id)
                if artifact is None or dataset is None:
                    raise RuntimeError("evaluation: missing artifact or dataset")
                if not artifact.ollama_model_tag:
                    raise RuntimeError(
                        f"Artifact {artifact.id} has no ollama_model_tag — "
                        "export to GGUF first"
                    )
                if not dataset.storage_uri:
                    raise RuntimeError(
                        f"Dataset {dataset.id} has no storage_uri (not yet generated)"
                    )

                ollama_tag = artifact.ollama_model_tag
                dataset_uri = dataset.storage_uri
                task_type = dataset.task_type
                tool_definitions = _extract_tool_definitions(dataset.generation_metadata)
                classification_labels = _extract_labels(dataset.generation_metadata)

                ev.status = JobStatus.RUNNING
                ev.started_at = datetime.now(timezone.utc)

            # ---- 2. Pull rows -----------------------------------------------
            log.info(
                "eval: job=%s loading dataset %s for tag=%s",
                job_id,
                dataset_uri,
                ollama_tag,
            )
            minio = get_minio_client()
            ds_bucket, ds_key = parse_s3_uri(dataset_uri)
            rows = get_jsonl(minio, ds_bucket, ds_key)
            if not rows:
                raise RuntimeError(f"Dataset {dataset_uri} is empty")

            # ---- 3. Predict via Ollama -------------------------------------
            ollama_base = str(settings.ollama_base_url).rstrip("/")
            predicted, expected, questions = _predict_rows(
                rows=rows,
                task_type=task_type,
                tool_definitions=tool_definitions,
                ollama_base_url=ollama_base,
                ollama_tag=ollama_tag,
            )

            # ---- 4. Per-task metrics ---------------------------------------
            metrics = _compute_metrics_for_task(
                task_type=task_type,
                predicted=predicted,
                expected=expected,
                classification_labels=classification_labels,
            )

            # ---- 5. Optional LLM judge -------------------------------------
            judge_score, judge_model_resolved = _apply_llm_judge(
                use_llm_judge=use_llm_judge,
                task_type=task_type,
                judge_model=judge_model,
                settings=settings,
                questions=questions,
                expected=expected,
                predicted=predicted,
                metrics=metrics,
            )

            # ---- 6. Persist + 7. Publish completion ------------------------
            with session_scope() as session:
                row = session.get(EvaluationRun, eval_uuid)
                if row is None:
                    raise RuntimeError(f"EvaluationRun {evaluation_id} disappeared")
                row.metrics_json = metrics
                row.llm_judge_score = judge_score
                row.llm_judge_model = judge_model_resolved
                row.status = JobStatus.COMPLETED
                row.ended_at = datetime.now(timezone.utc)

            publish(
                JobCompleted(
                    job_id=job_id,
                    result={
                        "evaluation_id": evaluation_id,
                        "metrics": metrics,
                        "llm_judge_score": judge_score,
                        "llm_judge_model": judge_model_resolved,
                    },
                )
            )
            log.info(
                "eval: job=%s done; %s metrics, judge=%s",
                job_id,
                len(metrics),
                judge_score,
            )
            return {
                "status": "completed",
                "evaluation_id": evaluation_id,
                "metrics": metrics,
                "llm_judge_score": judge_score,
            }

        except Exception as exc:
            log.exception("evaluation task failed (job=%s)", job_id)
            try:
                with session_scope() as session:
                    row = session.get(EvaluationRun, eval_uuid)
                    if row is not None:
                        row.status = JobStatus.FAILED
                        row.ended_at = datetime.now(timezone.utc)
                        row.error_message = (str(exc) or repr(exc))[:4000]
            except Exception:  # noqa: BLE001
                log.warning("could not persist FAILED for %s", evaluation_id, exc_info=True)
            try:
                publish(
                    JobFailed(
                        job_id=job_id,
                        error=str(exc) or repr(exc),
                        error_type=type(exc).__name__,
                    )
                )
            except Exception:  # noqa: BLE001
                log.warning("failed to publish JobFailed", exc_info=True)
            raise


# ---- per-task metric dispatch + LLM judge -----------------------------------


def _compute_metrics_for_task(
    *,
    task_type: TaskType,
    predicted: list[str],
    expected: list[str],
    classification_labels: list[str] | None,
) -> dict[str, Any]:
    """Route to the right metric module based on task_type.

    Pulled out of ``run_evaluation`` so the orchestrator stays thin and the
    dispatch contract is easy to characterize in unit tests.
    """
    if task_type is TaskType.CLASSIFICATION:
        labels = classification_labels or sorted(set(expected))
        return metrics_classification.compute_metrics(
            predicted=predicted, expected=expected, labels=labels
        )
    if task_type is TaskType.TOOL_CALLING:
        return metrics_tool_calling.compute_metrics(
            predicted=predicted, expected=expected
        )
    if task_type is TaskType.QA:
        return metrics_qa.compute_metrics(
            predicted=predicted, expected=expected
        )
    # pragma: no cover — schema gates this earlier
    raise ValueError(f"unsupported task_type: {task_type}")


def _apply_llm_judge(
    *,
    use_llm_judge: bool,
    task_type: TaskType,
    judge_model: str | None,
    settings: Any,
    questions: list[str],
    expected: list[str],
    predicted: list[str],
    metrics: dict[str, Any],
) -> tuple[float | None, str | None]:
    """Run optional LLM-as-judge over (questions, expected, predicted).

    Mutates ``metrics`` in-place to add either ``llm_judge_notes`` (for
    classification, where the closed-set rule-based metrics are the right tool)
    or ``llm_judge_skipped_rows`` (for QA/tool-calling actually judged).

    Returns ``(score, model)`` — both ``None`` when judge wasn't applied.
    """
    if not use_llm_judge:
        return None, None

    if task_type is TaskType.CLASSIFICATION:
        metrics["llm_judge_notes"] = (
            "LLM judge does not apply to classification — "
            "use rule-based metrics (accuracy, f1_macro) instead."
        )
        return None, None

    if task_type not in (TaskType.QA, TaskType.TOOL_CALLING):
        return None, None

    from ai_engine.data_gen.openrouter_client import OpenRouterClient
    from ai_engine.evaluation.llm_judge import judge_rows

    judge_model_resolved = judge_model or settings.llm_judge_model
    client = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        teacher_model=judge_model_resolved,
        http_referer=settings.openrouter_http_referer,
        app_title=settings.openrouter_app_title,
    )
    jb = judge_rows(
        client=client,
        judge_model=judge_model_resolved,
        questions=questions,
        expected=expected,
        predicted=predicted,
    )
    metrics["llm_judge_skipped_rows"] = jb.skipped
    return jb.mean_score, judge_model_resolved


# ---- prediction loop -------------------------------------------------------


def _predict_rows(
    *,
    rows: list[dict[str, Any]],
    task_type: TaskType,
    tool_definitions: list | None,
    ollama_base_url: str,
    ollama_tag: str,
) -> tuple[list[str], list[str], list[str]]:
    """Run inference once per row. Returns parallel `(predicted, expected, questions)`.

    For classification the `question` is the input `text`. For tool_calling /
    qa the row's `question` field is used directly. The expected answer is
    taken from the row's gold-label field.
    """
    predicted: list[str] = []
    expected: list[str] = []
    questions: list[str] = []
    timeout = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0)

    with httpx.Client(timeout=timeout) as client:
        for row in rows:
            user_prompt, gold = _prompt_and_gold(row, task_type, tool_definitions)
            try:
                resp = client.post(
                    f"{ollama_base_url}/v1/chat/completions",
                    json={
                        "model": ollama_tag,
                        "messages": [
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.0,
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"] or ""
            except Exception as exc:  # noqa: BLE001
                log.warning("eval: inference failed on row; treating as empty (%s)", exc)
                content = ""

            predicted.append(_postprocess(content, task_type))
            expected.append(gold)
            questions.append(user_prompt)

    return predicted, expected, questions


def _prompt_and_gold(
    row: dict[str, Any],
    task_type: TaskType,
    tool_definitions: list | None,
) -> tuple[str, str]:
    if task_type is TaskType.CLASSIFICATION:
        return row.get("text", ""), row.get("label", "")
    if task_type is TaskType.QA:
        return row.get("question", ""), row.get("answer", "")
    if task_type is TaskType.TOOL_CALLING:
        question = row.get("question", "")
        if tool_definitions:
            tools_text = json.dumps(
                [t.model_dump(mode="json") for t in tool_definitions],
                ensure_ascii=False,
            )
            prompt = (
                f"Available tools:\n{tools_text}\n\n"
                f"User: {question}\n\n"
                'Respond with a single JSON object: '
                '{"name": "<tool>", "parameters": {...}}.'
            )
        else:
            prompt = question
        return prompt, row.get("answer", "")
    raise ValueError(f"unsupported task_type: {task_type}")


def _postprocess(content: str, task_type: TaskType) -> str:
    """Strip whitespace; for classification keep only the first line."""
    text = (content or "").strip()
    if task_type is TaskType.CLASSIFICATION:
        return text.splitlines()[0].strip() if text else ""
    return text


# ---- metadata extractors ---------------------------------------------------


def _extract_tool_definitions(metadata: dict[str, Any] | None) -> list | None:
    if not metadata:
        return None
    raw = metadata.get("tool_definitions")
    if not raw:
        return None
    from api.schemas.data_formats import ToolDefinition

    return [ToolDefinition.model_validate(d) for d in raw]


def _extract_labels(metadata: dict[str, Any] | None) -> list[str] | None:
    if not metadata:
        return None
    cfg = metadata.get("classification_config") or {}
    labels = cfg.get("labels")
    return list(labels) if isinstance(labels, list) else None


__all__ = ["run_evaluation"]
