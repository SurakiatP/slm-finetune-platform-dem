"""Celery application factory.

Imported by both the API process (which only enqueues tasks) and the worker
process (which executes them). Task modules are pulled in via `include` so
the worker discovers them at startup without circular imports.
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.signals import (
    before_task_publish,
    setup_logging,
    task_failure,
    task_postrun,
    task_prerun,
)

from api.core import request_context
from api.core.config import get_settings
from api.core.logging_config import configure_logging

settings = get_settings()

celery_app = Celery(
    "slm-platform",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "workers.tasks.data_generation",
        "workers.tasks.training",
        "workers.tasks.hpo_training",
        "workers.tasks.model_export",
        "workers.tasks.evaluation",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_time_limit=3 * 60 * 60,           # hard kill after 3h
    task_soft_time_limit=3 * 60 * 60 - 60, # SoftTimeLimit 1m before hard kill
    worker_prefetch_multiplier=1,          # GPU jobs are heavy; don't prefetch
    # Releases CUDA memory between tasks on the GPU worker — a long-running
    # process holding a fragmented allocator is how you get an OOM on a task
    # that would fit fine in a fresh process. This is a *global* Celery
    # setting, so it also applies to the CPU worker (`-Q cpu`), where it buys
    # nothing (no CUDA context to release) but is harmless — an SDG task is
    # cheap to reload a fresh child process for. Special-casing this per-queue
    # (e.g. via a second `worker_max_tasks_per_child` override scoped to the
    # GPU worker only) would add config-plumbing complexity for zero benefit,
    # since "wasteful but harmless" on the CPU side is an acceptable trade.
    worker_max_tasks_per_child=1,
    # ---- CPU/GPU queue split ------------------------------------------
    #
    # Problem: originally there were no `task_routes` at all and a single
    # `worker` service at `--concurrency=1`, so all five task types
    # (sdg.generate, train.manual, train.hpo, model.export, evaluation.run)
    # serialized into one slot — a long CPU-only SDG run (calls OpenRouter,
    # no GPU involved) would block GPU training for the whole platform.
    #
    # TRAP: `task_routes` keys glob against the task **name** passed to
    # `@celery_app.task(name=...)`, NOT the module path the task function
    # lives in. `workers.tasks.data_generation.*` looks plausible but
    # silently matches nothing, because the registered name is `sdg.generate`
    # — the routes below key on the real names. Every other task
    # (train.manual, train.hpo, model.export, evaluation.run) touches a GPU
    # somewhere in ai_engine/training or ai_engine/hpo, so they fall through
    # to `task_default_queue` rather than needing an explicit route each.
    task_default_queue="gpu",
    task_routes={"sdg.*": {"queue": "cpu"}},
    timezone="UTC",
    enable_utc=True,
)

# ---- Deploy note -------------------------------------------------------
#
# Messages already sitting on the legacy default `celery` queue (from
# before this routing split shipped) will NOT be picked up by a worker
# started with `-Q gpu` (or `-Q cpu`) — Celery only consumes the queues it
# is told to. Either drain the old `celery` queue before deploying this
# change, or start the GPU worker with `-Q gpu,celery` for one release
# cycle so in-flight messages still get processed.


@setup_logging.connect
def _configure_celery_logging(*_args: object, **_kwargs: object) -> None:
    """Install the same JSON formatter + context filter the API uses.

    Sharing `configure_logging` is what makes an API log line and a worker
    log line for the same job comparable at all — same shape, same fields,
    same `request_id`.
    """
    configure_logging(settings.log_level)

    # The worker boots from the same `Settings`, so it is subject to the same
    # production credential checks — and it is the process that actually calls
    # OpenRouter, which makes the missing-API-key warning more urgent here than
    # in the API. Emitted after `configure_logging` for the reason documented
    # in `api/main.py`'s lifespan.
    for warning in settings.startup_warnings():
        logging.getLogger("workers").warning("config: %s", warning)


# ---- Request-context propagation -------------------------------------------
#
# A job starts in the API (someone clicked Launch) and finishes in a worker
# minutes or hours later. Without carrying the request id across that gap,
# "why did this training fail" means correlating by timestamp across two
# processes.
#
# The context rides in the message **headers**, not the task kwargs, on
# purpose: kwargs are part of each task's signature, and threading an extra
# argument through all five task bodies would be a breaking change to code
# that has nothing to do with logging. Headers are transport metadata, which
# is exactly what this is.


@before_task_publish.connect
def _inject_request_context(headers: dict | None = None, **_kwargs: object) -> None:
    """API side: stamp the current request's identity onto the message."""
    if headers is None:
        return
    request_id = request_context.current_request_id()
    if request_id:
        headers["x_request_id"] = request_id
    user_id = request_context.current_user_id()
    if user_id:
        headers["x_user_id"] = user_id


@task_prerun.connect
def _bind_request_context(task_id: str | None = None, task: object = None, **_kwargs: object) -> None:
    """Worker side: rebind it, so every `log.*` in the task body inherits it.

    `job_id` is bound from the Celery task id, which is the same value the
    client holds as `job_id` and subscribes to on `/ws/jobs/{job_id}` — so a
    user reporting "job X is stuck" can be grepped directly.
    """
    # Celery exposes custom message headers as attributes on `task.request`
    # (a `Context`), not as a plain dict — `getattr` is the supported read,
    # and it also degrades to None on a task published before this shipped.
    ctx = getattr(task, "request", None)
    request_context.set_request_id(getattr(ctx, "x_request_id", None))
    request_context.set_user_id(getattr(ctx, "x_user_id", None))
    request_context.set_job_id(task_id)
    logging.getLogger("workers.task").info(
        "task start: %s", getattr(task, "name", "?"), extra={"celery_task": getattr(task, "name", None)}
    )


@task_postrun.connect
def _clear_request_context(
    task_id: str | None = None, task: object = None, state: str | None = None, **_kwargs: object
) -> None:
    logging.getLogger("workers.task").info(
        "task end: %s (%s)", getattr(task, "name", "?"), state, extra={"celery_state": state}
    )
    request_context.clear()


@task_failure.connect
def _log_task_failure(
    task_id: str | None = None, exception: BaseException | None = None, **_kwargs: object
) -> None:
    logging.getLogger("workers.task").error(
        "task failed: %s", type(exception).__name__ if exception else "?",
        extra={"error_type": type(exception).__name__ if exception else None},
    )


__all__ = ["celery_app"]
