"""Enums shared across API contracts and ORM models.

These values are part of the public API contract — frontend and DB rows depend
on the exact string values. Treat additions as breaking changes (write an ADR
and a migration).
"""

from __future__ import annotations

from enum import Enum


class TaskType(str, Enum):
    """The 3 supported fine-tuning task types (ADR-005)."""

    CLASSIFICATION = "classification"
    TOOL_CALLING = "tool_calling"
    QA = "qa"


class TrainingMode(str, Enum):
    """How a training job picks hyperparameters."""

    MANUAL = "manual"  # user supplies all hyperparameters
    HPO = "hpo"  # Optuna searches; final model trained on best params


class SDGMode(str, Enum):
    """How synthetic data generation is bootstrapped."""

    WITH_SEED = "with_seed"  # user provides 10–50 seed examples
    DESCRIPTION_ONLY = "description_only"  # user provides only a task description


class JobStatus(str, Enum):
    """Lifecycle of an async Celery-backed job (SDG / training / evaluation)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DatasetSource(str, Enum):
    """Where a dataset's rows came from."""

    SEED = "seed"  # uploaded by the user
    SDG = "sdg"  # generated via OpenRouter
    MERGED = "merged"  # seed + sdg combined


class ArtifactFormat(str, Enum):
    """Supported model export formats."""

    LORA = "lora"  # PEFT adapter (default training output)
    GGUF = "gguf"  # for Ollama / llama.cpp inference
    SAFETENSORS = "safetensors"  # full merged weights


class WSMessageType(str, Enum):
    """Discriminator for WebSocket job-progress messages."""

    SDG_PROGRESS = "sdg_progress"
    TRAINING_PROGRESS = "training_progress"
    HPO_PROGRESS = "hpo_progress"
    EXPORT_PROGRESS = "export_progress"
    EVALUATION_PROGRESS = "evaluation_progress"
    COMPLETED = "completed"
    FAILED = "failed"


__all__ = [
    "TaskType",
    "TrainingMode",
    "SDGMode",
    "JobStatus",
    "DatasetSource",
    "ArtifactFormat",
    "WSMessageType",
]
