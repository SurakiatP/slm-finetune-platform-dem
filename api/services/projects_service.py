"""Application service for `/api/v1/projects` (full CRUD)."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core import request_context
from api.core.auth import CurrentUser
from api.models.project import Project
from api.schemas.projects import ProjectCreate, ProjectResponse, ProjectUpdate
from api.schemas.responses import Page
from api.services import audit_service, ownership, queue_position


def _actor() -> tuple[str | None, str | None]:
    """Who is acting, and under which request — read off the request-scoped
    context rather than threaded through every signature, so adding an audit
    call never forces a router change."""
    return request_context.current_user_id(), request_context.current_request_id()


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
    await db.flush()  # assigns project.id so the audit row can name it
    actor_id, request_id = _actor()
    audit_service.record(
        db,
        action="project.create",
        resource_type="project",
        resource_id=str(project.id),
        project_id=project.id,
        actor_id=actor_id,
        request_id=request_id,
        metadata={"name": project.name, "task_type": project.task_type.value},
    )
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
    response = ProjectResponse.model_validate(project)
    # Display-only, in-flight-only: populate the GPU queue fields on the
    # detail response only (never on list_projects — see queue_position.py).
    # `None` means "nothing in flight for this project", so the response's
    # default-None fields are left untouched in that case.
    info = await queue_position.project_queue_info(db, project_id)
    if info is not None:
        response = response.model_copy(
            update={
                "queue_state": info.queue_state,
                "queue_position": info.queue_position,
                "owner_queue_position": info.owner_queue_position,
            }
        )
    return response


async def update_project(
    db: AsyncSession, project_id: UUID, body: ProjectUpdate, user: CurrentUser | None = None
) -> ProjectResponse:
    project = await ownership.assert_project_access(db, project_id, user)
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    actor_id, request_id = _actor()
    audit_service.record(
        db,
        action="project.update",
        resource_type="project",
        resource_id=str(project.id),
        project_id=project.id,
        actor_id=actor_id,
        request_id=request_id,
    )
    await db.commit()
    await db.refresh(project)
    return ProjectResponse.model_validate(project)


async def delete_project(
    db: AsyncSession, project_id: UUID, user: CurrentUser | None = None
) -> None:
    project = await ownership.assert_project_access(db, project_id, user)
    actor_id, request_id = _actor()
    # Recorded before the delete, and deliberately keeps `resource_id` as a
    # plain string: `project_id` is ON DELETE SET NULL, so the FK drops away
    # but the trail of who deleted what survives the deletion it records.
    audit_service.record(
        db,
        action="project.delete",
        resource_type="project",
        resource_id=str(project.id),
        project_id=project.id,
        actor_id=actor_id,
        request_id=request_id,
        metadata={"name": project.name},
    )
    await db.delete(project)
    await db.commit()


__all__ = [
    "create_project",
    "list_projects",
    "get_project",
    "update_project",
    "delete_project",
]

