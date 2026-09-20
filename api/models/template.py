"""Durable template registrations, successful uses, and user ratings."""

from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base, TimestampMixin, uuid_pk


class TemplateDatasetVersion(Base, TimestampMixin):
    __tablename__ = "template_dataset_versions"

    template_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    definition_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    splits_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class TemplateUse(Base, TimestampMixin):
    __tablename__ = "template_uses"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key"),)

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Deliberately no FK: usage and retry responses survive project deletion.
    project_id: Mapped[UUID] = mapped_column(nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class TemplateRating(Base, TimestampMixin):
    __tablename__ = "template_ratings"
    __table_args__ = (CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),)

    template_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
