"""Celery application factory.

Imported by both the API process (which only enqueues tasks) and the worker
process (which executes them). Task modules are pulled in via `include` so
the worker discovers them at startup without circular imports.
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.signals import setup_logging

from api.core.config import get_settings

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
    worker_max_tasks_per_child=1,          # release CUDA memory between tasks
    timezone="UTC",
    enable_utc=True,
)


@setup_logging.connect
def _configure_celery_logging(*_args: object, **_kwargs: object) -> None:
    """Honor LOG_LEVEL from settings; let `basicConfig` install a single handler."""
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


__all__ = ["celery_app"]
