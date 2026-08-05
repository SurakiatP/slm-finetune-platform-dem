"""Application service for `/api/v1/projects` (full CRUD)."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser
from api.models.project import Project
from api.schemas.projects import ProjectCreate, ProjectResponse, ProjectUpdate
from api.schemas.responses import Page
from api.services import ownership


async def create_project(
    db: AsyncSession, body: ProjectCreate, user: CurrentUser | None = None
) -> ProjectResponse:
    if body.external_project_id is not None:
        existing = (
            await db.execute(
                select(Project).where(
                    Project.external_project_id == body.external_project_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"external_project_id '{body.external_project_id}' is already "
                    f"mapped to project {existing.id}"
                ),
            )

    project = Project(
        name=body.name,
        description=body.description,
        task_type=body.task_type,
        external_project_id=body.external_project_id,
        owner_id=ownership.owner_id_for(user),
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return ProjectResponse.model_validate(project)


async def list_projects(
    db: AsyncSession,
    *,
    limit: int,
    offset: int,
    external_project_id: str | None = None,
    user: CurrentUser | None = None,
) -> Page[ProjectResponse]:
    total_stmt = select(func.count()).select_from(Project)
    stmt = select(Project).order_by(Project.created_at.desc()).limit(limit).offset(offset)
    if external_project_id is not None:
        total_stmt = total_stmt.where(
            Project.external_project_id == external_project_id
        )
        stmt = stmt.where(Project.external_project_id == external_project_id)
    total_stmt = ownership.scope_projects_to_owner(total_stmt, user)
    stmt = ownership.scope_projects_to_owner(stmt, user)
    total = (await db.execute(total_stmt)).scalar_one()
    rows = (await db.execute(stmt)).scalars().all()
    return Page[ProjectResponse](
        items=[ProjectResponse.model_validate(r) for r in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


async def get_project(
    db: AsyncSession, project_id: UUID, user: CurrentUser | None = None
) -> ProjectResponse:
    project = await ownership.assert_project_access(db, project_id, user)
    return ProjectResponse.model_validate(project)


async def update_project(
    db: AsyncSession, project_id: UUID, body: ProjectUpdate, user: CurrentUser | None = None
) -> ProjectResponse:
    project = await ownership.assert_project_access(db, project_id, user)
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    await db.commit()
    await db.refresh(project)
    return ProjectResponse.model_validate(project)


async def delete_project(
    db: AsyncSession, project_id: UUID, user: CurrentUser | None = None
) -> None:
    project = await ownership.assert_project_access(db, project_id, user)
    await db.delete(project)
    await db.commit()


__all__ = [
    "create_project",
    "list_projects",
    "get_project",
    "update_project",
    "delete_project",
]

