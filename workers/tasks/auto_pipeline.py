"""Celery tasks: the auto-export -> auto-evaluate pipeline (W2-T1).

Triggered from the success-path hooks in `workers/tasks/training.py` and
`workers/tasks/hpo_training.py`, right after a `TrainingJob` row commits
COMPLETED and its `ModelArtifact` is durably persisted. Nothing here runs
unless `TrainingJob.auto_export` is true on that row.

Chain design
------------
`enqueue_auto_pipeline(...)` is a **plain function**, not itself a Celery
task — it runs synchronously, inline, in the training/HPO task's own process,
right after that task's own terminal DB write. It:

  1. No-ops (touches nothing) if `TrainingJob.auto_export` is false. An
     untouched `auto_pipeline` column (stays NULL) is how the API tells "no
     auto-pipeline was ever requested" apart from "one was requested and
     hasn't started yet".
  2. Otherwise seeds `TrainingJob.auto_pipeline` with the initial per-stage
     state (export=running, evaluate=pending-or-skipped depending on
     `auto_evaluate`) and enqueues the EXISTING `model.export` task
     (`workers.tasks.model_export.export_model`) — imported and enqueued
     only, never edited, per the module boundary this file must respect.

The "then run X after export succeeds" half is built with Celery's `link=`/
`link_error=` signature options — the same primitive `celery.canvas.chain()`
is sugar over — passed straight to `export_model.si(...).apply_async(...)`
rather than via `chain(...)`, so the two branches (auto_evaluate True/False)
and the distinct error path can each get their own plain kwargs without
fighting `chain()`'s "same errback for every member" semantics:

  * `link=` (success only) points at `auto_evaluate` (this module) when
    `auto_evaluate=True`, or `finalize_export_only` (this module) when it's
    False (export-only mode). Both are `.si(...)` — an **immutable**
    signature, deliberately: `export_model`'s return value is not needed by
    either (they already have `artifact_id` from the enqueue call), and
    immutability is what stops Celery from appending it as a surprise extra
    positional argument.
  * `link_error=` (failure only) points at `mark_export_failed`, a
    **mutable** `.s(...)` signature (not `.si()`) on purpose: Celery invokes
    an errback with the id of the task that raised as its sole positional
    argument (see Celery's own `error_handler(uuid)` example in the canvas
    docs) — `.si()` would discard that id instead of accepting it, and the
    handler needs it to look up *why* the export failed via
    `AsyncResult(id).result`.
  * Celery guarantees `link=` and `link_error=` are mutually exclusive per
    invocation — a failing `export_model` never fires `link=`, so
    `auto_evaluate` is provably never enqueued when export fails. That
    guarantee is what makes "export failure blocks evaluate" true without
    this module having to re-implement it.

The same `link=`/`link_error=` shape is used a second time, inside
`auto_evaluate` itself, around the EXISTING `evaluation.run` task
(`workers.tasks.evaluation.run_evaluation`) — again imported/enqueued only.
There the `link=` callback (`mark_evaluate_completed`) is deliberately
**mutable** (`.s()`, keeping the linked result): `run_evaluation` treats a
mid-run cancel as a *normal return* (`{"status": "cancelled", ...}`), not an
exception, so the only way to tell "genuinely COMPLETED" apart from
"returned normally after being cancelled" is to inspect that return value.

Why not import `api/services/evaluation_service.submit_evaluation_job`:
that module is API-only — it imports `fastapi.HTTPException` and takes an
`AsyncSession` — neither belongs in a worker process (see
`tests/unit/test_worker_import_surface.py`'s PyJWT/prometheus_client
import-hook guards, which this module is added to). `auto_evaluate` below
replicates its ownership-free subset of the same decisions sync-side
instead: create the `EvaluationRun` row, enqueue `evaluation.run`, persist
`celery_task_id`, write the same `evaluation.submit` audit action.

Stage bookkeeping
------------------
`TrainingJob.auto_pipeline` (JSONB, documented on the model) is always
reassigned as a brand-new dict, never mutated in place — SQLAlchemy only
detects attribute *replacement* on JSONB columns, not in-place dict
mutation, without an explicit `flag_modified()` call. `_update_pipeline_stage`
centralizes that read-modify-write so every transition below goes through
one path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from celery.result import AsyncResult
from celery.utils.log import get_task_logger
from sqlalchemy import select

from api.core import request_context
from api.core.config import get_settings
from api.models.dataset import Dataset
from api.models.evaluation_run import EvaluationRun
from api.models.model_artifact import ModelArtifact
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import JobStatus, TaskType
from api.services import audit_service
from workers.celery_app import celery_app
from workers.sync_db import session_scope

log = get_task_logger(__name__)

# Fixed per W2-T1: auto-pipeline always exports GGUF at this quantization.
# A user who wants a different format/quant uses the manual
# `POST /models/{id}/export` endpoint instead.
_EXPORT_FORMAT = "gguf"
_EXPORT_QUANTIZATION = "q4_k_m"


# ---- auto_pipeline JSONB helpers -------------------------------------------


def _initial_pipeline_state(*, artifact_id: str, want_evaluate: bool) -> dict[str, Any]:
    return {
        "export": {"status": "running", "artifact_id": artifact_id, "error": None},
        "evaluate": (
            {"status": "pending", "evaluation_id": None, "skip_reason": None, "error": None}
            if want_evaluate
            else {
                "status": "skipped",
                "evaluation_id": None,
                "skip_reason": "auto_evaluate is false",
                "error": None,
            }
        ),
    }


def _update_pipeline_stage(training_uuid: UUID, *, stage: str, **fields: Any) -> None:
    """Read-modify-write one stage sub-dict of `TrainingJob.auto_pipeline`.

    Missing row is a silent no-op (best-effort bookkeeping; the training run
    itself already succeeded or the export/evaluate task already did its own
    real work — a vanished `TrainingJob` row here should never raise out of
    what is, from the caller's perspective, a fire-and-forget status update).
    """
    with session_scope() as session:
        job = session.get(TrainingJob, training_uuid)
        if job is None:
            log.warning("auto_pipeline: TrainingJob %s vanished", training_uuid)
            return
        current = dict(job.auto_pipeline or {})
        stage_current = dict(current.get(stage) or {})
        stage_current.update(fields)
        current[stage] = stage_current
        job.auto_pipeline = current


def _resolve_failure_message(failed_task_id: str) -> str:
    """Best-effort: pull the exception a failed linked task raised, via the
    result backend. Never raises — an errback that itself blows up must not
    mask the failure it exists to record.
    """
    try:
        result = AsyncResult(failed_task_id, app=celery_app)
        exc = result.result  # already FAILURE state; `.result` holds the exception
        if exc is not None:
            return (str(exc) or repr(exc))[:4000]
    except Exception:  # noqa: BLE001 — best-effort
        log.warning(
            "auto_pipeline: could not resolve failure reason for task %s",
            failed_task_id,
            exc_info=True,
        )
    return "task failed (see worker logs for the underlying task id)"


# ---- entry point, called synchronously from training.py / hpo_training.py -


def enqueue_auto_pipeline(*, training_id: str, artifact_id: str) -> None:
    """Kick off the auto-export/auto-evaluate pipeline for one just-completed
    training run.

    No-ops unless `TrainingJob.auto_export` is true. Safe to call from a
    success path that has already committed the training's own terminal
    state (COMPLETED) — this only ever reads `auto_export`/`auto_evaluate`
    fresh and does not touch `TrainingJob.status` at all.
    """
    training_uuid = UUID(training_id)
    with session_scope() as session:
        job = session.get(TrainingJob, training_uuid)
        if job is None or not job.auto_export:
            return
        want_evaluate = bool(job.auto_evaluate)
        job.auto_pipeline = _initial_pipeline_state(
            artifact_id=artifact_id, want_evaluate=want_evaluate
        )

    from workers.tasks.model_export import export_model

    if want_evaluate:
        success_sig = auto_evaluate.si(training_id=training_id, artifact_id=artifact_id)
    else:
        success_sig = finalize_export_only.si(training_id=training_id, artifact_id=artifact_id)
    error_sig = mark_export_failed.s(training_id=training_id, artifact_id=artifact_id)

    export_model.si(
        artifact_id=artifact_id,
        format=_EXPORT_FORMAT,
        quantization=_EXPORT_QUANTIZATION,
    ).apply_async(link=success_sig, link_error=error_sig)

    log.info(
        "auto_pipeline: training=%s enqueued export (artifact=%s, auto_evaluate=%s)",
        training_id,
        artifact_id,
        want_evaluate,
    )


# ---- export-leg continuations ----------------------------------------------


@celery_app.task(name="pipeline.finalize_export_only")
def finalize_export_only(*, training_id: str, artifact_id: str) -> dict[str, Any]:
    """Export-only path (`auto_evaluate=False`): flips `auto_pipeline.export`
    to `completed`. Runs as the chain's success continuation once
    `model.export` returns without raising.
    """
    _update_pipeline_stage(UUID(training_id), stage="export", status="completed", error=None)
    return {"status": "completed", "training_id": training_id, "artifact_id": artifact_id}


@celery_app.task(name="pipeline.mark_export_failed")
def mark_export_failed(
    failed_task_id: str, *, training_id: str, artifact_id: str
) -> dict[str, Any]:
    """Errback for the `model.export` leg. `export_model`'s own `except
    BaseException` handler already marked `ModelArtifact.export_status`
    FAILED/CANCELLED with `export_error_message` — this callback's only job
    is to mirror that onto `TrainingJob.auto_pipeline` (which `export_model`
    has no reason to know exists) and make sure the evaluate stage is
    marked skipped rather than left dangling at `pending` forever.
    `auto_evaluate` is provably never enqueued on this path — it's the
    `link=` (success) signature, and this is `link_error=`; Celery does not
    invoke both for the same run.
    """
    training_uuid = UUID(training_id)
    error_message = _resolve_failure_message(failed_task_id)
    _update_pipeline_stage(training_uuid, stage="export", status="failed", error=error_message)
    _update_pipeline_stage(
        training_uuid,
        stage="evaluate",
        status="skipped",
        skip_reason="export failed; see auto_pipeline.export.error",
    )
    log.warning(
        "auto_pipeline: training=%s export failed (task=%s): %s",
        training_id,
        failed_task_id,
        error_message,
    )
    return {"status": "failed", "training_id": training_id, "artifact_id": artifact_id}


# ---- evaluate leg -----------------------------------------------------------


@celery_app.task(name="pipeline.auto_evaluate")
def auto_evaluate(*, training_id: str, artifact_id: str) -> dict[str, Any]:
    """Chain's success continuation once `model.export` completes with
    `auto_evaluate=True`.

    Marks the export stage completed, resolves the training dataset's
    holdout child (if any), creates the `EvaluationRun` row, and enqueues
    the existing `evaluation.run` task with the right `use_llm_judge`
    dispatch for the project's task type.

    Resolution query: `Dataset.parent_dataset_id == TrainingJob.dataset_id
    AND Dataset.generation_metadata['role'] == 'holdout'` — a holdout
    dataset's `parent_dataset_id` points at the *train*-role dataset it was
    split from (see `workers/tasks/data_generation.py`'s SDG-with-holdout
    path), which is exactly the dataset a `TrainingJob` trained on.
    """
    training_uuid = UUID(training_id)
    artifact_uuid = UUID(artifact_id)

    # The chain only reaches here after `model.export` succeeded — record
    # that before touching the evaluate stage below. Deliberately its own
    # `session_scope()` call, sequential with (not nested inside) the one
    # below: every evaluate-stage write happens in that single transaction
    # via direct mutation of the already-loaded `job`, specifically so this
    # task never has two overlapping sessions open on the same row at once.
    _update_pipeline_stage(training_uuid, stage="export", status="completed", error=None)

    result: dict[str, Any] | None = None

    with session_scope() as session:
        job = session.get(TrainingJob, training_uuid)

        def _set_evaluate(**fields: Any) -> None:
            current = dict(job.auto_pipeline or {})
            stage = dict(current.get("evaluate") or {})
            stage.update(fields)
            current["evaluate"] = stage
            job.auto_pipeline = current

        def _skip(reason: str) -> dict[str, Any]:
            _set_evaluate(status="skipped", skip_reason=reason, evaluation_id=None)
            log.info("auto_pipeline: training=%s evaluate skipped: %s", training_id, reason)
            return {"status": "skipped", "training_id": training_id, "skip_reason": reason}

        if job is None:
            result = {
                "status": "skipped",
                "training_id": training_id,
                "skip_reason": f"TrainingJob {training_id} vanished before auto-evaluate ran",
            }
        else:
            holdout = (
                session.execute(
                    select(Dataset).where(
                        Dataset.parent_dataset_id == job.dataset_id,
                        Dataset.generation_metadata["role"].astext == "holdout",
                    )
                )
                .scalars()
                .first()
            )
            artifact = session.get(ModelArtifact, artifact_uuid)

            if holdout is None:
                result = _skip(
                    f"No holdout dataset found for training {training_id} (dataset "
                    f"{job.dataset_id} has no holdout child — SDG was likely run "
                    "without holdout_size, or a seed-upload dataset was used)"
                )
            elif not holdout.storage_uri or holdout.num_samples == 0:
                result = _skip(f"Holdout dataset {holdout.id} has no rows persisted yet")
            elif artifact is None or not artifact.ollama_model_tag:
                result = _skip(
                    f"ModelArtifact {artifact_id} has no ollama_model_tag after export "
                    "— cannot evaluate"
                )
            else:
                project = session.get(Project, job.project_id)
                task_type = project.task_type if project is not None else holdout.task_type
                use_llm_judge = task_type is TaskType.QA
                settings = get_settings()
                judge_model = settings.llm_judge_model if use_llm_judge else None

                _set_evaluate(status="running", skip_reason=None, error=None)

                ev = EvaluationRun(
                    model_artifact_id=artifact.id,
                    dataset_id=holdout.id,
                    status=JobStatus.PENDING,
                )
                session.add(ev)
                session.flush()
                evaluation_id = ev.id

                from workers.tasks.evaluation import run_evaluation

                async_result = run_evaluation.apply_async(
                    kwargs={
                        "evaluation_id": str(evaluation_id),
                        "use_llm_judge": use_llm_judge,
                        "judge_model": judge_model,
                    },
                    link=mark_evaluate_completed.s(training_id=training_id),
                    link_error=mark_evaluate_failed.s(training_id=training_id),
                )
                job_id: str = async_result.id
                ev.celery_task_id = job_id
                ev.started_at = datetime.now(timezone.utc)

                _set_evaluate(evaluation_id=str(evaluation_id))

                audit_service.record(
                    session,
                    action="evaluation.submit",
                    resource_type="evaluation",
                    resource_id=str(evaluation_id),
                    project_id=job.project_id,
                    actor_id=request_context.current_user_id(),
                    request_id=request_context.current_request_id(),
                    metadata={
                        "job_id": job_id,
                        "model_artifact_id": str(artifact.id),
                        "dataset_id": str(holdout.id),
                        "auto_pipeline": True,
                        "use_llm_judge": use_llm_judge,
                    },
                )

                log.info(
                    "auto_pipeline: training=%s enqueued evaluation=%s (use_llm_judge=%s)",
                    training_id,
                    evaluation_id,
                    use_llm_judge,
                )
                result = {
                    "status": "submitted",
                    "training_id": training_id,
                    "evaluation_id": str(evaluation_id),
                }

    assert result is not None  # every branch above sets it
    return result


@celery_app.task(name="pipeline.mark_evaluate_completed")
def mark_evaluate_completed(
    result: dict[str, Any] | None = None, *, training_id: str
) -> dict[str, Any]:
    """Link callback for `evaluation.run`'s success.

    `result` is `evaluation.run`'s own return value — Celery's `link=`
    mechanism prepends it as this task's first positional argument (this
    signature is deliberately mutable, `.s()` not `.si()`, so that value is
    kept rather than discarded). It's read here to tell a genuinely
    COMPLETED run apart from one that returned normally after being
    cancelled mid-flight (`run_evaluation` treats a mid-run cancel as a
    clean `{"status": "cancelled", ...}` return, not an exception, so
    `link_error=` never fires for that case).
    """
    training_uuid = UUID(training_id)
    if isinstance(result, dict) and result.get("status") == "cancelled":
        _update_pipeline_stage(
            training_uuid,
            stage="evaluate",
            status="skipped",
            skip_reason="evaluation was cancelled",
        )
        return {"status": "skipped", "training_id": training_id}
    _update_pipeline_stage(training_uuid, stage="evaluate", status="completed", error=None)
    return {"status": "completed", "training_id": training_id}


@celery_app.task(name="pipeline.mark_evaluate_failed")
def mark_evaluate_failed(failed_task_id: str, *, training_id: str) -> dict[str, Any]:
    """Errback for `evaluation.run`'s failure — same shape as
    `mark_export_failed`, one stage over.
    """
    training_uuid = UUID(training_id)
    error_message = _resolve_failure_message(failed_task_id)
    _update_pipeline_stage(training_uuid, stage="evaluate", status="failed", error=error_message)
    log.warning(
        "auto_pipeline: training=%s evaluate failed (task=%s): %s",
        training_id,
        failed_task_id,
        error_message,
    )
    return {"status": "failed", "training_id": training_id}


__all__ = [
    "enqueue_auto_pipeline",
    "auto_evaluate",
    "finalize_export_only",
    "mark_export_failed",
    "mark_evaluate_completed",
    "mark_evaluate_failed",
]
