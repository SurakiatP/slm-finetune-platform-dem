"""API keys router — per-user `sk-slm-...` credentials for `/inference/*`.

Always requires a verified caller (`require_authenticated_user`), regardless
of `settings.auth_required` — there is no anonymous owner to bucket a key
under. Mounted in `api/main.py` at `/api/v1/api-keys` without `_AUTH`, like
templates.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser, require_authenticated_user
from api.core.database import get_db
from api.schemas.api_keys import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyResponse
from api.schemas.responses import Page
from api.services import api_keys_service

router = APIRouter()


@router.post(
    "",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key (plaintext shown once)",
)
async def create_api_key(
    body: ApiKeyCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_authenticated_user)],
) -> ApiKeyCreatedResponse:
    return await api_keys_service.create_key(db, body, user)


@router.get("", response_model=Page[ApiKeyResponse], summary="List your API keys")
async def list_api_keys(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_authenticated_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ApiKeyResponse]:
    return await api_keys_service.list_keys(db, user, limit=limit, offset=offset)


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key (soft, idempotent)",
)
async def revoke_api_key(
    key_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_authenticated_user)],
) -> None:
    await api_keys_service.revoke_key(db, key_id, user)
