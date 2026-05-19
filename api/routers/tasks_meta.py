"""Static metadata endpoints — fully implemented (no Celery/DB needed).

Two distinct routers in this file, mounted at different prefixes in main.py:
  • `tasks_router`        → `/api/v1/tasks` and `/api/v1/tasks/{task_type}/example`
  • `base_models_router`  → `/api/v1/base-models`

These power the frontend's dynamic forms.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from api.schemas.data_formats import (
    ClassificationSample,
    QASample,
    ToolCallingSample,
)
from api.schemas.enums import TaskType
from api.schemas.tasks_meta import BaseModelInfo, TaskTypeInfo
from api.services.base_model_catalog import get_ollama_base_tag

# ---- Tasks ----------------------------------------------------------------

tasks_router = APIRouter()


_TASK_REGISTRY: dict[TaskType, TaskTypeInfo] = {
    TaskType.CLASSIFICATION: TaskTypeInfo(
        task_type=TaskType.CLASSIFICATION,
        display_name="Text Classification",
        description=(
            "Assign one label from a closed set to each input text. "
            "Good for support ticket routing, intent detection, sentiment, etc."
        ),
        sample_schema=ClassificationSample.model_json_schema(),
        example={"text": "I can't log into my account", "label": "technical"},
    ),
    TaskType.TOOL_CALLING: TaskTypeInfo(
        task_type=TaskType.TOOL_CALLING,
        display_name="Tool Calling",
        description=(
            "Given a user instruction, emit a JSON object naming the tool to "
            "call and its parameters. The `answer` field is a JSON-encoded string."
        ),
        sample_schema=ToolCallingSample.model_json_schema(),
        example={
            "question": "Set the oven to 250 degrees Celsius",
            "answer": '{"name":"set_oven","parameters":{"celsius":250}}',
        },
    ),
    TaskType.QA: TaskTypeInfo(
        task_type=TaskType.QA,
        display_name="Question Answering",
        description=(
            "Generate a free-form answer to a user question. "
            "Good for FAQ bots, documentation assistants, etc."
        ),
        sample_schema=QASample.model_json_schema(),
        example={
            "question": "What is the return policy?",
            "answer": "You can return items within 30 days of purchase.",
        },
    ),
}


@tasks_router.get(
    "",
    response_model=list[TaskTypeInfo],
    summary="List supported task types",
)
async def list_task_types() -> list[TaskTypeInfo]:
    return list(_TASK_REGISTRY.values())


@tasks_router.get(
    "/{task_type}/example",
    response_model=dict,
    summary="Get one canonical example row for a task type",
)
async def get_task_example(task_type: TaskType) -> dict[str, object]:
    info = _TASK_REGISTRY.get(task_type)
    if info is None:  # pragma: no cover — Pydantic enum validation catches this first
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown task_type: {task_type}",
        )
    return info.example


# ---- Base models ----------------------------------------------------------

base_models_router = APIRouter()


# Note: `ollama_tag` is filled below from `base_model_catalog._BASE_TO_OLLAMA_TAG`
# so the mapping has a single source of truth.
_RAW_BASE_MODELS: list[BaseModelInfo] = [
    BaseModelInfo(
        id="unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
        display_name="Llama 3.2 1B Instruct (4-bit)",
        family="llama",
        params_billions=1.24,
        context_length=131072,
        recommended_max_seq_length=2048,
        license="llama-3.2",
        notes="Fastest, lowest VRAM. Good first choice for QA and classification PoCs.",
    ),
    BaseModelInfo(
        id="unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
        display_name="Llama 3.2 3B Instruct (4-bit) — default",
        family="llama",
        params_billions=3.21,
        context_length=131072,
        recommended_max_seq_length=2048,
        license="llama-3.2",
        notes="Default base model. ~3.21B params; fits comfortably on RTX 3060 12GB in QLoRA.",
    ),
    BaseModelInfo(
        id="unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit",
        display_name="Qwen2.5 0.5B Instruct (4-bit)",
        family="qwen",
        params_billions=0.49,
        context_length=32768,
        recommended_max_seq_length=2048,
        license="apache-2.0",
        notes="Smallest option; useful for fast iteration on small datasets.",
    ),
    BaseModelInfo(
        id="unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit",
        display_name="Qwen2.5 1.5B Instruct (4-bit)",
        family="qwen",
        params_billions=1.54,
        context_length=32768,
        recommended_max_seq_length=2048,
        license="apache-2.0",
    ),
    BaseModelInfo(
        id="unsloth/Qwen2.5-3B-Instruct-bnb-4bit",
        display_name="Qwen2.5 3B Instruct (4-bit)",
        family="qwen",
        params_billions=3.09,
        context_length=32768,
        recommended_max_seq_length=2048,
        license="qwen-research",
        notes="Strong general-purpose alternative to Llama 3.2 3B.",
    ),
    BaseModelInfo(
        id="unsloth/gemma-2-2b-it-bnb-4bit",
        display_name="Gemma 2 2B Instruct (4-bit)",
        family="gemma",
        params_billions=2.61,
        context_length=8192,
        recommended_max_seq_length=2048,
        license="gemma",
        notes="Good middle-ground in size; shorter native context window than Llama/Qwen.",
    ),
    # ---- Qwen3 family (newer generation — thinking-mode capable) ----------
    BaseModelInfo(
        id="unsloth/Qwen3-0.6B-unsloth-bnb-4bit",
        display_name="Qwen3 0.6B Instruct (4-bit)",
        family="qwen",
        params_billions=0.75,
        context_length=32768,
        recommended_max_seq_length=2048,
        license="apache-2.0",
        notes="Newer Qwen generation; thinking-mode capable. Smallest Qwen3 variant — drop-in upgrade for Qwen2.5-0.5B.",
    ),
    BaseModelInfo(
        id="unsloth/Qwen3-1.7B-unsloth-bnb-4bit",
        display_name="Qwen3 1.7B Instruct (4-bit)",
        family="qwen",
        params_billions=2.03,
        context_length=32768,
        recommended_max_seq_length=2048,
        license="apache-2.0",
        notes="Drop-in upgrade for Qwen2.5-1.5B. Thinking-mode capable; strong general-purpose at sub-2B size.",
    ),
    # ---- SmolLM (HuggingFace native, fine-tune-optimized) -----------------
    BaseModelInfo(
        id="unsloth/SmolLM2-1.7B-Instruct-bnb-4bit",
        display_name="SmolLM2 1.7B Instruct (4-bit)",
        family="smollm",
        params_billions=1.71,
        context_length=8192,
        recommended_max_seq_length=2048,
        license="apache-2.0",
        notes="HuggingFace TB native; trained on 11T tokens. Optimized for fine-tuning per Distil Labs benchmark. Apache 2.0.",
    ),
    # ---- TinyLlama (smallest serious chat) --------------------------------
    BaseModelInfo(
        id="unsloth/tinyllama-chat-bnb-4bit",
        display_name="TinyLlama 1.1B Chat (4-bit)",
        family="llama",
        params_billions=1.10,
        context_length=2048,
        recommended_max_seq_length=2048,
        license="apache-2.0",
        notes="Smallest VRAM footprint (~600MB 4-bit). Best for edge/IoT prototyping. Older Llama-2 architecture (2023) — capable but less polished than Llama 3.2.",
    ),
]

SUPPORTED_BASE_MODELS: list[BaseModelInfo] = [
    bm.model_copy(update={"ollama_tag": get_ollama_base_tag(bm.id)})
    for bm in _RAW_BASE_MODELS
]


@base_models_router.get(
    "",
    response_model=list[BaseModelInfo],
    summary="List supported base models",
)
async def list_base_models() -> list[BaseModelInfo]:
    return SUPPORTED_BASE_MODELS


__all__ = ["tasks_router", "base_models_router", "SUPPORTED_BASE_MODELS"]
