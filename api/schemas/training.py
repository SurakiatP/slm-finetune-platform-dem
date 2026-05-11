"""Training request / response schemas — both manual and HPO modes.

`POST /api/v1/trainings` accepts a discriminated `TrainingRequest`:
  • mode=manual → user-supplied hyperparameters (`ManualTrainingConfig`)
  • mode=hpo    → Optuna search space (`HPOConfig`); final model trained on best params

The actual training runs in the GPU-enabled Celery worker. Progress streams over
the WebSocket channel `job:{job_id}` (see schemas/progress.py).
"""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.schemas.enums import JobStatus, TrainingMode

# --- LoRA + manual hyperparameters ------------------------------------------


class LoRAConfig(BaseModel):
    """PEFT/Unsloth LoRA adapter settings."""

    model_config = ConfigDict(extra="forbid")

    r: int = Field(default=16, ge=1, le=128, description="LoRA rank.")
    alpha: int = Field(default=32, ge=1, le=256, description="LoRA scaling factor.")
    dropout: float = Field(default=0.05, ge=0.0, le=0.5)
    # QLoRA paper (Dettmers et al.): attaching LoRA to *all* linear layers
    # (q/k/v/o + gate/up/down) consistently beats q/k/v/o-only, and makes the
    # final accuracy nearly insensitive to `r`. Matches Unsloth Studio default.
    target_modules: list[str] = Field(
        default_factory=lambda: [
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        description="Module names to attach LoRA adapters to.",
        min_length=1,
    )


class ManualTrainingConfig(BaseModel):
    """User-supplied hyperparameters for `mode=manual`.

    Defaults are tuned for RTX 3060 12GB as the lowest-common-denominator GPU
    — they are safe for any base in `SUPPORTED_BASE_MODELS`, including 3B
    models. Users training on smaller bases (≤1.5B) can safely raise
    `per_device_train_batch_size` to 4–8.
    """

    model_config = ConfigDict(extra="forbid")

    learning_rate: float = Field(default=2e-4, gt=0.0, le=1.0)
    num_train_epochs: int = Field(default=3, ge=1, le=20)
    # batch=2 is the safe default for 3B+seq=2048 on 12GB VRAM. Upper bound
    # 16 reflects what a 0.5B model with seq=1024 can realistically use; the
    # HPO service guard rejects choices above the per-model safe ceiling.
    per_device_train_batch_size: int = Field(default=2, ge=1, le=16)
    # grad_accum=8 → effective batch 16 with batch=2 (Studio matches at eff=32
    # with batch=4×accum=8; on 3060 we keep batch low and recommend users
    # increase batch instead of accum when they have headroom).
    gradient_accumulation_steps: int = Field(default=8, ge=1, le=32)
    warmup_ratio: float = Field(default=0.03, ge=0.0, le=0.5)
    weight_decay: float = Field(default=0.01, ge=0.0, le=1.0)
    lr_scheduler_type: Literal["linear", "cosine", "constant"] = "cosine"
    max_seq_length: int = Field(default=2048, ge=128, le=8192)
    seed: int = Field(default=42, ge=0)
    # Optimizer choice: adamw_8bit (default) is bitsandbytes' 8-bit AdamW,
    # the canonical pick for QLoRA. paged_adamw_8bit pages optimizer state
    # to CPU when VRAM is tight — useful on 3B+long-seq runs. adamw_torch
    # is the unquantized reference (uses more VRAM, here for comparison).
    optim: Literal["adamw_8bit", "paged_adamw_8bit", "adamw_torch"] = "adamw_8bit"
    # Sequence packing concatenates short rows to reduce padding. Off by
    # default: chat-templated SFT has edge cases (special tokens leak across
    # boundaries) that hurt instruction-following.
    packing: bool = False
    # NEFTune injects uniform noise into embeddings during training. The
    # paper reports +5–35% AlpacaEval gains; null disables it (safe default).
    neftune_noise_alpha: float | None = Field(default=None, ge=0.0, le=15.0)
    lora: LoRAConfig = Field(default_factory=LoRAConfig)


# --- HPO search space -------------------------------------------------------


class HPOFloatRange(BaseModel):
    """Continuous range, mirrors `optuna.Trial.suggest_float`."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["float"] = "float"
    low: float
    high: float
    log: bool = False

    @model_validator(mode="after")
    def _high_gt_low(self) -> Self:
        if self.high <= self.low:
            raise ValueError("high must be > low")
        if self.log and self.low <= 0.0:
            raise ValueError("log=True requires low > 0")
        return self


class HPOIntRange(BaseModel):
    """Integer range, mirrors `optuna.Trial.suggest_int`."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["int"] = "int"
    low: int
    high: int
    step: int = Field(default=1, ge=1)
    log: bool = False

    @model_validator(mode="after")
    def _high_gt_low(self) -> Self:
        if self.high <= self.low:
            raise ValueError("high must be > low")
        if self.log and self.low <= 0:
            raise ValueError("log=True requires low > 0")
        return self


class HPOCategorical(BaseModel):
    """Discrete choice set, mirrors `optuna.Trial.suggest_categorical`."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["categorical"] = "categorical"
    choices: list[str | int | float | bool] = Field(..., min_length=1)


HPOParam = Annotated[
    HPOFloatRange | HPOIntRange | HPOCategorical,
    Field(discriminator="type"),
]


class HPOSearchSpace(BaseModel):
    """Per-hyperparameter search ranges. Any field left None is held at the
    `fixed_config` value (or its default).

    The fields are an explicit allow-list — adding new tunables requires touching
    both this schema and `ai_engine.hpo.search_spaces`.
    """

    model_config = ConfigDict(extra="forbid")

    learning_rate: HPOFloatRange | None = None
    num_train_epochs: HPOIntRange | None = None
    per_device_train_batch_size: HPOCategorical | None = None
    gradient_accumulation_steps: HPOCategorical | None = None
    warmup_ratio: HPOFloatRange | None = None
    weight_decay: HPOFloatRange | None = None
    lr_scheduler_type: HPOCategorical | None = None
    lora_r: HPOCategorical | None = None
    lora_alpha: HPOCategorical | None = None
    lora_dropout: HPOFloatRange | None = None

    @model_validator(mode="after")
    def _at_least_one_param(self) -> Self:
        if not any(getattr(self, f) is not None for f in self.model_fields):
            raise ValueError("HPOSearchSpace must define at least one parameter")
        return self


class HPOConfig(BaseModel):
    """Configuration for `mode=hpo` runs.

    `n_trials` upper bound and `timeout_seconds` default are calibrated for
    RTX 3060 12GB — one 3B trial takes ~1.5–3h, so 20 trials × 3B would
    already exceed 2 days. Operators training on larger GPUs can raise these
    via `Settings.default_hpo_max_trials` (defense-in-depth at the service
    layer) but the schema cap stays conservative.
    """

    model_config = ConfigDict(extra="forbid")

    n_trials: int = Field(default=6, ge=2, le=20)
    objective_metric: str = Field(
        default="eval_loss",
        description="Metric name as it appears in the HuggingFace Trainer logs.",
    )
    direction: Literal["minimize", "maximize"] = "minimize"
    # Default 4-hour cap so a runaway study can't burn the GPU all weekend.
    # Pass `null` to opt out (only for operators who know what they're doing).
    timeout_seconds: int | None = Field(
        default=14400,
        ge=60,
        description="Wall-clock cap across all trials. Default 4h; null disables.",
    )
    sampler: Literal["tpe", "random"] = "tpe"
    pruner: Literal["median", "none"] = "median"
    search_space: HPOSearchSpace
    fixed_config: ManualTrainingConfig = Field(
        default_factory=ManualTrainingConfig,
        description="Baseline values for hyperparameters not in the search space.",
    )


# --- Training request (discriminated union) ---------------------------------


class _TrainingRequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    dataset_id: UUID
    base_model: str | None = Field(
        default=None,
        description="Override DEFAULT_BASE_MODEL (e.g. 'unsloth/Llama-3.2-1B-Instruct-bnb-4bit').",
    )
    training_name: str | None = Field(
        default=None,
        description="MLflow run name; defaults to project + timestamp.",
    )


class ManualTrainingRequest(_TrainingRequestBase):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "mode": "manual",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "dataset_id": "00000000-0000-0000-0000-000000000010",
                    "base_model": "unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
                    "training_name": "qa-policy-v1",
                    "manual_config": {
                        "learning_rate": 2e-4,
                        "num_train_epochs": 3,
                        "per_device_train_batch_size": 2,
                        "gradient_accumulation_steps": 8,
                        "optim": "adamw_8bit",
                        "packing": False,
                        "neftune_noise_alpha": None,
                        "lora": {
                            "r": 16,
                            "alpha": 32,
                            "dropout": 0.05,
                            "target_modules": [
                                "q_proj", "k_proj", "v_proj", "o_proj",
                                "gate_proj", "up_proj", "down_proj"
                            ],
                        },
                    },
                }
            ]
        },
    )
    mode: Literal[TrainingMode.MANUAL] = TrainingMode.MANUAL
    manual_config: ManualTrainingConfig = Field(default_factory=ManualTrainingConfig)

    # Note: the example above uses 3060-safe defaults (batch=2, grad_accum=8,
    # all-7 target_modules, optim=adamw_8bit). For ≤1.5B bases on 12GB VRAM
    # you can safely raise per_device_train_batch_size to 4–8.


class HPOTrainingRequest(_TrainingRequestBase):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "mode": "hpo",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "dataset_id": "00000000-0000-0000-0000-000000000010",
                    "training_name": "qa-policy-search",
                    "hpo_config": {
                        "n_trials": 6,
                        "objective_metric": "eval_loss",
                        "direction": "minimize",
                        "sampler": "tpe",
                        "pruner": "median",
                        "timeout_seconds": 14400,
                        "search_space": {
                            "learning_rate": {
                                "type": "float", "low": 1e-5, "high": 5e-4, "log": True
                            },
                            "lora_r": {"type": "categorical", "choices": [8, 16, 32]},
                            "lora_alpha": {"type": "categorical", "choices": [16, 32, 64]},
                            "num_train_epochs": {"type": "int", "low": 2, "high": 4},
                            "gradient_accumulation_steps": {
                                "type": "categorical", "choices": [4, 8, 16]
                            },
                        },
                    },
                }
            ]
        },
    )
    mode: Literal[TrainingMode.HPO] = TrainingMode.HPO
    hpo_config: HPOConfig


TrainingRequest = Annotated[
    ManualTrainingRequest | HPOTrainingRequest,
    Field(discriminator="mode"),
]
"""Body for `POST /api/v1/trainings`."""


# --- Responses --------------------------------------------------------------


class TrainingJobAcceptedResponse(BaseModel):
    """202 Accepted body returned when a training job is enqueued."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    training_id: UUID
    mlflow_run_id: str | None = None
    mlflow_url: str | None = None
    status: JobStatus = JobStatus.PENDING
    websocket_url: str


__all__ = [
    "LoRAConfig",
    "ManualTrainingConfig",
    "HPOFloatRange",
    "HPOIntRange",
    "HPOCategorical",
    "HPOParam",
    "HPOSearchSpace",
    "HPOConfig",
    "ManualTrainingRequest",
    "HPOTrainingRequest",
    "TrainingRequest",
    "TrainingJobAcceptedResponse",
]
