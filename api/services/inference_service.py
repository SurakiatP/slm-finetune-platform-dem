"""Application service for `/api/v1/inference/*`.

Ollama exposes its own OpenAI-compatible router under `/v1/...`. Our endpoints
forward the validated body, swap the `model` field if the caller passed a
ModelArtifact UUID instead of an Ollama tag, and return Ollama's response.

We deliberately do NOT support streaming (`stream=true`) in this PoC — adding
SSE proxying is straightforward but out of scope per Phase 7 requirements.
"""

from __future__ import annotations

import logging
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.models.model_artifact import ModelArtifact
from api.schemas.inference import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    CompletionRequest,
    CompletionResponse,
    ModelDescriptor,
    ModelDescriptorList,
)

log = logging.getLogger(__name__)

# Ollama's OpenAI-compat router lives under /v1; native API under /api.
_OLLAMA_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0)


async def chat_completions(
    db: AsyncSession,
    body: ChatCompletionRequest,
) -> ChatCompletionResponse:
    if body.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="streaming is not supported on /api/v1/inference (set stream=false)",
        )
    tag = await _resolve_model_tag(db, body.model)
    payload = body.model_dump(exclude_none=True)
    payload["model"] = tag

    raw = await _post_json("/v1/chat/completions", payload)
    return ChatCompletionResponse.model_validate(raw)


async def text_completions(
    db: AsyncSession,
    body: CompletionRequest,
) -> CompletionResponse:
    if body.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="streaming is not supported on /api/v1/inference (set stream=false)",
        )
    tag = await _resolve_model_tag(db, body.model)
    payload = body.model_dump(exclude_none=True)
    payload["model"] = tag

    raw = await _post_json("/v1/completions", payload)
    return CompletionResponse.model_validate(raw)


async def list_models() -> ModelDescriptorList:
    """List models known to the local Ollama daemon (OpenAI shape)."""
    raw = await _get_json("/v1/models")
    items = []
    for entry in raw.get("data") or []:
        items.append(
            ModelDescriptor(
                id=entry["id"],
                created=int(entry.get("created", 0)),
                owned_by=entry.get("owned_by", "slm-platform"),
                metadata=None,
            )
        )
    return ModelDescriptorList(data=items)


# ---- helpers ---------------------------------------------------------------


async def _resolve_model_tag(db: AsyncSession, identifier: str) -> str:
    """Accept either a UUID (ModelArtifact id) or a literal Ollama tag.

    UUID → look up ollama_model_tag on the artifact (must be exported first).
    Otherwise pass through verbatim (allows callers to use base models the
    daemon already has, like `llama3.2:3b`).
    """
    try:
        artifact_id = UUID(identifier)
    except (ValueError, TypeError):
        return identifier

    artifact = await db.get(ModelArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model {artifact_id} not found",
        )
    if not artifact.ollama_model_tag:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Model {artifact_id} has not been exported to Ollama yet. "
                f"POST /api/v1/models/{artifact_id}/export with format=gguf first."
            ),
        )
    return artifact.ollama_model_tag


async def _post_json(path: str, payload: dict) -> dict:
    base = str(get_settings().ollama_base_url).rstrip("/")
    async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT) as client:
        try:
            resp = await client.post(f"{base}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"ollama unreachable: {exc}",
            ) from exc
    if resp.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ollama: model not found ({resp.text[:200]})",
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"ollama returned {resp.status_code}: {resp.text[:300]}",
        )
    return resp.json()


async def _get_json(path: str) -> dict:
    base = str(get_settings().ollama_base_url).rstrip("/")
    async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT) as client:
        try:
            resp = await client.get(f"{base}{path}")
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"ollama unreachable: {exc}",
            ) from exc
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"ollama returned {resp.status_code}: {resp.text[:300]}",
        )
    return resp.json()


__all__ = ["chat_completions", "text_completions", "list_models"]
