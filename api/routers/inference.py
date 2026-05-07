"""Inference router — OpenAI-compatible passthrough to Ollama."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.schemas.inference import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    CompletionRequest,
    CompletionResponse,
    ModelDescriptorList,
)
from api.services import inference_service

router = APIRouter()


@router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    summary="OpenAI-compatible chat completions (proxied to Ollama)",
)
async def chat_completions(
    body: ChatCompletionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatCompletionResponse:
    return await inference_service.chat_completions(db, body)


@router.post(
    "/completions",
    response_model=CompletionResponse,
    summary="OpenAI-compatible legacy text completions",
)
async def text_completions(
    body: CompletionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CompletionResponse:
    return await inference_service.text_completions(db, body)


@router.get(
    "/models",
    response_model=ModelDescriptorList,
    summary="OpenAI-compatible model listing (Ollama-served)",
)
async def list_inference_models() -> ModelDescriptorList:
    return await inference_service.list_models()
