"""Deployments router — logical serving slots on the shared Ollama daemon.

Always-auth resource (like `templates`/`api-keys`): every route depends on
`require_authenticated_user`, not the phased `require_user` — there is no
anonymous owner to bucket a deployment under, regardless of
`settings.auth_required`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser, require_authenticated_user
from api.core.database import get_db
from api.schemas.deployments import DeploymentCreate, DeploymentResponse, DeploymentUpdate
from api.schemas.enums import JobStatus
from api.schemas.responses import Page
from api.services import idempotency
from api.services.deployments_service import (
    create_deployment as _create_deployment,
    delete_deployment as _delete_deployment,
    get_deployment as _get_deployment,
    list_deployments as _list_deployments,
    stop_deployment as _stop_deployment,
    update_deployment as _update_deployment,
)

router = APIRouter()


@router.post(
    "",
    response_model=DeploymentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a deployment (enqueues a preload onto the shared Ollama daemon)",
)
async def create_deployment(
    body: DeploymentCreate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_authenticated_user)],
) -> DeploymentResponse | JSONResponse:
    # Same dedupe policy as SDG generate / training start / model export
    # (see `api.services.idempotency`) — a double-click here would otherwise
    # reserve a second deployment slot for the same artifact.
    body_json = body.model_dump(mode="json")
    if (replayed := await idempotency.replay(request, user, body_json)) is not None:
        return replayed
    resp = await _create_deployment(db, body, user)
    await idempotency.remember(request, user, body_json, resp.model_dump(mode="json"))
    return resp


@router.get(
    "",
    response_model=Page[DeploymentResponse],
    summary="List my deployments",
)
async def list_deployments(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_authenticated_user)],
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DeploymentResponse]:
    return await _list_deployments(
        db, user, status_filter=status_filter, limit=limit, offset=offset
    )


@router.get(
    "/{deployment_id}",
    response_model=DeploymentResponse,
    summary="Get a deployment by id",
)
async def get_deployment(
    deployment_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_authenticated_user)],
) -> DeploymentResponse:
    return await _get_deployment(db, deployment_id, user)


@router.patch(
    "/{deployment_id}",
    response_model=DeploymentResponse,
    summary="Rename a deployment or change its rate limit",
)
async def update_deployment(
    deployment_id: UUID,
    body: DeploymentUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_authenticated_user)],
) -> DeploymentResponse:
    return await _update_deployment(db, deployment_id, body, user)


@router.post(
    "/{deployment_id}/stop",
    response_model=DeploymentResponse,
    summary="Stop a deployment (idempotent once terminal)",
)
async def stop_deployment(
    deployment_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_authenticated_user)],
) -> DeploymentResponse:
    return await _stop_deployment(db, deployment_id, user)


@router.delete(
    "/{deployment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a deployment (stops it first if still active)",
)
async def delete_deployment(
    deployment_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_authenticated_user)],
) -> None:
    await _delete_deployment(db, deployment_id, user)
