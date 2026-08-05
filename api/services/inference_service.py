"""Application service for `/api/v1/inference/*`.

Ollama exposes its own OpenAI-compatible router under `/v1/...`. Our endpoints
forward the validated body, swap the `model` field if the caller passed a
ModelArtifact UUID instead of an Ollama tag, and return Ollama's response.

We deliberately do NOT support streaming (`stream=true`) in this PoC — adding
SSE proxying is straightforward but out of scope per Phase 7 requirements.

**Ownership decision (feat/be-auth001, W3)**: `chat_completions` /
`text_completions` ownership-check `body.model` whenever it's a
ModelArtifact UUID (`_resolve_model_tag` -> `ownership.assert_model_access`)
— running inference against someone else's private fine-tune is the same
class of attack as reading or exporting it. `list_models` (`GET
/inference/models`) is left UNFILTERED: it lists every tag the shared
Ollama daemon knows about, not per-caller. Reasoning: (1) Ollama's tag
namespace has no user-scoping concept at all — it's one daemon shared by
every project, per `require.md`'s single-GPU-box assumption; (2) a tag
*could* be reverse-mapped through `ModelArtifact.ollama_model_tag ->
TrainingJob -> Project.owner_id`, but silently dropping tags that don't
resolve that way (base models pulled directly into Ollama, never exported
through our pipeline) would make the listing inconsistent in a way that's
arguably more confusing than informative; (3) the acceptance criterion this
branch targets is DB-resource ownership (project/dataset/training/artifact/
evaluation/job-stream) — a bare tag string is a capability from Ollama's
own namespace, not one of ours. If per-user model listing becomes a real
product requirement later, it needs the reverse-map query, and should
probably also decide what a *base* model interception even means to hide.
"""

from __future__ import annotations

import logging
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser
from api.core.config import get_settings
from api.schemas.inference import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    CompletionRequest,
    CompletionResponse,
    ModelDescriptor,
    ModelDescriptorList,
)
from api.services import ownership

log = logging.getLogger(__name__)

# Ollama's OpenAI-compat router lives under /v1; native API under /api.
_OLLAMA_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0)


async def chat_completions(
    db: AsyncSession,
    body: ChatCompletionRequest,
    user: CurrentUser | None = None,
) -> ChatCompletionResponse:
    if body.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="streaming is not supported on /api/v1/inference (set stream=false)",
        )
    tag = await _resolve_model_tag(db, body.model, user)
    payload = body.model_dump(exclude_none=True)
    payload["model"] = tag

    raw = await _post_json("/v1/chat/completions", payload)
    return ChatCompletionResponse.model_validate(raw)


async def text_completions(
    db: AsyncSession,
    body: CompletionRequest,
    user: CurrentUser | None = None,
) -> CompletionResponse:
    if body.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="streaming is not supported on /api/v1/inference (set stream=false)",
        )
    tag = await _resolve_model_tag(db, body.model, user)
    payload = body.model_dump(exclude_none=True)
    payload["model"] = tag

    raw = await _post_json("/v1/completions", payload)
    return CompletionResponse.model_validate(raw)


async def list_models() -> ModelDescriptorList:
    """List models known to the local Ollama daemon (OpenAI shape).

    Deliberately NOT ownership-filtered — see module docstring's "inference
    decision" note. Every literal Ollama tag the shared daemon knows about
    is visible to any authenticated caller.
    """
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


async def _resolve_model_tag(
    db: AsyncSession, identifier: str, user: CurrentUser | None = None
) -> str:
    """Accept either a UUID (ModelArtifact id) or a literal Ollama tag.

    UUID → look up ollama_model_tag on the artifact (must be exported first),
    ownership-checked exactly like `GET /models/{id}` — running (free)
    inference against someone else's private fine-tune is the same class of
    attack as reading or exporting it. Otherwise pass through verbatim
    (allows callers to use base models the daemon already has, like
    `llama3.2:3b`) — a literal tag carries no ownership information to
    check against.
    """
    try:
        artifact_id = UUID(identifier)
    except (ValueError, TypeError):
        return identifier

    artifact = await ownership.assert_model_access(db, artifact_id, user)
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
