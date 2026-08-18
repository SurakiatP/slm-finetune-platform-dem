"""Celery task: generate a synthetic dataset and persist it (Phase 9 flow).

The Celery task body itself stays sync — Celery's prefork pool handles
that natively. Inside the task, `asyncio.run(...)` is the boundary for
the new `SyntheticDataGenerator.generate(...)` async loop.

Public progress is published to the Redis channel `job:{celery_task_id}`.
The WebSocket endpoint subscribes there.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from celery.utils.log import get_task_logger
from pydantic import TypeAdapter

from ai_engine.data_gen import models as sdg_models
from ai_engine.data_gen.generator import (
    GenerationProgress,
    SyntheticDataGenerator,
)
from ai_engine.data_gen.openrouter_client import (
    AsyncOpenRouterClient,
    OpenRouterClient,
)
from ai_engine.data_gen.usage import UsageAccumulator
from api.core.config import get_settings
from api.models.dataset import Dataset
from api.schemas.enums import DatasetSource, JobStatus
from api.core import request_context
from api.schemas.progress import JobCompleted, JobFailed, SDGProgress
from api.services import audit_service, circuit_breaker, model_pricing, usage_service
from api.schemas.sdg import (
    SDGRequest,
    SDGRequestDescriptionOnly,
    SDGRequestWithSeed,
)
from workers.celery_app import celery_app
from workers.progress import publish_ws_message, sync_redis_scope
from workers.storage import (
    get_jsonl,
    get_minio_client,
    parse_s3_uri,
    put_jsonl,
    remove_object,
    s3_uri,
)
from workers.sync_db import session_scope

log = get_task_logger(__name__)
_sdg_adapter: TypeAdapter[SDGRequestWithSeed | SDGRequestDescriptionOnly] = TypeAdapter(SDGRequest)


@celery_app.task(bind=True, name="sdg.generate", max_retries=0)
def generate_synthetic_data(
    self,
    *,
    request_payload: dict[str, Any],
    dataset_id: str,
) -> dict[str, Any]:
    """Run one SDG job (Phase 9). Async generator + quota + sentinel + judge.

    Args:
        request_payload: serialized `SDGRequest` (mode-discriminated).
        dataset_id: pre-created (parent) dataset row id (UUID string).
    """
    job_id: str = self.request.id
    settings = get_settings()
    request = _sdg_adapter.validate_python(request_payload)
    parent_uuid = UUID(dataset_id)
    holdout_size = request.holdout_size
    effective_target = request.num_samples + holdout_size
    actor_id = request_context.current_user_id()

    # Prices resolved here, as plain floats, and handed to the accumulator —
    # `ai_engine` must never learn about `api.core.config` (hexagonal rule).
    # A model missing from the pricing map is simply omitted; the
    # accumulator's `has_unpriced_usage` is how the gap gets surfaced.
    prices: dict[str, tuple[float, float]] = {}
    for model_id in {
        sdg_models.DIVERSITY_RULES,
        sdg_models.GENERATOR,
        sdg_models.JUDGE,
        sdg_models.PDF_QA,
        sdg_models.FORMAT_DETECTION,
    }:
        price = model_pricing.price_for(model_id)
        if price is not None:
            prices[model_id] = price

    # Constructed BEFORE the `try:` block below — and this is load-bearing,
    # not incidental: `usage` must still be readable inside the `except
    # BaseException` handler further down, because the accumulator is
    # passed INTO `generate()` rather than returned on `SDGRunResult` — a
    # run that raises never returns a result, and that handler must still
    # be able to read what was spent in order to bill it.
    budget_remaining_usd: float | None = None
    cancelled_before_start = False
    with session_scope() as session:
        ds = session.get(Dataset, parent_uuid)
        cancelled_before_start = ds is not None and ds.status == JobStatus.CANCELLED
        if not cancelled_before_start:
            if ds is not None:
                ds.status = JobStatus.RUNNING

            # Tighter of the per-actor and global remaining budget, computed
            # once at task start — not re-checked against the DB again mid-run;
            # `UsageAccumulator.check_budget()` enforces the ceiling against
            # this fixed number as tokens accumulate. `None` when both caps are
            # unset (unlimited). Shared with the evaluation task, which needs
            # the identical calculation for its LLM judge.
            budget_remaining_usd = usage_service.remaining_budget_usd_sync(
                session, actor_id=actor_id, settings=settings
            )

    if cancelled_before_start:
        # Cancelled while still queued — no worker ever flipped it to
        # RUNNING, so there is no work to do and nothing to undo. The
        # task's own `sync_redis_scope()` doesn't open until further
        # below, so this branch opens its own short-lived one just to
        # announce the terminal frame before returning.
        with sync_redis_scope() as redis:
            try:
                publish_ws_message(redis, job_id, _cancelled_frame(job_id))
            except Exception:  # noqa: BLE001 — announcement only
                log.warning(
                    "failed to publish JobFailed(cancelled) message", exc_info=True
                )
        return {"status": "cancelled", "dataset_id": dataset_id}

    usage = UsageAccumulator(prices=prices, budget_remaining_usd=budget_remaining_usd)
    # Every MinIO key written by this run, appended the instant `put_jsonl`
    # returns — i.e. before the `Dataset` row that references it is ever
    # committed.
    uploaded_keys: list[str] = []

    # The single "this run's terminal commit landed" flag, declared before
    # `try:` so it survives whatever raises and is readable in the
    # `except BaseException` handler. One flag, three jobs — all of them
    # variations on "the DB now says this run finished, so stop treating it
    # as in-flight":
    #   • don't bill the tokens a second time (a duplicate usage row doubles
    #     this actor's monthly spend and trips the budget cap early)
    #   • don't delete `uploaded_keys` — the Dataset row references them now,
    #     and deleting a live dataset would be catastrophic (item 13)
    #   • don't rewrite the terminal status or publish `JobFailed` after
    #     `JobCompleted`
    # The other four task modules carry the same flag under the same name;
    # `tests/unit/test_worker_progress_frames.py` asserts all five have it,
    # because the recurring defect here has never been "the logic is wrong",
    # it has been "one of the five files was missed".
    committed = False

    with sync_redis_scope() as redis:

        def emit_progress(p: GenerationProgress) -> None:
            publish_ws_message(
                redis,
                job_id,
                SDGProgress(
                    job_id=job_id,
                    phase=p.phase,
                    samples_generated=p.samples_generated,
                    samples_target=p.samples_target,
                    samples_valid=p.samples_valid,
                    samples_rejected=p.samples_rejected,
                    duplicates_removed=p.duplicates_removed,
                    current_loop=p.current_loop,
                    judge_rejected=p.judge_rejected,
                    judge_parse_failures=p.judge_parse_failures,
                    dedup_rejected=p.dedup_rejected,
                ),
            )

        def emit_endgame_phase(
            phase: str,
            *,
            samples_generated: int,
            samples_target: int,
            samples_valid: int,
            samples_rejected: int,
            duplicates_removed: int,
        ) -> None:
            """Publish one of the three post-generation-loop `SDGProgress`
            frames ("splitting_holdout" / "persisting_train" /
            "persisting_holdout") so the UI can say "splitting train/hold-out"
            and "saving training data / saving hold-out data".

            These aren't `GenerationPhase` values (that Literal lives in
            `ai_engine.data_gen.generator` and is out of scope here), so this
            builds `SDGProgress` directly instead of going through
            `emit_progress`/`GenerationProgress`.

            Wrapped in try/except like the terminal-frame publishes below: a
            Redis hiccup announcing an end-game phase must never fail a run
            whose actual generation work already succeeded.
            """
            try:
                publish_ws_message(
                    redis,
                    job_id,
                    SDGProgress(
                        job_id=job_id,
                        phase=phase,
                        samples_generated=samples_generated,
                        samples_target=samples_target,
                        samples_valid=samples_valid,
                        samples_rejected=samples_rejected,
                        duplicates_removed=duplicates_removed,
                    ),
                )
            except Exception:  # noqa: BLE001 — announcement only
                log.warning(
                    "failed to publish SDGProgress(phase=%s) message (job=%s)",
                    phase,
                    job_id,
                    exc_info=True,
                )

        try:
            log.info(
                "SDG starting: job=%s dataset=%s task=%s mode=%s "
                "train_target=%d holdout=%d effective=%d",
                job_id,
                dataset_id,
                request.task_type.value,
                request.sdg_mode.value,
                request.num_samples,
                holdout_size,
                effective_target,
            )

            seed_rows, pdf_bytes = _load_seed_payload(request, settings)

            result = asyncio.run(
                _run_generator(
                    request=request,
                    effective_target=effective_target,
                    seed_rows=seed_rows,
                    pdf_bytes=pdf_bytes,
                    settings=settings,
                    progress_cb=emit_progress,
                    usage=usage,
                )
            )

            import random as _random

            from ai_engine.data_gen.holdout_split import split_rows

            # Snapshot the final generation numbers once — every end-game
            # frame below (splitting/persisting) reports this same snapshot
            # rather than per-stage counts, so `samples_generated`/
            # `samples_target` stay consistent with the numbers the
            # generation loop actually finished with.
            endgame_counts = {
                "samples_generated": len(result.valid_rows),
                "samples_target": effective_target,
                "samples_valid": len(result.valid_rows),
                "samples_rejected": result.rejected_count,
                "duplicates_removed": result.duplicate_count,
            }

            if holdout_size > 0:
                emit_endgame_phase("splitting_holdout", **endgame_counts)

            rng = _random.Random(_split_seed(request, dataset_id))
            train_rows, holdout_rows = split_rows(
                result.valid_rows,
                request.task_type,
                holdout_size,
                rng=rng,
            )

            emit_endgame_phase("persisting_train", **endgame_counts)
            minio = get_minio_client()
            bucket = settings.minio_datasets_bucket
            train_key = f"sdg/{dataset_id}.jsonl"
            train_size = put_jsonl(minio, bucket, train_key, train_rows)
            # Uploaded, but no DB row references `train_key` yet — record it
            # so a cancel/failure before the commit below can clean it up.
            uploaded_keys.append(train_key)
            train_uri = s3_uri(bucket, train_key)

            holdout_uuid: UUID | None = None
            holdout_uri: str | None = None
            holdout_size_bytes: int | None = None
            if holdout_rows:
                from uuid import uuid4

                emit_endgame_phase("persisting_holdout", **endgame_counts)

                holdout_uuid = uuid4()
                holdout_key = f"sdg/{holdout_uuid}.jsonl"
                holdout_size_bytes = put_jsonl(
                    minio, bucket, holdout_key, holdout_rows
                )
                uploaded_keys.append(holdout_key)
                holdout_uri = s3_uri(bucket, holdout_key)

            discarded_as_cancelled = False
            with session_scope() as session:
                parent = session.get(Dataset, parent_uuid)
                if parent is None:
                    raise RuntimeError(
                        f"Dataset {dataset_id} disappeared mid-generation"
                    )
                if parent.status == JobStatus.CANCELLED:
                    # Cancelled after the start-check above already passed:
                    # the cancel endpoint flipped this row to CANCELLED and
                    # revoked the task, but the SIGTERM-turned-SystemExit
                    # lost the race with this commit window. Discard every
                    # write below — no COMPLETED flip, no sdg.completed
                    # audit, no metadata update, no holdout Dataset row —
                    # CANCELLED is the accurate terminal state and must not
                    # be clobbered back to COMPLETED. Usage must still be
                    # recorded, since the tokens were burned either way and
                    # the `except BaseException` handler never runs on this
                    # path (we return normally below).
                    discarded_as_cancelled = True
                    usage_service.record_run(
                        session,
                        usage.entries(),
                        actor_id=actor_id,
                        project_id=parent.project_id,
                        job_id=job_id,
                        outcome="cancelled",
                        provider="openrouter",
                    )
                else:
                    parent.num_samples = len(train_rows)
                    parent.storage_uri = train_uri
                    parent.size_bytes = train_size
                    parent.status = JobStatus.COMPLETED
                    audit_service.record(
                        session,
                        action="sdg.completed",
                        resource_type="dataset",
                        resource_id=str(parent.id),
                        project_id=parent.project_id,
                        actor_id=request_context.current_user_id(),
                        request_id=request_context.current_request_id(),
                        metadata={"job_id": job_id, "num_samples": parent.num_samples},
                    )
                    # Usage must be written on every terminal outcome — completed,
                    # failed AND cancelled — because the tokens were burned either
                    # way. This is the success leg; the failure/cancel leg mirrors
                    # it in the `except BaseException` handler below.
                    usage_service.record_run(
                        session,
                        usage.entries(),
                        actor_id=actor_id,
                        project_id=parent.project_id,
                        job_id=job_id,
                        outcome="completed",
                        provider="openrouter",
                    )
                    if usage.has_unpriced_usage:
                        # A model in use is missing from the pricing map, so the
                        # recorded cost is a floor, not a total.
                        log.warning(
                            "SDG usage has unpriced model(s): job=%s dataset=%s",
                            job_id,
                            dataset_id,
                        )
                    parent_meta = dict(parent.generation_metadata or {})
                    parent_meta.update(
                        {
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                            "rejected_count": result.rejected_count,
                            "duplicate_count": result.duplicate_count,
                            "judge_rejected_count": result.judge_rejected_count,
                            "judge_parse_failures": result.judge_parse_failures,
                            "api_calls": result.api_calls,
                            "holdout_size_requested": holdout_size,
                            "holdout_size_actual": len(holdout_rows),
                            "role": "train",
                            "holdout_dataset_id": (
                                str(holdout_uuid) if holdout_uuid else None
                            ),
                        }
                    )
                    parent.generation_metadata = parent_meta

                    if holdout_uuid is not None:
                        child = Dataset(
                            id=holdout_uuid,
                            project_id=parent.project_id,
                            name=request.holdout_name or f"{parent.name}-holdout",
                            task_type=parent.task_type,
                            source=DatasetSource.SDG,
                            num_samples=len(holdout_rows),
                            storage_uri=holdout_uri,
                            size_bytes=holdout_size_bytes,
                            parent_dataset_id=parent.id,
                            status=JobStatus.COMPLETED,
                            generation_metadata={
                                "role": "holdout",
                                "parent_dataset_id": str(parent.id),
                                "sdg_mode": request.sdg_mode.value,
                                "task_description": request.task_description,
                                "completed_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        session.add(child)

            if discarded_as_cancelled:
                # The commit above never durably referenced `uploaded_keys`
                # (the DB row stayed CANCELLED, not COMPLETED), so they are
                # orphans exactly like the `except BaseException` handler's
                # discard case — same cleanup shape, mirrored here.
                committed = True
                try:
                    cleanup_minio = get_minio_client()
                    cleanup_bucket = settings.minio_datasets_bucket
                    for key in uploaded_keys:
                        try:
                            remove_object(cleanup_minio, cleanup_bucket, key)
                        except Exception:  # noqa: BLE001 — best-effort, never mask original outcome
                            log.warning(
                                "failed to remove orphaned SDG object %s/%s (job=%s)",
                                cleanup_bucket,
                                key,
                                job_id,
                                exc_info=True,
                            )
                except Exception:  # noqa: BLE001 — best-effort, never mask original outcome
                    log.warning(
                        "could not obtain MinIO client for SDG orphan cleanup (job=%s)",
                        job_id,
                        exc_info=True,
                    )

                try:
                    publish_ws_message(redis, job_id, _cancelled_frame(job_id))
                except Exception:  # noqa: BLE001 — announcement only
                    log.warning(
                        "failed to publish JobFailed(cancelled) message", exc_info=True
                    )
                return {"status": "cancelled", "dataset_id": dataset_id}

            # The `Dataset` row(s) above just committed, durably referencing
            # every key in `uploaded_keys` (`train_uri` on `parent`, and
            # `holdout_uri` on `child` if a holdout was requested). From this
            # point on the objects are no longer orphans, so the `except
            # BaseException` handler's cleanup must never fire for them —
            # deleting a live, DB-referenced dataset would be catastrophic.
            committed = True

            # Wrapped for the same reason its `JobFailed` twin is: the work is
            # done and durably committed, so a Redis hiccup while announcing it
            # must not unwind a completed run into a FAILED one.
            try:
                publish_ws_message(
                    redis,
                    job_id,
                    JobCompleted(
                        job_id=job_id,
                        result={
                            "samples_generated": len(train_rows),
                            "holdout_samples": len(holdout_rows),
                            "rejected_count": result.rejected_count,
                            "duplicate_count": result.duplicate_count,
                            "judge_rejected_count": result.judge_rejected_count,
                            "judge_parse_failures": result.judge_parse_failures,
                            "api_calls": result.api_calls,
                            "storage_uri": train_uri,
                            "holdout_storage_uri": holdout_uri,
                            "holdout_dataset_id": (
                                str(holdout_uuid) if holdout_uuid else None
                            ),
                            "size_bytes": train_size,
                        },
                        dataset_id=parent_uuid,
                    ),
                )
            except Exception:  # noqa: BLE001 — announcement only, work is done
                log.warning("failed to publish JobCompleted message", exc_info=True)

            log.info(
                "SDG done: job=%s parent=%s holdout=%s train=%d holdout=%d "
                "rejected=%d dup=%d calls=%d",
                job_id,
                dataset_id,
                holdout_uuid,
                len(train_rows),
                len(holdout_rows),
                result.rejected_count,
                result.duplicate_count,
                result.api_calls,
            )

            return {
                "status": "completed",
                "dataset_id": dataset_id,
                "holdout_dataset_id": (
                    str(holdout_uuid) if holdout_uuid else None
                ),
                "samples_generated": len(train_rows),
                "holdout_samples": len(holdout_rows),
                "rejected_count": result.rejected_count,
                "duplicate_count": result.duplicate_count,
                "judge_rejected_count": result.judge_rejected_count,
                "storage_uri": train_uri,
                "holdout_storage_uri": holdout_uri,
            }

        except BaseException as exc:
            # BaseException, not Exception: `POST /datasets/{id}/cancel` revokes
            # this task with `celery_app.control.revoke(terminate=True,
            # signal="SIGTERM")`. Billiard's worker-child signal handler turns
            # that SIGTERM into `sys.exit(...)` — a `SystemExit` raised inside
            # this task body, which `except Exception` does NOT catch. Verified
            # on real hardware: an export cancelled that way surfaces
            # `error_message == "-241"`, i.e. `sys.exit(-(256-15))`.
            # Before this was widened, cancelling SDG skipped this whole block,
            # so no `JobFailed` frame was ever published and a WebSocket-only
            # client waited forever for a terminal frame that never came.
            # `workers/tasks/model_export.py` carries the same treatment.
            log.exception("SDG task failed (job=%s)", job_id)
            try:
                with session_scope() as session:
                    ds = session.get(Dataset, parent_uuid)
                    # `committed` means the success leg already committed:
                    # the JSONL is in MinIO and the row is durably COMPLETED.
                    # Anything raising after that point — a broken log handler,
                    # or a cancel's SIGTERM landing in this window and arriving
                    # as `SystemExit` — must NOT rewrite that terminal state.
                    # Doing so reports a run that finished as FAILED and emits
                    # a `JobFailed` frame after `JobCompleted`, leaving the
                    # WebSocket stream contradicting itself. The exception is
                    # still re-raised below, so Celery records the task as
                    # failed; it is the *dataset's* state that must stay true.
                    if ds is not None and not committed:
                        # The cancel endpoint sets status=CANCELLED *before*
                        # revoking. Don't clobber it back to FAILED — CANCELLED
                        # is the accurate terminal state for that run. The
                        # error message is still recorded either way.
                        if ds.status != JobStatus.CANCELLED:
                            ds.status = JobStatus.FAILED
                        ds.error_message = (str(exc) or repr(exc))[:4000]
                        audit_service.record(
                            session,
                            action=(
                                "sdg.cancelled"
                                if ds.status == JobStatus.CANCELLED
                                else "sdg.failed"
                            ),
                            resource_type="dataset",
                            resource_id=str(ds.id),
                            project_id=ds.project_id,
                            outcome="failure",
                            actor_id=request_context.current_user_id(),
                            request_id=request_context.current_request_id(),
                            metadata={"job_id": job_id, "error_type": type(exc).__name__},
                        )
                    # Usage must be written on every terminal outcome —
                    # completed, failed AND cancelled — because the tokens were
                    # burned either way. A run cancelled after 1800 calls is
                    # precisely the case a budget must count.
                    #
                    # Deliberately OUTSIDE the `if ds is not None` above: a run
                    # whose Dataset row was deleted mid-flight still spent real
                    # OpenRouter dollars, and `usage_events.project_id` is
                    # `ON DELETE SET NULL` for exactly this reason — billing
                    # history outlives the row it refers to. When the row is
                    # gone we simply do not know the project, so it is NULL.
                    #
                    # Skipped entirely when the success leg already committed
                    # its rows: without that guard, a failure *after* the
                    # commit (e.g. the terminal WS publish) bills the same
                    # tokens a second time and inflates monthly spend 2x.
                    #
                    # Rides this same session/transaction, so a usage-write
                    # failure can never mask the original SDG failure — it is
                    # still inside the enclosing `except Exception`.
                    if not committed:
                        usage_service.record_run(
                            session,
                            usage.entries(),
                            actor_id=request_context.current_user_id(),
                            project_id=ds.project_id if ds is not None else None,
                            job_id=job_id,
                            outcome=(
                                "cancelled"
                                if ds is not None and ds.status == JobStatus.CANCELLED
                                else "failed"
                            ),
                            provider="openrouter",
                        )
                        if usage.has_unpriced_usage:
                            log.warning(
                                "SDG usage has unpriced model(s): job=%s dataset=%s",
                                job_id,
                                dataset_id,
                            )

            except Exception:  # noqa: BLE001 — never mask the original SDG failure
                log.warning(
                    "could not persist FAILED status for dataset %s", dataset_id, exc_info=True
                )

            # Orphan cleanup (gap-analysis item 13): `committed` False means
            # the run reached this handler before the Dataset row(s) ever
            # came to reference `uploaded_keys` — i.e. this cancel/failure
            # landed in the upload-then-commit window, and the JSONL(s)
            # sitting in MinIO are unreachable from the DB. Delete them.
            # Gated on the exact same flag/reasoning as the status write
            # above — see its comment. Every delete is individually
            # try/excepted so a MinIO hiccup during cleanup can never mask
            # the original SDG failure being handled here, and a fresh
            # client is fetched rather than reusing any `minio` local from
            # the `try` block, since a failure early enough (e.g. inside
            # `_run_generator`) means that local was never assigned.
            #
            # Known limitation: this only runs when the handler runs at
            # all. A SIGKILL (as opposed to the SIGTERM the cancel endpoint
            # sends) runs no Python code here and still leaks the object —
            # closing that gap needs a separate sweeper, which was
            # deliberately not built: a sweeper that lists buckets and
            # joins against the DB can race a job that is mid-upload and
            # delete a live object.
            if not committed and uploaded_keys:
                try:
                    cleanup_minio = get_minio_client()
                    cleanup_bucket = settings.minio_datasets_bucket
                    for key in uploaded_keys:
                        try:
                            remove_object(cleanup_minio, cleanup_bucket, key)
                        except Exception:  # noqa: BLE001 — best-effort, never mask original failure
                            log.warning(
                                "failed to remove orphaned SDG object %s/%s (job=%s)",
                                cleanup_bucket,
                                key,
                                job_id,
                                exc_info=True,
                            )
                except Exception:  # noqa: BLE001 — best-effort, never mask original failure
                    log.warning(
                        "could not obtain MinIO client for SDG orphan cleanup (job=%s)",
                        job_id,
                        exc_info=True,
                    )

            # Same guard as the status write above: a client that already
            # received `JobCompleted` must never then receive `JobFailed` for
            # the same job_id. A terminal frame is terminal.
            if not committed:
                try:
                    publish_ws_message(
                        redis,
                        job_id,
                        JobFailed(
                            job_id=job_id,
                            error=str(exc) or repr(exc),
                            error_type=type(exc).__name__,
                        ),
                    )
                except Exception:  # noqa: BLE001
                    log.warning("failed to publish JobFailed message", exc_info=True)
            raise


# ---- helpers --------------------------------------------------------------


def _cancelled_frame(job_id: str) -> JobFailed:
    return JobFailed(job_id=job_id, error="job was cancelled", error_type="Cancelled")


async def _run_generator(
    *,
    request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
    effective_target: int,
    seed_rows: list[dict[str, Any]],
    pdf_bytes: bytes | None,
    settings,
    progress_cb,
    usage: UsageAccumulator | None = None,
):
    """Set up async + sync clients, run the generator with an explicit target.

    `effective_target = num_samples + holdout_size` when holdout is requested;
    otherwise equals num_samples.
    """
    effective_request = request.model_copy(update={"num_samples": effective_target})
    # `precheck`/`on_call_failure` wire both clients into the shared Redis
    # circuit breaker: `precheck()` fails fast (raises `CircuitOpenError`)
    # instead of grinding through tenacity retries against a provider
    # that's already known to be down, and `on_failure()` is what counts a
    # genuinely outage-shaped failure toward tripping the breaker for every
    # other in-flight/future SDG job.
    sync_client = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        teacher_model="placeholder/unused",
        http_referer=settings.openrouter_http_referer,
        app_title=settings.openrouter_app_title,
        precheck=circuit_breaker.precheck,
        on_call_failure=circuit_breaker.on_failure,
        on_call_success=circuit_breaker.record_success,
    )
    async with AsyncOpenRouterClient(
        api_key=settings.openrouter_api_key,
        http_referer=settings.openrouter_http_referer,
        app_title=settings.openrouter_app_title,
        precheck=circuit_breaker.precheck,
        on_call_failure=circuit_breaker.on_failure,
        on_call_success=circuit_breaker.record_success,
    ) as async_client:
        gen = SyntheticDataGenerator(async_client, sync_client)
        return await gen.generate(
            effective_request,
            seed_rows=seed_rows,
            pdf_bytes=pdf_bytes,
            progress_cb=progress_cb,
            usage=usage,
        )


def _load_seed_payload(
    request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
    settings,
) -> tuple[list[dict[str, Any]], bytes | None]:
    """For with_seed: fetch canonical JSONL from MinIO (and PDF bytes if any).

    For description_only: returns ([], None).
    """
    if isinstance(request, SDGRequestDescriptionOnly):
        return [], None

    seed_dataset_id = request.seed_dataset_id
    minio = get_minio_client()
    seed_rows: list[dict[str, Any]] = []
    pdf_bytes: bytes | None = None

    with session_scope() as session:
        seed_ds = session.get(Dataset, seed_dataset_id)
        if seed_ds is None:
            raise RuntimeError(
                f"seed dataset {seed_dataset_id} not found in DB"
            )
        if seed_ds.source != DatasetSource.SEED:
            raise RuntimeError(
                f"seed_dataset_id {seed_dataset_id} is source={seed_ds.source.value}, "
                f"expected seed"
            )
        meta = seed_ds.generation_metadata or {}
        pdf_uri = meta.get("pdf_uri")
        # JSONL rows (when present)
        if seed_ds.storage_uri:
            bucket, key = parse_s3_uri(seed_ds.storage_uri)
            seed_rows = get_jsonl(minio, bucket, key)
        # PDF bytes (QA + PDF only)
        if pdf_uri:
            bucket, key = parse_s3_uri(pdf_uri)
            response = minio.get_object(bucket_name=bucket, object_name=key)
            try:
                pdf_bytes = response.read()
            finally:
                response.close()
                response.release_conn()

    return seed_rows, pdf_bytes


def _split_seed(
    request: SDGRequestWithSeed | SDGRequestDescriptionOnly,
    dataset_id: str,
) -> int:
    """Deterministic split seed derived from request + dataset id.

    Keeping this deterministic means re-running the same SDG request against
    the same dataset_id produces the same train/holdout assignment - useful
    if a downstream step crashed and the operator needs to retry.
    """
    raw = f"{dataset_id}|{request.task_type.value}|{request.holdout_size}"
    return abs(hash(raw)) % (2**31 - 1)


__all__ = ["generate_synthetic_data"]
