"""Inference router — OpenAI-compatible passthrough to Ollama.

Accepts **either** a Supabase/OIDC JWT (unchanged) **or** an `sk-slm-...` API
key as the `Authorization: Bearer ...` credential — see `inference_caller`.
`api/main.py` mounts this router with `Depends(inference_caller)` (not the
JWT-only `_AUTH`), so an `sk-slm-...` key reaches the key path; FastAPI's
per-request dependency cache makes the router-level and per-route
declarations resolve once. Tests exercising the key path build a local
`FastAPI()` app around this router instead of importing the real app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser, current_user_optional, extract_bearer_token, require_user
from api.core.database import get_db
from api.schemas.inference import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    CompletionRequest,
    CompletionResponse,
    ModelDescriptorList,
)
from api.services import api_keys_service, idempotency, inference_service

router = APIRouter()

# Documents the `stream=true` shape for both POST routes below — Ollama's SSE
# framing terminated by `data: [DONE]` — alongside the existing JSON
# `response_model` for `stream=false` (FastAPI passes a returned
# `StreamingResponse` through untouched, so both shapes coexist on one route).
_STREAM_RESPONSE = {
    200: {
        "content": {
            "text/event-stream": {
                "schema": {
                    "type": "string",
                    "description": (
                        "OpenAI-style SSE: `data: {chunk}` lines, "
                        "terminated by `data: [DONE]`"
                    ),
                }
            }
        }
    }
}


@dataclass(frozen=True, slots=True)
class InferenceCaller:
    """The resolved caller of an `/inference/*` request, whichever credential
    shape it used. `api_key_id` is `None` on the JWT path (unchanged
    behaviour) and set to the key's id on the key path."""

    user: CurrentUser | None
    api_key_id: str | None = None


async def inference_caller(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> InferenceCaller:
    """Resolve either an `sk-slm-...` API key or a JWT, in that order.

    Only a bearer token that actually starts with the key prefix is treated
    as a key — anything else (including no header at all) falls through to
    the exact JWT path this router used before (`current_user_optional` then
    `require_user`), so JWT behaviour stays byte-for-byte identical.
    """
    token = extract_bearer_token(authorization)
    if token is not None and token.startswith(api_keys_service.KEY_PREFIX):
        key = await api_keys_service.authenticate(db, token)
        return InferenceCaller(
            user=CurrentUser(id=key.owner_id, email=None), api_key_id=str(key.id)
        )
    user = await require_user(await current_user_optional(authorization))
    return InferenceCaller(user=user)


@router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    summary="OpenAI-compatible chat completions (proxied to Ollama)",
    responses=_STREAM_RESPONSE,
)
async def chat_completions(
    body: ChatCompletionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    caller: Annotated[InferenceCaller, Depends(inference_caller)],
    request: Request,
) -> ChatCompletionResponse | StreamingResponse:
    return await inference_service.chat_completions(
        db, body, caller.user,
        api_key_id=caller.api_key_id,
        actor=idempotency.actor_for(request, caller.user),
    )


@router.post(
    "/completions",
    response_model=CompletionResponse,
    summary="OpenAI-compatible legacy text completions",
    responses=_STREAM_RESPONSE,
)
async def text_completions(
    body: CompletionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    caller: Annotated[InferenceCaller, Depends(inference_caller)],
    request: Request,
) -> CompletionResponse | StreamingResponse:
    return await inference_service.text_completions(
        db, body, caller.user,
        api_key_id=caller.api_key_id,
        actor=idempotency.actor_for(request, caller.user),
    )


@router.get(
    "/models",
    response_model=ModelDescriptorList,
    summary="OpenAI-compatible model listing (Ollama-served)",
)
async def list_inference_models(
    db: Annotated[AsyncSession, Depends(get_db)],
    caller: Annotated[InferenceCaller, Depends(inference_caller)],
) -> ModelDescriptorList:
    return await inference_service.list_models(db, caller.user, api_key_id=caller.api_key_id)
