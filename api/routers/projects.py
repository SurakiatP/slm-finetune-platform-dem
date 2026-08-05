"""Projects router — CRUD over the top-level grouping entity."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser, require_user
from api.core.database import get_db
from api.schemas.projects import ProjectCreate, ProjectResponse, ProjectUpdate
from api.schemas.responses import Page
from api.services import projects_service

router = APIRouter()


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
)
async def create_project(
    body: ProjectCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> ProjectResponse:
    return await projects_service.create_project(db, body, user)


@router.get(
    "",
    response_model=Page[ProjectResponse],
    summary="List projects",
)
async def list_projects(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    external_project_id: Annotated[str | None, Query()] = None,
) -> Page[ProjectResponse]:
    return await projects_service.list_projects(
        db,
        limit=limit,
        offset=offset,
        external_project_id=external_project_id,
        user=user,
    )


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Get a project by id",
)
async def get_project(
    project_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> ProjectResponse:
    return await projects_service.get_project(db, project_id, user)


@router.patch(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Update a project",
)
async def update_project(
    project_id: UUID,
    body: ProjectUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> ProjectResponse:
    return await projects_service.update_project(db, project_id, body, user)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a project (cascades to datasets / trainings)",
)
async def delete_project(
    project_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> None:
    await projects_service.delete_project(db, project_id, user)
