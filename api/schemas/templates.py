"""Snake-case marketplace contract and bounded user overrides."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from api.schemas.enums import TaskType


class TemplateOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_model: str | None = None
    system_prompt: str | None = Field(default=None, min_length=1, max_length=16000)
    epochs: StrictInt | None = Field(default=None, ge=1, le=20)
    learning_rate: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    train_sample_count: StrictInt | None = Field(default=None, ge=1)
    sampling_seed: StrictInt | None = Field(default=None, ge=0, le=2**32 - 1)

    @field_validator("base_model")
    @classmethod
    def supported_model(cls, value: str | None) -> str | None:
        from api.routers.tasks_meta import SUPPORTED_BASE_MODELS

        if value is not None and value not in {model.id for model in SUPPORTED_BASE_MODELS}:
            raise ValueError("base_model must be a supported training model")
        return value


class TemplateRatingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: StrictInt = Field(ge=1, le=5)


class TemplateRatingResponse(BaseModel):
    rating: float | None = None
    rating_count: int = 0
    my_rating: int | None = None


class TemplateResponse(TemplateRatingResponse):
    id: str
    version: str
    name: str
    description: str
    long_description: str
    category: str
    task_type: TaskType
    base_model: str
    prompt: str
    epochs: int
    learning_rate: float
    dataset_size: int
    forks: int = 0
    author: str
    tags: list[str]
    featured: bool
    available: bool
    unavailable_reason: str | None = None
    split_counts: dict[str, int]
    source_attribution: dict[str, Any] | None = None
