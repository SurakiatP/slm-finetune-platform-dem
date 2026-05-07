"""Translate `HPOSearchSpace` into Optuna `trial.suggest_*` calls.

The schema (`api.schemas.training.HPOSearchSpace`) is an explicit allow-list
of tunable hyperparameters. This module is the only place that knows how each
one maps to Optuna's API; adding a new tunable means touching the schema AND
the dispatch table here.

Pure domain code — no FastAPI/Celery imports, and Optuna is imported lazily
inside `sample_config` so this module is importable without `[training]`-style
extras (Optuna is in base deps but the type-checker sees it as runtime-only).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from api.schemas.training import (
    HPOCategorical,
    HPOFloatRange,
    HPOIntRange,
    HPOSearchSpace,
    LoRAConfig,
    ManualTrainingConfig,
)

if TYPE_CHECKING:  # pragma: no cover
    from optuna import Trial


@dataclass(frozen=True)
class SampledTrial:
    """Result of mapping one Optuna trial through the search space.

    `config` is a fresh `ManualTrainingConfig` with the sampled overrides applied
    on top of `fixed_config`; `params` is the flat dict you'd want to log to
    MLflow as that trial's hyperparameters.
    """

    config: ManualTrainingConfig
    params: dict[str, Any]


# ---- Per-field samplers ----------------------------------------------------


def _suggest_float(trial: Trial, name: str, spec: HPOFloatRange) -> float:
    return trial.suggest_float(name, spec.low, spec.high, log=spec.log)


def _suggest_int(trial: Trial, name: str, spec: HPOIntRange) -> int:
    return trial.suggest_int(name, spec.low, spec.high, step=spec.step, log=spec.log)


def _suggest_categorical(trial: Trial, name: str, spec: HPOCategorical) -> Any:
    # Optuna requires a sequence of hashable choices.
    return trial.suggest_categorical(name, list(spec.choices))


# ---- Public entrypoint -----------------------------------------------------


def sample_config(
    trial: Trial,
    search_space: HPOSearchSpace,
    fixed_config: ManualTrainingConfig,
) -> SampledTrial:
    """Map one Optuna `trial` through `search_space` to a `ManualTrainingConfig`.

    Fields not present (None) in `search_space` are taken from `fixed_config`.
    Fields present are sampled and override the fixed value.

    The LoRA-specific tunables (`lora_r`, `lora_alpha`, `lora_dropout`) are
    rebuilt into a fresh `LoRAConfig` because Pydantic models are frozen-ish.
    """
    sampled: dict[str, Any] = {}

    if search_space.learning_rate is not None:
        sampled["learning_rate"] = _suggest_float(
            trial, "learning_rate", search_space.learning_rate
        )
    if search_space.num_train_epochs is not None:
        sampled["num_train_epochs"] = _suggest_int(
            trial, "num_train_epochs", search_space.num_train_epochs
        )
    if search_space.per_device_train_batch_size is not None:
        sampled["per_device_train_batch_size"] = _suggest_categorical(
            trial,
            "per_device_train_batch_size",
            search_space.per_device_train_batch_size,
        )
    if search_space.gradient_accumulation_steps is not None:
        sampled["gradient_accumulation_steps"] = _suggest_categorical(
            trial,
            "gradient_accumulation_steps",
            search_space.gradient_accumulation_steps,
        )
    if search_space.warmup_ratio is not None:
        sampled["warmup_ratio"] = _suggest_float(
            trial, "warmup_ratio", search_space.warmup_ratio
        )
    if search_space.weight_decay is not None:
        sampled["weight_decay"] = _suggest_float(
            trial, "weight_decay", search_space.weight_decay
        )
    if search_space.lr_scheduler_type is not None:
        sampled["lr_scheduler_type"] = _suggest_categorical(
            trial, "lr_scheduler_type", search_space.lr_scheduler_type
        )

    # LoRA-side tunables go into a separate dict and are merged below.
    lora_overrides: dict[str, Any] = {}
    if search_space.lora_r is not None:
        lora_overrides["r"] = int(_suggest_categorical(trial, "lora_r", search_space.lora_r))
    if search_space.lora_alpha is not None:
        lora_overrides["alpha"] = int(
            _suggest_categorical(trial, "lora_alpha", search_space.lora_alpha)
        )
    if search_space.lora_dropout is not None:
        lora_overrides["dropout"] = _suggest_float(
            trial, "lora_dropout", search_space.lora_dropout
        )

    # Merge: top-level config overrides
    base = fixed_config.model_dump()
    base.update({k: v for k, v in sampled.items() if k != "lora"})

    # Merge: LoRA overrides on top of the existing LoRA dict
    if lora_overrides:
        merged_lora = dict(base["lora"])
        merged_lora.update(lora_overrides)
        base["lora"] = merged_lora

    new_config = ManualTrainingConfig.model_validate(base)

    # Build flat params dict for MLflow logging — sampled values only,
    # not the whole config (the parent run logs the full fixed config).
    flat_params: dict[str, Any] = dict(sampled)
    for k, v in lora_overrides.items():
        flat_params[f"lora.{k}"] = v

    return SampledTrial(config=new_config, params=flat_params)


def best_params_to_config(
    best_params: dict[str, Any],
    fixed_config: ManualTrainingConfig,
) -> ManualTrainingConfig:
    """Rebuild a `ManualTrainingConfig` from Optuna's flat `study.best_params`.

    Used at the end of an HPO run to retrain on the winning hyperparameters.
    """
    base = fixed_config.model_dump()
    lora_dict = dict(base["lora"])

    for key, value in best_params.items():
        if key == "lora_r":
            lora_dict["r"] = int(value)
        elif key == "lora_alpha":
            lora_dict["alpha"] = int(value)
        elif key == "lora_dropout":
            lora_dict["dropout"] = float(value)
        else:
            base[key] = value

    base["lora"] = lora_dict
    return ManualTrainingConfig.model_validate(base)


__all__ = [
    "SampledTrial",
    "sample_config",
    "best_params_to_config",
    # re-exported for convenience
    "HPOSearchSpace",
    "ManualTrainingConfig",
    "LoRAConfig",
]
