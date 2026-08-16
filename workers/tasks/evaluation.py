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

Deliberately NOT wired to `workers/vram.py`'s `preflight_gpu_vram()` (unlike
`training.py` / `hpo_training.py` / `model_export.py`): evaluation never
touches the GPU in this worker's own process. Inference happens on the
Ollama server over HTTP, in a separate process (and often a separate
container) — this worker process's `torch.cuda.mem_get_info()` reports on
*this* process's CUDA context, which says nothing about whatever headroom
Ollama has (or doesn't) for the model it's about to load. A preflight check
here would either check the wrong thing or need an entirely different
mechanism (e.g. asking Ollama's own API), which is out of scope here.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
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
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus, TaskType
from api.core import request_context
from api.schemas.progress import EvaluationProgress, JobCompleted, JobFailed
from ai_engine.data_gen.usage import UsageAccumulator
from api.services import audit_service, model_pricing, usage_service
from workers.celery_app import celery_app
from workers.progress import publish_ws_message, sync_redis_scope
from workers.storage import get_jsonl, get_minio_client, parse_s3_uri
from workers.sync_db import session_scope

log = get_task_logger(__name__)


def _cancelled_frame(job_id: str) -> JobFailed:
    return JobFailed(job_id=job_id, error="job was cancelled", error_type="Cancelled")

# Minimum seconds between `EvaluationProgress(phase="predicting")` frames.
# Publishing once per row would flood the WS the same way `hpo_training.py:184-186`
# suppresses per-step inner training progress across trials ("the WS firehose
# would be too chatty"). The final row always publishes regardless of this gap.
_PREDICT_PROGRESS_THROTTLE_SECONDS: float = 2.0



def _project_id_for_run(session, row):
    """EvaluationRun -> ModelArtifact -> TrainingJob -> Project (3 hops).

    Sync session; mirrors the depth `api/services/job_ownership.py` documents.
    Returns None rather than raising — an audit row with an unresolved project
    is still worth keeping, and this runs inside a terminal-status write that
    must not acquire new failure modes.
    """
    artifact = session.get(ModelArtifact, row.model_artifact_id)
    if artifact is None:
        return None
    training_job = session.get(TrainingJob, artifact.training_job_id)
    return training_job.project_id if training_job is not None else None


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

        # Declared before the `try:` so the `except BaseException` handler can
        # read it. Evaluation uploads nothing — it only reads the dataset — so
        # unlike the other four tasks there is no orphaned artifact to clean
        # up here. What this flag protects is the run's terminal state: a
        # failure after the COMPLETED commit (a broken log handler, or a
        # cancel's SIGTERM arriving as `SystemExit` in that window) must not
        # rewrite a finished evaluation as failed, nor publish `JobFailed`
        # after `JobCompleted`.
        committed = False

        # Second flag, same shape as `data_generation.py`'s: "this run's
        # OpenRouter spend has already been written to `usage_events`".
        # Without it the failure handler would bill a cancelled-after-commit
        # run twice, and a double row inflates the actor's monthly spend and
        # trips their budget cap early.
        usage_recorded = False

        # Resolve the judge model here, not just inside `_apply_llm_judge`,
        # because its price has to be in the map before the first call.
        # Same hexagonal constraint as SDG: `ai_engine` must never learn
        # about `api.core.config`, so pricing is looked up here and handed
        # in. A model absent from the map is simply unpriced, and the
        # accumulator's `has_unpriced_usage` is how that surfaces — which
        # also covers OpenRouter serving a different concrete model than
        # the one requested.
        actor_id = request_context.current_user_id()
        judge_model_resolved_for_pricing = judge_model or settings.llm_judge_model
        prices: dict[str, tuple[float, float]] = {}
        judge_price = model_pricing.price_for(judge_model_resolved_for_pricing)
        if judge_price is not None:
            prices[judge_model_resolved_for_pricing] = judge_price

        with session_scope() as session:
            budget_remaining_usd = usage_service.remaining_budget_usd_sync(
                session, actor_id=actor_id, settings=settings
            )

        # Built before `try:` and passed INTO the judge rather than returned
        # from it — a judge pass that raises at row 400 of 500 never returns
        # a result, but those 400 rows were paid for and must still be
        # billed. Until 2026-08-08 evaluation spent OpenRouter money that was
        # counted nowhere and protected by no breaker; this is that hole.
        usage = UsageAccumulator(
            prices=prices, budget_remaining_usd=budget_remaining_usd
        )

        try:
            # ---- 1. Load context --------------------------------------------
            with session_scope() as session:
                ev = session.get(EvaluationRun, eval_uuid)
                if ev is None:
                    raise RuntimeError(f"EvaluationRun {evaluation_id} not found")

                if ev.status == JobStatus.CANCELLED:
                    # Row was already CANCELLED at context-load time (the
                    # cancel endpoint won the race before this task ever
                    # picked up work) — no RUNNING flip happens, and none of
                    # the artifact/dataset validation below runs either: a
                    # cancelled zombie whose artifact was since deleted must
                    # still exit cleanly, not FAILED.
                    cancelled_at_start = True
                else:
                    cancelled_at_start = False
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

            if cancelled_at_start:
                # Nothing was spent on this path — no usage row to record.
                try:
                    publish(_cancelled_frame(job_id))
                except Exception:  # noqa: BLE001
                    log.warning(
                        "eval: job=%s failed to publish cancelled-at-start frame",
                        job_id,
                        exc_info=True,
                    )
                return {"status": "cancelled", "evaluation_id": evaluation_id}

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
            last_publish_ts = 0.0

            def on_predict_progress(rows_done: int, rows_total: int) -> None:
                nonlocal last_publish_ts
                now = time.monotonic()
                is_last = rows_done >= rows_total
                if not is_last and (now - last_publish_ts) < _PREDICT_PROGRESS_THROTTLE_SECONDS:
                    return
                last_publish_ts = now
                try:
                    publish(
                        EvaluationProgress(
                            job_id=job_id,
                            phase="predicting",
                            rows_done=rows_done,
                            rows_total=rows_total,
                        )
                    )
                except Exception:  # noqa: BLE001
                    log.warning(
                        "eval: job=%s failed to publish predicting progress",
                        job_id,
                        exc_info=True,
                    )

            predicted, expected, questions = _predict_rows(
                rows=rows,
                task_type=task_type,
                tool_definitions=tool_definitions,
                ollama_base_url=ollama_base,
                ollama_tag=ollama_tag,
                progress_cb=on_predict_progress,
            )

            # ---- 4. Per-task metrics ---------------------------------------
            # (No intermediate "scoring" frame: `_compute_metrics_for_task` is a
            # pure in-memory sklearn/string computation over already-collected
            # predictions — no I/O, not a meaningful progress checkpoint.)
            metrics = _compute_metrics_for_task(
                task_type=task_type,
                predicted=predicted,
                expected=expected,
                classification_labels=classification_labels,
            )

            # ---- 5. Optional LLM judge -------------------------------------
            if use_llm_judge and task_type in (TaskType.QA, TaskType.TOOL_CALLING):
                try:
                    publish(
                        EvaluationProgress(
                            job_id=job_id,
                            phase="judging",
                            rows_done=0,
                            rows_total=len(questions),
                        )
                    )
                except Exception:  # noqa: BLE001
                    log.warning(
                        "eval: job=%s failed to publish judging progress",
                        job_id,
                        exc_info=True,
                    )

            judge_score, judge_model_resolved = _apply_llm_judge(
                use_llm_judge=use_llm_judge,
                task_type=task_type,
                judge_model=judge_model,
                settings=settings,
                questions=questions,
                expected=expected,
                predicted=predicted,
                metrics=metrics,
                usage=usage,
            )

            # ---- 6. Persist + 7. Publish completion ------------------------
            with session_scope() as session:
                row = session.get(EvaluationRun, eval_uuid)
                if row is None:
                    raise RuntimeError(f"EvaluationRun {evaluation_id} disappeared")
                eval_project_id = _project_id_for_run(session, row)

                if row.status == JobStatus.CANCELLED:
                    # Cancelled while this task was predicting/judging — the
                    # cancel endpoint already wrote CANCELLED, so the
                    # terminal-success writes below (metrics/judge fields/
                    # COMPLETED/ended_at, and the evaluation.completed audit)
                    # must be discarded rather than overwrite it. The judge
                    # may still have spent real OpenRouter money, and the
                    # `except BaseException` handler never runs on this path
                    # (no exception was raised) — bill it here or it is
                    # billed nowhere, which is the whole point of the
                    # usage-on-every-terminal-outcome invariant.
                    cancelled_at_persist = True
                    usage_service.record_run(
                        session,
                        usage.entries(),
                        actor_id=actor_id,
                        project_id=eval_project_id,
                        job_id=job_id,
                        outcome="cancelled",
                        provider="openrouter",
                    )
                    usage_recorded = True
                else:
                    cancelled_at_persist = False
                    row.metrics_json = metrics
                    row.llm_judge_score = judge_score
                    row.llm_judge_model = judge_model_resolved
                    row.status = JobStatus.COMPLETED
                    row.ended_at = datetime.now(timezone.utc)
                    # Same transaction as the status flip, like the audit row
                    # below it: if billing fails the run does not silently
                    # report success having spent unbilled money.
                    usage_service.record_run(
                        session,
                        usage.entries(),
                        actor_id=actor_id,
                        project_id=eval_project_id,
                        job_id=job_id,
                        outcome="completed",
                        provider="openrouter",
                    )
                    usage_recorded = True
                    if usage.has_unpriced_usage:
                        log.warning(
                            "eval usage has unpriced model(s): job=%s evaluation=%s",
                            job_id,
                            evaluation_id,
                        )
                    audit_service.record(
                        session,
                        action="evaluation.completed",
                        resource_type="evaluation",
                        resource_id=str(row.id),
                        project_id=eval_project_id,
                        actor_id=request_context.current_user_id(),
                        request_id=request_context.current_request_id(),
                        metadata={"job_id": job_id, "llm_judge_score": judge_score},
                    )

            # The run is durably terminal (COMPLETED, or discarded in favor
            # of the CANCELLED the cancel endpoint already wrote). From here
            # the handler must not rewrite its terminal state or emit a
            # contradicting frame.
            committed = True

            if cancelled_at_persist:
                try:
                    publish(_cancelled_frame(job_id))
                except Exception:  # noqa: BLE001
                    log.warning(
                        "eval: job=%s failed to publish cancelled frame",
                        job_id,
                        exc_info=True,
                    )
                return {"status": "cancelled", "evaluation_id": evaluation_id}

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

        except BaseException as exc:
            # BaseException, not Exception — same reasoning as
            # `workers/tasks/data_generation.py` and
            # `workers/tasks/model_export.py`: `POST /evaluations/{id}/cancel`
            # revokes with SIGTERM, which billiard turns into a `SystemExit`
            # inside this task body. `except Exception` misses it, so cleanup
            # was skipped and no terminal `JobFailed` frame was ever published
            # for a cancelled evaluation.
            log.exception("evaluation task failed (job=%s)", job_id)
            try:
                with session_scope() as session:
                    row = session.get(EvaluationRun, eval_uuid)
                    # Same guard as the other four tasks: `committed` means
                    # this run already finished and its row says so. The
                    # exception is still re-raised so Celery records the task
                    # failure; it is the row that must stay true.
                    if row is not None and not committed:
                        # The cancel endpoint already set CANCELLED before
                        # revoking; don't overwrite it with FAILED.
                        if row.status != JobStatus.CANCELLED:
                            row.status = JobStatus.FAILED
                        row.ended_at = datetime.now(timezone.utc)
                        row.error_message = (str(exc) or repr(exc))[:4000]
                        audit_service.record(
                            session,
                            action=(
                                "evaluation.cancelled"
                                if row.status == JobStatus.CANCELLED
                                else "evaluation.failed"
                            ),
                            resource_type="evaluation",
                            resource_id=str(row.id),
                            project_id=_project_id_for_run(session, row),
                            outcome="failure",
                            actor_id=request_context.current_user_id(),
                            request_id=request_context.current_request_id(),
                            metadata={"job_id": job_id, "error_type": type(exc).__name__},
                        )
                    # Bill on EVERY terminal outcome, not just success — a run
                    # cancelled after 400 judged rows is precisely what a
                    # budget has to count. Gated on `usage_recorded` so a
                    # failure landing after the COMPLETED commit can't write
                    # the same spend twice. Outside the `not committed` branch
                    # above on purpose: the row's status is already correct in
                    # that case, but the spend may still be unbilled if the
                    # crash happened between the two.
                    if not usage_recorded:
                        usage_service.record_run(
                            session,
                            usage.entries(),
                            actor_id=actor_id,
                            project_id=(
                                _project_id_for_run(session, row)
                                if row is not None
                                else None
                            ),
                            job_id=job_id,
                            outcome=(
                                "cancelled"
                                if (row is not None and row.status == JobStatus.CANCELLED)
                                else "failed"
                            ),
                            provider="openrouter",
                        )
                        usage_recorded = True
            except Exception:  # noqa: BLE001
                log.warning("could not persist FAILED for %s", evaluation_id, exc_info=True)
            # A client that already received `JobCompleted` must never then
            # receive `JobFailed` for the same job_id. A terminal frame is
            # terminal.
            if not committed:
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


_CLASSIFICATION_JUDGE_NOTE = (
    "LLM judge does not apply to classification — "
    "use rule-based metrics (accuracy, f1_macro) instead."
)


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
    usage: Any = None,
) -> tuple[float | None, str | None]:
    """Run optional LLM-as-judge over (questions, expected, predicted).

    Mutates ``metrics`` in-place to add either ``llm_judge_notes`` (for
    classification, where the closed-set rule-based metrics are the right tool)
    or ``llm_judge_skipped_rows`` (for QA/tool-calling actually judged).

    ``usage`` is an ``ai_engine.data_gen.usage.UsageAccumulator`` (typed
    ``Any`` only to keep this module's imports lazy, like the client above).
    It is mutated in place, so the caller can bill whatever was spent even
    when this function raises part-way through — which is the entire point:
    a judge pass cancelled at row 400 of 500 still cost real money.

    Returns ``(score, model)`` — both ``None`` when judge wasn't applied.
    """
    if not use_llm_judge:
        return None, None

    if task_type is TaskType.CLASSIFICATION:
        metrics["llm_judge_notes"] = _CLASSIFICATION_JUDGE_NOTE
        return None, None

    if task_type not in (TaskType.QA, TaskType.TOOL_CALLING):
        return None, None

    from ai_engine.evaluation.llm_judge import judge_rows

    judge_model_resolved = judge_model or settings.llm_judge_model
    client = _build_judge_client(settings, judge_model_resolved)
    jb = judge_rows(
        client=client,
        judge_model=judge_model_resolved,
        questions=questions,
        expected=expected,
        predicted=predicted,
        usage=usage,
    )
    metrics["llm_judge_skipped_rows"] = jb.skipped
    return jb.mean_score, judge_model_resolved


def _build_judge_client(settings: Any, judge_model: str) -> Any:
    """Construct an ``OpenRouterClient`` wired to the configured judge model.

    Centralises the OpenRouter attribution threading (HTTP-Referer, X-Title)
    so the orchestrator stays focused on judge orchestration rather than
    client wiring. Returns ``Any`` to keep the import lazy — pulling
    ``OpenRouterClient`` at module scope would force the openai SDK to load
    even on Celery tasks that never run the judge.

    The three breaker hooks are the same set `data_generation.py` passes,
    and they were missing here until 2026-08-08: evaluation was the one
    OpenRouter caller in the codebase that could keep hammering a provider
    already known to be down, and whose failures counted toward nothing.
    `precheck` fails fast against an open breaker; `on_call_failure` is what
    trips it; `record_success` is what lets it close again (round 2 shipped
    that hook dead — see the circuit-breaker row in TASK_TRACKER.md — so
    wiring all three, not two, is deliberate).
    """
    from ai_engine.data_gen.openrouter_client import OpenRouterClient

    from api.services import circuit_breaker

    return OpenRouterClient(
        api_key=settings.openrouter_api_key,
        teacher_model=judge_model,
        http_referer=settings.openrouter_http_referer,
        app_title=settings.openrouter_app_title,
        precheck=circuit_breaker.precheck,
        on_call_failure=circuit_breaker.on_failure,
        on_call_success=circuit_breaker.record_success,
    )


# ---- prediction loop -------------------------------------------------------


def _predict_rows(
    *,
    rows: list[dict[str, Any]],
    task_type: TaskType,
    tool_definitions: list | None,
    ollama_base_url: str,
    ollama_tag: str,
    progress_cb: Callable[[int, int], None] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Run inference once per row. Returns parallel `(predicted, expected, questions)`.

    For classification the `question` is the input `text`. For tool_calling /
    qa the row's `question` field is used directly. The expected answer is
    taken from the row's gold-label field.

    ``progress_cb``, if given, is invoked as ``(rows_done, rows_total)`` after
    each row completes. It's optional and keyword-only so existing callers
    (and tests) that don't pass it keep working unchanged.
    """
    predicted: list[str] = []
    expected: list[str] = []
    questions: list[str] = []
    rows_total = len(rows)
    timeout = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0)

    with httpx.Client(timeout=timeout) as client:
        for idx, row in enumerate(rows):
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

            if progress_cb is not None:
                progress_cb(idx + 1, rows_total)

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
