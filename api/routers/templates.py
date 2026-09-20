"""Authenticated curated catalog and editable eligible-user ratings."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.auth import CurrentUser, require_user
from api.core.database import get_db
from api.schemas.responses import Page
from api.schemas.templates import TemplateRatingRequest, TemplateRatingResponse, TemplateResponse
from api.services import templates_service

router = APIRouter()


@router.get("", response_model=Page[TemplateResponse], summary="List curated templates")
async def list_templates(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
    category: str | None = None,
    featured: bool | None = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    sort: Literal["popular", "rating", "forks"] = "popular",
    include_unavailable: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[TemplateResponse]:
    return await templates_service.list_templates(
        db,
        user=user,
        category=category,
        featured=featured,
        search=search,
        sort=sort,
        include_unavailable=include_unavailable,
        limit=limit,
        offset=offset,
    )


@router.put(
    "/{template_id}/rating",
    response_model=TemplateRatingResponse,
    summary="Set or edit your template rating",
)
async def rate_template(
    template_id: str,
    body: TemplateRatingRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser | None, Depends(require_user)],
) -> TemplateRatingResponse:
    return await templates_service.rate_template(db, template_id, body.rating, user)
